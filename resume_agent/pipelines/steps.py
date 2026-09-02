"""Step implementations for the fit and tailor pipelines.

Each step is a plain function over a context. It reads what the pipeline
guarantees is present and returns what it produced -- no step re-derives an
input, because the engine will not run it out of order.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional

from ..config import settings
from ..llm.pipeline import PipelineError
from ..utils.logger import logger
from .context import FitContext, TailorContext


# --------------------------------------------------------------------------
# Shared: loading and understanding the inputs
# --------------------------------------------------------------------------


def load_profile(context) -> Dict[str, Any]:
    """Load the authenticated user's confirmed skills and evidence, if any."""
    from ..services.profile_context_service import load_profile_context

    profile = load_profile_context(context.request.local_user_id)
    if profile is not None:
        context.services.set_confirmed_skills(getattr(profile, "confirmed_skills", []) or [])
    return {"profile_context": profile}


def load_resume(context) -> Dict[str, Any]:
    """Read the resume text, unless the caller already supplied it."""
    from ..services.resume_source import load_resume_text, resume_source_cache_key

    if context.request.resume_text:
        text = context.request.resume_text
    else:
        text = load_resume_text(
            context.services.google_services,
            context.request.resume_doc_id,
        )

    produced = {
        "resume_text": text,
        "resume_source_cache_key": resume_source_cache_key(
            context.services.google_services, context.request.resume_doc_id
        ),
    }
    if isinstance(context, TailorContext):
        produced["original_resume_text"] = text
    return produced


def understand_inputs(context) -> Dict[str, Any]:
    """Parse the resume and analyse the JD.

    Independent of one another, so they run concurrently. Both are cached on
    their own inputs, which is what makes a discovery sweep cheap: the resume
    parse is paid once and reused for every role.
    """
    services = context.services
    with ThreadPoolExecutor(max_workers=2) as executor:
        parse_future = executor.submit(
            services.agent("resume_parser").parse,
            context.resume_text,
            True,
            context.resume_source_cache_key,
        )
        analyze_future = executor.submit(
            services.agent("jd_analyzer").analyze,
            context.request.jd_text,
            context.request.job_title,
            context.request.company,
        )
        parsed_resume = parse_future.result()
        analyzed_jd = analyze_future.result()

    logger.info(
        "Inputs understood",
        skills=len(parsed_resume.all_skills),
        required=len(analyzed_jd.required_skills),
    )
    return {"parsed_resume": parsed_resume, "analyzed_jd": analyzed_jd}


def evaluate_fit(context) -> Dict[str, Any]:
    """Judge the match. Raises rather than substituting a score."""
    from ..services.archetype_strategy import apply_target_alignment, detect_job_archetype
    from ..models.resume import FitEvaluation

    evaluation = context.services.agent("fit_evaluator").evaluate_fit(
        context.parsed_resume, context.analyzed_jd
    )

    preferences = getattr(context.profile_context, "target_archetype_preferences", None)
    if preferences:
        evaluation, alignment = apply_target_alignment(
            evaluation,
            archetype=detect_job_archetype(context.analyzed_jd),
            preferences=preferences,
        )
        evaluation = FitEvaluation(
            score=evaluation.score,
            should_apply=evaluation.should_apply,
            confidence=evaluation.confidence,
            matching_areas=evaluation.matching_areas,
            missing_areas=evaluation.missing_areas,
            recommendations=evaluation.recommendations[:8],
            reasoning=f"{evaluation.reasoning or ''} Target alignment: {alignment}.".strip(),
        )

    return {"evaluation": evaluation}


# --------------------------------------------------------------------------
# Tailoring
# --------------------------------------------------------------------------


def build_strategy(context: TailorContext) -> Dict[str, Any]:
    """Produce or reuse the strategy brief that gates tailoring."""
    from ..storage.user_store import (
        add_job_strategy_event_for_user,
        link_discovered_role_strategy_brief_for_user,
    )

    service = context.services.agent("strategy_brief")
    request = context.request

    brief = service.find_existing_brief(
        request.local_user_id,
        company=request.company or context.analyzed_jd.company or "",
        job_title=request.job_title or context.analyzed_jd.job_title or "",
    )
    if brief is None:
        brief = service.build_brief(
            company=request.company,
            job_title=request.job_title,
            job_url=request.job_url,
            jd_text=request.jd_text,
            parsed_resume=context.parsed_resume,
            analyzed_jd=context.analyzed_jd,
            fit_evaluation=context.evaluation,
            profile_context=context.profile_context,
        )
    brief = service.persist_brief(request.local_user_id, brief)

    if request.local_user_id and request.discovered_role_id and brief.id:
        link_discovered_role_strategy_brief_for_user(
            request.local_user_id, request.discovered_role_id, brief.id
        )
    if request.local_user_id and brief.id:
        add_job_strategy_event_for_user(
            request.local_user_id,
            strategy_brief_id=brief.id,
            event_type="strategy_brief_review_requested",
            payload={"gating_decision": brief.gating_decision},
        )

    return {"strategy_brief": brief, "strategy_brief_id": brief.id}


