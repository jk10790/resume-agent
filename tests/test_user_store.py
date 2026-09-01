import pytest

from resume_agent.storage import user_store
from resume_agent.storage import user_memory
from resume_agent.storage.user_context import reset_current_user, set_current_user


def test_upsert_google_user_and_skill_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub",
        email="tester@example.com",
        name="Test User",
        picture_url="https://example.com/avatar.png",
    )

    assert user["id"] is not None
    assert user["email"] == "tester@example.com"

    user_store.replace_user_skills(user["id"], ["Python", "AWS", "Python"])
    skills = user_store.get_user_skills(user["id"])

    assert skills == ["AWS", "Python"]


def test_quality_report_and_improved_resume_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-2",
        email="tester2@example.com",
        name="Test User Two",
        picture_url=None,
    )

    saved_report = user_store.save_quality_report_for_user(
        user["id"],
        "doc-123",
        {"overall_score": 82, "ats_score": 79},
    )
    loaded_report = user_store.get_quality_report_for_user(user["id"], "doc-123")

    assert saved_report["report"]["overall_score"] == 82
    assert loaded_report["report"]["ats_score"] == 79

    saved_resume = user_store.save_improved_resume_for_user(
        user["id"],
        "Improved resume text",
        original_doc_id="doc-123",
        score=88,
        metadata={"notes": "first version"},
    )
    loaded_resume = user_store.get_improved_resume_for_user(user["id"], "doc-123")

    assert saved_resume["score"] == 88
    assert loaded_resume["text"] == "Improved resume text"
    assert loaded_resume["metadata"]["notes"] == "first version"


def test_metric_inventory_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-3",
        email="tester3@example.com",
        name="Metric User",
        picture_url=None,
    )

    saved_metrics = user_store.replace_user_metric_records(
        user["id"],
        [
            {
                "raw": "35%",
                "normalized": "percent:35",
                "line": "Reduced costs by 35%",
                "category": "percent",
            },
            {
                "raw": "12 services",
                "normalized": "count:services:12",
                "line": "Owned 12 services",
                "category": "count",
            },
        ],
        state="confirmed",
        source="user_confirmed",
    )

    assert len(saved_metrics) == 2
    assert saved_metrics[0]["state"] == "confirmed"

    loaded_metrics = user_store.get_user_metric_records(user["id"], state="confirmed")
    assert {item["normalized"] for item in loaded_metrics} == {"percent:35", "count:services:12"}


def test_preferred_resume_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-pref",
        email="pref@example.com",
        name="Preferred Resume User",
        picture_url=None,
    )

    updated = user_store.set_user_preferred_resume(user["id"], "doc-preferred-123", "Resume Latest")
    fetched = user_store.get_user_by_id(user["id"])

    assert updated["preferred_resume_doc_id"] == "doc-preferred-123"
    assert fetched["preferred_resume_name"] == "Resume Latest"


def test_save_user_metric_answers_merges_existing_metrics(monkeypatch):
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-4",
        email="tester4@example.com",
        name="Metric Merge User",
        picture_url=None,
    )
    user_store.replace_user_metric_records(
        user["id"],
        [
            {
                "raw": "35%",
                "normalized": "percent:35",
                "line": "Reduced costs by 35%",
                "category": "percent",
            }
        ],
    )

    token = set_current_user(user)
    try:
        metrics = user_memory.save_user_metric_answers({"metrics_details": "Supported 12 services"})
        assert {item["normalized"] for item in metrics} == {"percent:35", "count:services:12"}
    finally:
        reset_current_user(token)


