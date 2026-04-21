from resume_agent.models.resume import FitEvaluation
from resume_agent.services.archetype_strategy import (
    apply_target_alignment,
    detect_job_archetype,
    get_target_alignment,
    infer_gap_reason_code,
    normalize_target_archetype_preferences,
)


def test_detect_job_archetype_prefers_platform_tokens():
    analyzed_jd = type(
        "AnalyzedJD",
        (),
        {
            "job_title": "Senior Platform Engineer",
            "summary": "Own Kubernetes, Terraform, and observability systems.",
            "technologies_needed": ["Kubernetes", "Terraform", "AWS"],
            "required_skills": ["Kubernetes", "Terraform"],
        },
    )()

    assert detect_job_archetype(analyzed_jd) == "platform_infrastructure"


def test_apply_target_alignment_adjusts_primary_and_adjacent_scores():
    base = FitEvaluation(
        score=6,
        should_apply=True,
        matching_areas=["Python"],
        missing_areas=["Kubernetes"],
        recommendations=["Base recommendation"],
        confidence=0.7,
    )

    boosted, primary_tier = apply_target_alignment(
        base,
        archetype="platform_infrastructure",
        preferences=[{"archetype": "platform_infrastructure", "tier": "primary"}],
    )
    stretched, adjacent_tier = apply_target_alignment(
        base,
        archetype="platform_infrastructure",
        preferences=[{"archetype": "platform_infrastructure", "tier": "adjacent"}],
    )

    assert primary_tier == "primary"
    assert boosted.score == 7
    assert "primary target archetype" in boosted.recommendations[0]
    assert adjacent_tier == "adjacent"
    assert stretched.score == 5
    assert "stretch path" in stretched.recommendations[0]


def test_normalize_target_preferences_and_reason_codes():
    normalized = normalize_target_archetype_preferences(
        [
            {"archetype": "Platform / Infrastructure / SRE", "tier": "primary"},
            {"archetype": "Platform / Infrastructure / SRE", "tier": "secondary"},
            {"archetype": "Product / Technical Product", "tier": "secondary"},
        ]
    )

    assert normalized == [
        {"archetype": "platform_infrastructure", "tier": "primary"},
        {"archetype": "product_technical_product", "tier": "secondary"},
    ]
    assert get_target_alignment("platform_infrastructure", normalized) == "primary"
    assert infer_gap_reason_code("US-only remote role", "") == "geo_restriction"
    assert infer_gap_reason_code("Need deep Kubernetes and Terraform experience", "") == "stack_mismatch"
