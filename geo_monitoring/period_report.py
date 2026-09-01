"""Strict, brand-neutral adjacent-period comparison and HTML report."""

from __future__ import annotations

import csv
import html
import json
from collections import Counter
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


CONTEXT_KEYS = (
    "market_region", "language_locale", "platform_product_surface",
    "account_session_class", "web_search_state", "mode_reasoning_state",
    "collection_mode",
)
RUN_KEYS = (
    "project_id", "profile_version", "project_profile_sha256",
    "question_catalog_version", "question_set_version",
)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _domain(value: object) -> str:
    raw = str(value or "").casefold().strip()
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    return raw.removeprefix("www.").rstrip(".")


def official_domains(profile: Mapping[str, Any]) -> set[str]:
    scope = profile.get("target_scope") or {}
    values = scope.get("official_domains") or profile.get("official_domains") or []
    return {
        _domain(item.get("domain") if isinstance(item, Mapping) else item)
        for item in values
        if _domain(item.get("domain") if isinstance(item, Mapping) else item)
    }


def _official(url: object, domains: set[str]) -> bool:
    host = _domain(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def _load_run(run_dir: Path, domains: set[str]) -> dict[str, Any]:
    manifest = _json(run_dir / "manifest.json")
    snapshot = _json(run_dir / "step5" / "step5_snapshot.json")
    normalized = _jsonl(run_dir / "step4" / "normalized_observations.jsonl")
    decisions_path = run_dir / "step4" / "signal_decisions.jsonl"
    decisions = {str(row.get("observation_id")): row for row in _jsonl(decisions_path)} if decisions_path.exists() else {}
    questions = {str(row.get("question_id")): row for row in manifest.get("planned_questions", [])}
    rows = {}
    for row in normalized:
        decision = decisions.get(str(row.get("observation_id")), {})
        citations = ((decision.get("citation") or {}).get("verified_citations") or [])
        enriched = dict(row)
        enriched["official_citation_presence"] = any(_official(item.get("visible_url") or item.get("url"), domains) for item in citations)
        enriched["verified_citation_count"] = len(citations)
        rows[(str(row.get("question_id")), str(row.get("platform")))] = enriched
    return {"run_dir": run_dir, "manifest": manifest, "snapshot": snapshot, "rows": rows, "questions": questions}


def _run_value(run: Mapping[str, Any], key: str) -> object:
    return run["snapshot"].get(key) or run["manifest"].get(key)


def _reasons(previous: Mapping[str, Any], current: Mapping[str, Any], old: Mapping[str, Any], new: Mapping[str, Any]) -> list[str]:
    reasons = [f"{key}_mismatch" for key in RUN_KEYS if _run_value(previous, key) != _run_value(current, key)]
    for key in ("competitor_catalog_version", "signal_definition_version"):
        if _run_value(previous, key) != _run_value(current, key):
            reasons.append(f"{key}_mismatch")
    old_context, new_context = old.get("measurement_context") or {}, new.get("measurement_context") or {}
    reasons.extend(f"measurement_context.{key}_mismatch" for key in CONTEXT_KEYS if old_context.get(key) != new_context.get(key))
    if old.get("comparable") is not True or new.get("comparable") is not True:
        reasons.append("observation_not_comparable")
    if old.get("trend_eligible") is not True or new.get("trend_eligible") is not True:
        reasons.append("trend_ineligible")
    return sorted(set(reasons))


def _state(old: object, new: object) -> str:
    if old == new:
        return "persistent"
    if not old and new:
        return "new_in_comparable_cohort"
    if old and not new:
        return "not_observed_in_current_comparable_cohort"
    return "changed"


def _metric(pairs: list[dict[str, Any]], old_key: str, new_key: str) -> dict[str, Any]:
    denominator = len(pairs)
    baseline = sum(bool(row[old_key]) for row in pairs)
    current = sum(bool(row[new_key]) for row in pairs)
    return {
        "baseline_count": baseline, "current_count": current, "denominator": denominator,
        "baseline_rate": baseline / denominator if denominator else None,
        "current_rate": current / denominator if denominator else None,
        "change_pp": (current - baseline) / denominator * 100 if denominator else None,
    }


def _is_recommendation_question(question: Mapping[str, Any]) -> bool:
    return question.get("question_group") == "scenario_provider_recommendation"


def _full_metrics(run: Mapping[str, Any]) -> dict[str, Any]:
    rows = list(run["rows"].values())
    mention = [row for row in rows if row.get("mention_eligible") is True]
    recommendation = [row for row in mention if _is_recommendation_question(run["questions"].get(str(row.get("question_id")), {}))]
    citations = [row for row in rows if row.get("citation_eligible") is True and row.get("citation_status") == "verified"]
    return {
        "valid_observations": len(rows),
        "brand_mention": {"numerator": sum(row.get("brand_mention") is True for row in mention), "denominator": len(mention)},
        "explicit_recommendation": {"numerator": sum(row.get("brand_mention") is True for row in recommendation), "denominator": len(recommendation)},
        "official_domain_citation_presence": {"numerator": sum(row.get("official_citation_presence") is True for row in citations), "denominator": len(citations)},
        "verified_visible_citations": {"count": sum(int(row.get("verified_citation_count") or 0) for row in citations), "denominator": len(citations)},
        "sentiment_distribution": dict(Counter(str(row.get("brand_sentiment") or "unavailable") for row in rows)),
    }


def compare_runs(previous_dir: Path, current_dir: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    domains = official_domains(profile)
    previous, current = _load_run(previous_dir, domains), _load_run(current_dir, domains)
    old_keys, new_keys = set(previous["rows"]), set(current["rows"])
    exclusions: list[dict[str, Any]] = []
    for question_id, platform in sorted(old_keys - new_keys):
        exclusions.append({"question_id": question_id, "platform": platform, "reasons": ["missing_current_observation"]})
    for question_id, platform in sorted(new_keys - old_keys):
        exclusions.append({"question_id": question_id, "platform": platform, "reasons": ["missing_baseline_observation"]})
    mention_pairs, recommendation_pairs, citation_pairs, rank_pairs, details = [], [], [], [], []
    for key in sorted(old_keys & new_keys):
        old, new = previous["rows"][key], current["rows"][key]
        question_id, platform = key
        reasons = _reasons(previous, current, old, new)
        if reasons:
            exclusions.append({"question_id": question_id, "platform": platform, "reasons": reasons})
            continue
        evidence = {
            "question_id": question_id,
            "question": current["questions"].get(question_id, {}).get("exact_question_text"),
            "platform": platform,
            "baseline_observation_id": old.get("observation_id"),
            "current_observation_id": new.get("observation_id"),
            "baseline_evidence_id": old.get("evidence_id"),
            "current_evidence_id": new.get("evidence_id"),
        }
        if old.get("mention_eligible") is True and new.get("mention_eligible") is True:
            pair = {**evidence, "old": bool(old.get("brand_mention")), "new": bool(new.get("brand_mention"))}
            mention_pairs.append(pair)
            details.append({**evidence, "signal_type": "brand_mention", "baseline_value": pair["old"], "current_value": pair["new"], "status": _state(pair["old"], pair["new"])})
            if _is_recommendation_question(current["questions"].get(question_id, {})):
                recommendation_pairs.append(pair)
                details.append({**evidence, "signal_type": "explicit_recommendation", "baseline_value": pair["old"], "current_value": pair["new"], "status": _state(pair["old"], pair["new"])})
        if all(row.get("citation_eligible") is True and row.get("citation_status") == "verified" for row in (old, new)):
            pair = {**evidence, "old": bool(old.get("official_citation_presence")), "new": bool(new.get("official_citation_presence")), "old_count": int(old.get("verified_citation_count") or 0), "new_count": int(new.get("verified_citation_count") or 0)}
            citation_pairs.append(pair)
            details.append({**evidence, "signal_type": "official_domain_citation_presence", "baseline_value": pair["old"], "current_value": pair["new"], "status": _state(pair["old"], pair["new"])})
        if old.get("rank_eligible") is True and new.get("rank_eligible") is True:
            old_rank, new_rank = old.get("recommendation_rank"), new.get("recommendation_rank")
            rank_pairs.append({**evidence, "baseline_rank": old_rank, "current_rank": new_rank, "rank_delta": new_rank - old_rank if isinstance(old_rank, int) and isinstance(new_rank, int) else None})
    citation_metric = _metric(citation_pairs, "old", "new")
    citation_metric.update({
        "baseline_visible_citation_count": sum(row["old_count"] for row in citation_pairs),
        "current_visible_citation_count": sum(row["new_count"] for row in citation_pairs),
        "baseline_average": sum(row["old_count"] for row in citation_pairs) / len(citation_pairs) if citation_pairs else None,
        "current_average": sum(row["new_count"] for row in citation_pairs) / len(citation_pairs) if citation_pairs else None,
    })
    return {
        "comparison_id": f"{previous['snapshot'].get('snapshot_id')}__{current['snapshot'].get('snapshot_id')}",
        "status": "comparable" if mention_pairs else "unassessable",
        "baseline_run_id": previous["manifest"].get("run_id"),
        "current_run_id": current["manifest"].get("run_id"),
        "current_full_snapshot": _full_metrics(current),
        "strict_comparable_metrics": {
            "brand_mention": _metric(mention_pairs, "old", "new"),
            "explicit_recommendation": _metric(recommendation_pairs, "old", "new"),
            "official_domain_citation_presence": citation_metric,
            "formal_recommendation_rank": {"denominator": len(rank_pairs), "pairs": rank_pairs},
        },
        "details": details,
        "exclusions": exclusions,
        "definitions": {
            "brand_mention": "两期同问题×平台可比回答中，目标品牌在回答正文出现的比例。",
            "explicit_recommendation": "仅无品牌提示的服务商推荐题中，目标品牌进入候选集合的比例；不等同 Top1。",
            "official_domain_citation_presence": "两期均完成可见来源审计的回答中，目标官方域出现的比例。",
            "formal_recommendation_rank": "仅两期均存在明确整体推荐列表/表格时比较位次。",
        },
    }


def _pct(value: object) -> str:
    return "不可评估" if value is None else f"{float(value):.1%}"


def _pp(value: object) -> str:
    return "—" if value is None else f"{float(value):+.1f}pp"


def write_comparison(report: Mapping[str, Any], output_dir: Path, brand: str) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "GEO_周期对比数据.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    fields = ("platform", "question_id", "question", "signal_type", "baseline_value", "current_value", "status", "baseline_observation_id", "current_observation_id", "baseline_evidence_id", "current_evidence_id")
    with (output_dir / "GEO_周期对比明细.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows({key: row.get(key) for key in fields} for row in report.get("details", []))
    metrics = report["strict_comparable_metrics"]
    cards = []
    for label, key in (("品牌提及率", "brand_mention"), ("明确推荐率", "explicit_recommendation"), ("官方域引用出现率", "official_domain_citation_presence")):
        metric = metrics[key]
        cards.append(f'<article><span>{html.escape(label)}</span><strong>{_pct(metric.get("current_rate"))}</strong><b>{_pp(metric.get("change_pp"))}</b><small>上期 {_pct(metric.get("baseline_rate"))} · 可比 {metric.get("denominator", 0)} 组</small></article>')
    changes = [row for row in report.get("details", []) if row.get("status") != "persistent"]
    change_rows = "".join(f'<tr><td>{html.escape(str(row.get("platform")))}</td><td>{html.escape(str(row.get("question") or row.get("question_id")))}</td><td>{html.escape(str(row.get("signal_type")))}</td><td>{html.escape(str(row.get("baseline_value")))}</td><td>{html.escape(str(row.get("current_value")))}</td><td>{html.escape(str(row.get("status")))}</td></tr>' for row in changes) or '<tr><td colspan="6">可比队列中未观察到变化。</td></tr>'
    exclusion_rows = "".join(f'<tr><td>{html.escape(str(row.get("platform")))}</td><td>{html.escape(str(row.get("question_id")))}</td><td>{html.escape("、".join(row.get("reasons", [])))}</td></tr>' for row in report.get("exclusions", [])) or '<tr><td colspan="3">无排除项。</td></tr>'
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(brand)} GEO 周期对比</title><style>:root{{--ink:#14213d;--muted:#667085;--line:#e5e9f1;--bg:#f5f7fb;--paper:#fff;--accent:#315efb}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{max-width:1200px;margin:auto;padding:40px 24px 72px}}header{{padding:32px;border-radius:20px;background:#14213d;color:white}}header h1{{margin:0;font-size:36px}}header p{{margin:6px 0 0;color:#dbe3ff}}.cards{{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin:20px 0}}article,section{{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:20px}}article span,article small{{display:block;color:var(--muted)}}article strong{{display:block;font-size:32px}}article b{{color:var(--accent)}}section{{margin-top:16px;overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{text-align:left;padding:10px;border-bottom:1px solid var(--line);vertical-align:top}}th{{color:var(--muted);font-size:13px}}.note{{border-left:4px solid var(--accent);padding:10px 14px;background:#eef3ff}}@media(max-width:760px){{.cards{{grid-template-columns:1fr}}main{{padding:18px 12px}}}}</style></head><body><main><header><h1>{html.escape(brand)} GEO 周期对比</h1><p>{html.escape(str(report.get("baseline_run_id")))} → {html.escape(str(report.get("current_run_id")))}</p></header><div class="cards">{"".join(cards)}</div><section><h2>如何解读</h2><p class="note">本页只描述相同问题、平台和测量条件下的变化，不把内容动作或其他因素写成原因。当前全量指标与严格可比队列指标不可混用。</p><p>当前全量有效观察：{report.get("current_full_snapshot", {}).get("valid_observations", 0)}；严格比较排除：{len(report.get("exclusions", []))} 组。</p></section><section><h2>发生变化的配对</h2><table><thead><tr><th>平台</th><th>问题</th><th>信号</th><th>上期</th><th>本期</th><th>状态</th></tr></thead><tbody>{change_rows}</tbody></table></section><section><h2>不可比较 / 不可评估</h2><table><thead><tr><th>平台</th><th>问题 ID</th><th>原因</th></tr></thead><tbody>{exclusion_rows}</tbody></table></section><section><h2>指标口径</h2>{"".join(f"<p><b>{html.escape(k)}</b>：{html.escape(v)}</p>" for k,v in report.get("definitions", {}).items())}</section></main></body></html>'''
    (output_dir / "GEO_周期对比报告.html").write_text(document, encoding="utf-8")
