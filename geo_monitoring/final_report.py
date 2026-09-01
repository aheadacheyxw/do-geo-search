"""Portable, brand-neutral GEO monitoring summary report."""

from __future__ import annotations

import html
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .report_metrics import build_explicit_recommendation_audit, summarize_explicit_recommendation


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _domain(value: object) -> str:
    raw = str(value or "").casefold().strip()
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    return raw.removeprefix("www.").rstrip(".")


def _official_domains(profile: Mapping[str, Any]) -> set[str]:
    values = (profile.get("target_scope") or {}).get("official_domains") or []
    return {_domain(item.get("domain") if isinstance(item, Mapping) else item) for item in values if _domain(item.get("domain") if isinstance(item, Mapping) else item)}


def _is_official(url: object, domains: set[str]) -> bool:
    host = _domain(url)
    return any(host == domain or host.endswith(f".{domain}") for domain in domains)


def build_report_data(run_dir: Path, profile: Mapping[str, Any]) -> dict[str, Any]:
    manifest = _json(run_dir / "manifest.json")
    normalized = _jsonl(run_dir / "step4" / "normalized_observations.jsonl")
    decisions = {str(row.get("observation_id")): row for row in _jsonl(run_dir / "step4" / "signal_decisions.jsonl")}
    questions = {str(row.get("question_id")): row for row in manifest.get("planned_questions", [])}
    mention_rows = [row for row in normalized if row.get("mention_eligible") is True]
    citation_rows = [row for row in normalized if row.get("citation_eligible") is True and row.get("citation_status") == "verified"]
    domains = _official_domains(profile)
    official_count = 0
    visible_count = 0
    platform = defaultdict(lambda: {"valid_observations": 0, "mention_count": 0, "mention_denominator": 0, "citation_observable_answers": 0, "official_citation_answers": 0, "visible_citations": 0})
    for row in normalized:
        p = platform[str(row.get("platform") or "unknown")]
        p["valid_observations"] += 1
        if row.get("mention_eligible") is True:
            p["mention_denominator"] += 1
            p["mention_count"] += int(row.get("brand_mention") is True)
        if row in citation_rows:
            citations = ((decisions.get(str(row.get("observation_id")), {}).get("citation") or {}).get("verified_citations") or [])
            present = any(_is_official(item.get("visible_url") or item.get("url"), domains) for item in citations)
            p["citation_observable_answers"] += 1
            p["official_citation_answers"] += int(present)
            p["visible_citations"] += len(citations)
            official_count += int(present)
            visible_count += len(citations)
    audit = build_explicit_recommendation_audit(normalized, questions, decisions)
    recommendation = summarize_explicit_recommendation(audit)
    recommendation_by_platform = defaultdict(lambda: {"count": 0, "denominator": 0})
    for row in audit:
        if row.get("recommendation_eligible") is True:
            item = recommendation_by_platform[str(row.get("platform") or "unknown")]
            item["denominator"] += 1
            item["count"] += int(row.get("target_explicitly_recommended") is True)
    platform_rows = []
    for name in sorted(platform):
        row = {"platform": name, **platform[name]}
        rec = recommendation_by_platform[name]
        row.update({
            "mention_rate": row["mention_count"] / row["mention_denominator"] if row["mention_denominator"] else None,
            "explicit_recommendation_count": rec["count"],
            "explicit_recommendation_denominator": rec["denominator"],
            "explicit_recommendation_rate": rec["count"] / rec["denominator"] if rec["denominator"] else None,
            "official_citation_rate": row["official_citation_answers"] / row["citation_observable_answers"] if row["citation_observable_answers"] else None,
        })
        platform_rows.append(row)
    topology = _jsonl(run_dir / "step5" / "verified_source_topology.jsonl")
    domain_pages, domain_questions = defaultdict(set), defaultdict(set)
    for row in topology:
        domain = str(row.get("source_domain") or _domain(row.get("url")) or "unknown")
        domain_pages[domain].add(str(row.get("url") or row.get("evidence_id")))
        domain_questions[domain].add(str(row.get("question_id")))
    top_sources = [
        {"source_domain": domain, "verified_source_pages": len(pages), "covered_questions": len(domain_questions[domain])}
        for domain, pages in sorted(domain_pages.items(), key=lambda item: (-len(item[1]), item[0]))[:20]
    ]
    opportunities = _jsonl(run_dir / "step5" / "content_opportunities.jsonl")
    return {
        "run_id": manifest.get("run_id"),
        "generated_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        "brand": (profile.get("target_scope") or {}).get("canonical_brand") or "目标品牌",
        "planned_observations": int(manifest.get("planned_observation_count") or 0),
        "valid_observations": len(normalized),
        "brand_mention": {"numerator": sum(row.get("brand_mention") is True for row in mention_rows), "denominator": len(mention_rows)},
        "explicit_recommendation": recommendation,
        "official_domain_citation": {"numerator": official_count, "denominator": len(citation_rows)},
        "verified_visible_citations": {"count": visible_count, "denominator": len(citation_rows)},
        "formal_rank_observable_count": sum(row.get("rank_eligible") is True for row in normalized),
        "sentiment_distribution": dict(Counter(str(row.get("brand_sentiment") or "unavailable") for row in normalized)),
        "platform_metrics": platform_rows,
        "top_source_domains": top_sources,
        "content_opportunity_count": len(opportunities),
        "priority_distribution": dict(Counter(str(row.get("priority") or "unknown") for row in opportunities)),
    }


