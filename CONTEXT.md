# Project Architecture & Context Overview

This document serves as the primary system reference for the `auto_apply` backend codebase. AI coding assistants should read this file first to instantly understand the system design, model relationships, resume optimization pipelines, and database constraints without re-indexing the entire workspace.

---

## 1. Core Technology Stack & Commands

- **Framework**: Python 3.14 + FastAPI (async REST API)
- **Database & ORM**: PostgreSQL + AsyncSQLAlchemy + Pydantic v2
- **Storage & Compilation**: Cloudflare R2 (presigned URLs for LaTeX/PDF) + pdflatex compilation API
- **Testing**: `pytest` with `pytest-asyncio` and `pytest-mock`
  - **Run Tests**: `./venv/bin/pytest` (46 active unit tests, 100% passing)

---

## 2. Database Models & Circular Import Rules (`app/*/models.py`)

- **Models Overview**:
  - `User` (`app/users/models.py`): User profile, `linkedin_message` (non-null string with default `"I'm exploring opportunities and would love to connect"`), original resume URLs (`original_resume_latex_url`, `original_resume_pdf_url`), LLM provider settings, encrypted API keys.
  - `Job` (`app/jobs/models.py`): Job applications with `workday_url`, `status` enum (`JobStatus`), `referral_received` boolean, `optimized_resume_pdf_url`, `optimized_resume_latex_url`, and timestamps (`created_at`, `updated_at`).
  - `JobJD` (`app/job_jd/models.py`): Parsed Job Descriptions linked 1-to-1 with `Job`. Stores `company`, `role`, `workday_job_id`, `raw_text`, `skills`, `keywords`, `extracted_department` (Google X-Ray queries), and `learning` JSON (`{ topic: [questions] }`).
  - `Referral` (`app/referrals/models.py`): Referral tracking per job (`ReferralStatus`).
  - `Task` (`app/tasks/models.py`): Background task execution (`TaskType`, `TaskStatus`, `task_state_machine`).

- **CRITICAL IMPORT RULE**:
  - **Prevent Circular Imports & Double Table Registrations**: All cross-module relationship imports (`Job`, `User`, `JobJD`, `Referral`, `Task`) MUST be enclosed inside `if TYPE_CHECKING:` blocks in model files.

---

## 3. Resume Service & 3-Slot Storage Strategy (`app/resumes/`)

### R2 Object Key Recycling (Fixed 3 Slots Per User)
To minimize Cloudflare R2 storage usage, AI resumes are NOT saved with random keys. Each user is allocated exactly **3 fixed slot keys**:
- `resume/{user_id}/slot_1.tex` & `resume/{user_id}/slot_1.pdf`
- `resume/{user_id}/slot_2.tex` & `resume/{user_id}/slot_2.pdf`
- `resume/{user_id}/slot_3.tex` & `resume/{user_id}/slot_3.pdf`

### Slot Allocation & Eviction Policy
1. **Re-generation for Existing Job**: If a `Job` already has a slot assigned, regenerating its resume overwrites its exact slot key in R2.
2. **New Job (Free Slot Available)**: If a job does not have a slot assigned, it takes the first available free slot among `slot_1`, `slot_2`, `slot_3`.
3. **Cyclic Eviction (All 3 Slots Occupied)**: When all 3 slots are occupied across 3 jobs, the system finds the **least recently updated job** (`min(ai_jobs, key=lambda j: j.updated_at or j.created_at)`), clears its database URLs (`optimized_resume_latex_url=None`, `optimized_resume_pdf_url=None`), and reuses its slot key for the new job.

### Resume Endpoints (`app/resumes/router.py`)
- `POST /resumes/upload-url`: Returns presigned PUT URL for candidate's original LaTeX resume.
- `POST /resumes/finalize/{resume_type}`: Compiles original LaTeX to PDF.
- `POST /resumes/generate/{job_id}`: Runs parallel LLM optimization on requested sections, reconstructs LaTeX, compiles to PDF, updates `Job` DB record, and returns PDF presigned URL.
- `POST /resumes/compile/{job_id}`: Accepts full custom/edited LaTeX from frontend, compiles to PDF, updates `.tex` and `.pdf` in R2 and DB, and returns presigned PDF GET URL.
- `GET /resumes/download/{version}`: Accepts `version` (`"original"` | `"ai"`), optional `job_id`, and `isPdf: bool = Query(True, alias="isPdf")`.
  - `?isPdf=true`: Returns presigned GET URL for `.pdf`.
  - `?isPdf=false`: Returns presigned GET URL for `.tex` source.

