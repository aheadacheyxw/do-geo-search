"""Step 4: normalize admitted Web-UI observations into auditable facts.

This module deliberately prefers ``unavailable`` to an inferred metric.  It
never promotes a DOM URL, a source-card domain, text order, or a brand mention
into a verified citation or a recommendation rank.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable, Mapping

from .evidence_audit import derive_visible_source_cards
from .io import write_json, write_jsonl
from .validation import validate_raw_evidence_package


class _VisibleSourceTextParser(HTMLParser):
    """Extract only text nodes inside source-card title/content elements.

    It intentionally does not inspect ``href``, ``data-url`` or similar DOM
    attributes.  Those attributes were not necessarily visible in the UI.
    """

    _markers = ("search-view-card__title", "ref_card-title", "source-item-")

    def __init__(self) -> None:
        super().__init__()
        self._depth = 0
        self._active: list[tuple[int, list[str]]] = []
        self.items: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._depth += 1
        class_name = next((value or "" for key, value in attrs if key == "class"), "")
        if any(marker in class_name for marker in self._markers):
            self._active.append((self._depth, []))

    def handle_data(self, data: str) -> None:
        for _, chunks in self._active:
            chunks.append(data)

    def handle_endtag(self, tag: str) -> None:
        while self._active and self._active[-1][0] == self._depth:
            _, chunks = self._active.pop()
            value = " ".join("".join(chunks).split())
            if value:
                self.items.append(value)
        self._depth -= 1


def derive_partial_source_cards(expanded_dom: str, observation_id: str) -> list[dict[str, Any]]:
    """Create *derived partial* cards from captured visible-source DOM text.

    The derived cards make a missing Step 3 card index auditable, without
    treating DOM attributes as visible URLs or as citations.
    """

    parser = _VisibleSourceTextParser()
    parser.feed(unescape(expanded_dom))
    seen: set[str] = set()
    cards: list[dict[str, Any]] = []
    for text in parser.items:
        if text in seen:
            continue
        seen.add(text)
        cards.append({
            "source_card_id": f"{observation_id}:derived-source:{len(cards) + 1}",
            "visible_anchor_text": text,
            "visible_url": None,
            "visible_domain_text": None,
            "source_card_status": "partial_derived_from_visible_source_dom",
            "derivation_basis": "captured_expanded_source_dom_visible_text_only",
        })
    return cards


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _read_evidence_supplements(run_dir: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Read valid append-only citation recovery packages without creating samples."""

    cards: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    supplement_root = run_dir / "raw" / "evidence_supplements"
    for metadata_path in sorted(supplement_root.glob("*/*/supplement.json")):
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        observation_id = metadata.get("observation_id")
        supplement_id = metadata.get("evidence_supplement_id")
        package = metadata_path.parent
        expanded_dom = package / "expanded_answer_source_dom.html"
        expanded_png = package / "expanded.png"
        if (
            not observation_id
            or not supplement_id
            or metadata.get("does_not_create_new_observation") is not True
            or not expanded_dom.is_file()
            or not expanded_png.is_file()
            or expanded_png.stat().st_size == 0
            or "<!--VISIBLE_SOURCE_CARDS-->" not in expanded_dom.read_text(encoding="utf-8")
        ):
            continue
        for filename, output in (("source-cards.json", cards), ("citation-candidates.json", candidates)):
            path = package / filename
            if not path.is_file():
                continue
            for item in json.loads(path.read_text(encoding="utf-8")):
                record = dict(item)
                record["observation_id"] = observation_id
                record["evidence_supplement_id"] = supplement_id
                output.append(record)
    return cards, candidates


