import uuid
import asyncio
import base64
import httpx
import json
import re
from typing import Optional, List

from app.common.logging import get_logger

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.jobs.models import Job
from app.jobs.repository import JobRepository
from app.job_jd.repository import JobJDRepository
from app.users.models import User
from app.users.repository import UserRepository
from app.users.service import UserService
from app.llm.client import LLMClient
from app.llm.prompts import (
    SKILLS_SECTION_SYSTEM,
    SKILLS_SECTION_USER,
    PROFESSIONAL_SUMMARY_SECTION_SYSTEM,
    PROFESSIONAL_SUMMARY_SECTION_USER,
    WORK_EXPERIENCE_SECTION_SYSTEM,
    WORK_EXPERIENCE_SECTION_USER,
    PROJECT_SECTION_SYSTEM,
    USER_PROJECT_SECTION_USER,
    EDUCATION_SECTION_SYSTEM,
    EDUCATION_SECTION_USER,
)
from app.storage.r2 import r2_storage
from app.resumes.repository import LatexPackageRepository
from app.resumes.schemas import (
    CreateResumeUploadUrlsResponse,
    GenerateAiResumeResponse,
    GetResumeDownloadResponse,
    PreviewRequest,
    PreviewResponse,
    SectionDiff,
    BulletChange,
    FinalizeRequest,
    FinalizeResponse,
)
from app.common.service import BaseService
from app.common.exceptions import (
    BadRequestError,
    NotFoundError,
    ForbiddenError,
)

logger = get_logger(__name__)

RESUME_TEX_CONTENT_TYPE = "text/x-tex"
RESUME_PDF_CONTENT_TYPE = "application/pdf"
PRESIGN_EXPIRY_SECONDS = 900

_SLOTS = ["slot_1", "slot_2", "slot_3"]


def _slot_name_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    for s in _SLOTS:
        if f"/{s}." in url:
            return s
    return None


def _extract_bullet_diffs(section_key: str, original: str, optimized: str) -> list[dict]:
    import uuid
    import re
    
    if section_key not in ("work_experience", "projects"):
        return [{
            "change_id": str(uuid.uuid4()),
            "section": section_key,
            "original_text": original,
            "optimized_text": optimized,
            "change_type": "modified" if original.strip() != optimized.strip() else "unchanged"
        }]

    parts_orig = re.split(r'(\\resumeItem\{|\\item\s)', original)
    parts_opt = re.split(r'(\\resumeItem\{|\\item\s)', optimized)
    
    if len(parts_orig) > 1 and len(parts_orig) == len(parts_opt):
        preamble_orig = parts_orig[0]
        preamble_opt = parts_opt[0]
        
        res = []
        if preamble_orig.strip() or preamble_opt.strip():
            res.append({
                "change_id": str(uuid.uuid4()),
                "section": f"{section_key}_preamble",
                "original_text": preamble_orig,
                "optimized_text": preamble_opt,
                "change_type": "modified" if preamble_orig != preamble_opt else "unchanged"
            })
            
        for i in range(1, len(parts_orig), 2):
            delim_orig = parts_orig[i]
            content_orig = parts_orig[i+1]
            delim_opt = parts_opt[i]
            content_opt = parts_opt[i+1]
            
            full_orig = delim_orig + content_orig
            full_opt = delim_opt + content_opt
            
            res.append({
                "change_id": str(uuid.uuid4()),
                "section": section_key,
                "original_text": full_orig,
                "optimized_text": full_opt,
                "change_type": "modified" if full_orig != full_opt else "unchanged"
            })
            
        return res
        
    # Fallback
    return [{
        "change_id": str(uuid.uuid4()),
        "section": section_key,
        "original_text": original,
        "optimized_text": optimized,
        "change_type": "modified" if original.strip() != optimized.strip() else "unchanged"
    }]

def _clean_llm_latex_output(text: Optional[str]) -> str:
    """Sanitize raw LLM completion string by stripping markdown code blocks, quotes, and preambles."""
    if not text:
        return ""
    cleaned = text.strip()

    # Strip markdown code blocks (```latex ... ``` or ``` ...)
    if cleaned.startswith("```"):
        cleaned = re.sub(
            r"^```(?:latex|tex)?\n?", "", cleaned, flags=re.IGNORECASE
        )
    if cleaned.endswith("```"):
        cleaned = re.sub(r"\n?```$", "", cleaned)
    cleaned = cleaned.strip()

    # Strip any internal/residual markdown code fences
    if "```" in cleaned:
        cleaned = re.sub(
            r"```(?:latex|tex)?", "", cleaned, flags=re.IGNORECASE
        )
        cleaned = cleaned.replace("```", "").strip()

    # Strip surrounding quotes or backticks (e.g., “...”, "...", '...', ``...'' or `...`)
    quote_chars = "\"'`“”‘’"
    while (
        cleaned
        and len(cleaned) >= 2
        and cleaned[0] in quote_chars
        and cleaned[-1] in quote_chars
    ):
        cleaned = cleaned[1:-1].strip()

    # Strip opening preambles like "Here is the updated LaTeX snippet:"
    lines = cleaned.splitlines()
    if lines and (
        lines[0].lower().startswith("here is")
        or lines[0].lower().startswith("here's")
        or lines[0].lower().startswith("updated latex")
        or lines[0].lower().startswith("optimized latex")
    ):
        lines = lines[1:]
        cleaned = "\n".join(lines).strip()

    return cleaned