def _editing_baseline(context: TailorContext) -> str:
    return context.current_draft_text or context.original_resume_text or context.resume_text or ""


def tailor_resume(context: TailorContext) -> Dict[str, Any]:
    """Rewrite the resume for this role, or edit one entry of an existing draft."""
    from ..utils.agent_cache import get_agent_cache

    request = context.request
    tailor = context.services.agent("resume_tailor")
    intensity = request.intensity or settings.tailoring_intensity_default
    sections = _normalize_sections(request.sections_to_tailor)
    baseline = _editing_baseline(context)

    cache = get_agent_cache()
    cache_args = dict(
        intensity=intensity,
        refinement_feedback=request.refinement_feedback,
        sections_to_tailor=sections,
        current_draft_text=context.current_draft_text,
        target_entry_text=request.target_entry_text,
        protected_entry_texts=request.protected_entry_texts,
        revert_target_entry=request.revert_target_entry,
    )
    cached = cache.get_tailored_result(context.resume_text, request.jd_text, **cache_args)
    if cached is not None:
        logger.info("Using cached tailored resume")
        return {"tailored_resume": cached}

    if context.current_draft_text and request.target_entry_text and request.revert_target_entry:
        result = tailor.revert_single_entry(
            current_resume_text=baseline,
            original_resume_text=context.original_resume_text or context.resume_text,
            target_entry_text=request.target_entry_text,
            preserve_sections=request.preserve_sections,
            protected_entry_texts=request.protected_entry_texts,
        ) or baseline
    elif request.refinement_feedback and context.current_draft_text and request.target_entry_text:
        result = tailor.refine_single_entry(
            current_resume_text=baseline,
            original_resume_text=context.original_resume_text or context.resume_text,
            target_entry_text=request.target_entry_text,
            feedback=request.refinement_feedback,
            analyzed_jd=context.analyzed_jd,
            preserve_sections=request.preserve_sections,
            protected_entry_texts=request.protected_entry_texts,
        ) or baseline
    else:
        result = tailor.tailor(
            context.original_resume_text or context.resume_text,
            context.parsed_resume,
            context.analyzed_jd,
            context.evaluation,
            None,
            strategy_brief=context.strategy_brief,
            intensity=intensity,
            refinement_feedback=request.refinement_feedback,
            current_draft_text=context.current_draft_text,
            preserve_sections=request.preserve_sections,
            protected_entry_texts=request.protected_entry_texts,
        )
        if sections and not request.target_entry_text:
            from ..utils.resume_parser import merge_resume_sections, parse_resume_sections

            result = merge_resume_sections(
                parse_resume_sections(baseline),
                parse_resume_sections(result),
                sections,
            )

    cache.set_tailored_result(context.resume_text, request.jd_text, result, **cache_args)
    return {"tailored_resume": result}


def humanize(context: TailorContext) -> Dict[str, Any]:
    """Single pass to soften templated phrasing.

    The only rewrite after the draft. A critique/revise pair used to run as well,
    on a truncated copy, and three sequential rewrites drift further from the
    candidate's voice than one does.
    """
    if not settings.humanizer_enabled:
        return {}
    return {
        "tailored_resume": context.services.agent("humanizer").humanize(
            context.original_resume_text or context.resume_text,
            context.tailored_resume,
        )
    }


def score_ats(context: TailorContext) -> Dict[str, Any]:
    """Score the tailored resume for ATS friendliness."""
    evaluation = context.evaluation
    if not (evaluation.should_apply or evaluation.score >= settings.ats_scoring_min_fit_score):
        logger.info("Skipping ATS scoring - fit score too low", score=evaluation.score)
        return {}

    score = context.services.agent("ats_scorer").score(
        context.tailored_resume, context.analyzed_jd, context.parsed_resume
    )
    return {"ats_score": score.score, "ats_score_object": score}


def validate(context: TailorContext) -> Dict[str, Any]:
    """Check the draft against the source. Rules by default, LLM only on request."""
    from ..validation import validate_tailored_resume

    validation = validate_tailored_resume(
        original_resume=context.original_resume_text or context.resume_text or "",
        tailored_resume=context.tailored_resume or "",
        jd_text=context.request.jd_text or "",
        analyzed_jd=context.analyzed_jd,
        profile_context=context.profile_context,
        llm_service=context.services.llm_service,
    )
    return {"validation": validation}


def build_review(context: TailorContext) -> Dict[str, Any]:
    """Assemble the structured review the UI renders.

    Deterministic: it recombines the validation findings, ATS score, fit and
    strategy brief into per-surface verdicts. No model call.
    """
    from ..review.bundle_builder import build_review_bundle

    return {
        "review_bundle": build_review_bundle(
            tailored_resume=context.tailored_resume,
            validation=context.validation,
            ats_score=context.ats_score_object,
            fit_evaluation=context.evaluation,
            analyzed_jd=context.analyzed_jd,
            strategy_brief=context.strategy_brief,
        )
    }


