import uuid
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.common.exceptions import BadRequestError, NotFoundError
from app.job_jd.service import JobJDService, _is_job_closed, fetch_jd_html
from tests.conftest import make_user


def test_is_job_closed():
    assert _is_job_closed("We are sorry, this job has expired and is no longer available.")
    assert _is_job_closed("Position has been filled.")
    assert not _is_job_closed("We are looking for a Senior Software Engineer.")


@pytest.mark.asyncio
async def test_fetch_jd_html_jsonld():
    html_content = """
    <html>
    <head>
    <script type="application/ld+json">
    {
        "@type": "JobPosting",
        "title": "Backend Developer",
        "hiringOrganization": {"name": "1234 Acme Corp"},
        "identifier": {"value": "REQ-1001"},
        "description": "Awesome python role",
        "skills": ["Python", "FastAPI"]
    }
    </script>
    </head>
    <body></body>
    </html>
    """
    mock_resp = MagicMock()
    mock_resp.text = html_content
    mock_resp.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient.get", return_value=mock_resp):
        raw_html, text, meta = await fetch_jd_html("https://example.com/job")
        assert text == "Awesome python role"
        assert meta["role"] == "Backend Developer"
        assert meta["company"] == "Acme Corp"
        assert meta["workday_job_id"] == "REQ-1001"
        assert meta["skills"]["required"] == ["Python", "FastAPI"]


@pytest.mark.asyncio
async def test_parse_and_store_no_llm_key_raises():
    db = AsyncMock()
    user = make_user(llm_provider="openai", openai_llm_api_key=None)
    svc = JobJDService(db)
    with patch("app.job_jd.service.get_decrypted_llm_key", return_value=None):
        with pytest.raises(BadRequestError, match="LLM provider and API key must be configured"):
            await svc.parse_and_store(uuid.uuid4(), "https://example.com/job", user)


@pytest.mark.asyncio
async def test_parse_and_store_closed_job_raises():
    db = AsyncMock()
    user = make_user()
    svc = JobJDService(db)

    with patch("app.job_jd.service.get_decrypted_llm_key", return_value="fake_key"):
        with patch(
            "app.job_jd.service.fetch_jd_html",
            return_value=("<html></html>", "This job is no longer available", {}),
        ):
            with pytest.raises(BadRequestError, match="no longer available"):
                await svc.parse_and_store(uuid.uuid4(), "https://example.com/job", user)


@pytest.mark.asyncio
async def test_parse_and_store_ai_mode():
    db = AsyncMock()
    user = make_user(llm_provider="openai")
    svc = JobJDService(db)
    job_id = uuid.uuid4()

    mock_llm_res = {
        "company": "Acme Corp",
        "role": "Senior Engineer",
        "workday_job_id": "JOB-123",
        "skills": {"required": ["Python", "Docker"], "preferred": ["Kubernetes"]},
        "keywords": ["distributed systems"],
        "extracted_department": ['site:linkedin.com/in company_name "engineering"'],
        "learning": [{"topic": "System Design", "questions": ["How to scale a database?", "Explain CAP theorem"]}],
    }

    mock_jd_obj = MagicMock()

    with patch("app.job_jd.service.get_decrypted_llm_key", return_value="fake_key"):
        with patch(
            "app.job_jd.service.fetch_jd_html",
            return_value=("<html></html>", "Job text description python docker kubernetes", {}),
        ):
            with patch("app.job_jd.service.JobJDRepository") as MockRepo:
                mock_repo = AsyncMock()
                mock_repo.get_by_job_id = AsyncMock(return_value=None)
                mock_repo.upsert = AsyncMock(return_value=mock_jd_obj)
                MockRepo.return_value = mock_repo
                svc.repo = mock_repo

                with patch("app.job_jd.service.LLMClient") as MockLLM:
                    mock_llm_client = AsyncMock()
                    mock_llm_client.complete_json = AsyncMock(return_value=mock_llm_res)
                    MockLLM.return_value = mock_llm_client

                    jd, parsed = await svc.parse_and_store(
                        job_id, "https://example.com/job", user, ai=True
                    )

                    assert jd is mock_jd_obj
                    assert parsed["company"] == "Acme Corp"
                    mock_repo.upsert.assert_called_once()
                    _, kwargs = mock_repo.upsert.call_args
                    assert kwargs["company"] == "Acme Corp"
                    assert kwargs["extracted_department"] == ['site:linkedin.com/in "Acme Corp" "engineering"']
