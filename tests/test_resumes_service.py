import pytest
from app.resumes.service import (
    _parse_latex_sections,
    _reconstruct_latex,
    _validate_latex,
    _strip_all_section_headings,
)

SAMPLE_LATEX = r"""\documentclass{article}
\begin{document}

\name{John Doe}
\contact{john@example.com}

\section{Summary}
Experienced software engineer with a track record of building microservices.

\section{Skills}
\begin{itemize}
  \item Python, FastAPI, PostgreSQL
\end{itemize}

\section{Work Experience}
\resumeSubheading{Company A}{2020-Present}
\begin{itemize}
  \item \resumeItem{Built payment backend serving 10k RPS.}
\end{itemize}

\section{Education}
\resumeSubheading{University X}{2016-2020}
\begin{itemize}
  \item \resumeItem{BS in Computer Science.}
\end{itemize}

\end{document}
"""

SAMPLE_LATEX_WITH_UNRECOGNIZED_SECTION = r"""\documentclass{article}
\begin{document}

\section{Summary}
Software engineer summary.

\section{Skills}
Python, AWS.

\section{Certifications}
AWS Certified Solutions Architect.

\section{Work Experience}
Worked at Tech Corp.

\end{document}
"""


def test_parse_latex_sections_identifies_known_sections():
    parsed = _parse_latex_sections(SAMPLE_LATEX)
    assert "header" in parsed
    assert "professional_summary" in parsed
    assert "skills" in parsed
    assert "work_experience" in parsed
    assert "education" in parsed
    assert "John Doe" in parsed["header"]
    assert "Python, FastAPI" in parsed["skills"]


def test_parse_latex_sections_handles_missing_sections():
    # If a section (e.g. summary) is absent, its key should not be in parsed
    latex = r"""\documentclass{article}
\begin{document}
\section{Skills}
Python, Java.
\end{document}
"""
    parsed = _parse_latex_sections(latex)
    assert "skills" in parsed
    assert "professional_summary" not in parsed
    assert "work_experience" not in parsed


def test_reconstruct_latex_preserves_unrecognized_sections():
    parsed = _parse_latex_sections(SAMPLE_LATEX_WITH_UNRECOGNIZED_SECTION)
    optimized = {
        "skills": "Python 3.11, AWS (EC2, Lambda), Docker.",
    }
    rebuilt = _reconstruct_latex(
        SAMPLE_LATEX_WITH_UNRECOGNIZED_SECTION, parsed, optimized
    )
    assert "AWS Certified Solutions Architect" in rebuilt
    assert "\\section{Certifications}" in rebuilt
    assert "Python 3.11" in rebuilt


def test_reconstruct_latex_does_not_inject_missing_headers():
    # If original has no Summary, summary should not appear in rebuilt latex
    latex_no_summary = r"""\documentclass{article}
\begin{document}
\section{Skills}
Python.
\end{document}"""
    parsed = _parse_latex_sections(latex_no_summary)
    optimized = {"skills": "Python, Go."}
    rebuilt = _reconstruct_latex(latex_no_summary, parsed, optimized)
    assert "Summary" not in rebuilt
    assert "Python, Go." in rebuilt


def test_validate_latex_detects_unbalanced_braces():
    assert _validate_latex(r"\section{Skills} \item Test") is True
    assert _validate_latex(r"\section{Skills} \item {Unclosed") is False
    assert _validate_latex(r"\section{Skills} \item Stray } brace") is False


def test_strip_all_section_headings_removes_section():
    block = r"\section{Skills}\begin{itemize}\item Python\end{itemize}"
    cleaned, count = _strip_all_section_headings(block)
    assert count == 1
    assert "\\section{Skills}" not in cleaned
    assert "\\begin{itemize}" in cleaned