def test_strategy_brief_event_and_lookup_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-strategy",
        email="strategy@example.com",
        name="Strategy User",
        picture_url=None,
    )

    saved = user_store.save_job_strategy_brief_for_user(
        user["id"],
        {
            "company": "Acme",
            "job_title": "Platform Engineer",
            "archetype": "platform_infrastructure",
            "fit_score": 7,
            "should_apply": True,
            "confidence": 0.8,
            "role_summary": "Good fit for platform work.",
            "requirement_evidence": [],
            "gap_assessments": [],
            "positioning_strategy": ["Lead with reliability and automation work."],
            "tailoring_directives": [],
            "interview_seeds": [],
            "risk_notes": [],
            "approval_status": "pending",
        },
    )

    found = user_store.find_job_strategy_brief_for_user(
        user["id"],
        company="Acme",
        job_title="Platform Engineer",
    )
    assert found is not None
    assert found["id"] == saved["id"]

    event = user_store.add_job_strategy_event_for_user(
        user["id"],
        strategy_brief_id=saved["id"],
        event_type="strategy_approved",
        payload={"reason": "Looks accurate"},
    )
    events = user_store.list_job_strategy_events_for_user(user["id"], saved["id"])

    assert event["event_type"] == "strategy_approved"
    assert events[-1]["payload"]["reason"] == "Looks accurate"


def test_user_evidence_inventory_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-evidence",
        email="evidence@example.com",
        name="Evidence User",
        picture_url=None,
    )

    saved = user_store.replace_user_evidence_records(
        user["id"],
        [
            {
                "kind": "achievement",
                "title": "Scaled API platform",
                "content": "Scaled a backend API platform to millions of requests per day.",
                "tags": ["backend", "platform"],
            },
            {
                "kind": "interview_seed",
                "title": "Reliability incident",
                "content": "Story about leading a postmortem and fixing reliability gaps.",
                "tags": ["sre", "incident-response"],
            },
        ],
        state="confirmed",
        source="user_confirmed",
    )

    assert len(saved) == 2
    loaded = user_store.get_user_evidence_records(user["id"], state="confirmed")
    assert {item["kind"] for item in loaded} == {"achievement", "interview_seed"}


def test_target_archetypes_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-targets",
        email="targets@example.com",
        name="Target User",
        picture_url=None,
    )

    saved = user_store.replace_user_target_archetypes(
        user["id"],
        [
            {"archetype": "platform_infrastructure", "tier": "primary"},
            {"archetype": "product_technical_product", "tier": "secondary"},
        ],
    )

    assert saved[0]["tier"] == "primary"
    loaded = user_store.get_user_target_archetypes(user["id"])
    assert [item["archetype"] for item in loaded] == [
        "platform_infrastructure",
        "product_technical_product",
    ]


def test_discovered_role_upsert_preserves_dismissed_state():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-discover",
        email="discover@example.com",
        name="Discover User",
        picture_url=None,
    )

    saved = user_store.save_or_merge_discovered_role_for_user(
        user["id"],
        {
            "canonical_url": "https://jobs.example.com/role/123",
            "source_urls": ["https://jobs.example.com/role/123?gh_jid=1"],
            "source_domain": "jobs.example.com",
            "company": "Acme",
            "job_title": "Applied AI Engineer",
            "remote_mode": "remote",
            "employment_type": "full_time",
            "posted_label": "2 days ago",
            "archetype": "applied_ai_llmops",
            "extraction_confidence": 0.9,
            "raw_text": "A" * 500,
            "short_tldr": "Build AI systems.",
            "matched_filters": ["remote"],
            "possible_blockers": [],
            "rank_score": 88,
        },
    )

    user_store.update_discovered_role_inbox_state_for_user(user["id"], saved["id"], "dismissed")
    merged = user_store.save_or_merge_discovered_role_for_user(
        user["id"],
        {
            "canonical_url": "https://jobs.example.com/role/123",
            "source_urls": ["https://boards.example.com/job/123"],
            "source_domain": "jobs.example.com",
            "company": "Acme",
            "job_title": "Applied AI Engineer",
            "remote_mode": "remote",
            "employment_type": "full_time",
            "posted_label": "Today",
            "archetype": "applied_ai_llmops",
            "extraction_confidence": 0.92,
            "raw_text": "B" * 500,
            "short_tldr": "Build AI systems faster.",
            "matched_filters": ["remote", "python"],
            "possible_blockers": [],
            "rank_score": 90,
        },
    )

    assert merged["id"] == saved["id"]
    assert merged["inbox_state"] == "dismissed"
    assert set(merged["source_urls"]) == {
        "https://jobs.example.com/role/123",
        "https://jobs.example.com/role/123?gh_jid=1",
        "https://boards.example.com/job/123",
    }


