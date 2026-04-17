from resume_agent.agents.skill_recommender import (
    build_skill_records,
    infer_role_tags,
    recommend_profile_skills,
    suggest_as_you_type,
)


def test_infer_role_tags_from_titles():
    tags = infer_role_tags(["Senior Backend Engineer", "Platform Developer"])
    assert "backend" in tags
    assert "platform" in tags


def test_build_skill_records_preserves_categories():
    records = build_skill_records(
        {"frameworks": ["React"], "tools": ["Docker"]},
        ["Docker", "React"],
    )
    assert any(record["name"] == "React" and record["category"] == "frameworks" for record in records)
    assert any(record["name"] == "Docker" and record["category"] == "tools" for record in records)


def test_recommend_profile_skills_uses_adjacency_and_role():
    recommendations = recommend_profile_skills(
        detected_skills=["Python", "AWS"],
        confirmed_skills=[],
        job_titles=["Senior Backend Engineer"],
        total_years=6,
    )
    names = {item["name"] for item in recommendations}
    assert "FastAPI" in names
    assert "Docker" in names


def test_suggest_as_you_type_filters_confirmed():
    suggestions = suggest_as_you_type(
        query="py",
        confirmed_skills=["Python"],
        role_tags=["backend"],
        limit=10,
    )
    names = [item["name"] for item in suggestions]
    assert "Python" not in names
