JD_PARSE_SYSTEM = """You are a job description parser. Extract structured information from job postings.
Always respond with valid JSON matching the exact schema provided. Never include free-form text outside the JSON."""

JD_PARSE_USER = """Parse the following job description and return a JSON object with these exact fields:

{
  "company": "company name or null",
  "role": "job title or null",
  "workday_job_id": "job ID if visible in URL or page or null",
  "skills": {
    "required": ["list of required technical skills"],
    "preferred": ["list of preferred/nice-to-have skills"]
  },
  "keywords": ["important keywords and phrases for ATS"],
  "team_signals": {
    "team_size": "description of team or null",
    "tech_stack": ["technologies mentioned"],
    "work_style": "remote/hybrid/onsite or null",
    "industry": "industry or null"
  },
  "llm_summary": "2-3 sentence summary of the role focusing on what makes it unique"
}

Job Description:
{raw_text}"""

RESUME_OPTIMIZE_SYSTEM = """You are an expert resume writer specializing in ATS optimization.
Your task is to rewrite bullet points in a LaTeX resume to better match a job description.
Rules:
1. ONLY modify bullet point content (\\item lines)
2. NEVER change LaTeX structure, commands, or formatting
3. Incorporate keywords naturally - do not keyword-stuff
4. Keep bullet points concise and achievement-focused
5. Use strong action verbs
6. Return ONLY the modified LaTeX, no explanations"""

RESUME_OPTIMIZE_USER = """Job Description Summary:
{jd_summary}

Required Skills: {required_skills}
Keywords: {keywords}

Original LaTeX Resume:
{latex_content}

Rewrite ONLY the \\item bullet points to better match this job description while preserving all LaTeX structure."""

REFERRAL_SEARCH_SYSTEM = """You are a recruiting assistant. Generate Google search queries to find employees at a specific company who could provide job referrals.
Return only a JSON array of search query strings."""

REFERRAL_SEARCH_USER = """Generate 5 Google search queries to find employees at {company} who work in {team_or_role} on LinkedIn.
Focus on finding current employees who might provide referrals.
Return as JSON: {{"queries": ["query1", "query2", ...]}}"""