def _brand_spans(answer: str, aliases: Iterable[str]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for alias in sorted({alias for alias in aliases if alias}, key=len, reverse=True):
        for match in re.finditer(re.escape(alias), answer, flags=re.IGNORECASE):
            spans.append({"start": match.start(), "end": match.end(), "text": match.group(0)})
    return sorted(spans, key=lambda item: (item["start"], item["end"]))


def _formal_rank(answer: str, aliases: Iterable[str]) -> tuple[int | str, dict[str, Any] | None]:
    """Return a rank only for an explicit overall recommendation list/table."""

    if not re.search(r"(?:推荐(?:品牌|服务商|供应商|方案)?|(?:top|TOP)\s*\d+|排名)", answer):
        return "unranked", None
    for line_number, line in enumerate(answer.splitlines(), 1):
        listed = re.match(r"\s*(\d{1,2})[.、．]\s*(.+)", line)
        if not listed:
            continue
        if any(alias.casefold() in listed.group(2).casefold() for alias in aliases):
            return int(listed.group(1)), {
                "line_number": line_number,
                "line": line,
                "list_type": "explicit_numbered_recommendation_list",
            }
    return "unranked", None


def _citation_status(
    source_cards: list[Mapping[str, Any]], candidates: list[Mapping[str, Any]],
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    verified = [
        dict(card) for card in source_cards
        if str(card.get("visible_url", "")).startswith(("http://", "https://"))
        and bool(card.get("visible_anchor_text"))
    ]
    rejected = [dict(candidate) for candidate in candidates if candidate.get("candidate_status") == "rejected"]
    if verified:
        return "verified", verified, rejected
    if source_cards:
        return "partial", [], rejected
    if rejected:
        return "rejected", [], rejected
    return "unavailable", [], []


RISK_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("price", re.compile(r"价格|报价|¥|￥|\d+\s*元\s*/\s*月")),
    ("performance", re.compile(r"延迟|抖动|丢包|带宽|\d+\s*(?:Mbps|Gbps|M|G)\b", re.IGNORECASE)),
    ("compliance", re.compile(r"合规|备案|许可证|监管|法规|政策")),
    ("absolute_claim", re.compile(r"100%|绝对|保证|永不|零风险|完全")),
)


def _risk_flags(answer: str, observation: Mapping[str, Any]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    for risk_type, pattern in RISK_PATTERNS:
        match = pattern.search(answer)
        if match:
            flags.append({
                "observation_id": observation["observation_id"],
                "evidence_id": observation["evidence_id"],
                "question_id": observation["question_id"],
                "risk_type": risk_type,
                "claim_verification_status": "unverified",
                "span": {"start": match.start(), "end": match.end(), "text": match.group(0)},
            })
    return flags


def _normalize_one(
    observation: Mapping[str, Any],
    answer: str,
    source_cards: list[Mapping[str, Any]],
    candidates: list[Mapping[str, Any]],
    aliases: list[str],
    question: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    answer_type = "substantive" if answer.strip() else "unknown"
    observable = answer_type == "substantive"
    mention_spans = _brand_spans(answer, aliases) if observable else []
    recommendation_rank, rank_evidence = _formal_rank(answer, aliases) if observable else ("unranked", None)
    citation_status, verified_citations, rejected_candidates = _citation_status(source_cards, candidates)
    risks = _risk_flags(answer, observation) if observable else []

    normalized = {
        "observation_id": observation["observation_id"],
        "evidence_id": observation["evidence_id"],
        "run_id": observation["run_id"],
        "question_id": observation["question_id"],
        "question_revision_id": observation["question_revision_id"],
        "platform": observation["measurement_context"]["platform"],
        "measurement_context": observation["measurement_context"],
        "prompt_integrity_state": observation["prompt_integrity_state"],
        "comparable": observation.get("comparable", False),
        "non_comparable_reasons": observation.get("non_comparable_reasons", []),
        "answer_type": answer_type,
        "brand_mention": bool(mention_spans),
        "recommendation_rank": recommendation_rank,
        "section_rank": "unavailable",
        "citation_status": citation_status,
        "brand_sentiment": "unavailable",
        "business_importance": question.get("business_importance"),
        "primary_intent": question.get("primary_intent"),
        "semantic_cluster_id": question.get("semantic_cluster_id"),
        "coverage_eligible": True,
        "mention_eligible": observable,
        "rank_eligible": observable and recommendation_rank != "unranked",
        "citation_eligible": citation_status == "verified",
        "trend_eligible": bool(observation.get("comparable", False)),
        "competitor_comparison_eligible": citation_status == "verified",
        "status": "complete" if citation_status == "verified" else "limited",
    }
    decision = {
        "observation_id": observation["observation_id"],
        "evidence_id": observation["evidence_id"],
        "brand_mention": {"value": bool(mention_spans), "spans": mention_spans},
        "recommendation_rank": {"value": recommendation_rank, "evidence": rank_evidence},
        "section_rank": {"value": "unavailable", "reason": "no_verified_section_list"},
        "citation": {
            "status": citation_status,
            "verified_citations": verified_citations,
            "rejected_candidate_ids": [candidate.get("candidate_id") for candidate in rejected_candidates],
        },
        "brand_sentiment": {"value": "unavailable", "reason": "no human-reviewed sentiment classifier in_step4"},
    }
    return normalized, decision, risks


def build_step4_package(
    run_dir: Path,
    project_profile: Mapping[str, Any],
    questions: Mapping[str, Mapping[str, Any]],
    output_dir: Path | None = None,
) -> dict[str, Any]:
    """Build deterministic Step 4 facts from Step 3 admitted observations only."""

    output_dir = output_dir or run_dir / "step4"
    output_dir.mkdir(parents=True, exist_ok=True)
    target = project_profile.get("target_scope", {})
    aliases = [target.get("canonical_brand", ""), *target.get("aliases", [])]
    validations = _read_jsonl(run_dir / "validation" / "observation_validation.jsonl")
    admitted = {item["observation_id"] for item in validations if item.get("step4_admission") == "admitted"}
    context_overlays = {
        str(item.get("observation_id")): item
        for item in _read_jsonl(run_dir / "control" / "measurement_context_overlays.jsonl")
        if item.get("observation_id") and isinstance(item.get("corrected_fields"), dict)
    }
    applied_context_overlays: list[dict[str, Any]] = []
    source_by_observation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    candidate_by_observation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for source_card in _read_jsonl(run_dir / "raw" / "source_cards.jsonl"):
        source_by_observation[source_card["observation_id"]].append(source_card)
    for candidate in _read_jsonl(run_dir / "raw" / "citation_candidates.jsonl"):
        candidate_by_observation[candidate["observation_id"]].append(candidate)
    supplement_cards, supplement_candidates = _read_evidence_supplements(run_dir)
    for source_card in supplement_cards:
        source_by_observation[source_card["observation_id"]].append(source_card)
    for candidate in supplement_candidates:
        candidate_by_observation[candidate["observation_id"]].append(candidate)

    normalized: list[dict[str, Any]] = []
    signal_decisions: list[dict[str, Any]] = []
    risk_flags: list[dict[str, Any]] = []
    exclusions: list[dict[str, Any]] = []
    for observation_dir in sorted((run_dir / "raw" / "observations").glob("*")):
        observation_path = observation_dir / "observation.json"
        if not observation_path.exists():
            continue
        observation = json.loads(observation_path.read_text(encoding="utf-8"))
        observation_id = observation["observation_id"]
        if observation_id not in admitted:
            exclusions.append({"observation_id": observation_id, "status": "blocked", "exclusion_reason": "step3_not_admitted"})
            continue
        evidence_result = validate_raw_evidence_package({
            "answer_dom_artifact": observation_dir / "initial_answer_source_dom.html",
            "initial_screenshot": observation_dir / "initial.png",
            "expanded_screenshot": observation_dir / "expanded.png",
            "source_cards_expanded_dom": observation_dir / "expanded_answer_source_dom.html",
        })
        if not evidence_result.ok:
            exclusions.append({
                "observation_id": observation_id,
                "status": "blocked",
                "exclusion_reason": "raw_evidence_revalidation_failed",
                "evidence_codes": list(evidence_result.codes),
            })
            continue
        overlay = context_overlays.get(observation_id)
        if overlay:
            observation = dict(observation)
            observation["measurement_context"] = {
                **dict(observation.get("measurement_context") or {}),
                **dict(overlay["corrected_fields"]),
            }
            observation["measurement_context_overlay_id"] = overlay.get("overlay_id")
            applied_context_overlays.append({
                "overlay_id": overlay.get("overlay_id"),
                "observation_id": observation_id,
                "corrected_fields": overlay["corrected_fields"],
                "basis": overlay.get("basis"),
                "raw_observation_unchanged": True,
            })
        answer = (observation_dir / "answer.txt").read_text(encoding="utf-8")
        expanded_dom = observation_dir / "expanded_answer_source_dom.html"
        if expanded_dom.exists():
            dom_text = expanded_dom.read_text(encoding="utf-8")
            derived_visible = derive_visible_source_cards(dom_text, observation_id)
            existing_keys = {
                (str(card.get("visible_url", "")), str(card.get("visible_anchor_text", "")))
                for card in source_by_observation[observation_id]
            }
            source_by_observation[observation_id].extend(
                card for card in derived_visible
                if (str(card.get("visible_url", "")), str(card.get("visible_anchor_text", ""))) not in existing_keys
            )
            if not source_by_observation[observation_id]:
                source_by_observation[observation_id].extend(
                    derive_partial_source_cards(dom_text, observation_id)
                )
        record, decision, risks = _normalize_one(
            observation, answer, source_by_observation[observation_id],
            candidate_by_observation[observation_id], aliases, questions.get(observation["question_id"], {}),
        )
        normalized.append(record)
        signal_decisions.append(decision)
        risk_flags.extend(risks)
        exclusions.append({
            "observation_id": observation_id,
            "status": record["status"],
            "exclusion_reason": None,
            "citation_limitation": record["citation_status"],
        })
    for validation in validations:
        if validation.get("step4_admission") != "admitted":
            exclusions.append({"observation_id": validation["observation_id"], "status": "blocked", "exclusion_reason": "step3_not_admitted"})

    queue: list[dict[str, Any]] = []
    for record in normalized:
        if record["business_importance"] == "high" and record["mention_eligible"] and not record["brand_mention"]:
            queue.append({
                "observation_id": record["observation_id"], "question_id": record["question_id"],
                "platform": record["platform"], "priority": "P0",
                "queue_reason": "high_value_target_not_mentioned", "evidence_id": record["evidence_id"],
            })
    for flag in risk_flags:
        queue.append({
            "observation_id": flag["observation_id"], "question_id": flag["question_id"],
            "priority": "P2", "queue_reason": "factual_risk_candidate", "evidence_id": flag["evidence_id"],
            "risk_type": flag["risk_type"],
        })
    queue.sort(key=lambda item: (item["priority"], item["observation_id"], item["queue_reason"]))

    summary = {
        "run_id": json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))["run_id"] if (run_dir / "manifest.json").exists() else None,
        "included_observation_count": len(normalized),
        "excluded_observation_count": sum(1 for item in exclusions if item["status"] == "blocked"),
        "signal_distribution": {
            "brand_mention": dict(Counter(str(record["brand_mention"]).lower() for record in normalized)),
            "recommendation_rank": dict(Counter(str(record["recommendation_rank"]) for record in normalized)),
            "citation_status": dict(Counter(record["citation_status"] for record in normalized)),
            "brand_sentiment": dict(Counter(record["brand_sentiment"] for record in normalized)),
        },
        "limitations": [
            "仅可见来源卡带 HTTP(S) URL 与锚文本/答案片段可作为 verified citation。",
            "来源卡仅展示标题或域名时为 partial，不作为未引用或竞品引用的零值。",
            "未观察到明确整体推荐列表时 recommendation_rank=unranked；不以提及顺序推断排名。",
            "情感倾向未通过人工/经验证分类器复核时保持 unavailable。",
            "事实风险为待核验候选，非“回答错误”结论。",
        ],
    }
    evidence_audit = [
        {"observation_id": record["observation_id"], "source_cards": source_by_observation[record["observation_id"]], "candidates": candidate_by_observation[record["observation_id"]]}
        for record in normalized
    ]
    write_jsonl(output_dir / "normalized_observations.jsonl", normalized)
    write_jsonl(output_dir / "signal_decisions.jsonl", signal_decisions)
    write_jsonl(output_dir / "evidence_audit.jsonl", evidence_audit)
    write_jsonl(output_dir / "quality_and_exclusions.jsonl", exclusions)
    write_jsonl(output_dir / "factual_risk_flags.jsonl", risk_flags)
    write_jsonl(output_dir / "investigation_queue.jsonl", queue)
    write_jsonl(output_dir / "measurement_context_overlays_applied.jsonl", applied_context_overlays)
    write_json(output_dir / "initial_pattern_summary.json", summary)
    report = "\n".join((
        "# 04 观察清洗与初步诊断",
        "",
        f"- 纳入观察：{len(normalized)} 条；排除观察：{summary['excluded_observation_count']} 条。",
        f"- 正文提及：{summary['signal_distribution']['brand_mention']}。",
        f"- 正式推荐位次：{summary['signal_distribution']['recommendation_rank']}。",
        f"- 引用状态：{summary['signal_distribution']['citation_status']}。",
        "- 本报告仅描述有效样本、信号和限制；不作竞品归因或内容行动结论。",
    )) + "\n"
    (output_dir / "04_观察清洗与初步诊断.md").write_text(report, encoding="utf-8")
    return {
        "normalized_observations": normalized,
        "signal_decisions": signal_decisions,
        "quality_and_exclusions": exclusions,
        "factual_risk_flags": risk_flags,
        "investigation_queue": queue,
        "summary": summary,
    }