def test_discovered_role_feedback_and_listing_order():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-discover-feedback",
        email="discover-feedback@example.com",
        name="Discover Feedback User",
        picture_url=None,
    )
    first = user_store.save_or_merge_discovered_role_for_user(
        user["id"],
        {
            "canonical_url": "https://jobs.example.com/role/first",
            "source_urls": ["https://jobs.example.com/role/first"],
            "source_domain": "jobs.example.com",
            "company": "Bravo",
            "job_title": "Platform Engineer",
            "remote_mode": "remote",
            "employment_type": "full_time",
            "posted_at": "2026-04-20T00:00:00+00:00",
            "posted_label": "Today",
            "archetype": "platform_infrastructure",
            "extraction_confidence": 0.9,
            "raw_text": "C" * 500,
            "short_tldr": "Platform work.",
            "rank_score": 95,
        },
    )
    second = user_store.save_or_merge_discovered_role_for_user(
        user["id"],
        {
            "canonical_url": "https://jobs.example.com/role/second",
            "source_urls": ["https://jobs.example.com/role/second"],
            "source_domain": "jobs.example.com",
            "company": "Acme",
            "job_title": "Backend Engineer",
            "remote_mode": "remote",
            "employment_type": "full_time",
            "posted_at": "2026-04-19T00:00:00+00:00",
            "posted_label": "1 day ago",
            "archetype": "software_engineering",
            "extraction_confidence": 0.88,
            "raw_text": "D" * 500,
            "short_tldr": "Backend role.",
            "rank_score": 80,
        },
    )

    user_store.record_discovered_role_feedback_for_user(
        user["id"],
        second["id"],
        "not_relevant",
        ["wrong role family"],
        "Too frontend-heavy",
    )
    user_store.update_discovered_role_inbox_state_for_user(user["id"], second["id"], "dismissed")

    active_roles = user_store.list_discovered_roles_for_user(user["id"], inbox_state="active")
    dismissed_roles = user_store.list_discovered_roles_for_user(user["id"], inbox_state="dismissed")
    feedback = user_store.list_discovered_role_feedback_for_user(user["id"], second["id"], limit=5)

    assert [role["id"] for role in active_roles] == [first["id"]]
    assert dismissed_roles[0]["id"] == second["id"]
    assert feedback[0]["decision"] == "not_relevant"
    assert feedback[0]["reasons"] == ["wrong role family"]


def test_discovery_saved_searches_and_preferences_roundtrip():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-discover-saved",
        email="discover-saved@example.com",
        name="Discover Saved Search User",
        picture_url=None,
    )

    saved = user_store.save_discovery_saved_search_for_user(
        user["id"],
        name="Remote AI",
        criteria={"search_intent": "applied ai", "remote_modes": ["remote"]},
        is_default=True,
    )
    listed = user_store.list_discovery_saved_searches_for_user(user["id"])
    loaded = user_store.get_discovery_saved_search_for_user(user["id"], saved["id"])
    prefs = user_store.save_discovery_user_preferences_for_user(
        user["id"],
        {"avoid_keywords": ["frontend"], "remote_modes": ["remote"]},
    )

    assert saved["is_default"] is True
    assert listed[0]["name"] == "Remote AI"
    assert loaded["criteria"]["search_intent"] == "applied ai"
    assert prefs["defaults"]["avoid_keywords"] == ["frontend"]


