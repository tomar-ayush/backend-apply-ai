from pydantic import BaseModel, Field
from typing import List, Optional, Literal, Dict


class Skills(BaseModel):
    required: List[str] = Field(
        description="List of required core technical or functional skills explicitly requested."
    )
    preferred: List[str] = Field(
        description="List of preferred, optional, or nice-to-have skills."
    )


class TeamSignals(BaseModel):
    team_size: Optional[str] = Field(
        None,
        description="Description or count of team size if mentioned, else null.",
    )
    tech_stack: List[str] = Field(
        description="List of technologies, frameworks, or tools mentioned."
    )
    work_style: Optional[Literal["remote", "hybrid", "onsite"]] = Field(
        None, description="Strictly choose one if mentioned."
    )
    industry: Optional[str] = Field(
        None, description="The specific industrial sector or domain."
    )


class JobParseSchema(BaseModel):
    """Combined JD parse: extracted fields + interview-prep learning material."""

    company: Optional[str] = Field(
        None, description="Official company name."
    )
    role: Optional[str] = Field(None, description="Formal job title.")
    workday_job_id: Optional[str] = Field(
        None,
        description="Job posting identification code (e.g., R0012345) if present.",
    )
    skills: Skills
    keywords: List[str] = Field(
        description="Important keywords and key phrases optimized for an ATS search indexing system."
    )
    team_signals: TeamSignals
    llm_summary: str = Field(
        description="A brief 2-3 sentence summary highlighting the distinctive responsibilities of the position."
    )
    learning: Dict[str, List[str]] = Field(
        description="Topic name -> list of challenging, frequently-asked interview questions (the {topic: [questions]} format)."
    )
