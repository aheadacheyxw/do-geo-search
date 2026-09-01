"""Cross-step validation that does not interpret GEO signals."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping

from .contracts import ValidationResult, require_fields


STEP3_V3_OBSERVATION_FIELDS = (
    "profile_version",
    "project_profile_sha256",
    "question_catalog_version",
    "question_catalog_sha256",
    "question_set_version",
    "question_set_sha256",
    "evidence_id",
    "sent_at",
    "completed_at",
    "session_reference",
    "retry_lineage",
    "adapter_version",
    "parser_version",
    "rule_version",
)

STEP3_V3_CONTEXT_FIELDS = (
    "platform",
    "surface_class",
    "platform_product_surface",
    "market_region",
    "language_locale",
    "answer_language",
    "account_session_class",
    "web_search_state",
    "mode_reasoning_state",
    "collection_mode",
)


def validate_run_lineage(record: Mapping[str, Any]) -> ValidationResult:
    """Require the minimum traceability keys for an observation-derived record."""

    return require_fields(record, ("run_id", "evidence_id", "raw_artifact_hash"))


def reject_legacy_input(record: Mapping[str, Any]) -> ValidationResult:
    """Prevent historical material from becoming a v2 fact or metric input."""

    if record.get("legacy_reference_only") is True:
        return ValidationResult(ok=False, codes=("legacy_input",))
    return ValidationResult(ok=True)


def validate_raw_evidence_package(record: Mapping[str, Any]) -> ValidationResult:
    """Gate Step 4 on the immutable evidence required by the v2 contract.

    A body-text export or chat URL alone is recoverable operational context, not
    evidence that can support a mention, rank, or citation metric.
    """

    result = require_fields(
        record,
        (
            "answer_dom_artifact", "initial_screenshot", "expanded_screenshot",
            "source_cards_expanded_dom",
        ),
    )
    initial_dom = record.get("answer_dom_artifact")
    if isinstance(initial_dom, Path) and initial_dom.is_file():
        # The initial capture represents the unexpanded UI state.  An expanded
        # source-card marker here proves that the collector mixed states.
        if "VISIBLE_SOURCE_CARDS" in initial_dom.read_text(encoding="utf-8", errors="replace"):
            return ValidationResult(
                ok=False,
                codes=tuple(sorted({*result.codes, "initial_capture_contains_expanded_sources"})),
            )
    expanded_dom = record.get("source_cards_expanded_dom")
    if isinstance(expanded_dom, Path) and expanded_dom.is_file():
        expanded_text = expanded_dom.read_text(encoding="utf-8", errors="replace").lstrip()
        if not expanded_text.startswith("<"):
            return ValidationResult(
                ok=False,
                codes=tuple(sorted({*result.codes, "expanded_source_dom_not_html"})),
            )
    return result


def validate_observation_metadata(record: Mapping[str, Any]) -> ValidationResult:
    """Require the lineage and prompt-integrity fields needed for Step 4."""

    result = require_fields(
        record,
        (
            "observation_id",
            "run_id",
            "question_id",
            "question_revision_id",
            "frozen_question_text",
            "actual_sent_text",
            "prompt_integrity_state",
            "transform_type",
            "transform_observability",
            "measurement_context",
        ),
    )
    if not str(record.get("step3_contract_version") or "").startswith("step3-v"):
        return result

    issues = set(result.codes)
    for field in STEP3_V3_OBSERVATION_FIELDS:
        if not record.get(field):
            issues.add(field)
    context = record.get("measurement_context") or {}
    for field in STEP3_V3_CONTEXT_FIELDS:
        if not context.get(field):
            issues.add(f"measurement_context.{field}")
    return ValidationResult(ok=not issues, codes=tuple(sorted(issues)))
