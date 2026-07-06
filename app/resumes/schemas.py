from typing import Optional, Literal

from pydantic import BaseModel


class GenerateResumeResponse(BaseModel):
    latex_url: Optional[str] = None
    pdf_url: Optional[str] = None
    message: str


class SelectResumeRequest(BaseModel):
    version: Literal["original", "optimized"]


class ResumeResponse(BaseModel):
    version: str
    pdf_url: Optional[str] = None
    latex_url: Optional[str] = None