def test_parse_and_reconstruct_ignores_commented_headings():
    latex_with_commented_summary = r"""\documentclass{article}
\begin{document}
% \section{Summary}
%-------------------------------------------

\section{Education}
BS in CS

\section{Work Experience}
Software Engineer
\end{document}
"""
    parsed = _parse_latex_sections(latex_with_commented_summary)
    assert "professional_summary" not in parsed
    assert "education" in parsed
    assert "work_experience" in parsed

    optimized = {"work_experience": "Senior Software Engineer"}
    rebuilt = _reconstruct_latex(latex_with_commented_summary, parsed, optimized)

    # Ensure \section{Summary} is NOT uncommented into active LaTeX structure
    assert "\n\\section{Summary}" not in rebuilt
    assert "% \\section{Summary}" in rebuilt


@pytest.mark.asyncio
async def test_generate_ai_enforces_three_slots_limit():
    from unittest.mock import AsyncMock, MagicMock, patch
    import uuid
    from datetime import datetime, timezone
    from app.resumes.service import ResumeService
    from tests.conftest import make_user, make_job

    db = AsyncMock()
    user = make_user(
        original_resume_latex_url="https://r2.example.com/original.tex",
        llm_provider="openai",
    )
    job_target = make_job(id=uuid.uuid4(), user_id=user.id)

    # 3 existing jobs that ALREADY have AI resumes in slots 1, 2, 3
    j1 = make_job(
        id=uuid.uuid4(),
        user_id=user.id,
        optimized_resume_pdf_url=f"https://r2/resume/{user.id}/slot_1.pdf",
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    j2 = make_job(
        id=uuid.uuid4(),
        user_id=user.id,
        optimized_resume_pdf_url=f"https://r2/resume/{user.id}/slot_2.pdf",
        updated_at=datetime(2026, 1, 2, tzinfo=timezone.utc),
    )
    j3 = make_job(
        id=uuid.uuid4(),
        user_id=user.id,
        optimized_resume_pdf_url=f"https://r2/resume/{user.id}/slot_3.pdf",
        updated_at=datetime(2026, 1, 3, tzinfo=timezone.utc),
    )

    jd_mock = MagicMock()
    jd_mock.raw_text = "Python developer role"

    svc = ResumeService(db)
    svc.job_repo.get_by_id = AsyncMock(return_value=job_target)
    svc.job_repo.list_jobs_with_ai_resumes = AsyncMock(return_value=[j1, j2, j3])
    svc.job_repo.update = AsyncMock()
    svc.jd_repo.get_by_job_id = AsyncMock(return_value=jd_mock)

    with patch("app.resumes.service.UserService.get_decrypted_llm_key", return_value="fake_key"):
        with patch("app.storage.r2.r2_storage.download_text", return_value=SAMPLE_LATEX):
            with patch("app.storage.r2.r2_storage.upload_text") as mock_upload_text:
                with patch("app.storage.r2.r2_storage.upload_bytes") as mock_upload_bytes:
                    mock_upload_text.return_value = f"https://r2/resume/{user.id}/slot_1.tex"
                    mock_upload_bytes.return_value = f"https://r2/resume/{user.id}/slot_1.pdf"
                    with patch("app.resumes.service._compile_latex_to_pdf_via_api", return_value=b"%PDF"):
                        with patch("app.resumes.service.LLMClient") as MockLLM:
                            mock_client = AsyncMock()
                            mock_client.complete = AsyncMock(return_value="\\item Optimized skills")
                            MockLLM.return_value = mock_client

                            res = await svc.generate_ai(job_target.id, ["skills"], user)
                            assert res.validated is True

                            # Verify that oldest job (j1) was evicted to reuse slot_1
                            svc.job_repo.update.assert_any_call(
                                j1,
                                optimized_resume_latex_url=None,
                                optimized_resume_pdf_url=None,
                            )
                            # Verify uploaded to slot_1 key
                            mock_upload_text.assert_called_once()
                            assert f"resume/{user.id}/slot_1.tex" in mock_upload_text.call_args[0][0]


@pytest.mark.asyncio
async def test_get_download_url_supports_tex_and_pdf_formats():
    from unittest.mock import AsyncMock, patch
    import uuid
    from app.resumes.service import ResumeService
    from tests.conftest import make_user, make_job

    db = AsyncMock()
    user = make_user(
        original_resume_latex_url="https://r2.example.com/resume/user123/original.tex",
        original_resume_pdf_url="https://r2.example.com/resume/user123/original.pdf",
    )
    job = make_job(
        id=uuid.uuid4(),
        user_id=user.id,
        optimized_resume_latex_url="https://r2.example.com/resume/user123/slot_1.tex",
        optimized_resume_pdf_url="https://r2.example.com/resume/user123/slot_1.pdf",
    )

    svc = ResumeService(db)
    svc.job_repo.get_by_id = AsyncMock(return_value=job)

    with patch("app.storage.r2.r2_storage.generate_presigned_get_url", return_value="https://presigned.download"):
        # Original PDF
        res_orig_pdf = await svc.get_download_url(user, "original", is_pdf=True)
        assert res_orig_pdf.download_url == "https://presigned.download"
        assert "PDF" in res_orig_pdf.message

        # Original Tex
        res_orig_tex = await svc.get_download_url(user, "original", is_pdf=False)
        assert res_orig_tex.download_url == "https://presigned.download"
        assert "LaTeX" in res_orig_tex.message

        # AI PDF for Job
        res_ai_pdf = await svc.get_download_url(user, "ai", job_id=job.id, is_pdf=True)
        assert res_ai_pdf.download_url == "https://presigned.download"
        assert "PDF" in res_ai_pdf.message

        # AI Tex for Job
        res_ai_tex = await svc.get_download_url(user, "ai", job_id=job.id, is_pdf=False)
        assert res_ai_tex.download_url == "https://presigned.download"
        assert "LaTeX" in res_ai_tex.message


@pytest.mark.asyncio
async def test_compile_custom_latex_updates_r2_and_db():
    from unittest.mock import AsyncMock, patch
    import uuid
    from app.resumes.service import ResumeService
    from tests.conftest import make_user, make_job

    db = AsyncMock()
    user = make_user()
    job = make_job(id=uuid.uuid4(), user_id=user.id)

    svc = ResumeService(db)
    svc.job_repo.get_by_id = AsyncMock(return_value=job)
    svc.job_repo.list_jobs_with_ai_resumes = AsyncMock(return_value=[])
    svc.job_repo.update = AsyncMock()

    custom_latex = r"\documentclass{article}\begin{document}Custom LaTeX\end{document}"

    with patch("app.storage.r2.r2_storage.upload_text", return_value="https://r2/slot_1.tex") as mock_up_text:
        with patch("app.storage.r2.r2_storage.upload_bytes", return_value="https://r2/slot_1.pdf") as mock_up_bytes:
            with patch("app.resumes.service._compile_latex_to_pdf_via_api", return_value=b"%PDF"):
                with patch("app.storage.r2.r2_storage.generate_presigned_get_url", return_value="https://presigned.download/pdf"):
                    res = await svc.compile_custom_latex(job.id, custom_latex, user)

                    assert res.download_url == "https://presigned.download/pdf"
                    assert "successfully" in res.message

                    mock_up_text.assert_called_once()
                    mock_up_bytes.assert_called_once()
                    svc.job_repo.update.assert_called_once_with(
                        job,
                        optimized_resume_latex_url="https://r2/slot_1.tex",
                        optimized_resume_pdf_url="https://r2/slot_1.pdf",
                    )


def test_clean_llm_latex_output_strips_code_fences_and_quotes():
    from app.resumes.service import _clean_llm_latex_output

    # Markdown fence with latex tag
    raw1 = "```latex\n\\begin{itemize}\n\\item Python\n\\end{itemize}\n```"
    assert _clean_llm_latex_output(raw1) == "\\begin{itemize}\n\\item Python\n\\end{itemize}"

    # Smart quotes and preambles
    raw2 = "“Here is the updated skills block:\n\\item Python 3.11”"
    assert _clean_llm_latex_output(raw2) == "\\item Python 3.11"