def _pct(metric: Mapping[str, Any]) -> str:
    denominator = int(metric.get("denominator") or 0)
    return "不可评估" if not denominator else f"{int(metric.get('numerator') or 0) / denominator:.1%}"


def _rate(value: object) -> str:
    return "不可评估" if value is None else f"{float(value):.1%}"


def render_report(data: Mapping[str, Any], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "GEO_监测总报告数据.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    platform_rows = "".join(
        f'<tr><td>{html.escape(str(row["platform"]))}</td><td>{row["valid_observations"]}</td><td>{row["mention_count"]}/{row["mention_denominator"]} · {_rate(row["mention_rate"])}</td><td>{row["explicit_recommendation_count"]}/{row["explicit_recommendation_denominator"]} · {_rate(row["explicit_recommendation_rate"])}</td><td>{row["official_citation_answers"]}/{row["citation_observable_answers"]} · {_rate(row["official_citation_rate"])}</td><td>{row["visible_citations"]}</td></tr>'
        for row in data.get("platform_metrics", [])
    )
    source_rows = "".join(f'<tr><td>{html.escape(str(row["source_domain"]))}</td><td>{row["verified_source_pages"]}</td><td>{row["covered_questions"]}</td></tr>' for row in data.get("top_source_domains", [])) or '<tr><td colspan="3">无已验证来源。</td></tr>'
    sentiment = "、".join(f"{html.escape(key)} {value}" for key, value in sorted(data.get("sentiment_distribution", {}).items()))
    links = []
    for relative, label in (
        ("../step5/05_竞品差距与内容机会.html", "Step 5 竞品差距与内容机会"),
        ("../step6/06_内容规划骨架.html", "Step 6 内容规划骨架"),
        ("../comparison/GEO_周期对比报告.html", "周期对比报告"),
    ):
        if (output_dir / relative).resolve().is_file():
            links.append(f'<a href="{relative}">{label}</a>')
    downstream = " · ".join(links) or "本次尚无可链接的下游报告。"
    document = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(str(data["brand"]))} GEO 监测总报告</title><style>:root{{--ink:#162033;--muted:#667085;--line:#e5e9f1;--paper:#fff;--bg:#f5f7fb;--blue:#315efb}}*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC",sans-serif}}main{{max-width:1240px;margin:auto;padding:42px 24px 76px}}header{{background:linear-gradient(135deg,#14213d,#284592);color:#fff;border-radius:22px;padding:36px}}header h1{{font-size:38px;margin:0}}header p{{color:#dce5ff;margin:6px 0 0}}.cards{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin:20px 0}}article,section{{background:var(--paper);border:1px solid var(--line);border-radius:14px;padding:20px}}article span,article small{{display:block;color:var(--muted)}}article strong{{display:block;font-size:30px}}section{{margin-top:16px;overflow:auto}}table{{width:100%;min-width:780px;border-collapse:collapse}}th,td{{padding:11px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}}th{{font-size:13px;color:var(--muted)}}.note{{border-left:4px solid var(--blue);padding:10px 14px;background:#eef3ff}}@media(max-width:850px){{.cards{{grid-template-columns:1fr 1fr}}}}@media(max-width:560px){{.cards{{grid-template-columns:1fr}}main{{padding:16px 10px}}}}</style></head><body><main><header><h1>{html.escape(str(data["brand"]))} GEO 监测总报告</h1><p>{html.escape(str(data["run_id"]))} · {data["valid_observations"]}/{data["planned_observations"]} 有效观察</p></header><div class="cards"><article><span>品牌提及率</span><strong>{_pct(data["brand_mention"])}</strong><small>{data["brand_mention"]["numerator"]}/{data["brand_mention"]["denominator"]}</small></article><article><span>明确推荐率</span><strong>{_pct(data["explicit_recommendation"])}</strong><small>{data["explicit_recommendation"]["numerator"]}/{data["explicit_recommendation"]["denominator"]} · 不等同 Top1</small></article><article><span>官方域引用出现率</span><strong>{_pct(data["official_domain_citation"])}</strong><small>{data["official_domain_citation"]["numerator"]}/{data["official_domain_citation"]["denominator"]}</small></article><article><span>内容机会</span><strong>{data["content_opportunity_count"]}</strong><small>逐题保留，不合并</small></article></div><section><h2>口径提示</h2><p class="note">品牌提及、明确推荐、正式位次、可见引用、情感倾向和事实风险互相独立；不可观察证据不填零。</p><p>情感倾向：{sentiment or "无记录"}。未经人工或经验证分类器复核的情感保持 unavailable。</p></section><section><h2>平台表现</h2><table><thead><tr><th>平台</th><th>有效观察</th><th>品牌提及</th><th>明确推荐</th><th>官方域引用</th><th>可见引用数</th></tr></thead><tbody>{platform_rows}</tbody></table></section><section><h2>已验证来源渠道 Top 20</h2><p>按 AI 回答中被验证可见引用的唯一来源页数统计，不代表该站点总发文量。</p><table><thead><tr><th>来源域</th><th>来源页数</th><th>覆盖问题数</th></tr></thead><tbody>{source_rows}</tbody></table></section><section><h2>下游产物</h2><p>{downstream}</p></section></main></body></html>'''
    path = output_dir / "GEO_监测总报告.html"
    path.write_text(document, encoding="utf-8")
    return path


def build_and_render(run_dir: Path, profile: Mapping[str, Any], output_dir: Path) -> Path:
    return render_report(build_report_data(run_dir, profile), output_dir)
