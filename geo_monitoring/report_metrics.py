"""Report-only metrics derived from the stable Step 2/4 contracts."""

from __future__ import annotations

from typing import Any, Mapping, Sequence


EXPLICIT_RECOMMENDATION_GROUP = "scenario_provider_recommendation"


def build_explicit_recommendation_audit(
    normalized_observations: Sequence[Mapping[str, Any]],
    questions: Mapping[str, Mapping[str, Any]],
    decisions: Mapping[str, Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Audit target inclusion in unbranded provider-recommendation answers.

    Step 2 freezes this group as unbranded, scenario-based prompts asking which
    providers to choose or evaluate.  A target-brand span in the assistant
    answer therefore means that the target entered the answer's provider
    consideration set.  This remains separate from Top-1, rank and sentiment.
    """

    rows: list[dict[str, Any]] = []
    for observation in normalized_observations:
        question_id = str(observation.get("question_id") or "")
        question = questions.get(question_id, {})
        if question.get("question_group") != EXPLICIT_RECOMMENDATION_GROUP:
            continue
        eligible = (
            observation.get("answer_type") == "substantive"
            and observation.get("mention_eligible") is True
        )
        decision = decisions.get(str(observation.get("observation_id") or ""), {})
        spans = list((decision.get("brand_mention") or {}).get("spans") or [])
        recommended = bool(eligible and observation.get("brand_mention") is True and spans)
        rows.append({
            "observation_id": observation.get("observation_id"),
            "evidence_id": observation.get("evidence_id"),
            "question_id": question_id,
            "question_text": question.get("exact_question_text"),
            "platform": observation.get("platform"),
            "question_group": EXPLICIT_RECOMMENDATION_GROUP,
            "recommendation_eligible": eligible,
            "target_explicitly_recommended": recommended if eligible else None,
            "answer_spans": spans if recommended else [],
            "evidence_basis": (
                "target_brand_span_in_unbranded_provider_recommendation_answer"
                if recommended
                else "target_brand_not_observed_in_assistant_answer"
                if eligible
                else "answer_not_eligible"
            ),
            "metric_limitation": "candidate_inclusion_not_top1_rank_or_sentiment",
        })
    return rows


def summarize_explicit_recommendation(
    audit_rows: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    eligible = [row for row in audit_rows if row.get("recommendation_eligible") is True]
    numerator = sum(row.get("target_explicitly_recommended") is True for row in eligible)
    denominator = len(eligible)
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": numerator / denominator if denominator else None,
        "definition": (
            "目标品牌被列入无品牌提示的场景服务商推荐题回答数 "
            "/ 该组推荐可观察有效回答数"
        ),
        "scope": EXPLICIT_RECOMMENDATION_GROUP,
        "limitation": "表示进入服务商/候选集合，不等同于 Top1、正式排名或正面情感。",
    }
