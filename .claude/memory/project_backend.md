---
name: project-backend-architecture
description: AI job application backend — FastAPI/PostgreSQL service at /Users/ayush/code/auto_apply/backend
metadata:
  type: project
---

Backend for an AI job application platform. Does NOT perform browser automation — delegates to local Playwright agents via Tasks API.

**Why:** MVP backend to manage users, jobs, JD parsing, resume optimization, referrals, and task orchestration.

**How to apply:** When making changes, follow the module pattern: models → schemas → repository → service → router. Business logic lives only in service layer.

Tech stack: FastAPI, PostgreSQL + asyncpg, SQLAlchemy 2.0 async, Alembic, Cloudflare R2, OpenAI/Anthropic LLM, Fernet encryption for API keys, JWT auth.

Key design decisions:
- Status transitions enforced in service layer; invalid transitions → HTTP 400
- Task updates to COMPLETED/FAILED are idempotent (ignored, return current state)
- LLM API keys and Google Search API keys stored encrypted (Fernet) — never returned in API responses
- JD parsing happens automatically on POST /jobs
- LaTeX compilation attempted with pdflatex; falls back gracefully if not installed
