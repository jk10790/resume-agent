from resume_agent.agents.ats_scorer_agent import ATSScorerAgent
from resume_agent.models.agent_models import AnalyzedJD, ParsedResume


class DummyLLMService:
    def invoke_with_retry(self, *args, **kwargs):
        return '{"format_score": 80, "content_score": 80, "recommendations": []}'


def _sample_parsed_resume(raw_text: str) -> ParsedResume:
    return ParsedResume(
        all_skills=["Python", "AWS", "Docker", "Kubernetes"],
        job_titles=["Senior Software Engineer"],
        companies=["Example"],
        raw_text=raw_text,
        sections={"summary": "Summary", "experience": "Experience", "education": "Education", "skills": "Skills"},
    )


def _sample_jd() -> AnalyzedJD:
    return AnalyzedJD(
        job_title="Senior Software Engineer",
        company="Example",
        required_skills=["Python", "AWS"],
        preferred_skills=["Kubernetes"],
        technologies_needed=["Docker"],
        tools_needed=[],
        frameworks_needed=[],
        key_responsibilities=[],
        summary="",
        raw_text="Senior Software Engineer requiring Python, AWS, Docker, Kubernetes",
    )


def test_ats_scorer_rewards_exact_job_title_in_header():
    agent = ATSScorerAgent(DummyLLMService())
    resume = """Senior Software Engineer
jane@example.com | 555-123-4567

Work Experience
- Built Python services on AWS using Docker

Education
BS

Skills
Python, AWS, Docker, Kubernetes
"""

    score = agent.score(resume, _sample_jd(), _sample_parsed_resume(resume))

    assert score.score >= 80
    assert "exact job title" not in " ".join(score.recommendations).lower()


def test_ats_scorer_penalizes_missing_exact_job_title_and_mixed_dates():
    agent = ATSScorerAgent(DummyLLMService())
    resume = """Platform Engineering Lead
jane@example.com | 555-123-4567

Experience
- Built Python services on AWS using Docker
- Jan 2021 - Present
- 03/2019 - 2020

Education
BS

Skills
Python, AWS
"""

    score = agent.score(resume, _sample_jd(), _sample_parsed_resume(resume))

    assert score.score < 80
    joined = " ".join(score.recommendations).lower()
    assert "exact job title" in joined
    assert "date formatting" in joined
