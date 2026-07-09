from typing import Optional, Literal

from pydantic import BaseModel


class CreateResumeUploadUrlsResponse(BaseModel):
    """Presigned PUT URL for the client to upload the LaTeX source of a resume copy."""
    latex_presigned_url: str


class GenerateAiResumeResponse(BaseModel):
    """Result of generating the AI-optimized resume for a job."""
    download_url: Optional[str]            # presigned GET URL for the compiled PDF (None if compile failed)
    validated: bool                        # passed pylatexenc validation


class GetResumeDownloadResponse(BaseModel):
    """Presigned download URL for a stored resume copy (PDF only)."""
    version: Literal["original", "ai"]
    download_url: Optional[str] = None     # presigned GET URL for the PDF (None if not compiled yet)
    message: str