class ResumeService(BaseService):
    def __init__(self, db: AsyncSession):
        super().__init__(db)
        self.job_repo = JobRepository(db)
        self.jd_repo = JobJDRepository(db)

    # ------------------------------------------------------------------
    # Resume endpoints decoupled from job_id
    # ------------------------------------------------------------------

    @staticmethod
    def _resume_key(user_id: uuid.UUID, kind: str, type: str) -> str:
        """kind is 'original' or 'ai'. LaTeX source stored here."""
        if type == "tex":
            return f"resume/{user_id}/{kind}_resume.tex"
        elif type == "pdf":
            return f"resume/{user_id}/{kind}_resume.pdf"
        raise ValueError(f"Unknown resume key type: {type!r}")

    async def create_upload_url(
        self, user: User
    ) -> CreateResumeUploadUrlsResponse:
        """Mint a presigned PUT URL so the client uploads the LaTeX source of one resume copy.

        The client uploads LaTeX only; the server compiles it to PDF (see finalize_resume)
        and stores both. The canonical LaTeX URL is recorded immediately since the key
        is deterministic.
        """
        key = self._resume_key(user.id, "original", "tex")
        presigned_url = r2_storage.generate_presigned_put_url(
            key, RESUME_TEX_CONTENT_TYPE, PRESIGN_EXPIRY_SECONDS
        )
        final = r2_storage._public_url(key)

        user_repo = UserRepository(self.db)
        await user_repo.update(user, original_resume_latex_url=final)

        logger.info(
            "resume_upload_url_created user_id=%s type=%s",
            str(user.id),
            "original",
        )
        return CreateResumeUploadUrlsResponse(
            latex_presigned_url=presigned_url
        )

    async def _extract_keywords_from_resume(self, latex_text: str, user: User) -> list[str]:
        # 1. Fallback NLP extraction based on user.skills if any exist
        nlp_keywords = set()
        if user.skills and isinstance(user.skills, dict):
            for cat, items in user.skills.items():
                if isinstance(items, list):
                    for item in items:
                        nlp_keywords.add(str(item).strip())
        
        # 2. Add some basic standard tech words from latex just in case
        common_tech = ["Python", "Java", "C++", "C#", "JavaScript", "TypeScript", "React", "Angular", "Vue", "Node.js", "AWS", "Azure", "GCP", "SQL", "NoSQL", "Docker", "Kubernetes", "Linux", "Git"]
        for tech in common_tech:
            if re.search(r'\b' + re.escape(tech) + r'\b', latex_text, re.IGNORECASE):
                nlp_keywords.add(tech)

        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            return list(nlp_keywords)

        # 3. Primary LLM extraction
        try:
            from app.llm.prompts import RESUME_KEYWORD_EXTRACTION_SYSTEM
            llm = LLMClient(provider=user.llm_provider, api_key=llm_key)
            prompt = f"Resume LaTeX:\n{latex_text[:10000]}"
            
            from pydantic import BaseModel
            class KeywordsSchema(BaseModel):
                keywords: list[str]

            parsed = await llm.complete_json(
                system=RESUME_KEYWORD_EXTRACTION_SYSTEM,
                user=prompt,
                model=user.current_llm_model,
                response_schema=KeywordsSchema,
                max_tokens=2048,
            )
            llm_keywords = parsed.get("keywords", [])
            
            # Merge with NLP keywords to be safe
            combined = set(nlp_keywords)
            for k in llm_keywords:
                combined.add(k.strip())
                
            return list(combined)
        except Exception as e:
            logger.warning("llm_keyword_extraction_failed user_id=%s error=%s", str(user.id), str(e))
            return list(nlp_keywords)

    async def finalize_resume(
        self, resume_type: str, user: User
    ) -> GetResumeDownloadResponse:
        """Compile the just-uploaded LaTeX to PDF via latexonline.cc and store it.

        Call this after the client PUTs the LaTeX to the presigned URL. Returns the PDF
        download URL. If compilation fails, the PDF URL is None but the LaTeX is kept.
        """
        kind = resume_type
        latex_key = self._resume_key(user.id, kind, "tex")
        latex_text = r2_storage.download_text(latex_key)

        pdf_url = None
        
        # Run PDF compilation and keyword extraction concurrently
        compile_task = asyncio.create_task(_compile_latex_to_pdf_via_api(latex_text, self.db))
        extract_task = None
        if kind == "original":
            extract_task = asyncio.create_task(self._extract_keywords_from_resume(latex_text, user))

        pdf_bytes = await compile_task
        
        if pdf_bytes:
            pdf_key = self._resume_key(user.id, kind, "pdf")
            pdf_url = r2_storage.upload_bytes(
                pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE
            )
        else:
            logger.warning(
                "resume_pdf_compile_failed type=%s user_id=%s",
                kind,
                str(user.id),
            )

        keywords = None
        if extract_task:
            try:
                keywords = await extract_task
            except Exception as e:
                logger.warning("resume_keyword_extraction_failed user_id=%s error=%s", str(user.id), str(e))

        user_repo = UserRepository(self.db)
        if keywords is not None:
            await user_repo.update(user, original_resume_pdf_url=pdf_url, resume_keywords=keywords)
        else:
            await user_repo.update(user, original_resume_pdf_url=pdf_url)

        download_url = (
            r2_storage.generate_presigned_get_url(
                r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS
            )
            if pdf_url
            else None
        )
        logger.info(
            "resume_finalized type=%s has_pdf=%s", kind, bool(pdf_url)
        )
        return GetResumeDownloadResponse(
            version=kind,
            download_url=download_url,
            message=(
                f"{kind} resume compiled to PDF"
                if pdf_url
                else f"{kind} LaTeX uploaded but PDF compilation failed"
            ),
        )

    async def generate_preview(
        self, job_id: uuid.UUID, payload: PreviewRequest, user: User
    ) -> PreviewResponse:
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            raise BadRequestError("LLM provider and API key must be configured")

        job = await self.job_repo.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        self.assert_ownership(job, user.id, "job")

        if not user.original_resume_latex_url:
            raise BadRequestError("Original LaTeX resume must be uploaded")

        jd = await self.jd_repo.get_by_job_id(job_id)
        if jd is None or not jd.raw_text or not jd.raw_text.strip():
            raise BadRequestError("Job description text is empty")

        latex_key = r2_storage.key_from_url(user.original_resume_latex_url)
        original_latex = r2_storage.download_text(latex_key)

        parsed = _parse_latex_sections(original_latex)
        
        _STRUCTURAL_KEYS = {"header", "footer"}
        valid_sections = [s for s in payload.sections if s in parsed and s not in _STRUCTURAL_KEYS]

        if not valid_sections:
            raise BadRequestError("No valid sections found to optimize")

        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)
        
        extra_kws = ", ".join(payload.extra_keywords) if payload.extra_keywords else "None"

        async def _optimize(section: str):
            cfg = _SECTION_PROMPTS.get(section)
            if not cfg: return section, None
            block = parsed.get(section)
            if not block or not block.strip(): return section, None
            
            prompt = cfg["user"].format(
                job_description=jd.raw_text,
                extra_keywords=extra_kws,
                **{cfg["arg"]: block},
            )
            new_block = await llm.complete(
                system=cfg["system"],
                user=prompt,
                model=user.current_llm_model,
                max_tokens=8192,
            )
            cleaned = _clean_llm_latex_output(new_block)
            return section, cleaned

        tasks = [_optimize(s) for s in valid_sections]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        optimized_sections: dict[str, str] = {}
        for res in results:
            if isinstance(res, Exception): continue
            section, new_block = res
            if new_block and _validate_latex(new_block):
                optimized_sections[section] = new_block

        from app.resumes.models import ResumePreview
        from datetime import datetime, timezone, timedelta
        
        section_diffs = []
        section_diffs_db = {}
        
        for section in valid_sections:
            orig = parsed.get(section, "")
            opt = optimized_sections.get(section, orig)
            opt_stripped, _ = _strip_all_section_headings(opt)
            
            if not opt_stripped.strip():
                opt = orig
                opt_stripped = orig
                
            changes = _extract_bullet_diffs(section, orig, opt_stripped)
            section_diffs.append(SectionDiff(
                section_key=section,
                section_title=section.replace("_", " ").title(),
                changes=[BulletChange(**c) for c in changes]
            ))
            section_diffs_db[section] = changes
            
        preview = ResumePreview(
            job_id=job_id,
            user_id=user.id,
            original_latex=original_latex,
            section_diffs=section_diffs_db,
            extra_keywords=payload.extra_keywords or [],
            expires_at=datetime.now(timezone.utc) + timedelta(days=7)
        )
        self.db.add(preview)
        await self.db.commit()
        await self.db.refresh(preview)
        
        return PreviewResponse(
            preview_id=preview.id,
            sections=section_diffs,
            expires_at=preview.expires_at
        )

    async def finalize_preview(
        self, job_id: uuid.UUID, payload: FinalizeRequest, user: User
    ) -> FinalizeResponse:
        from app.resumes.models import ResumePreview
        from sqlalchemy import select
        
        job = await self.job_repo.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        self.assert_ownership(job, user.id, "job")
            
        result = await self.db.execute(
            select(ResumePreview)
            .where(ResumePreview.id == payload.preview_id)
            .where(ResumePreview.user_id == user.id)
            .where(ResumePreview.job_id == job_id)
        )
        preview = result.scalar_one_or_none()
        
        if not preview:
            raise NotFoundError("ResumePreview", str(payload.preview_id))
            
        accepted_ids = set(payload.accepted_change_ids)
        parsed = _parse_latex_sections(preview.original_latex)
        
        optimized_sections = {}
        accepted_count = 0
        rejected_count = 0
        
        for section, changes in preview.section_diffs.items():
            rebuilt_blocks = []
            for c in changes:
                if c["change_type"] == "unchanged":
                    rebuilt_blocks.append(c["original_text"])
                elif c["change_id"] in accepted_ids:
                    rebuilt_blocks.append(c["optimized_text"])
                    accepted_count += 1
                else:
                    rebuilt_blocks.append(c["original_text"])
                    rejected_count += 1
                    
            optimized_sections[section] = "".join(rebuilt_blocks)
            
        optimized_latex = _reconstruct_latex(
            preview.original_latex, parsed, optimized_sections
        )
        
        validated = _validate_latex(optimized_latex)
        
        assigned_slot = _slot_name_from_url(job.optimized_resume_pdf_url) or _slot_name_from_url(job.optimized_resume_latex_url)
        if not assigned_slot:
            ai_jobs = await self.job_repo.list_jobs_with_ai_resumes(user.id)
            used_slots = {
                _slot_name_from_url(j.optimized_resume_pdf_url) or _slot_name_from_url(j.optimized_resume_latex_url): j 
                for j in ai_jobs
            }
            used_slots = {k: v for k, v in used_slots.items() if k}
            free_slots = [s for s in _SLOTS if s not in used_slots]
            if free_slots:
                assigned_slot = free_slots[0]
            else:
                oldest_job = min(used_slots.values(), key=lambda j: j.updated_at or j.created_at)
                assigned_slot = _slot_name_from_url(oldest_job.optimized_resume_pdf_url) or _slot_name_from_url(oldest_job.optimized_resume_latex_url) or "slot_1"
                await self.job_repo.update(oldest_job, optimized_resume_latex_url=None, optimized_resume_pdf_url=None)
                
        ai_tex_key = f"resume/{user.id}/{assigned_slot}.tex"
        ai_pdf_key = f"resume/{user.id}/{assigned_slot}.pdf"
        
        latex_url = r2_storage.upload_text(ai_tex_key, optimized_latex, RESUME_TEX_CONTENT_TYPE)
        
        pdf_url = None
        pdf_bytes = await _compile_latex_to_pdf_via_api(optimized_latex, self.db)
        if pdf_bytes:
            pdf_url = r2_storage.upload_bytes(ai_pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE)
            
        await self.job_repo.update(job, optimized_resume_latex_url=latex_url, optimized_resume_pdf_url=pdf_url)
        
        download_url = r2_storage.generate_presigned_get_url(r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS) if pdf_url else None
        
        return FinalizeResponse(
            download_url=download_url,
            validated=validated,
            accepted_count=accepted_count,
            rejected_count=rejected_count
        )

    async def generate_ai(
        self, job_id: uuid.UUID, sections: list[str], user: User
    ) -> GenerateAiResumeResponse:
        """Generate an ATS-friendly AI resume for a job by optimizing the requested sections.

        Pipeline:
          1. Parse the original LaTeX into JSON sections (deterministic, no LLM).
          2. Filter requested sections to only those that actually exist in the
             original document (prevents fabricating sections that weren't there).
          3. Run one LLM call PER valid section in PARALLEL.
          4. For any section whose LLM output fails validation, fall back to the
             ORIGINAL section block so the resume is always complete and compilable.
          5. Reconstruct the full LaTeX deterministically by splicing optimized
             blocks back into the original (no LLM in reconstruction).
        """
        user_svc = UserService(None)
        llm_key = user_svc.get_decrypted_llm_key(user)
        if not llm_key or not user.llm_provider:
            raise BadRequestError(
                "LLM provider and API key must be configured"
            )

        job = await self.job_repo.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        self.assert_ownership(job, user.id, "job")

        if not user.original_resume_latex_url:
            raise BadRequestError(
                "Original LaTeX resume must be uploaded before generating the AI resume"
            )

        jd = await self.jd_repo.get_by_job_id(job_id)
        if jd is None:
            raise BadRequestError(
                "Job description must be parsed before generating resume"
            )

        if not jd.raw_text or not jd.raw_text.strip():
            raise BadRequestError(
                "Job description text is empty; parse the JD before generating a resume"
            )
        # ── 3-slot AI resume limit per user (slot_1, slot_2, slot_3) ─────
        assigned_slot = _slot_name_from_url(
            job.optimized_resume_pdf_url
        ) or _slot_name_from_url(job.optimized_resume_latex_url)

        if not assigned_slot:
            ai_jobs = await self.job_repo.list_jobs_with_ai_resumes(
                user.id
            )
            used_slots: dict[str, Job] = {}
            for j in ai_jobs:
                s = _slot_name_from_url(
                    j.optimized_resume_pdf_url
                ) or _slot_name_from_url(j.optimized_resume_latex_url)
                if s:
                    used_slots[s] = j

            free_slots = [s for s in _SLOTS if s not in used_slots]
            if free_slots:
                assigned_slot = free_slots[0]
            else:
                # All 3 slots are taken; evict least recently updated job
                oldest_job = min(
                    used_slots.values(),
                    key=lambda j: j.updated_at or j.created_at,
                )
                assigned_slot = (
                    _slot_name_from_url(
                        oldest_job.optimized_resume_pdf_url
                    )
                    or _slot_name_from_url(
                        oldest_job.optimized_resume_latex_url
                    )
                    or "slot_1"
                )
                await self.job_repo.update(
                    oldest_job,
                    optimized_resume_latex_url=None,
                    optimized_resume_pdf_url=None,
                )
                logger.info(
                    "ai_resume_slot_evicted user_id=%s evicted_job_id=%s freed_slot=%s",
                    str(user.id),
                    str(oldest_job.id),
                    assigned_slot,
                )

        ai_tex_key = f"resume/{user.id}/{assigned_slot}.tex"
        ai_pdf_key = f"resume/{user.id}/{assigned_slot}.pdf"

        latex_key = r2_storage.key_from_url(
            user.original_resume_latex_url
        )
        original_latex = r2_storage.download_text(latex_key)
        logger.info(
            "ai_resume_original_latex_len=%d", len(original_latex)
        )

        # ── Step 1: parse into JSON sections (deterministic) ──────────
        parsed = _parse_latex_sections(original_latex)
        parsed_keys = {k: len(v) for k, v in parsed.items()}
        logger.info(
            "ai_resume_generate_start job_id=%s user_id=%s slot=%s sections=%s parsed_keys=%s",
            str(job_id),
            str(user.id),
            assigned_slot,
            sections,
            parsed_keys,
        )

        # ── Step 2: filter to sections that actually exist ────────────
        _STRUCTURAL_KEYS = {"header", "footer"}
        valid_sections = [
            s
            for s in sections
            if s in parsed and s not in _STRUCTURAL_KEYS
        ]
        skipped_sections = [
            s for s in sections if s not in valid_sections
        ]
        if skipped_sections:
            logger.warning(
                "ai_resume_sections_not_in_original job_id=%s skipped=%s "
                "(these sections have no \\section heading in the original resume "
                "and will NOT be added)",
                str(job_id),
                skipped_sections,
            )

        if not valid_sections:
            logger.warning(
                "ai_resume_no_valid_sections job_id=%s requested=%s",
                str(job_id),
                sections,
            )
            # Nothing to optimise – return the original resume as-is.
            latex_url = r2_storage.upload_text(
                ai_tex_key, original_latex, RESUME_TEX_CONTENT_TYPE
            )
            pdf_url = None
            pdf_bytes = await _compile_latex_to_pdf_via_api(
                original_latex, self.db
            )
            if pdf_bytes:
                pdf_url = r2_storage.upload_bytes(
                    ai_pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE
                )
            await self.job_repo.update(
                job,
                optimized_resume_latex_url=latex_url,
                optimized_resume_pdf_url=pdf_url,
            )
            download_url = (
                r2_storage.generate_presigned_get_url(
                    r2_storage.key_from_url(pdf_url),
                    PRESIGN_EXPIRY_SECONDS,
                )
                if pdf_url
                else None
            )
            return GenerateAiResumeResponse(
                download_url=download_url,
                validated=True,
            )

        llm = LLMClient(provider=user.llm_provider, api_key=llm_key)

        # ── Step 3: parallel LLM calls (one per valid section) ────────
        async def _optimize(section: str):
            cfg = _SECTION_PROMPTS.get(section)
            if cfg is None:
                logger.warning(
                    "ai_resume_unknown_section job_id=%s section=%s",
                    str(job_id),
                    section,
                )
                return section, None

            block = parsed.get(section)

            if not block or not block.strip():
                logger.warning(
                    "ai_resume_section_absent job_id=%s section=%s "
                    "(not present in original resume – skipping)",
                    str(job_id),
                    section,
                )
                return section, None

            body_only = _section_body(block)
            if not body_only.strip():
                logger.info(
                    "ai_resume_section_heading_only job_id=%s section=%s "
                    "(heading exists but body is empty – LLM will generate from JD)",
                    str(job_id),
                    section,
                )

            logger.info(
                "ai_resume_optimize_start job_id=%s section=%s block_len=%d",
                str(job_id),
                section,
                len(block),
            )
            prompt = cfg["user"].format(
                job_description=jd.raw_text,
                extra_keywords="None",
                **{cfg["arg"]: block},
            )
            new_block = await llm.complete(
                system=cfg["system"],
                user=prompt,
                model=user.current_llm_model,
                max_tokens=8192,
            )
            cleaned_block = _clean_llm_latex_output(new_block)
            logger.info(
                "ai_resume_llm_raw job_id=%s section=%s new_block_len=%d "
                "cleaned_len=%d preview=%r",
                str(job_id),
                section,
                len(new_block or ""),
                len(cleaned_block),
                cleaned_block[:400],
            )
            if cleaned_block and cleaned_block.strip() == block.strip():
                logger.warning(
                    "ai_resume_section_unchanged job_id=%s section=%s "
                    "(model returned identical block – check JD relevance/length)",
                    str(job_id),
                    section,
                )
            return section, cleaned_block

        optimize_tasks = [_optimize(s) for s in valid_sections]
        optimized_results = await asyncio.gather(
            *optimize_tasks, return_exceptions=True
        )

        # ── Step 4: validate; fall back to original on failure ────────
        optimized_sections: dict[str, str] = {}
        for res in optimized_results:
            if isinstance(res, Exception):
                logger.warning(
                    "ai_resume_section_failed error=%s", str(res)
                )
                continue
            section, new_block = res
            if not new_block:
                logger.warning(
                    "ai_resume_section_empty job_id=%s section=%s",
                    str(job_id),
                    section,
                )
                continue
            if _validate_latex(new_block):
                optimized_sections[section] = new_block
            else:
                logger.warning(
                    "ai_resume_section_invalid_fallback job_id=%s section=%s",
                    str(job_id),
                    section,
                )

        # ── Step 5: deterministic reconstruction (no LLM) ─────────────
        optimized_latex = _reconstruct_latex(
            original_latex, parsed, optimized_sections
        )

        validated = _validate_latex(optimized_latex)
        if not validated:
            logger.warning(
                "ai_resume_latex_validation_failed job_id=%s",
                str(job_id),
            )

        latex_url = r2_storage.upload_text(
            ai_tex_key, optimized_latex, RESUME_TEX_CONTENT_TYPE
        )

        # Compile the optimized LaTeX to PDF.
        pdf_url = None
        pdf_bytes = await _compile_latex_to_pdf_via_api(
            optimized_latex, self.db
        )
        if pdf_bytes:
            pdf_url = r2_storage.upload_bytes(
                ai_pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE
            )
        else:
            logger.warning(
                "ai_resume_pdf_compile_failed job_id=%s slot=%s",
                str(job_id),
                assigned_slot,
            )

        await self.job_repo.update(
            job,
            optimized_resume_latex_url=latex_url,
            optimized_resume_pdf_url=pdf_url,
        )

        download_url = (
            r2_storage.generate_presigned_get_url(
                r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS
            )
            if pdf_url
            else None
        )
        logger.info(
            "ai_resume_generated job_id=%s valid_sections=%s skipped=%s "
            "validated=%s has_pdf=%s",
            str(job_id),
            valid_sections,
            skipped_sections,
            validated,
            bool(pdf_url),
        )
        return GenerateAiResumeResponse(
            download_url=download_url,
            validated=validated,
        )

    async def get_download_url(
        self,
        user: User,
        version: str,
        job_id: Optional[uuid.UUID] = None,
        is_pdf: bool = True,
    ) -> GetResumeDownloadResponse:
        """Return the presigned GET URL for the compiled PDF or LaTeX source of a stored resume copy."""
        target_fmt = "PDF" if is_pdf else "LaTeX"
        if version == "original":
            target_url = (
                user.original_resume_pdf_url
                if is_pdf
                else user.original_resume_latex_url
            )
        elif version == "ai":
            if job_id:
                job = await self.job_repo.get_by_id(job_id)
                if job is None:
                    raise NotFoundError("Job", str(job_id))
                self.assert_ownership(job, user.id, "job")
                target_url = (
                    job.optimized_resume_pdf_url
                    if is_pdf
                    else job.optimized_resume_latex_url
                )
            else:
                raise BadRequestError(
                    "job_id is required to fetch the AI resume PDF"
                )
        else:
            raise BadRequestError(f"Unknown version: '{version}'")

        if not target_url:
            return GetResumeDownloadResponse(
                version=version,
                download_url=None,
                message=f"No {version} resume {target_fmt} available yet",
            )

        download_url = r2_storage.generate_presigned_get_url(
            r2_storage.key_from_url(target_url), PRESIGN_EXPIRY_SECONDS
        )
        return GetResumeDownloadResponse(
            version=version,
            download_url=download_url,
            message=f"Use the download_url to fetch the {version} resume {target_fmt}",
        )

    async def compile_custom_latex(
        self, job_id: uuid.UUID, latex: str, user: User
    ) -> GetResumeDownloadResponse:
        """Compile custom LaTeX submitted from frontend, update .tex and .pdf in R2, and return presigned PDF GET URL."""
        if not latex or not latex.strip():
            raise BadRequestError("LaTeX content cannot be empty")

        job = await self.job_repo.get_by_id(job_id)
        if job is None:
            raise NotFoundError("Job", str(job_id))
        self.assert_ownership(job, user.id, "job")

        # ── Slot allocation: reuse job's slot or allocate from slot_1, slot_2, slot_3 ──
        assigned_slot = _slot_name_from_url(
            job.optimized_resume_pdf_url
        ) or _slot_name_from_url(job.optimized_resume_latex_url)

        if not assigned_slot:
            ai_jobs = await self.job_repo.list_jobs_with_ai_resumes(
                user.id
            )
            used_slots: dict[str, Job] = {}
            for j in ai_jobs:
                s = _slot_name_from_url(
                    j.optimized_resume_pdf_url
                ) or _slot_name_from_url(j.optimized_resume_latex_url)
                if s:
                    used_slots[s] = j

            free_slots = [s for s in _SLOTS if s not in used_slots]
            if free_slots:
                assigned_slot = free_slots[0]
            else:
                oldest_job = min(
                    used_slots.values(),
                    key=lambda j: j.updated_at or j.created_at,
                )
                assigned_slot = (
                    _slot_name_from_url(
                        oldest_job.optimized_resume_pdf_url
                    )
                    or _slot_name_from_url(
                        oldest_job.optimized_resume_latex_url
                    )
                    or "slot_1"
                )
                await self.job_repo.update(
                    oldest_job,
                    optimized_resume_latex_url=None,
                    optimized_resume_pdf_url=None,
                )
                logger.info(
                    "ai_resume_slot_evicted user_id=%s evicted_job_id=%s freed_slot=%s",
                    str(user.id),
                    str(oldest_job.id),
                    assigned_slot,
                )

        tex_key = f"resume/{user.id}/{assigned_slot}.tex"
        pdf_key = f"resume/{user.id}/{assigned_slot}.pdf"

        # 1. Upload LaTeX to R2
        latex_url = r2_storage.upload_text(
            tex_key, latex, RESUME_TEX_CONTENT_TYPE
        )

        # 2. Compile to PDF
        pdf_bytes = await _compile_latex_to_pdf_via_api(latex, self.db)
        pdf_url = None
        if pdf_bytes:
            pdf_url = r2_storage.upload_bytes(
                pdf_key, pdf_bytes, RESUME_PDF_CONTENT_TYPE
            )
        else:
            logger.warning(
                "compile_custom_latex_pdf_failed job_id=%s slot=%s",
                str(job_id),
                assigned_slot,
            )

        # 3. Update Job in DB
        await self.job_repo.update(
            job,
            optimized_resume_latex_url=latex_url,
            optimized_resume_pdf_url=pdf_url,
        )

        download_url = (
            r2_storage.generate_presigned_get_url(
                r2_storage.key_from_url(pdf_url), PRESIGN_EXPIRY_SECONDS
            )
            if pdf_url
            else None
        )

        return GetResumeDownloadResponse(
            version="ai",
            download_url=download_url,
            message=(
                "LaTeX saved and PDF compiled successfully"
                if pdf_url
                else "LaTeX saved to R2, but PDF compilation failed"
            ),
        )


