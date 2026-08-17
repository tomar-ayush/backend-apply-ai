# Project Handoff: Keyword Optimization & Approve/Reject Workflow

## Goal
Implement a robust keyword-driven resume optimization workflow featuring a two-phase approve/reject process for granular AI changes.

## Completed Tasks
1. **Keyword Extraction (Phase 0)**
   - Added `resume_keywords` JSON column to the `Users` table via Alembic migration.
   - Implemented `_extract_keywords_from_resume` in `ResumeService` combining NLP fallbacks (from `user.skills` and regex) with an LLM prompt.
   - Automatically triggered during original resume upload (`POST /resumes/finalize/original`).

2. **JD Keyword Matching (Phase 1)**
   - Updated `JobJDResponse` schema to include `missing_keywords`.
   - Updated `app/jobs/router.py` endpoints (`get_jd`, `reparse_jd`, `update_jd`) to dynamically compute `missing_keywords` by subtracting `user.resume_keywords` from JD required/preferred skills.

3. **Two-Phase Generation (Phase 2)**
   - Created `ResumePreview` model and applied database migrations to store temporary AI diffs.
   - Updated `app/llm/prompts.py` to accept `{extra_keywords}` for all section rewrites.
   - Implemented `POST /resumes/preview/{job_id}`:
     - Prompts the LLM with the specified missing keywords.
     - Parses the LLM output and generates bullet-by-bullet diffs against the original LaTeX section using `_extract_bullet_diffs`.
     - Returns a structured diff for the frontend to render an approve/reject UI.
   - Implemented `POST /resumes/finalize-ai/{job_id}`:
     - Accepts `preview_id` and a list of `accepted_change_ids`.
     - Splices together the accepted AI changes and rejected original text.
     - Compiles the final LaTeX into a PDF and assigns a storage slot exactly like the old workflow.

## Open Blockers / Known Limitations
- **Headless Summaries**: If a candidate's original LaTeX resume places their professional summary directly in the document preamble (without a dedicated `\section{Summary}` heading), the parsing logic will treat it as part of the `header` and skip optimizing it. This requires a more complex AST-based LaTeX parser to fully resolve, but the current approve/reject system mitigates the risk of catastrophic hallucination when integrating changes.
- **Frontend Integration**: The frontend must now be updated to hit the new `/preview` and `/finalize-ai` routes and render the diff UI.

## File Paths Modified
- `app/users/models.py`
- `app/resumes/models.py`
- `app/job_jd/schemas.py`
- `app/jobs/router.py`
- `app/llm/prompts.py`
- `app/resumes/schemas.py`
- `app/resumes/router.py`
- `app/resumes/service.py`

## Next Steps
1. The frontend team needs to build the diff-viewer UI utilizing the `PreviewResponse` format.
2. Test the keyword extraction accuracy during new resume uploads and tune the extraction LLM prompt if necessary.
