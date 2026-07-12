from typing import Optional, Literal, List

from pydantic import BaseModel


RESUME_SECTIONS = Literal[
    "professional_summary", "skills", "work_experience", "projects"
]


class CreateResumeUploadUrlsResponse(BaseModel):
    """Presigned PUT URL for the client to upload the LaTeX source of a resume copy."""

    latex_presigned_url: str


class GenerateAiResumeRequest(BaseModel):
    """Sections of the resume to optimize. They are rewritten in parallel."""

    sections: List[RESUME_SECTIONS]


class GenerateAiResumeResponse(BaseModel):
    """Result of generating the AI-optimized resume for a job."""

    download_url: Optional[
        str
    ]  # presigned GET URL for the compiled PDF (None if compile failed)
    validated: bool  # passed pylatexenc validation


class GetResumeDownloadResponse(BaseModel):
    """Presigned download URL for a stored resume copy (PDF only)."""

    version: Literal["original", "ai"]
    download_url: Optional[str] = (
        None  # presigned GET URL for the PDF (None if not compiled yet)
    )
    message: str