def _strip_all_section_headings(block: str) -> tuple[str, int]:
    """Remove every \\section{...} / \\section*{...} heading from an LLM-optimised
    block.  The reconstruction loop emits the ORIGINAL heading separately, so any
    \\section heading inside the optimised body is either an echo or a hallucinated
    extra section — both must go.

    \\subsection, \\subsubsection, etc. are intentionally PRESERVED.

    Returns (cleaned_block, number_of_headings_removed).
    """
    matches = list(_SECTION_HEADING_RE.finditer(block))
    if not matches:
        return block, 0
    cleaned = _SECTION_HEADING_RE.sub("", block)
    # Collapse blank lines left behind by removed headings
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip("\n")
    return cleaned, len(matches)


def _section_body(block: str) -> str:
    """Return only the body of a parsed section block (everything after the
    \\section{...} heading line).  Used to check whether a section that HAS a
    heading actually contains any content worth optimising."""
    m = _SECTION_HEADING_RE.match(block)
    return block[m.end() :] if m else block


def _validate_latex(content: str) -> bool:
    """Validate LaTeX structurally before it reaches the real pdflatex compiler.

    pylatexenc runs in TOLERANT mode and silently swallows unbalanced braces and
    unclosed custom commands, so it cannot catch the class of error that actually
    breaks compilation (e.g. `! Argument of \\@vspace has an extra }.` caused by a
    stray/missing brace in an optimized section). We therefore do a STRICT global
    brace-balance check that mirrors what pdflatex enforces:

      - Strip line comments (`% ...`) and verbatim environments first, since `%`
        and braces inside them are not real LaTeX structure.
      - Treat `\\{` and `\\}` (escaped braces) as literal text, not grouping.
      - Count `{` vs `}`; they must balance exactly, and depth must never go
        negative (a `}` with no matching `{`).

    This is the cheapest reliable proxy for "will pdflatex choke on this block".
    """
    # 1. Remove verbatim environments (their contents are literal).
    text = re.sub(
        r"\\begin\{verbatim\}.*?\\end\{verbatim\}",
        "",
        content,
        flags=re.DOTALL,
    )
    text = re.sub(
        r"\\begin\{lstlisting\}.*?\\end\{lstlisting\}",
        "",
        text,
        flags=re.DOTALL,
    )

    # 2. Remove % line comments (but not \% which is an escaped percent).
    #    Replace `\%` with a placeholder so the comment stripper won't eat it.
    text = text.replace("\\%", "\x00")
    text = re.sub(r"%.*", "", text)
    text = text.replace("\x00", "\\%")

    # 3. Remove escaped braces so they aren't counted as grouping.
    text = text.replace("\\{", "").replace("\\}", "")

    depth = 0
    for ch in text:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                logger.warning(
                    "latex_validation_error negative_depth (stray '}')"
                )
                return False
    if depth != 0:
        logger.warning(
            "latex_validation_error unbalanced_braces depth=%d", depth
        )
        return False
    return True


