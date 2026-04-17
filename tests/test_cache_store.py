from resume_agent.storage.cache_store import SQLiteCacheStore
from resume_agent.utils.agent_cache import AgentCache
from resume_agent.utils.cache_tailoring import TailoringCache


def test_sqlite_cache_store_put_get_and_namespace_delete():
    store = SQLiteCacheStore()
    store.delete_namespace("test_namespace")

    store.put(
        "test_namespace",
        "entry1",
        {"value": 123},
        source_hash="source-a",
        schema_version="schema-v1",
    )

    assert store.get("test_namespace", "entry1") == {"value": 123}

    store.delete_namespace("test_namespace")
    assert store.get("test_namespace", "entry1") is None


def test_agent_cache_round_trip():
    cache = AgentCache()
    cache.clear_cache()

    resume_text = "Jane Doe\nPython AWS"
    jd_text = "Need Python and AWS"
    parsed = {"all_skills": ["Python", "AWS"]}
    analyzed = {"required_skills": ["Python", "AWS"]}

    cache.set_parsed_resume(resume_text, parsed)
    cache.set_analyzed_jd(jd_text, analyzed)
    cache.set_tailored_result(resume_text, jd_text, "tailored output", intensity="medium")

    assert cache.get_parsed_resume(resume_text) == parsed
    assert cache.get_analyzed_jd(jd_text) == analyzed
    assert cache.get_tailored_result(resume_text, jd_text, intensity="medium") == "tailored output"


def test_agent_cache_uses_source_cache_key_for_parsed_resume():
    cache = AgentCache()
    cache.clear_cache()

    parsed = {"all_skills": ["Python"]}
    source_cache_key = "drive:doc_123:application/vnd.google-apps.document:2026-04-15T10:00:00Z"

    cache.set_parsed_resume("Original resume text", parsed, source_cache_key=source_cache_key)

    assert (
        cache.get_parsed_resume("Different text, same source version", source_cache_key=source_cache_key)
        == parsed
    )


def test_tailoring_cache_round_trip():
    cache = TailoringCache()
    cache.clear_cache()

    pattern_id = cache.save_pattern(
        jd_text="Need Python AWS and Docker",
        jd_requirements={"required_skills": ["Python", "AWS", "Docker"]},
        tailoring_changes={"experience": "Added AWS bullet"},
        intensity="medium",
        quality_score=84,
    )

    pattern = cache.get_pattern(pattern_id)
    similar = cache.find_similar_patterns(
        jd_text="Looking for AWS Python Docker background",
        jd_requirements={"required_skills": ["Python", "AWS", "Docker"]},
        min_similarity=0.7,
    )

    assert pattern is not None
    assert pattern.quality_score == 84
    assert similar
    assert similar[0][0].pattern_id == pattern_id
