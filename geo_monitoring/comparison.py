"""Step 7 adjacent, evidence-linked descriptive comparison."""

from __future__ import annotations

from typing import Any, Mapping
from urllib.parse import urlparse


CONTEXT_KEYS = (
    "project_id",
    "question_set_version",
    "competitor_catalog_version",
    "collection_mode",
    "signal_definition_version",
)


def _key(signal: Mapping[str, Any]) -> tuple[str, str, str, str]:
    return tuple(str(signal.get(name, "")) for name in ("question_id", "platform", "competitor_id", "signal_type"))


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _is_official_url(url: object, official_domains: set[str]) -> bool:
    try:
        host = (urlparse(str(url)).hostname or "").casefold().rstrip(".")
    except ValueError:
        return False
    return any(host == domain or host.endswith(f".{domain}") for domain in official_domains)


def build_first_observed_report(
    current_snapshot: Mapping[str, Any],
    normalized_observations: list[Mapping[str, Any]],
    signal_decisions: list[Mapping[str, Any]],
    *,
    official_domains: list[str],
    planned_observation_count: int,
) -> dict[str, Any]:
    """Build the first-period baseline with independent, disclosed denominators."""

    decision_by_observation = {
        str(row.get("observation_id")): row for row in signal_decisions
    }
    mention_rows = [row for row in normalized_observations if row.get("mention_eligible") is True]
    mentioned_rows = [row for row in mention_rows if row.get("brand_mention") is True]
    citation_rows = [
        row for row in normalized_observations
        if row.get("citation_eligible") is True and row.get("citation_status") == "verified"
    ]
    official_domain_set = set()
    for raw_domain in official_domains:
        domain = str(raw_domain).casefold().rstrip(".")
        official_domain_set.add(domain[4:] if domain.startswith("www.") else domain)
    official_presence_rows: list[Mapping[str, Any]] = []
    verified_citation_count = 0
    citation_evidence_ids: list[str] = []
    for row in citation_rows:
        decision = decision_by_observation.get(str(row.get("observation_id")), {})
        citations = decision.get("citation", {}).get("verified_citations", [])
        verified_citation_count += len(citations)
        citation_evidence_ids.extend(str(row.get("evidence_id")) for _ in citations if row.get("evidence_id"))
        if any(_is_official_url(item.get("visible_url"), official_domain_set) for item in citations):
            official_presence_rows.append(row)
    rank_rows = [row for row in normalized_observations if row.get("rank_eligible") is True]
    rank_distribution: dict[str, int] = {}
    for row in rank_rows:
        label = str(row.get("recommendation_rank", "unranked"))
        rank_distribution[label] = rank_distribution.get(label, 0) + 1
    platforms = sorted({str(row.get("platform")) for row in normalized_observations})
    questions = sorted({str(row.get("question_id")) for row in normalized_observations})
    snapshot_id = str(current_snapshot.get("snapshot_id", ""))
    return {
        "comparison_id": f"first-observed-{snapshot_id}",
        "status": "first_observed",
        "project_id": current_snapshot.get("project_id"),
        "current_step5_snapshot_id": snapshot_id,
        "baseline_step5_snapshot_id": None,
        "question_set_version": current_snapshot.get("question_set_version"),
        "competitor_catalog_version": current_snapshot.get("competitor_catalog_version"),
        "signal_definition_version": current_snapshot.get("signal_definition_version"),
        "collection_mode": current_snapshot.get("collection_mode"),
        "comparison_scope": {
            "valid_observation_count": len(normalized_observations),
            "planned_observation_count": planned_observation_count,
            "platforms_with_valid_observations": platforms,
            "questions_with_valid_observations": questions,
        },
        "signal_families": {
            "target_brand_mention": {
                "numerator": len(mentioned_rows),
                "denominator": len(mention_rows),
                "rate": _rate(len(mentioned_rows), len(mention_rows)),
                "definition": "目标品牌正文提及率；不包含排名或引用。",
                "evidence_ids": [str(row.get("evidence_id")) for row in mentioned_rows if row.get("evidence_id")],
            },
            "official_domain_citation_presence": {
                "numerator": len(official_presence_rows),
                "denominator": len(citation_rows),
                "rate": _rate(len(official_presence_rows), len(citation_rows)),
                "definition": "目标官方域在引用可观察回答中的出现率；partial/unavailable/rejected 不进入分母。",
                "evidence_ids": [str(row.get("evidence_id")) for row in official_presence_rows if row.get("evidence_id")],
            },
            "verified_visible_citation_count_per_answer": {
                "citation_count": verified_citation_count,
                "denominator": len(citation_rows),
                "average": _rate(verified_citation_count, len(citation_rows)),
                "definition": "已验证可见引用总数 / 引用可观察回答数；与目标官方域引用率分开。",
                "evidence_ids": sorted(set(citation_evidence_ids)),
            },
            "formal_recommendation_rank": {
                "denominator": len(rank_rows),
                "distribution": rank_distribution,
                "status": "descriptive_only" if rank_rows else "unassessable",
                "definition": "仅明确整体推荐列表/表格进入正式位次分母。",
                "evidence_ids": [str(row.get("evidence_id")) for row in rank_rows if row.get("evidence_id")],
            },
        },
        "comparison_details": [],
        "limitations": [
            "没有同一冻结问题集、平台与采集上下文的上一期可比 Step 5 快照，因此本期只建立首个基线，不能声明改善、恶化或趋势。",
            "partial、unavailable 或 rejected 引用证据不进入引用率和引用数量分母，也不作为零值。",
            "腾讯元宝 Q3 技术重试耗尽，本期保留为不可评估，不进入有效观察分母。",
        ],
    }


def compare_adjacent_snapshots(previous: Mapping[str, Any], current: Mapping[str, Any]) -> dict[str, Any]:
    """Compare matching signal atoms only; absence never implies resolution."""

    if any(previous.get(key) != current.get(key) for key in CONTEXT_KEYS):
        return {"status": "not_comparable", "details": []}
    prior = {_key(signal): signal for signal in previous.get("signals", [])}
    details = []
    for now in current.get("signals", []):
        old = prior.get(_key(now))
        detail = {"atom": _key(now), "baseline_evidence_ids": old.get("evidence_ids", []) if old else [], "current_evidence_ids": now.get("evidence_ids", [])}
        if not old or not old.get("observable") or not now.get("observable"):
            detail["status"] = "unassessable"
        elif old.get("value") and not now.get("value"):
            detail["status"] = "not_observed_in_current_comparable_cohort"
        elif not old.get("value") and now.get("value"):
            detail["status"] = "new_in_comparable_cohort"
        else:
            detail["status"] = "persistent"
        details.append(detail)
    return {"status": "comparable", "comparison_id": f"{previous.get('snapshot_id')}__{current.get('snapshot_id')}", "details": details}