# Maps each requested section key to its prompt + the template arg name that
# carries the section's current LaTeX block.
_SECTION_PROMPTS = {
    "professional_summary": {
        "system": PROFESSIONAL_SUMMARY_SECTION_SYSTEM,
        "user": PROFESSIONAL_SUMMARY_SECTION_USER,
        "arg": "current_summary_latex",
    },
    "skills": {
        "system": SKILLS_SECTION_SYSTEM,
        "user": SKILLS_SECTION_USER,
        "arg": "skills_latex",
    },
    "work_experience": {
        "system": WORK_EXPERIENCE_SECTION_SYSTEM,
        "user": WORK_EXPERIENCE_SECTION_USER,
        "arg": "experience_latex",
    },
    "projects": {
        "system": PROJECT_SECTION_SYSTEM,
        "user": USER_PROJECT_SECTION_USER,
        "arg": "projects_latex",
    },
    "education": {
        "system": EDUCATION_SECTION_SYSTEM,
        "user": EDUCATION_SECTION_USER,
        "arg": "education_latex",
    },
}

# Keyword hints used to locate each section's section heading in the document.
_SECTION_KEYWORDS = {
    "professional_summary": [
        "summary",
        "profile",
        "about",
        "objective",
    ],
    "skills": [
        "skill",
        "technical",
        "competenc",
        "technologies",
        "expertise",
    ],
    "work_experience": [
        "experience",
        "work",
        "employment",
        "work history",
        "professional experience",
        "work experience",
        "professional",
    ],
    "projects": ["project"],
    "education": [
        "education",
        "academic",
        "university",
        "college",
        "degree",
    ],
}