def _seed_discovered_role(user_id: int, url: str, *, title: str = "Applied AI Engineer", rank: float = 80.0):
    return user_store.save_or_merge_discovered_role_for_user(
        user_id,
        {
            "canonical_url": url,
            "source_urls": [url],
            "source_domain": "jobs.example.com",
            "company": "Acme",
            "job_title": title,
            "remote_mode": "remote",
            "employment_type": "full_time",
            "posted_label": "2 days ago",
            "archetype": "applied_ai_llmops",
            "extraction_confidence": 0.9,
            "raw_text": "A" * 500,
            "short_tldr": "Build AI systems.",
            "matched_filters": ["remote"],
            "possible_blockers": [],
            "rank_score": rank,
        },
    )


def test_discovered_role_list_omits_raw_text_but_detail_keeps_it():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-discover-list-payload",
        email="discover-list@example.com",
        name="Discover User",
        picture_url=None,
    )
    saved = _seed_discovered_role(user["id"], "https://jobs.example.com/role/payload")

    listed = user_store.list_discovered_roles_for_user(user["id"], inbox_state="active")
    detail = user_store.get_discovered_role_for_user(user["id"], saved["id"])

    assert len(listed) == 1
    assert "raw_text" not in listed[0]
    assert listed[0]["short_tldr"] == "Build AI systems."
    assert detail["raw_text"] == "A" * 500


def test_discovered_role_fit_is_persisted_and_sortable():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-discover-fit",
        email="discover-fit@example.com",
        name="Discover User",
        picture_url=None,
    )
    strong = _seed_discovered_role(user["id"], "https://jobs.example.com/role/strong", title="Strong", rank=10.0)
    weak = _seed_discovered_role(user["id"], "https://jobs.example.com/role/weak", title="Weak", rank=99.0)
    _seed_discovered_role(user["id"], "https://jobs.example.com/role/unscored", title="Unscored", rank=95.0)

    user_store.save_discovered_role_fit_for_user(
        user["id"],
        strong["id"],
        {"score": 9, "should_apply": True, "matching_areas": ["python"], "missing_areas": []},
    )
    user_store.save_discovered_role_fit_for_user(
        user["id"],
        weak["id"],
        {"score": 3, "should_apply": False, "matching_areas": [], "missing_areas": ["kubernetes"]},
    )

    detail = user_store.get_discovered_role_for_user(user["id"], strong["id"])
    assert detail["fit_score"] == 9
    assert detail["fit_should_apply"] is True
    assert detail["fit_evaluation"]["matching_areas"] == ["python"]
    assert detail["fit_evaluated_at"]

    by_fit = user_store.list_discovered_roles_for_user(user["id"], inbox_state="active", sort="fit")
    # Scored roles lead, best first; unscored sinks below them despite its higher rank score.
    assert [row["job_title"] for row in by_fit] == ["Strong", "Weak", "Unscored"]
    assert by_fit[0]["fit_should_apply"] is True

    evaluated = user_store.list_discovered_roles_for_user(
        user["id"], inbox_state="active", sort="fit", evaluated_only=True
    )
    assert [row["job_title"] for row in evaluated] == ["Strong", "Weak"]


def test_discovered_role_list_rejects_unknown_sort():
    user = user_store.upsert_google_user(
        google_sub="test-google-sub-discover-sort",
        email="discover-sort@example.com",
        name="Discover User",
        picture_url=None,
    )

    with pytest.raises(ValueError):
        user_store.list_discovered_roles_for_user(user["id"], sort="nonsense")


def test_fit_columns_are_added_to_an_existing_discovered_roles_table():
    conn = user_store.get_db_connection()
    try:
        for column in ("fit_score", "fit_should_apply", "fit_evaluation_json", "fit_evaluated_at"):
            conn.execute(f"ALTER TABLE discovered_roles DROP COLUMN {column}")
        conn.commit()
        remaining = {row["name"] for row in conn.execute("PRAGMA table_info(discovered_roles)").fetchall()}
        assert "fit_score" not in remaining
    finally:
        conn.close()

    # get_db_connection() re-runs the schema init, which should migrate the older table.
    conn = user_store.get_db_connection()
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(discovered_roles)").fetchall()}
    finally:
        conn.close()

    assert {"fit_score", "fit_should_apply", "fit_evaluation_json", "fit_evaluated_at"} <= columns
