"""Step 1–2 governance gates for profiles and frozen questions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence, Tuple

from .contracts import ValidationResult, require_fields


PROFILE_FIELDS = (
    "official_domains",
    "in_scope_products",
    "markets_languages",
    "target_platforms",
    "success_signals",
    "human_confirmation",
)
VALID_INTENTS = {"understand", "discover", "solve", "evaluate", "transact"}
DEFAULT_GROWTH_PORTFOLIO = {
    "scenario_provider_recommendation": 20,
    "registered_competitor_comparison": 10,
    "branded_product_procurement": 5,
    "high_value_pain_point": 10,
    "risk_procurement_check": 5,
}
FACT_STATUSES = {"verified", "supplied_unverified", "unknown", "disputed"}
MEASUREMENT_CONTEXT_FIELDS = (
    "market_region", "language_locale", "platform_product_surface", "account_session_class",
    "web_search_mode", "mode_reasoning_state", "collection_mode", "session_rule",
)


@dataclass(frozen=True)
class FreezeResult:
    status: str
    question_ids: Tuple[str, ...] = ()


def validate_project_profile(profile: Mapping[str, Any]) -> ValidationResult:
    """Require a legacy-compatible or full v3 Step 1 project profile."""

    if "target_scope" in profile:
        return _validate_v3_project_profile(profile)

    return require_fields(profile, PROFILE_FIELDS)


def _validate_v3_project_profile(profile: Mapping[str, Any]) -> ValidationResult:
    """Validate the approved Step 1 contract without making business judgments."""

    issues: set[str] = set()
    if not profile.get("profile_version"):
        issues.add("profile_version")
    if not profile.get("project_id"):
        issues.add("project_id")
    if profile.get("status") != "approved":
        issues.add("profile_not_approved")

    scope = profile.get("target_scope") or {}
    for field in ("canonical_brand", "official_domains"):
        if not scope.get(field):
            issues.add(f"target_scope.{field}")
    if not profile.get("in_scope_products_services"):
        issues.add("in_scope_products_services")
    if not profile.get("audiences"):
        issues.add("audiences")
    if not profile.get("markets_languages"):
        issues.add("markets_languages")
    if not profile.get("target_ai_platforms"):
        issues.add("target_ai_platforms")
    if not profile.get("business_goals"):
        issues.add("business_goals")
    if not profile.get("success_signals"):
        issues.add("success_signals")
    if "declared_competitors" not in profile:
        issues.add("declared_competitors")
    if "exclusions" not in profile:
        issues.add("exclusions")

    for fact in profile.get("fact_sources") or []:
        if fact.get("status") not in FACT_STATUSES or not fact.get("claim"):
            issues.add("fact_sources")
    if not profile.get("fact_sources"):
        issues.add("fact_sources")

    context = profile.get("measurement_context") or {}
    for field in MEASUREMENT_CONTEXT_FIELDS:
        if not context.get(field):
            issues.add(f"measurement_context.{field}")

    confirmation = profile.get("human_confirmation") or {}
    if confirmation.get("decision") != "approved":
        issues.add("human_confirmation.decision")
    if confirmation.get("profile_version") != profile.get("profile_version"):
        issues.add("human_confirmation.profile_version")
    if not confirmation.get("confirmed_by") or not confirmation.get("confirmed_at"):
        issues.add("human_confirmation.receipt")

    return ValidationResult(ok=not issues, codes=tuple(sorted(issues)))


def validate_question_catalog(questions: Sequence[Mapping[str, Any]]) -> ValidationResult:
    """Reject changed text under a stable revision and invalid primary intent."""

    by_revision: dict[str, str] = {}
    issues: set[str] = set()
    for question in questions:
        revision = question.get("question_revision_id")
        text = question.get("exact_question_text")
        intent = question.get("primary_intent")
        if not revision or not text:
            issues.add("question_identity_missing")
            continue
        previous = by_revision.setdefault(str(revision), str(text))
        if previous != text:
            issues.add("question_revision_required")
        if intent not in VALID_INTENTS:
            issues.add("primary_intent_invalid")
    return ValidationResult(ok=not issues, codes=tuple(sorted(issues)))


def validate_growth_question_catalog(
    questions: Sequence[Mapping[str, Any]],
    expected_group_counts: Mapping[str, int] | None = None,
) -> ValidationResult:
    """Validate a growth portfolio without tying it to one brand or industry."""

    issues = set(validate_question_catalog(questions).codes)
    expected = dict(expected_group_counts or DEFAULT_GROWTH_PORTFOLIO)
    if len(questions) != sum(expected.values()):
        issues.add("growth_catalog_question_count")
    ids = [str(item.get("question_id") or "") for item in questions]
    if len(set(ids)) != len(ids) or not all(ids):
        issues.add("question_id_duplicate_or_missing")
    groups: dict[str, int] = {}
    for item in questions:
        group = str(item.get("question_group") or "")
        groups[group] = groups.get(group, 0) + 1
        if any(str(entity).startswith("candidate:") for entity in item.get("comparison_entities", [])):
            issues.add("candidate_competitor_not_allowed")
    if groups != expected:
        issues.add("growth_question_group_counts")
    if sum(item.get("set_membership") in {"core", "explore"} for item in questions) != len(questions):
        issues.add("question_set_membership_missing")
    return ValidationResult(ok=not issues, codes=tuple(sorted(issues)))


def freeze_question_set(profile: Mapping[str, Any], questions: Sequence[Mapping[str, Any]]) -> FreezeResult:
    """Freeze only an approved profile and a valid one-intent question catalog."""

    confirmation = profile.get("human_confirmation") or {}
    if confirmation.get("decision") != "approved":
        return FreezeResult(status="blocked_profile_unapproved")
    if not validate_project_profile(profile).ok or not validate_question_catalog(questions).ok:
        return FreezeResult(status="blocked_schema")
    ordered_ids = tuple(sorted(str(question["question_id"]) for question in questions))
    return FreezeResult(status="ready_business_geo", question_ids=ordered_ids)