_SECTION_HEADING_RE = re.compile(r"\\section\*?\s*\{(.*?)\}", re.DOTALL)


def _find_uncommented_headings(latex: str) -> list[re.Match]:
    """Find all \\section{...} / \\section*{...} headings in LaTeX that are NOT
    commented out by a preceding '%' on the same line.
    """
    matches = []
    for m in _SECTION_HEADING_RE.finditer(latex):
        line_start = latex.rfind("\n", 0, m.start())
        line_start = 0 if line_start == -1 else line_start + 1
        prefix = latex[line_start : m.start()]
        prefix_clean = prefix.replace("\\%", "\x00")
        if "%" in prefix_clean:
            # Commented out by '%' on the same line — skip
            continue
        matches.append(m)
    return matches


def _parse_latex_sections(latex: str) -> dict[str, str]:
    """Step 1: deterministically split the LaTeX document into named section blocks.

    Returns a dict with keys for each known section whose \\section{...} heading
    is present, plus 'header' (everything before the first section, e.g. preamble
    + name/contact) and 'footer' (everything after the last known section).
    """
    headings = _find_uncommented_headings(latex)
    result: dict[str, str] = {}

    # Locate each known section's [start, end) span.
    spans: dict[str, tuple[int, int]] = {}
    for i, m in enumerate(headings):
        title = m.group(1).lower()
        for key, kws in _SECTION_KEYWORDS.items():
            if any(kw in title for kw in kws):
                start = m.start()
                end = (
                    headings[i + 1].start()
                    if i + 1 < len(headings)
                    else len(latex)
                )
                spans[key] = (start, end)
                break

    # Header = everything before the first section heading (or whole doc if none).
    first_heading_start = (
        headings[0].start() if headings else len(latex)
    )
    result["header"] = latex[:first_heading_start]

    for key, (start, end) in spans.items():
        result[key] = latex[start:end]

    # Footer = everything after the last known section span.
    last_end = max(
        (end for _, (_, end) in spans.items()),
        default=first_heading_start,
    )
    result["footer"] = latex[last_end:]
    return result


