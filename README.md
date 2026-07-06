# AI Job Application Backend

FastAPI backend for an AI-assisted job application platform. Manages users, jobs, JD parsing, resume optimization, referral generation, and task delegation to local Playwright automation agents.

---

## Architecture

```
┌─────────────────────────────────────────┐
│              FastAPI Backend             │
│                                          │
│  /auth    /users   /jobs   /tasks       │
│     │        │       │        │          │
│  AuthSvc  UserSvc  JobSvc  TaskSvc      │
│               │       │                  │
│           UserRepo  JobRepo             │
│                   │                      │
│              PostgreSQL                  │
│                   │                      │
│        Cloudflare R2 (resumes)          │
│                   │                      │
│        LLM API (OpenAI/Anthropic)       │
└─────────────────────────────────────────┘
           ↑  REST API  ↑
    Local Playwright Agents
```

The backend **never runs browser automation** — it delegates work to local agents via the Tasks API.

---

## Tech Stack

- **FastAPI** — async API framework
- **PostgreSQL** — primary database
- **SQLAlchemy 2.0** — async ORM with `asyncpg` driver
- **Alembic** — database migrations
- **Cloudflare R2** — resume file storage (S3-compatible)
- **Pydantic v2** — request/response validation
- **structlog** — structured JSON logging
- **cryptography (Fernet)** — LLM API key encryption
- **python-jose** — JWT authentication

---

## Project Structure

```
backend/
├── app/
│   ├── main.py              # FastAPI app, middleware, router registration
│   ├── config.py            # Settings from environment variables
│   ├── auth/                # JWT auth (register, login)
│   ├── users/               # User profile management
│   ├── jobs/                # Job lifecycle management
│   ├── job_jd/              # JD parsing and storage
│   ├── referrals/           # Referral generation and tracking
│   ├── resumes/             # Resume optimization
│   ├── tasks/               # Automation task delegation
│   ├── llm/                 # LLM client (OpenAI/Anthropic)
│   ├── storage/             # Cloudflare R2 storage
│   ├── common/              # Shared exceptions, logging, security
│   └── database/            # SQLAlchemy session and Base
├── migrations/              # Alembic migrations
├── tests/                   # Service-layer unit tests
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

Each module follows: `models → schemas → repository → service → router`

---

## Setup

### Prerequisites

- Python 3.11+
- PostgreSQL 14+
- (Optional) `pdflatex` for LaTeX compilation

### 1. Clone and install

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set:

| Variable | Description |
|---|---|
| `SECRET_KEY` | Random 64-char string for JWT signing |
| `ENCRYPTION_KEY` | Fernet key for API key encryption |
| `DATABASE_URL` | PostgreSQL async connection string |
| `DATABASE_URL_SYNC` | PostgreSQL sync connection (Alembic) |
| `R2_ACCOUNT_ID` | Cloudflare account ID |
| `R2_ACCESS_KEY_ID` | R2 access key |
| `R2_SECRET_ACCESS_KEY` | R2 secret key |
| `R2_BUCKET_NAME` | R2 bucket name |
| `R2_PUBLIC_URL` | Public base URL for R2 files |

**Generate a Fernet key:**
```python
from cryptography.fernet import Fernet
print(Fernet.generate_key().decode())
```

### 3. Run migrations

```bash
alembic upgrade head
```

### 4. Start the server

```bash
uvicorn app.main:app --reload
```

API docs available at: http://localhost:8000/docs

---

## Docker

```bash
# Start PostgreSQL and API
docker-compose up -d

# Run migrations
docker-compose exec api alembic upgrade head
```

---

## API Overview

### Authentication

```
POST /auth/register   # Create account → returns JWT
POST /auth/login      # Login → returns JWT
```

All other endpoints require `Authorization: Bearer <token>`.

### Users

```
GET    /users/me          # Get profile
PATCH  /users/me          # Update profile / set LLM API key
POST   /users/resume      # Upload original resume (PDF + LaTeX)
GET    /users/resume      # Get resume URLs
```

### Jobs

```
POST   /jobs                      # Create job (fetches & parses JD automatically)
GET    /jobs                      # List jobs (filter by ?status=)
GET    /jobs/{jobId}              # Get job
DELETE /jobs/{jobId}              # Delete job
PATCH  /jobs/{jobId}/status       # Manual status transition

GET    /jobs/{jobId}/jd           # Get parsed JD
POST   /jobs/{jobId}/parse        # Re-parse JD

POST   /jobs/{jobId}/referrals/generate  # Generate referral candidates
GET    /jobs/{jobId}/referrals           # List referrals

POST   /jobs/{jobId}/resume/generate     # Generate optimized resume
GET    /jobs/{jobId}/resume              # Get resume URLs
POST   /jobs/{jobId}/resume/select       # Select version (original/optimized)
```

### Referrals

```
PATCH  /referrals/{referralId}    # Update referral status
```

### Tasks (for Playwright agents)

```
POST   /tasks               # Create automation task
GET    /tasks/{taskId}      # Get task status
PATCH  /tasks/{taskId}      # Agent updates task status
```

---

## Job Status Flow

```
NEW → JD_PARSED → REFERRAL_IN_PROGRESS → WAITING_FOR_REFERRAL
                ↘                                            ↘
                 RESUME_GENERATED ←── REFERRAL_RECEIVED ←───┘
                       ↓
                 READY_TO_APPLY → WORKDAY_RUNNING → APPLIED
                                                       ↓
                                              OA → INTERVIEW → OFFER
                                                           ↘
                                                          REJECTED
```

Invalid transitions return HTTP 400.

---

## Task Status Flow

```
QUEUED → RUNNING → WAITING_USER → RUNNING
               ↘                ↘
            COMPLETED         FAILED
```

Updates to `COMPLETED` or `FAILED` tasks are silently ignored (idempotent) — agents can safely retry.

---

## User Configuration

Before using JD parsing, referrals, or resume optimization, set your LLM credentials:

```bash
PATCH /users/me
{
  "llm_provider": "openai",           # or "anthropic"
  "llm_api_key": "sk-...",            # stored encrypted
  "google_search_api_key": "...",     # for referral search
  "google_search_engine_id": "..."    # Google CSE ID
}
```

Keys are encrypted at rest using Fernet symmetric encryption. The plaintext key is never returned by any API endpoint.

---

## Running Tests

```bash
pytest tests/ -v
```

Tests use `pytest-asyncio` and mock the database layer. No live database or external services required.

---

## Playwright Agent Integration

Agents poll for tasks and update status:

```bash
# Create a task (backend)
POST /tasks
{
  "job_id": "...",
  "task_type": "WORKDAY_APPLY",
  "payload": {
    "resume_pdf_url": "...",
    "workday_url": "...",
    "user_profile": {...}
  }
}

# Agent picks up the task and runs it, then reports back:
PATCH /tasks/{taskId}
{ "status": "COMPLETED" }

# or on failure:
PATCH /tasks/{taskId}
{ "status": "FAILED", "error_message": "Login required" }
```

---

## LaTeX Resume Compilation

The backend attempts to compile optimized LaTeX resumes to PDF using `pdflatex`. If LaTeX is not installed, the optimized `.tex` file is stored in R2 and the PDF URL will be `null`. The Docker image includes `texlive-latex-extra` for compilation.

Install locally:
```bash
# macOS
brew install --cask mactex-no-gui

# Ubuntu
apt-get install texlive-latex-extra
```
