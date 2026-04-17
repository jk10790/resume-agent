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