def _reconstruct_latex(
    original_latex: str,
    parsed: dict[str, str],
    optimized: dict[str, str],
) -> str:
    """Step 4: rebuild the full LaTeX document deterministically.

    Walks the original document heading-by-heading and emits, for each section,
    the ORIGINAL heading followed by the optimized body (if that section was
    optimized) or the original body.
    """
    headings = _find_uncommented_headings(original_latex)
    if not headings:
        # No headings -> nothing to splice; return original unchanged.
        logger.warning(
            "reconstruct_no_headings original_len=%d",
            len(original_latex),
        )
        return original_latex

    def _key_for(title: str):
        title = title.lower()
        for key, kws in _SECTION_KEYWORDS.items():
            if any(kw in title for kw in kws):
                return key
        return None

    logger.info(
        "reconstruct_headings found=%d titles=%s",
        len(headings),
        [h.group(1) for h in headings],
    )

    parts: list[str] = []
    # Everything before the first heading (preamble + name/contact).
    parts.append(original_latex[: headings[0].start()])

    for i, m in enumerate(headings):
        end = (
            headings[i + 1].start()
            if i + 1 < len(headings)
            else len(original_latex)
        )
        heading_line = original_latex[m.start() : m.end()]
        body = original_latex[m.end() : end]

        key = _key_for(m.group(1))
        new_block = optimized.get(key) if key else None
        if new_block:
            # Strip ALL \section headings the LLM may have echoed or hallucinated.
            new_block, stripped_count = _strip_all_section_headings(
                new_block
            )
            if stripped_count:
                logger.info(
                    "reconstruct_stripped_headings section=%s count=%d",
                    key,
                    stripped_count,
                )
            # Guard: if the block is empty after stripping, keep the original body.
            if new_block.strip():
                body = "\n" + new_block
                logger.info(
                    "reconstruct_section i=%d title=%r key=%s OPTIMIZED body_len=%d",
                    i,
                    m.group(1),
                    key,
                    len(body),
                )
            else:
                logger.warning(
                    "reconstruct_section_empty_after_strip i=%d title=%r key=%s "
                    "– falling back to ORIGINAL body",
                    i,
                    m.group(1),
                    key,
                )
        else:
            logger.info(
                "reconstruct_section i=%d title=%r key=%s ORIGINAL body_len=%d",
                i,
                m.group(1),
                key,
                len(body),
            )

        # Ensure the heading starts on its own line (a preceding "%---" comment
        # on the same line would otherwise swallow it).
        parts.append("\n" + heading_line)
        parts.append(body)

    rebuilt = "".join(parts)

    # ── Final safety net ──────────────────────────────────────────────
    # Verify every original heading survived AND no new heading appeared.
    original_titles = {h.group(1) for h in headings}
    for h in headings:
        if h.group(0) not in rebuilt:
            logger.error(
                "reconstruct_MISSING_HEADING title=%r – heading was dropped!",
                h.group(1),
            )
    for h in _SECTION_HEADING_RE.finditer(rebuilt):
        if h.group(1) not in original_titles:
            logger.error(
                "reconstruct_NEW_HEADING title=%r – LLM injected a section "
                "that was not in the original resume!",
                h.group(1),
            )

    logger.info(
        "reconstruct_done headings_in_output=%d",
        sum(1 for h in headings if h.group(0) in rebuilt),
    )
    return rebuilt