def enforce_grounding(context: TailorContext) -> Dict[str, Any]:
    """Remove numeric claims the rules could not trace to a source.

    Detection alone is not enough. An earlier version of this pipeline flagged
    fabricated metrics and then handed the user the draft with them still in it;
    on a real resume that produced "99.9% uptime" and "sub-200ms p95 latency"
    against a resume containing neither.

    The removal is targeted: the rules supply the exact offending strings, so
    this is a find-and-soften edit rather than another whole-document rewrite,
    and it costs nothing at all when the draft is already clean.
    """
    flagged = list((getattr(context.validation, "metric_provenance", None) or {}).get("flagged") or [])
    if not flagged:
        return {}

    logger.warning("Removing unverified metrics from draft", metrics=flagged)
    cleaned = context.services.agent("resume_tailor").strip_unverified_metrics(
        context.tailored_resume, flagged
    )
    if not cleaned:
        return {}

    # Re-check once. If something survived, the validation the user sees still
    # reports it rather than the pipeline quietly claiming success.
    from ..validation import validate_tailored_resume

    revalidated = validate_tailored_resume(
        original_resume=context.original_resume_text or context.resume_text or "",
        tailored_resume=cleaned,
        jd_text=context.request.jd_text or "",
        analyzed_jd=context.analyzed_jd,
        profile_context=context.profile_context,
        llm_service=context.services.llm_service,
    )
    still = list((revalidated.metric_provenance or {}).get("flagged") or [])
    if still:
        logger.warning("Unverified metrics survived the removal pass", metrics=still)
    return {"tailored_resume": cleaned, "validation": revalidated}


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def save_to_google(context: TailorContext) -> Dict[str, Any]:
    """Copy the tailored resume into the user's Drive folder."""
    from ..config import GOOGLE_FOLDER_ID, RESUME_DOC_ID
    from ..storage.google_docs import create_google_doc_in_folder, write_to_google_doc
    from ..storage.google_drive import copy_doc_to_folder, get_subfolder_id_for_job
    from ..storage.google_drive_utils import GOOGLE_DOC_MIME, get_file_metadata

    if not context.services.google_services:
        raise PipelineError("Google services not available", code="no_google_session")

    drive_service, docs_service = context.services.google_services
    base_folder_id = context.request.save_folder_id or GOOGLE_FOLDER_ID
    if not base_folder_id:
        raise PipelineError(
            "Save folder ID not provided. Set GOOGLE_FOLDER_ID in settings or choose a folder when tailoring.",
            code="no_folder",
        )
    source_file_id = context.request.resume_doc_id or RESUME_DOC_ID
    if not source_file_id:
        raise PipelineError(
            "Source resume document ID not available. Set RESUME_DOC_ID or select a resume when tailoring.",
            code="no_source_doc",
        )

    job_display = (context.request.job_title or "").strip() or "Job_Application"
    company_display = (context.request.company or "").strip() or "Application"
    subfolder_id = get_subfolder_id_for_job(
        base_folder_id, job_display, company_display, drive_service=drive_service
    )
    doc_name = f"{job_display}_Tailored"

    meta = get_file_metadata(drive_service, source_file_id)
    if meta and meta.get("mimeType") == GOOGLE_DOC_MIME:
        doc_id = copy_doc_to_folder(source_file_id, subfolder_id, doc_name, drive_service=drive_service)
        write_to_google_doc(doc_id, context.tailored_resume, docs_service=docs_service)
    else:
        doc_id = create_google_doc_in_folder(
            drive_service, subfolder_id, doc_name, context.tailored_resume, docs_service=docs_service
        )

    return {
        "tailored_doc_id": doc_id,
        "doc_url": f"https://docs.google.com/document/d/{doc_id}",
    }


def generate_diff(context: TailorContext) -> Dict[str, Any]:
    """Write a markdown diff between source and tailored resume."""
    from ..utils.diff import generate_diff_markdown

    path = generate_diff_markdown(
        context.resume_text,
        context.tailored_resume,
        context.request.job_title,
        context.request.company,
    )
    return {"diff_path": str(path) if path else None}


def track_application(context: TailorContext) -> Dict[str, Any]:
    """Record the application against the user's tracker."""
    from ..tracking.application_tracker import add_or_update_application

    if not context.request.track_application:
        return {}

    job_title = (context.request.job_title or "").strip() or (
        getattr(context.analyzed_jd, "job_title", None) or "Job Application"
    )
    company = (context.request.company or "").strip() or (
        getattr(context.analyzed_jd, "company", None) or "Unknown"
    )
    return {
        "application_id": add_or_update_application(
            job_title=job_title,
            company=company,
            user_id=context.request.local_user_id,
            job_url=context.request.job_url or "",
            fit_score=context.evaluation.score if context.evaluation else None,
            strategy_brief_id=context.strategy_brief_id,
            resume_doc_id=context.tailored_doc_id,
        )
    }


def _normalize_sections(sections: Optional[List[str]]) -> Optional[List[str]]:
    if sections is None:
        return None
    normalized = [str(s).strip().lower() for s in sections if str(s).strip()]
    return normalized or None
