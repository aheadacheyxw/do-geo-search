"""Step 3–4 evidence normalization without platform automation."""

from __future__ import annotations

from typing import Any, Mapping


REFUSAL_TYPES = {"refusal", "partial_refusal"}


def normalize_observation(raw: Mapping[str, Any]) -> dict[str, Any]:
    """Normalize only explicit answer and visible-source evidence."""

    answer_type = str(raw.get("answer_type", "unknown"))
    answer = str(raw.get("answer_text", ""))
    target_brand = str(raw.get("target_brand", ""))
    rejected = []
    verified = []
    for candidate in raw.get("citation_candidates", []):
        if candidate.get("kind") in {"runtime", "resource", "tracking", "login", "captcha"}:
            rejected.append({**candidate, "reason": "runtime_or_resource_url"})
        elif candidate.get("url") and candidate.get("anchor_or_span"):
            verified.append(candidate)
    observable = answer_type not in REFUSAL_TYPES
    citation_status = "verified" if verified else ("rejected" if rejected else "unavailable")
    rank = raw.get("recommendation_rank", "unranked")
    return {
        "answer_type": answer_type,
        "brand_mention": bool(target_brand and target_brand.casefold() in answer.casefold()) if observable else False,
        "recommendation_rank": rank if observable else "unranked",
        "citation_status": citation_status,
        "verified_citations": verified,
        "rejected_candidates": rejected,
        "mention_eligible": observable,
        "rank_eligible": observable and rank != "unranked",
        "citation_eligible": citation_status == "verified",
    }