async def _compile_latex_to_pdf_via_api(
    latex: str, db: Optional[AsyncSession] = None, max_retries: int = 2
) -> Optional[bytes]:
    """Compile LaTeX to PDF via the self-hosted AWS Lambda (TeXLive) compiler.

    POSTs the .tex as JSON `{"latex_base64": "<base64 source>"}` (matches the Lambda
    handler's `latex_base64` branch — safest for backslash-heavy source). On success
    Over a Lambda Function URL the handler's `isBase64Encoded` envelope is stripped
    by API Gateway, so a successful compile returns the RAW PDF bytes with
    `Content-Type: application/pdf` — we capture `resp.content` directly. If the
    Lambda had to install missing packages on the fly it echoes them in the
    `X-Latex-Fallback-Used` header; those are upserted into latex_package_usage when
    `db` is provided. On failure the Lambda returns 422 with a JSON body containing
    the LaTeX log tail. Retries up to `max_retries` times with a short backoff.
    Returns raw PDF bytes, or None if every attempt fails.
    """
    url = settings.LATEX_COMPILE_URL
    if not url:
        logger.error(
            "latex_compile_no_url LATEX_COMPILE_URL is not configured"
        )
        return None

    payload = {
        "latex_base64": base64.b64encode(latex.encode("utf-8")).decode(
            "utf-8"
        )
    }
    logger.info(
        "latex_compile_start url_set=%s latex_len=%d retries=%d",
        bool(url),
        len(latex),
        max_retries,
    )

    for attempt in range(1, max_retries + 1):
        try:
            async with httpx.AsyncClient(
                timeout=90, follow_redirects=True
            ) as client:
                resp = await client.post(url, json=payload)
            logger.info(
                "latex_compile_attempt attempt=%d/%d status=%d ctype=%s",
                attempt,
                max_retries,
                resp.status_code,
                resp.headers.get("content-type"),
            )

            if resp.status_code == 200:
                ctype = (resp.headers.get("content-type") or "").lower()
                if "application/pdf" in ctype:
                    pdf = resp.content
                    if pdf.startswith(b"%PDF"):
                        logger.info(
                            "latex_compile_ok attempt=%d/%d bytes=%d",
                            attempt,
                            max_retries,
                            len(pdf),
                        )
                        await _record_fallback_packages(resp, db)
                        return pdf
                    logger.warning(
                        "latex_compile_bad_pdf attempt=%d head=%s",
                        attempt,
                        pdf[:40],
                    )
                    return None
                logger.warning(
                    "latex_compile_bad_response attempt=%d ctype=%s head=%s",
                    attempt,
                    ctype,
                    resp.content[:40],
                )
                return None

            # 422 etc: surface the log tail so callers can debug missing packages.
            try:
                detail = resp.json()
                logger.warning(
                    "latex_compile_failed attempt=%d status=%d detail=%s",
                    attempt,
                    resp.status_code,
                    detail,
                )
            except Exception:
                logger.warning(
                    "latex_compile_failed attempt=%d status=%d body=%s",
                    attempt,
                    resp.status_code,
                    resp.text[:500],
                )
        except Exception as e:
            logger.warning(
                "latex_compile_error attempt=%d error=%s",
                attempt,
                str(e),
            )

        if attempt < max_retries:
            await asyncio.sleep(1 * attempt)

    logger.error("latex_compile_all_failed")
    return None


async def _record_fallback_packages(resp, db) -> None:
    """If the Lambda installed packages on the fly (X-Latex-Fallback-Used header),
    upsert each into latex_package_usage, incrementing its download count."""
    if db is None:
        return
    header = resp.headers.get("X-Latex-Fallback-Used")
    if not header:
        return
    try:
        packages = json.loads(header)
    except Exception:
        logger.warning(
            "latex_fallback_header_parse_failed header=%s", header[:200]
        )
        return
    if not isinstance(packages, list):
        return
    repo = LatexPackageRepository(db)
    for pkg in packages:
        if not pkg:
            continue
        try:
            await repo.record_usage(pkg)
        except Exception as e:
            logger.warning(
                "latex_fallback_record_failed pkg=%s error=%s",
                pkg,
                str(e),
            )
