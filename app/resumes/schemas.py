from typing import Optional, Literal

from pydantic import BaseModel


class CreateResumeUploadUrlsResponse(BaseModel):
    """Presigned URL for the client to upload a resume copy directly to R2."""
    presigned_url: str


class GenerateAiResumeResponse(BaseModel):
    """Result of generating the AI-optimized resume for a job."""
    download_url: str                    # presigned GET URL for the client to fetch it
    validated: bool                      # passed pylatexenc validation


class GetResumeDownloadResponse(BaseModel):
    """Presigned download URL for a stored resume copy."""
    version: Literal["original", "ai"]
    latex_url: Optional[str] = None
    download_url: Optional[str] = None   # presigned GET URL (None if not uploaded yet)
    message: str