---

## 4. LLM Client & Prompt Design System (`app/llm/`)

### Gemini Structured Output Constraints
- `LLMClient._sanitize_schema_for_gemini` strips `"additionalProperties"`.
- Free-form dictionaries like `Dict[str, List[str]]` collapse to `{}` in Gemini structured outputs.
- **Solution**: Represent free-form dicts as lists of explicit objects (e.g. `List[TopicLearning]`) in Pydantic schemas, and convert to dictionary format in `JobJDService` before saving to DB.
- **OpenRouter & Structured Output Fallbacks (`app/llm/client.py`)**:
  - `complete_json()` injects the target `Pydantic` model's JSON Schema into system instructions and sets `json_mode=True` across all providers (including OpenRouter).
  - If `beta.chat.completions.parse()` fails or is not supported by a specific OpenRouter model, `_openai_complete` gracefully falls back to `response_format={"type": "json_object"}`.
  - On validation retries, `complete_json()` provides the exact error details and instructs the model to re-output the **FULL, COMPLETE JSON object from start to finish** (instead of requesting partial text continuations).

### XML-Style Resume Optimization Prompts (`app/llm/prompts.py`)
All section prompts (`professional_summary`, `skills`, `work_experience`, `projects`, `education`) use XML tag rules (`<summary_optimization_rules>`, `<skills_optimization_rules>`, `<structural_preservation_rules>`, `<layout_and_length_constraints>`, `<linguistic_and_data_guardrails>`, `<output_delivery_constraint>`):
- **Structure & Wrapper Preservation**: Preserves LaTeX wrapper commands (`\resumeSubheading`, `\resumeItem`, `\begin{itemize}`, etc.).
- **Scope Scoping**: Modifies only bullet text; never alters company names, job titles, dates, locations, or degree titles.
- **Factual Integrity & Metric Pinning**: Retains all quantitative metrics and numbers; forbids fake credentials or invented numbers.
- **LaTeX Escaping**: Escapes `%` (`\%`), `&` (`\&`), `$` (`\$`), `#` (`\#`), and `_` (`\_`).
- **Output Sanitization**: `_clean_llm_latex_output(text)` in `app/resumes/service.py` automatically strips code fences (```latex), smart quotes (`“`, `”`), and preambles from LLM outputs.

---

## 5. LaTeX Section Parsing & Reconstruction (`app/resumes/service.py`)

- **Commented Heading Handling**: `_find_uncommented_headings(latex)` ignores section headings preceded by `%` on the same line (e.g. `% \section{Summary}`). Commented-out headings remain untouched as commented text in preambles and are never parsed as active sections or uncommented.
- **Deterministic Reconstruction**: `_reconstruct_latex()` splices optimized section blocks back into the original LaTeX structure without using LLMs for reconstruction, guaranteeing section headings appear exactly once and layout remains stable.

---

## 6. Directory Map

```text
backend/
├── app/
│   ├── common/         # BaseRepository, BaseService, StateMachine, dependencies
│   ├── database/       # Session management and Base declarative class
│   ├── job_jd/         # JD parsing, Pydantic schemas, JobJD model, and repository
│   ├── jobs/           # Job model, JobStatus enum, job state machine, repository, service, router
│   ├── llm/            # LLMClient (OpenAI/Gemini/Claude/OpenRouter), prompts, and schemas
│   ├── referrals/      # Referral model and service
│   ├── resumes/        # ResumeService (3-slot management, LaTeX parsing/reconstruction), schemas, router
│   ├── storage/        # Cloudflare R2 storage integration (r2_storage)
│   ├── tasks/          # Background task model and queue execution
│   └── users/          # User model, authentication, and encrypted API key management
├── tests/              # Test suite (test_resumes_service, test_job_jd_service, test_llm_client, etc.)
├── CONTEXT.md          # Project context file for AI assistants
└── pytest.ini          # Pytest configuration
```
