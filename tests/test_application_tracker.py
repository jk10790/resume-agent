from resume_agent.storage import user_store
from resume_agent.tracking import application_tracker


def test_applications_are_scoped_to_local_user():
    user_one = user_store.upsert_google_user(
        google_sub="tracker-user-1",
        email="tracker1@example.com",
        name="Tracker One",
        picture_url=None,
    )
    user_two = user_store.upsert_google_user(
        google_sub="tracker-user-2",
        email="tracker2@example.com",
        name="Tracker Two",
        picture_url=None,
    )

    app_one = application_tracker.add_or_update_application(
        user_id=user_one["id"],
        company="Acme",
        job_title="Platform Engineer",
        fit_score=8,
    )
    application_tracker.add_or_update_application(
        user_id=user_two["id"],
        company="Beta",
        job_title="Product Engineer",
        fit_score=6,
    )

    visible_to_user_one = application_tracker.list_applications(user_id=user_one["id"])
    visible_to_user_two = application_tracker.list_applications(user_id=user_two["id"])

    assert [app["id"] for app in visible_to_user_one] == [app_one]
    assert [app["company"] for app in visible_to_user_two] == ["Beta"]


def test_application_rows_include_linked_strategy_snapshot():
    user = user_store.upsert_google_user(
        google_sub="tracker-strategy-user",
        email="tracker-strategy@example.com",
        name="Tracker Strategy",
        picture_url=None,
    )

    brief = user_store.save_job_strategy_brief_for_user(
        user["id"],
        {
            "company": "Acme",
            "job_title": "Applied AI Engineer",
            "archetype": "applied_ai_llmops_agentic_systems",
            "fit_score": 8,
            "should_apply": True,
            "confidence": 0.81,
            "gating_decision": "stop_and_ask",
            "role_summary": "Strong adjacent fit with clear platform-to-agentic positioning.",
            "requirement_evidence": [],
            "gap_assessments": [],
            "positioning_strategy": [],
            "tailoring_directives": [],
            "interview_seeds": [],
            "risk_notes": [],
            "approval_status": "override_approved",
        },
    )
    user_store.add_job_strategy_event_for_user(
        user["id"],
        strategy_brief_id=brief["id"],
        event_type="strategy_override_approved",
        payload={"stage": "strategy"},
    )

    application_id = application_tracker.add_or_update_application(
        user_id=user["id"],
        company="Acme",
        job_title="Applied AI Engineer",
        fit_score=8,
        strategy_brief_id=brief["id"],
    )

    application = application_tracker.get_application(application_id, user_id=user["id"])

    assert application is not None
    assert application["strategy_brief"]["id"] == brief["id"]
    assert application["strategy_brief"]["approval_status"] == "override_approved"
    assert application["strategy_brief"]["archetype"] == "applied_ai_llmops_agentic_systems"
    assert application["strategy_brief"]["last_event_type"] == "strategy_override_approved"


def test_application_statistics_return_tracker_summary_fields():
    user = user_store.upsert_google_user(
        google_sub="tracker-stats-user",
        email="tracker-stats@example.com",
        name="Tracker Stats",
        picture_url=None,
    )

    application_tracker.add_or_update_application(
        user_id=user["id"],
        company="Acme",
        job_title="Platform Engineer",
        fit_score=8,
        status="applied",
    )
    interview_id = application_tracker.add_or_update_application(
        user_id=user["id"],
        company="Beta",
        job_title="Data Engineer",
        fit_score=7,
        status="interview",
    )
    application_tracker.update_application_status(interview_id, "interview")
    rejected_id = application_tracker.add_or_update_application(
        user_id=user["id"],
        company="Gamma",
        job_title="ML Engineer",
        fit_score=5,
        status="applied",
    )
    application_tracker.update_application_status(rejected_id, "rejected")

    stats = application_tracker.get_statistics(user_id=user["id"])

    assert stats["total"] == 3
    assert stats["total_applications"] == 3
    assert stats["interview"] == 1
    assert stats["active"] == 2
    assert stats["avg_fit_score"] == stats["average_fit_score"]


def test_pattern_analysis_uses_strategy_blockers_and_fit_floor():
    user = user_store.upsert_google_user(
        google_sub="tracker-pattern-user",
        email="tracker-pattern@example.com",
        name="Tracker Pattern",
        picture_url=None,
    )

    brief = user_store.save_job_strategy_brief_for_user(
        user["id"],
        {
            "company": "Acme",
            "job_title": "Platform Engineer",
            "archetype": "platform_infrastructure",
            "target_alignment": "primary",
            "fit_score": 8,
            "should_apply": True,
            "confidence": 0.82,
            "gating_decision": "proceed",
            "role_summary": "Platform role",
            "requirement_evidence": [{"requirement": "Kubernetes", "status": "matched", "evidence": "Owned clusters", "source_section": "experience"}],
            "gap_assessments": [{"requirement": "Terraform", "severity": "stretch", "mitigation": "Use adjacent IaC examples", "reason_code": "stack_mismatch"}],
            "positioning_strategy": [],
            "tailoring_directives": [],
            "interview_seeds": [],
            "risk_notes": [],
            "approval_status": "approved",
        },
    )

    application_tracker.add_or_update_application(
        user_id=user["id"],
        company="Acme",
        job_title="Platform Engineer",
        fit_score=8,
        strategy_brief_id=brief["id"],
        status="interview",
    )
    application_tracker.add_or_update_application(
        user_id=user["id"],
        company="Beta",
        job_title="Software Engineer",
        fit_score=6,
        status="rejected",
    )

    analysis = application_tracker.get_pattern_analysis(user_id=user["id"])

    assert analysis["fit_floor_recommendation"] == 8
    assert analysis["blocker_reason_codes"][0]["reason_code"] == "stack_mismatch"
    assert any(item["archetype"] == "platform_infrastructure" for item in analysis["archetype_breakdown"])
