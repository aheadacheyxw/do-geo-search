"""Step 5 evidence-bounded competitor gap and opportunity construction."""

from __future__ import annotations

import csv
import hashlib
import html
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


def _owner(url: str, registry: Sequence[Mapping[str, Any]]) -> str | None:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    for competitor in registry:
        for domain in competitor.get("domains", []):
            if host == str(domain).casefold().removeprefix("www."):
                return str(competitor["competitor_id"])
    return None


def build_step5_snapshot(
    observations: Sequence[Mapping[str, Any]], registry: Sequence[Mapping[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """Create citation-gap facts only for comparable verified observations."""

    facts: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    for observation in observations:
        if not observation.get("comparable") or not observation.get("citation_eligible"):
            continue
        if observation.get("citation_status") != "verified":
            continue
        for citation in observation.get("verified_citations", []):
            competitor_id = _owner(str(citation.get("url", "")), registry)
            if not competitor_id:
                manual_review.append({"question_id": observation.get("question_id"), "reason": "unresolved_domain"})
                continue
            if observation.get("target_verified_citation"):
                continue
            key = ":".join((str(observation["question_id"]), str(observation["platform"]), competitor_id, "verified_citation_gap"))
            fact = {
                "opportunity_key": key,
                "question_id": observation["question_id"],
                "platform": observation["platform"],
                "competitor_id": competitor_id,
                "signal_type": "verified_citation",
                "gap_type": "verified_citation_gap",
                "evidence": citation,
            }
            facts.append(fact)
            opportunities.append({
                **fact,
                "eligible_for_comparison": True,
                "signal_definition_version": "v2",
                "business_importance": observation.get("business_importance", "medium"),
            })
    return {"competitor_facts": facts, "opportunities": opportunities, "manual_review": manual_review}


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _registered_domains(competitor: Mapping[str, Any]) -> list[str]:
    return [str(value) for value in competitor.get("official_domains", competitor.get("domains", []))]


def _mention_spans(answer: str, aliases: Sequence[str]) -> list[dict[str, Any]]:
    spans: list[dict[str, Any]] = []
    for alias in sorted({str(alias) for alias in aliases if alias}, key=len, reverse=True):
        for match in re.finditer(re.escape(alias), answer, flags=re.IGNORECASE):
            spans.append({"start": match.start(), "end": match.end(), "text": match.group(0)})
    deduplicated: dict[tuple[int, int], dict[str, Any]] = {}
    for span in spans:
        deduplicated[(span["start"], span["end"])] = span
    return [deduplicated[key] for key in sorted(deduplicated)]


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fieldnames: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })


def _json_value(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _domain_owner(url: str, target_domains: Sequence[str], registry: Sequence[Mapping[str, Any]]) -> tuple[str, str | None]:
    host = urlparse(url).netloc.casefold().removeprefix("www.")
    if not host:
        return "unresolved", None
    if any(host == str(domain).casefold().removeprefix("www.") for domain in target_domains):
        return "target", None
    for competitor in registry:
        if any(host == domain.casefold().removeprefix("www.") for domain in _registered_domains(competitor)):
            return "verified_competitor", str(competitor["competitor_id"])
    return "third_party", None


def _explicit_overall_ranks(answer: str, aliases_by_entity: Mapping[str, Sequence[str]]) -> dict[str, dict[str, Any]]:
    """Extract positions only from an explicitly labelled, numbered overall list."""

    if not re.search(r"(?:推荐(?:品牌|服务商|供应商|方案)?|(?:top|TOP)\s*\d+|排名)", answer):
        return {}
    ranks: dict[str, dict[str, Any]] = {}
    for line_number, line in enumerate(answer.splitlines(), 1):
        listed = re.match(r"\s*(\d{1,2})[.、．]\s*(.+)", line)
        if not listed:
            continue
        for entity_id, aliases in aliases_by_entity.items():
            if any(alias and alias.casefold() in listed.group(2).casefold() for alias in aliases):
                ranks.setdefault(entity_id, {
                    "rank": int(listed.group(1)), "line_number": line_number,
                    "line": line, "list_type": "explicit_numbered_recommendation_list",
                })
    return ranks


def extract_candidate_competitor_review(
    answer: str,
    registered_aliases: set[str],
    lineage: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Queue only unregistered names from explicit answer recommendation lists.

    This deliberately does not transform arbitrary source domains, page URLs, or
    prose nouns into competitors.  The returned text is a human-review lead,
    never a formal competitor identity.
    """

    known = {value.casefold() for value in registered_aliases if value}
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines = answer.splitlines()
    recommendation_header = re.compile(r"(?:推荐(?:品牌|服务商|供应商|方案)?|(?:top|TOP)\s*\d+|排名)", re.IGNORECASE)
    provider_header = re.compile(r"(?:主要(?:被)?(?:涉及|提及|包括)(?:的)?服务商|(?:代表|推荐)(?:的)?服务商|服务商(?:包括|有))")
    in_named_provider_list = False
    in_numbered_recommendation = False
    offset = 0

    def add_candidate(raw: str, line: str, line_number: int, reason: str) -> None:
        normalized = raw.strip(" \t-—–:：,，。；;、|丨·•*#（）()[]【】")
        generic = (
            "运营商", "云服务商", "授权服务商", "服务商", "供应商", "方案类型",
            "核心优势", "适合团队", "推荐理由", "你的情况", "推荐计费方式",
            "使用场景", "适用场景", "计费方式", "套餐", "需求", "需要", "养号", "爬虫", "选择建议",
        )
        if (
            not normalized
            or normalized.casefold() in known
            or normalized.casefold() in seen
            or len(normalized) > 48
            or "、" in normalized
            or normalized.endswith("型")
            or any(term in normalized for term in generic)
            or not re.search(r"[A-Za-z\u4e00-\u9fff]", normalized)
        ):
            return
        start = answer.find(line, offset)
        rows.append({
            **dict(lineage),
            "candidate_name_raw": normalized,
            "candidate_aliases_suggested": [],
            "candidate_type": "unresolved_named_recommendation",
            "status": "candidate_only",
            "manual_review_required": True,
            "answer_span": {"start": start, "end": start + len(line), "text": line},
            "line_number": line_number,
            "reason": reason,
        })
        seen.add(normalized.casefold())

    for line_number, line in enumerate(lines, 1):
        stripped = line.strip()
        if provider_header.search(stripped):
            in_named_provider_list = True
            in_numbered_recommendation = bool(recommendation_header.search(stripped))
            offset += len(line) + 1
            continue
        if recommendation_header.search(stripped) and stripped.endswith(("：", ":")):
            in_numbered_recommendation = True
            offset += len(line) + 1
            continue
        listed = re.match(r"\s*(\d{1,2})[.、．]\s*(.+?)\s*$", line)
        if in_numbered_recommendation and listed:
            raw = re.split(r"[（(：:，,。；;]", listed.group(2), maxsplit=1)[0]
            add_candidate(raw, line, line_number, "AI 回答的明确推荐列表出现未登记名称；需人工确认实体、官网与竞争关系。")
        elif stripped.endswith(("：", ":")) or re.match(r"^(?:类型\s*\d+|[一二三四五六七八九十]+、)", stripped):
            in_numbered_recommendation = False
            in_named_provider_list = False
        elif in_named_provider_list:
            if stripped.endswith(("：", ":")) or re.match(r"^(?:类型\s*\d+|[一二三四五六七八九十]+、)", stripped):
                in_named_provider_list = False
            elif "\t" in line:
                cells = [cell.strip() for cell in stripped.split("\t") if cell.strip()]
                if not cells:
                    offset += len(line) + 1
                    continue
                first = cells[0]
                # AI answers often render a table as "provider type | named brands | traits".
                # In that shape the first cell is a category, while the second contains
                # the actual names.  Keep only the names and leave all ambiguous cells
                # for manual review outside the competitor registry.
                if first.endswith("型") and len(cells) > 1:
                    for raw in re.split(r"[、，,；;\s]+", cells[1]):
                        add_candidate(raw, line, line_number, "AI 回答的明确服务商名单出现未登记名称；需人工确认实体、官网与竞争关系。")
                elif first not in {"服务商", "品牌", "供应商"}:
                    add_candidate(first, line, line_number, "AI 回答的明确服务商名单出现未登记名称；需人工确认实体、官网与竞争关系。")
            else:
                item = re.match(r"\s*(?:[-*•·]\s*)?(.+?)(?:\s+(?:—|-)\s+.*)$", line)
                if item:
                    add_candidate(item.group(1), line, line_number, "AI 回答的明确服务商名单出现未登记名称；需人工确认实体、官网与竞争关系。")
        offset += len(line) + 1
    return rows


def _render_growth_workbench(
    output_dir: Path,
    workbench: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
    opportunities: Sequence[Mapping[str, Any]],
    coverage: Mapping[str, Any],
    target_brand: str,
) -> None:
    """Render the operator-facing Step 5 report without hiding raw facts."""

    def esc(value: Any) -> str:
        return html.escape(str("—" if value is None or value == "" else value))

    def pill(value: Any, kind: str = "") -> str:
        return f'<span class="pill {esc(kind)}">{esc(value)}</span>'

    families = coverage.get("metric_families", {})
    mention = families.get("brand_mention", {})
    rank = families.get("formal_rank", {})
    citation = families.get("verified_citation", {})
    growth_rows = [row for row in workbench if row.get("growth_trigger")]
    target_mentions = sum(bool(row.get("target_brand_mention")) for row in growth_rows if row.get("mention_eligible"))
    growth_mention_denominator = sum(bool(row.get("mention_eligible")) for row in growth_rows)
    target_ranks = sum(row.get("target_recommendation_rank") not in (None, "", "unranked") for row in growth_rows if row.get("rank_observable"))
    rank_denominator = sum(bool(row.get("rank_observable")) for row in growth_rows)
    target_citations = sum(bool(row.get("target_official_citation")) for row in growth_rows if row.get("citation_eligible"))
    citation_denominator = sum(bool(row.get("citation_eligible")) for row in growth_rows)

    def source_list(row: Mapping[str, Any]) -> str:
        sources = row.get("verified_sources") or []
        if not sources:
            return '<span class="muted">无已验证可见来源；不是“未引用”。</span>'
        return "<ul class=\"sources\">" + "".join(
            f'<li><a href="{esc(item.get("url"))}" target="_blank" rel="noreferrer">{esc(item.get("anchor_or_span") or item.get("source_domain"))}</a><small>{esc(item.get("ownership"))}</small></li>'
            for item in sources
        ) + "</ul>"

    def facts(row: Mapping[str, Any]) -> str:
        entries = row.get("registered_competitor_facts") or []
        if not entries:
            return '<span class="muted">本观察未形成登记竞品差距事实。</span>'
        return "<ul class=\"facts\">" + "".join(
            f'<li>{pill(item.get("gap_type"), "fact")} {esc(item.get("competitor_name") or item.get("competitor_id"))}</li>'
            for item in entries
        ) + "</ul>"

    cards = "".join(
        f'''<article class="question-card" data-growth="{str(bool(row.get("growth_trigger"))).lower()}">
<div class="card-head"><div>{pill("增长触发" if row.get("growth_trigger") else "需求/风险观察", "growth" if row.get("growth_trigger") else "neutral")} {pill(row.get("question_group"), "neutral")}<h3>{esc(row.get("question_text") or row.get("question_id"))}</h3><p>{esc(row.get("platform"))} · {esc(row.get("business_importance"))}重要度 · {esc(row.get("observation_id"))}</p></div><div>{pill(row.get("evidence_state"), "state")}</div></div>
<div class="signal-grid"><div><b>{esc(target_brand)} 正文提及</b><span>{"是" if row.get("target_brand_mention") else "否" if row.get("mention_eligible") else "不可评估"}</span></div><div><b>正式推荐位次</b><span>{esc(row.get("target_recommendation_rank") if row.get("rank_observable") else "不可评估")}</span></div><div><b>{esc(target_brand)} 官网引用</b><span>{"有" if row.get("target_official_citation") else "未观察到" if row.get("citation_eligible") else "不可评估"}</span></div><div><b>可见来源</b><span>{esc(row.get("verified_source_count"))}</span></div></div>
<div class="detail-grid"><div><h4>登记竞品事实</h4>{facts(row)}</div><div><h4>已验证可见来源</h4>{source_list(row)}</div></div>
<details><summary>展开完整 AI 回答与证据说明</summary><pre>{esc(row.get("answer_text") or "原始回答缺失")}</pre><p class="muted">证据 ID：{esc(row.get("evidence_id"))}；限制：{esc(row.get("limitation") or "无")}</p></details></article>'''
        for row in workbench
    ) or '<p class="empty">当前运行还没有 Step 4 已接纳观察。</p>'
    candidates_html = "".join(
        f'<tr><td>{esc(row.get("candidate_name_raw"))}</td><td>{esc(row.get("question_text") or row.get("question_id"))}</td><td>{esc(row.get("platform"))}</td><td>{esc((row.get("answer_span") or {}).get("text"))}</td><td>{pill("仅候选，待人工登记", "review")}</td></tr>'
        for row in candidates
    ) or '<tr><td colspan="5" class="empty">本期未在明确推荐列表发现未登记名称。</td></tr>'
    opportunity_html = "".join(
        f'<tr><td>{pill(row.get("opportunity_tier"), "tier")}</td><td>{esc(row.get("question_text") or row.get("question_id"))}</td><td>{esc(row.get("priority"))}</td><td>{esc(row.get("opportunity_basis"))}</td><td>{esc(row.get("step6_eligibility"))}</td></tr>'
        for row in opportunities
    ) or '<tr><td colspan="5" class="empty">本期没有可交接内容机会。</td></tr>'
    html_text = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{esc(target_brand)} GEO 增长工作台</title>
<style>:root{{--ink:#152033;--muted:#67748a;--line:#d9e1ed;--paper:#f4f7fb;--surface:#fff;--blue:#1b63d9;--blue-soft:#eaf2ff;--amber:#875900;--amber-soft:#fff4d7;--red:#a42b2b;--red-soft:#ffebe9}}*{{box-sizing:border-box}}body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 ui-sans-serif,-apple-system,BlinkMacSystemFont,"PingFang SC","Microsoft YaHei",sans-serif}}main{{max-width:1320px;margin:auto;padding:28px 20px 56px}}header{{display:flex;justify-content:space-between;gap:16px;align-items:end;border-bottom:1px solid var(--line);padding-bottom:20px}}h1{{margin:0;font-size:32px;letter-spacing:-.04em}}h2{{font-size:21px;margin:0 0 12px}}h3{{margin:8px 0 3px;font-size:19px;line-height:1.35}}h4{{font-size:13px;margin:0 0 5px;color:var(--muted)}}p{{margin:0;color:var(--muted)}}.meta,.muted,small{{color:var(--muted);font-size:13px}}.notice{{border-left:4px solid var(--blue);background:var(--blue-soft);padding:12px 14px;margin:22px 0}}.stats{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin:18px 0 28px}}.stat,.question-card,section.table,.notice{{border:1px solid var(--line);border-radius:10px;background:var(--surface)}}.stat{{padding:16px}}.stat b{{display:block;font-size:28px;line-height:1.1}}.stat span{{color:var(--muted);font-size:13px}}.question-card{{padding:18px;margin:14px 0}}.card-head{{display:flex;justify-content:space-between;gap:12px}}.pill{{display:inline-block;padding:2px 8px;border-radius:999px;background:#eef1f6;color:#38465b;font-size:12px;margin-right:4px}}.growth{{background:var(--blue-soft);color:var(--blue)}}.neutral{{background:#eef1f6}}.state{{background:#f1f8f3;color:#267245}}.fact{{background:var(--amber-soft);color:var(--amber)}}.review{{background:var(--red-soft);color:var(--red)}}.tier{{background:#edf0ff;color:#4c3c96}}.signal-grid,.detail-grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-top:16px}}.signal-grid div,.detail-grid div{{border:1px solid var(--line);border-radius:7px;padding:9px}}.signal-grid b{{display:block;font-size:12px;color:var(--muted)}}.signal-grid span{{font-weight:650}}.detail-grid{{grid-template-columns:1fr 1fr}}ul{{margin:0;padding-left:18px}}.sources li,.facts li{{margin:3px 0}}.sources small{{margin-left:5px}}details{{margin-top:14px;border-top:1px dashed var(--line);padding-top:10px}}summary{{cursor:pointer;font-weight:650}}pre{{white-space:pre-wrap;overflow-wrap:anywhere;background:#f7f9fc;border-radius:6px;padding:12px;max-height:440px;overflow:auto;font:13px/1.6 ui-monospace,SFMono-Regular,Menlo,monospace}}section.table{{padding:18px;margin-top:26px;overflow:auto}}table{{width:100%;border-collapse:collapse;min-width:760px}}th,td{{text-align:left;vertical-align:top;border-bottom:1px solid var(--line);padding:9px}}th{{font-size:12px;color:var(--muted)}}.empty{{color:var(--muted);padding:16px}}@media(max-width:800px){{.stats,.signal-grid,.detail-grid{{grid-template-columns:1fr 1fr}}header{{align-items:start;flex-direction:column}}}}@media(max-width:520px){{.stats,.signal-grid,.detail-grid{{grid-template-columns:1fr}}main{{padding:20px 12px}}h1{{font-size:27px}}}}</style></head><body><main>
<header><div><p class="meta">Step 5 · 增长工作台 · {esc(coverage.get("snapshot_id"))}</p><h1>{esc(target_brand)} AI 搜索表现：逐题事实与证据</h1></div><p class="meta">只将已登记竞品计入正式比较；候选品牌单独人工复核。</p></header>
<aside class="notice"><b>如何读：</b>每张卡是一条 AI 平台回答。回答、已验证可见来源、登记竞品信号和限制分开显示；“不可评估”不是零，也不是“未被引用”。</aside>
<section class="stats"><div class="stat"><b>{target_mentions} / {growth_mention_denominator}</b><span>增长题中 {esc(target_brand)} 正文提及</span></div><div class="stat"><b>{target_ranks} / {rank_denominator}</b><span>增长题中进入明确推荐列表</span></div><div class="stat"><b>{target_citations} / {citation_denominator}</b><span>增长题中 {esc(target_brand)} 官网可见引用</span></div><div class="stat"><b>{len(candidates)}</b><span>待人工确认的新候选名称</span></div></section>
<section><h2>问题工作台（{len(workbench)} 条已接纳观察）</h2>{cards}</section>
<section class="table"><h2>登记竞品与内容机会</h2><table><thead><tr><th>等级</th><th>问题</th><th>优先级</th><th>本次事实依据</th><th>Step 6</th></tr></thead><tbody>{opportunity_html}</tbody></table></section>
<section class="table"><h2>候选竞品复核表（不进入正式指标）</h2><p>这些名称仅来自 AI 回答的明确推荐列表；尚未验证实体、官网或竞争关系。</p><table><thead><tr><th>原始名称</th><th>问题</th><th>平台</th><th>回答中的原文行</th><th>状态</th></tr></thead><tbody>{candidates_html}</tbody></table></section>
<section class="table"><h2>指标口径</h2><table><tbody><tr><th>正文提及可评估</th><td>{esc(mention.get("eligible_observation_count", 0))}</td><th>正式推荐位次可评估</th><td>{esc(rank.get("eligible_observation_count", 0))}</td><th>已验证引用可评估</th><td>{esc(citation.get("eligible_observation_count", 0))}</td></tr></tbody></table></section>
</main></body></html>'''
    (output_dir / "05_竞品差距与内容机会.html").write_text(html_text, encoding="utf-8")


def _artifact_reference(run_dir: Path, observation_id: str) -> str:
    return str(run_dir / "raw" / "observations" / observation_id / "answer.txt")


def _render_html(output_dir: Path, facts: Sequence[Mapping[str, Any]], opportunities: Sequence[Mapping[str, Any]], reviews: Sequence[Mapping[str, Any]], coverage: Mapping[str, Any], source_topology: Sequence[Mapping[str, Any]], platform_coverage: Sequence[Mapping[str, Any]]) -> None:
    """Render a decision-first Step 5 workbench from the bounded fact tables.

    The UI deliberately never converts an unavailable metric into a zero.  Its
    job is to make the fact layer legible for an operator; CSV/JSONL remain the
    machine-readable source of truth.
    """

    def text(value: Any) -> str:
        return html.escape(str("—" if value is None or value == "" else value))

    def label(gap_type: str) -> str:
        return {
            "mention_gap": "正文提及缺口",
            "verified_citation_gap": "已验证引用缺口",
            "formal_rank_gap": "正式推荐位次缺口",
            "third_party_channel_gap": "第三方来源覆盖",
        }.get(gap_type, gap_type)

    def tier_label(tier: str) -> str:
        return {
            "A_competitive_gap": "A · 竞争差距",
            "B_citation_whitespace": "B · 引用空位",
            "C_review_required": "C · 待核验",
        }.get(tier, tier)

    def review_label(reason: str) -> str:
        return {
            "citation_not_assessable_partial": "引用证据不完整（不可按未引用处理）",
            "citation_not_assessable_unavailable": "未捕获到可计量引用证据",
            "formal_rank_not_observable": "未出现可识别的整体推荐列表",
            "not_comparable": "上下文不可比",
            "raw_answer_missing": "原始回答缺失",
        }.get(reason, reason)

    def observed(fact: Mapping[str, Any]) -> str:
        gap_type = str(fact.get("gap_type"))
        if gap_type == "verified_citation_gap":
            citation = fact.get("citation") or {}
            return "可见来源审计中出现登记竞品官方域；本次未观察到目标官方域。"
        if gap_type == "formal_rank_gap":
            return f"明确整体推荐列表中，竞品为第 {fact.get('competitor_rank')} 位；目标方未入列或位次更后。"
        if gap_type == "third_party_channel_gap":
            return "可见第三方来源标题/片段中出现登记竞品。"
        return "回答正文出现登记竞品，未出现目标品牌。"

    families = coverage.get("metric_families", {})
    mention = families.get("brand_mention", {})
    citation = families.get("verified_citation", {})
    rank = families.get("formal_rank", {})
    citation_unassessable = int(citation.get("excluded_observation_count", 0))
    fact_by_type: dict[str, int] = defaultdict(int)
    for fact in facts:
        fact_by_type[str(fact.get("gap_type"))] += 1
    fact_rows = "".join(
        "<tr class=\"fact-row\" data-signal=\"" + text(fact.get("gap_type")) + "\">"
        f"<td><span class=\"signal {text(fact.get('gap_type'))}\">{text(label(str(fact.get('gap_type'))))}</span></td>"
        f"<td><strong>{text(fact.get('question_text') or fact.get('question_id'))}</strong><small>{text(fact.get('question_id'))} · {text(fact.get('business_importance'))}重要度</small></td>"
        f"<td>{text(fact.get('platform'))}</td><td>{text(fact.get('competitor_name') or fact.get('competitor_id'))}</td>"
        f"<td>{text(observed(fact))}<small>证据 ID：{text(fact.get('evidence_id'))} · 单次观察，不作因果判断。</small></td>"
        "</tr>"
        for fact in facts
    ) or "<tr><td colspan=\"5\" class=\"empty\">本期没有满足全部资格条件的竞品事实。</td></tr>"
    opportunity_rows = "".join(
        f"<article class=\"opportunity\"><div><span class=\"priority {text(item.get('priority'))}\">{text(item.get('priority'))}</span> <span class=\"signal\">{text(tier_label(str(item.get('opportunity_tier'))))}</span><h3>{text(item.get('question_text') or item.get('question_id'))}</h3><p>{text(item.get('opportunity_basis') or '、'.join(label(str(kind)) for kind in item.get('gap_types', [])))}</p></div>"
        f"<dl><div><dt>观察平台</dt><dd>{text('、'.join(item.get('platforms', [])))}</dd></div><div><dt>登记竞品</dt><dd>{text('、'.join(item.get('competitor_names') or item.get('competitor_ids', [])))}</dd></div><div><dt>样本</dt><dd>{text(item.get('verified_observation_count'))} 个有效观察</dd></div></dl></article>"
        for item in opportunities
    ) or "<p class=\"empty\">本期没有可交接的内容机会。</p>"
    source_rows = "".join(
        f"<tr><td>{text(item.get('source_publisher') or item.get('source_domain'))}</td><td><a href=\"{text(item.get('url'))}\" target=\"_blank\" rel=\"noreferrer\">打开已验证来源页 ↗</a></td><td>{text(item.get('ownership'))}</td><td>{text(item.get('question_id'))}</td><td>{text(item.get('platform'))}</td></tr>"
        for item in source_topology
    ) or "<tr><td colspan=\"5\" class=\"empty\">本期没有已验证可见来源卡。</td></tr>"
    review_counts: dict[str, int] = defaultdict(int)
    for review in reviews:
        review_counts[str(review.get("reason") or "其他限制")] += 1
    review_rows = "".join(f"<li><span>{text(review_label(reason))}</span><b>{count}</b></li>" for reason, count in sorted(review_counts.items())) or "<li><span>无</span><b>0</b></li>"
    platform_rows = "".join(
        f"<tr><td>{text(row.get('platform'))}</td><td>{text(row.get('observation_count'))}</td><td>{text(row.get('target_mention_count'))} / {text(row.get('mention_eligible_count'))}</td><td>{text(row.get('source_audited_observation_count'))}</td><td>{text(row.get('target_official_citation_observation_count'))}</td><td>{text(row.get('visible_verified_source_card_count'))}</td><td>{text(row.get('citation_limited_count'))}</td><td>{text(row.get('formal_rank_observable_count'))}</td></tr>"
        for row in platform_coverage
    ) or "<tr><td colspan=\"6\" class=\"empty\">无平台覆盖记录。</td></tr>"
    fact_total = len(facts)
    html_text = f"""<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>05 竞品差距工作台</title>
<style>
/* Hallmark · pre-emit critique: P5 H5 E4 S5 R5 V4 · contrast: pass (46–50) · slop: pass (51–55) · honest: pass (56) · chrome: pass (57) · tokens: pass (58) · responsive: pass (59) · icons: pass (60) */
/* Hallmark · genre: modern-minimal · macrostructure: Stat-Led · theme: Quiet · enrichment: none · nav: N9 · footer: Ft2
 * H4 Stat-Led knobs: tabular-display, qualifier-below, secondary-stats=row-of-four */
:root{{--color-paper:oklch(97% .006 250);--color-surface:oklch(99% .004 250);--color-ink:oklch(20% .018 250);--color-muted:oklch(48% .012 250);--color-rule:oklch(88% .012 250);--color-accent:oklch(49% .16 255);--color-accent-ink:oklch(97% .006 250);--color-accent-soft:oklch(94% .018 250);--color-warning:oklch(59% .17 38);--color-warning-soft:oklch(94% .04 38);--color-danger:oklch(54% .19 25);--color-danger-soft:oklch(93% .06 25);--font-display:"Geist",ui-sans-serif,sans-serif;--font-body:"IBM Plex Sans",ui-sans-serif,sans-serif;--font-mono:"Geist Mono",ui-monospace,monospace;--space-xs:.5rem;--space-sm:.75rem;--space-md:1rem;--space-lg:1.5rem;--space-xl:2.5rem;--space-2xl:4rem;--radius-card:.75rem;--radius-pill:999px;--ease-out:cubic-bezier(.16,1,.3,1);--dur-short:220ms}}html,body{{margin:0;overflow-x:clip;background:var(--color-paper);color:var(--color-ink)}}body{{font-family:var(--font-body);line-height:1.55}}*{{box-sizing:border-box}}a{{color:var(--color-accent);text-decoration-thickness:1px;text-underline-offset:3px}}button{{font:inherit}}.shell{{max-width:1440px;margin:auto;padding:var(--space-lg)}}.topbar{{display:flex;justify-content:space-between;gap:var(--space-md);align-items:baseline;border-bottom:1px solid var(--color-rule);padding-bottom:var(--space-md)}}.brand{{font:700 1rem var(--font-display);letter-spacing:-.02em}}.meta,.eyebrow,small,dt{{font:500 .74rem var(--font-mono);color:var(--color-muted)}}.hero{{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(18rem,.8fr);gap:var(--space-2xl);padding:clamp(2.5rem,7vw,6rem) 0 var(--space-xl);align-items:end}}h1,h2,h3,p{{margin:0}}h1{{font:650 clamp(2.3rem,5vw,5.1rem)/1.02 var(--font-display);letter-spacing:-.055em;max-width:12ch;overflow-wrap:anywhere;min-width:0}}.lede{{max-width:42ch;font-size:1.06rem;color:var(--color-muted)}}.lede b{{color:var(--color-ink)}}.stats{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));border-top:1px solid var(--color-rule);border-bottom:1px solid var(--color-rule)}}.stat{{padding:var(--space-lg) var(--space-md);border-right:1px solid var(--color-rule)}}.stat:last-child{{border-right:0}}.stat b{{display:block;font:650 clamp(1.8rem,3vw,3.2rem)/1 var(--font-display);letter-spacing:-.05em;font-variant-numeric:tabular-nums}}.stat span{{font-size:.82rem;color:var(--color-muted)}}.notice{{margin-top:var(--space-xl);padding:var(--space-md);border:1px solid var(--color-rule);border-radius:var(--radius-card);background:var(--color-surface)}}.notice b{{display:block;font:600 1rem var(--font-display);margin-bottom:.25rem}}.grid{{display:grid;grid-template-columns:minmax(0,1.65fr) minmax(17rem,.65fr);gap:var(--space-xl);padding-top:var(--space-2xl)}}section h2{{font:650 1.35rem/1.2 var(--font-display);letter-spacing:-.03em;margin-bottom:var(--space-md)}}.panel{{background:var(--color-surface);border:1px solid var(--color-rule);border-radius:var(--radius-card);overflow:hidden}}.filters{{display:flex;gap:var(--space-xs);padding:var(--space-md);border-bottom:1px solid var(--color-rule);flex-wrap:wrap}}.filter{{border:1px solid var(--color-rule);background:transparent;padding:.35rem .65rem;border-radius:var(--radius-pill);cursor:pointer;white-space:nowrap;color:var(--color-muted)}}.filter:hover{{border-color:var(--color-ink);color:var(--color-ink)}}.filter:focus-visible{{outline:2px solid var(--color-accent);outline-offset:2px}}.filter:active{{transform:translateY(1px)}}.filter:disabled{{opacity:.55;cursor:not-allowed}}.filter[aria-pressed=\"true\"]{{background:var(--color-ink);border-color:var(--color-ink);color:var(--color-paper)}}table{{width:100%;border-collapse:collapse}}th{{font:500 .7rem var(--font-mono);text-align:left;color:var(--color-muted);padding:var(--space-sm) var(--space-md);border-bottom:1px solid var(--color-rule)}}td{{padding:var(--space-md);border-bottom:1px solid var(--color-rule);vertical-align:top;font-size:.9rem}}td strong,td small{{display:block}}td small{{margin-top:.3rem}}.signal,.priority{{display:inline-flex;border-radius:var(--radius-pill);padding:.18rem .5rem;font:500 .68rem var(--font-mono);white-space:nowrap;background:var(--color-accent-soft);color:var(--color-accent)}}.signal.verified_citation_gap{{background:var(--color-warning-soft);color:var(--color-warning)}}.signal.formal_rank_gap{{background:var(--color-danger-soft);color:var(--color-danger)}}.priority.P0{{background:var(--color-danger-soft);color:var(--color-danger)}}.priority.P1{{background:var(--color-warning-soft);color:var(--color-warning)}}.opportunity{{display:grid;grid-template-columns:1fr auto;gap:var(--space-lg);padding:var(--space-lg);border-bottom:1px solid var(--color-rule)}}.opportunity:last-child{{border-bottom:0}}.opportunity h3{{font:600 1.08rem/1.3 var(--font-display);margin-top:.45rem}}.opportunity p{{font-size:.88rem;color:var(--color-muted);margin-top:.35rem}}dl{{display:flex;gap:var(--space-lg);margin:0;align-items:start}}dd{{margin:.2rem 0 0;font-size:.84rem;max-width:12ch}}.side{{display:grid;gap:var(--space-xl);align-content:start}}.metric-list{{list-style:none;padding:0;margin:0}}.metric-list li{{display:flex;justify-content:space-between;gap:var(--space-md);padding:var(--space-sm) 0;border-bottom:1px solid var(--color-rule);font-size:.88rem}}.metric-list b{{font-family:var(--font-mono);font-variant-numeric:tabular-nums}}details{{border-top:1px solid var(--color-rule);margin-top:var(--space-2xl);padding-top:var(--space-lg)}}summary{{cursor:pointer;font:600 1.1rem var(--font-display)}}.method{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:var(--space-sm);margin-top:var(--space-md)}}.method div{{border-left:2px solid var(--color-rule);padding-left:var(--space-sm);font-size:.82rem;color:var(--color-muted)}}.method b{{display:block;color:var(--color-ink);margin-bottom:.25rem}}.sources{{margin-top:var(--space-xl)}}.empty{{color:var(--color-muted);padding:var(--space-xl)}}footer{{border-top:1px solid var(--color-rule);margin-top:var(--space-2xl);padding:var(--space-md) 0;color:var(--color-muted);font:.75rem var(--font-mono);display:flex;justify-content:space-between;gap:var(--space-md)}}@media(max-width:900px){{.hero,.grid{{grid-template-columns:1fr}}.stats,.method{{grid-template-columns:repeat(2,minmax(0,1fr))}}.stat:nth-child(2){{border-right:0}}.stat:nth-child(-n+2){{border-bottom:1px solid var(--color-rule)}}.opportunity{{grid-template-columns:1fr}}}}@media(max-width:640px){{.shell{{padding:var(--space-md)}}.topbar{{align-items:flex-start;flex-direction:column}}h1{{font-size:2.4rem}}.stats,.method{{grid-template-columns:1fr}}.stat{{border-right:0;border-bottom:1px solid var(--color-rule)}}.stat:last-child{{border-bottom:0}}.opportunity,td,th{{padding:var(--space-sm)}}dl{{gap:var(--space-sm);flex-wrap:wrap}}table{{min-width:680px}}.panel{{overflow-x:auto}}}}@media(prefers-reduced-motion:reduce){{*,*::before,*::after{{transition-duration:150ms!important;animation-duration:150ms!important}}}}
</style></head><body><main class=\"shell\"><header class=\"topbar\"><span class=\"brand\">目标品牌 / GEO 监测</span><span class=\"meta\">Step 5 · snapshot {text(coverage.get('snapshot_id'))}</span></header><section class=\"hero\"><div><p class=\"eyebrow\">竞品差距工作台 · 本次运行</p><h1>本期可确认的事实</h1></div><p class=\"lede\">本页只展示 <b>已登记竞品</b>、<b>可比观察</b> 和 <b>可回放证据</b>。提及、正式推荐位次、已验证引用分别统计；无法判定不等于零。</p></section><section class=\"stats\"><div class=\"stat\"><b>{fact_by_type['mention_gap']}</b><span>正文提及缺口</span></div><div class=\"stat\"><b>{fact_by_type['verified_citation_gap']}</b><span>已验证引用缺口</span></div><div class=\"stat\"><b>{fact_by_type['formal_rank_gap']}</b><span>正式推荐排名结论</span></div><div class=\"stat\"><b>{citation_unassessable}</b><span>引用不可评估</span></div></section><aside class=\"notice\"><b>怎么读这页</b>“缺口”表示本次合格观察中发生的事实；它不代表长期结论，也不解释 AI 为什么这样回答。优先查看下方的题目、平台与证据，再进入 Step 6。</aside><section class=\"sources\"><h2>平台观察概览</h2><div class=\"panel\"><table><thead><tr><th>AI 平台</th><th>有效观察</th><th>提及目标品牌</th><th>完成来源审计</th><th>目标品牌官网可见引用</th><th>可见来源卡</th><th>来源证据受限</th><th>正式推荐排名可判定</th></tr></thead><tbody>{platform_rows}</tbody></table></div></section><div class=\"grid\"><section><h2>优先处理的观察</h2><div class=\"panel\">{opportunity_rows}</div><h2 style=\"margin-top:var(--space-2xl)\">证据明细</h2><div class=\"panel\"><div class=\"filters\"><button class=\"filter\" data-filter=\"all\" aria-pressed=\"true\">全部 {fact_total}</button><button class=\"filter\" data-filter=\"mention_gap\" aria-pressed=\"false\">提及</button><button class=\"filter\" data-filter=\"verified_citation_gap\" aria-pressed=\"false\">引用</button><button class=\"filter\" data-filter=\"formal_rank_gap\" aria-pressed=\"false\">正式排名</button></div><table><thead><tr><th>信号</th><th>问题</th><th>平台</th><th>登记竞品</th><th>本次观察到什么</th></tr></thead><tbody>{fact_rows}</tbody></table></div></section><aside class=\"side\"><section><h2>指标分母</h2><div class=\"panel\"><ul class=\"metric-list\"><li><span>提及：可评估</span><b>{mention.get('eligible_observation_count', 0)}</b></li><li><span>正式推荐排名：可评估</span><b>{rank.get('eligible_observation_count', 0)}</b></li><li><span>引用：可评估</span><b>{citation.get('eligible_observation_count', 0)}</b></li><li><span>引用：不可评估</span><b>{citation_unassessable}</b></li></ul></div></section><section><h2>不可评估 / 待复核</h2><div class=\"panel\"><ul class=\"metric-list\">{review_rows}</ul></div></section></aside></div><section class=\"sources\"><h2>可见引用来源</h2><div class=\"panel\"><table><thead><tr><th>来源平台</th><th>来源页</th><th>归属</th><th>问题</th><th>AI 平台</th></tr></thead><tbody>{source_rows}</tbody></table></div></section><details><summary>口径与限制</summary><div class=\"method\"><div><b>提及</b>仅分析 assistant-answer 正文；用户题目回显、导航和仅来源卡的品牌字样不计入。</div><div><b>正式推荐位次</b>只接受明确整体推荐列表或表格；正文先后顺序和局部小节不构成排名。</div><div><b>已验证引用</b>必须同时有 AI 可见 URL 与锚文本/答案片段；任一缺失即保持不可评估。</div><div><b>竞品边界</b>只计算 Step 1 已登记品牌及其官网域名；第三方媒体不自动成为竞品。</div></div></details><footer><span>数据源：Step 4 已验证、可比观察；详情见同目录 CSV / JSONL。</span><span>run {text(coverage.get('run_id'))}</span></footer></main><script>document.querySelectorAll('.filter').forEach(button=>button.addEventListener('click',()=>{{const f=button.dataset.filter;document.querySelectorAll('.filter').forEach(x=>x.setAttribute('aria-pressed',String(x===button)));document.querySelectorAll('.fact-row').forEach(row=>row.hidden=f!=='all'&&row.dataset.signal!==f)}}));</script></body></html>"""
    (output_dir / "05_竞品差距与内容机会.html").write_text(html_text, encoding="utf-8")


def build_step5_package(
    step4_dir: Path,
    run_dir: Path,
    registry: Sequence[Mapping[str, Any]],
    output_dir: Path | None = None,
    project_profile: Mapping[str, Any] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Build evidence-bounded Step 5 facts from real Step 4 observations.

    A competitor is considered only when it is declared in ``registry``.
    Partial/unavailable citation evidence creates a review record, never a zero
    or an inferred citation gap.
    """

    output_dir = output_dir or run_dir / "step5"
    output_dir.mkdir(parents=True, exist_ok=True)
    project_profile = project_profile or {}
    manifest_path = run_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    questions_by_id = {
        str(item.get("question_id")): item
        for item in manifest.get("planned_questions", [])
        if item.get("question_id")
    }
    target_scope = project_profile.get("target_scope", {})
    target_domains = [str(item) for item in target_scope.get("official_domains", [])]
    target_aliases = [str(target_scope.get("canonical_brand", "")), *[str(item) for item in target_scope.get("aliases", [])]]
    registry_hash = hashlib.sha256(_json_value(sorted([
        {"competitor_id": item.get("competitor_id"), "domains": sorted(_registered_domains(item))}
        for item in registry
    ], key=lambda item: str(item["competitor_id"]))).encode("utf-8")).hexdigest()
    lineage = {
        "run_id": manifest.get("run_id"), "project_id": manifest.get("project_id") or project_profile.get("project_id"),
        "profile_version": project_profile.get("profile_version"), "project_profile_sha256": manifest.get("project_profile_sha256"),
        "question_catalog_version": manifest.get("question_catalog_version"), "question_set_version": manifest.get("question_set_version"),
        "competitor_catalog_version": str(project_profile.get("competitor_catalog_version") or f"registry-sha256:{registry_hash}"),
        "collection_mode": manifest.get("collection_mode"), "signal_definition_version": "step5-v2",
    }
    observations = _read_jsonl(step4_dir / "normalized_observations.jsonl")
    decisions = {str(item.get("observation_id")): item for item in _read_jsonl(step4_dir / "signal_decisions.jsonl")}
    facts: list[dict[str, Any]] = []
    opportunities: list[dict[str, Any]] = []
    manual_review: list[dict[str, Any]] = []
    source_topology: list[dict[str, Any]] = []
    candidate_reviews: list[dict[str, Any]] = []
    coverage: dict[str, dict[str, Any]] = {
        "brand_mention": {"definition": "目标品牌未在正文出现且已登记竞品出现", "eligible_observation_count": 0, "excluded_observation_count": 0, "fact_count": 0},
        "formal_rank": {"definition": "明确整体推荐列表/表格中的显示位置", "eligible_observation_count": 0, "excluded_observation_count": 0, "fact_count": 0},
        "verified_citation": {"definition": "可见 URL 与锚文本/答案片段同时存在", "eligible_observation_count": 0, "excluded_observation_count": 0, "fact_count": 0},
        "third_party_channel": {"definition": "已验证第三方来源对登记竞品的覆盖", "eligible_observation_count": 0, "excluded_observation_count": 0, "fact_count": 0},
    }

    for observation in observations:
        observation_id = str(observation["observation_id"])
        raw_observation_path = run_dir / "raw" / "observations" / observation_id / "observation.json"
        raw_observation = json.loads(raw_observation_path.read_text(encoding="utf-8")) if raw_observation_path.exists() else {}
        question = questions_by_id.get(str(observation.get("question_id")), {})
        base = {**lineage, "observation_id": observation_id, "evidence_id": observation.get("evidence_id"), "question_id": observation.get("question_id"), "question_text": question.get("exact_question_text"), "question_revision_id": observation.get("question_revision_id"), "platform": observation.get("platform"), "parser_version": raw_observation.get("parser_version"), "adapter_version": raw_observation.get("adapter_version"), "rule_version": raw_observation.get("rule_version"), "artifact_reference": _artifact_reference(run_dir, observation_id)}
        answer_path = run_dir / "raw" / "observations" / observation_id / "answer.txt"
        if not answer_path.exists() or not observation.get("comparable"):
            reason = "raw_answer_missing" if not answer_path.exists() else "not_comparable"
            manual_review.append({**base, "reason": reason, "metric": "all", "eligibility_state": "not_evaluable", "limitation": json.dumps(observation.get("non_comparable_reasons", []), ensure_ascii=False)})
            for metric in coverage.values():
                metric["excluded_observation_count"] += 1
            continue
        answer = answer_path.read_text(encoding="utf-8")
        registered_aliases = {
            *target_aliases,
            *(str(item.get("name") or "") for item in registry),
            *(str(alias) for item in registry for alias in item.get("aliases", [])),
        }
        candidate_reviews.extend(extract_candidate_competitor_review(answer, registered_aliases, base))
        if observation.get("mention_eligible"):
            coverage["brand_mention"]["eligible_observation_count"] += 1
            if not observation.get("brand_mention"):
                for competitor in registry:
                    spans = _mention_spans(answer, [str(competitor.get("name", "")), *[str(item) for item in competitor.get("aliases", [])]])
                    if spans:
                        facts.append({**base, "opportunity_key": str(observation["question_id"]), "competitor_id": competitor["competitor_id"], "competitor_name": competitor.get("name"), "signal_type": "brand_mention", "gap_type": "mention_gap", "target_value": False, "competitor_value": True, "competitor_mention_spans": spans, "business_importance": observation.get("business_importance"), "primary_intent": observation.get("primary_intent"), "status": "observed_single_sample", "eligible_for_comparison": True, "citation_status": observation.get("citation_status"), "limitation": "单次可比观察；不构成推荐、排名、引用或因果结论。"})
        else:
            coverage["brand_mention"]["excluded_observation_count"] += 1

        aliases = {"target": target_aliases, **{str(item["competitor_id"]): [str(item.get("name", "")), *[str(alias) for alias in item.get("aliases", [])]] for item in registry}}
        ranks = _explicit_overall_ranks(answer, aliases)
        rank_review: dict[str, Any] | None = None
        if ranks:
            coverage["formal_rank"]["eligible_observation_count"] += 1
            target_rank = ranks.get("target", {}).get("rank", "unranked")
            for competitor in registry:
                rank = ranks.get(str(competitor["competitor_id"]))
                if rank and (target_rank == "unranked" or target_rank > rank["rank"]):
                    facts.append({**base, "opportunity_key": str(observation["question_id"]), "competitor_id": competitor["competitor_id"], "competitor_name": competitor.get("name"), "signal_type": "formal_rank", "gap_type": "formal_rank_gap", "target_rank": target_rank, "competitor_rank": rank["rank"], "rank_delta": None if target_rank == "unranked" else target_rank - rank["rank"], "rank_evidence": rank, "business_importance": observation.get("business_importance"), "primary_intent": observation.get("primary_intent"), "status": "observed_single_sample", "eligible_for_comparison": True, "citation_status": observation.get("citation_status"), "limitation": "排名只代表明确整体推荐列表的显示位置。"})
        else:
            coverage["formal_rank"]["excluded_observation_count"] += 1
            rank_review = {**base, "reason": "formal_rank_not_observable", "metric": "formal_rank_gap", "eligibility_state": "not_evaluable", "limitation": "未观察到明确整体推荐列表或表格；不得从正文顺序推断排名。"}

        citations = ((decisions.get(observation_id, {}).get("citation") or {}).get("verified_citations") or [])
        if observation.get("citation_status") == "verified" and observation.get("citation_eligible"):
            coverage["verified_citation"]["eligible_observation_count"] += 1
            topology_rows: list[dict[str, Any]] = []
            for index, citation in enumerate(citations, 1):
                url = str(citation.get("visible_url") or citation.get("url") or "")
                anchor = str(citation.get("visible_anchor_text") or citation.get("anchor_or_span") or "")
                if not url or not anchor:
                    continue
                ownership, owner_id = _domain_owner(url, target_domains, registry)
                topology_rows.append({**base, "source_domain": urlparse(url).netloc.casefold().removeprefix("www."), "source_publisher": citation.get("visible_domain_text") or None, "url": url, "type": "visible_citation", "ownership": ownership, "competitor_id": owner_id, "anchor_or_span": anchor, "access_state": "visible_verified", "citation_path_depth": "unavailable", "source_card_index": index})
            source_topology.extend(topology_rows)
            target_cited = any(row["ownership"] == "target" for row in topology_rows)
            for row in topology_rows:
                if row["ownership"] == "verified_competitor" and row["competitor_id"] and not target_cited:
                    competitor = next(item for item in registry if str(item["competitor_id"]) == row["competitor_id"])
                    facts.append({**base, "opportunity_key": str(observation["question_id"]), "competitor_id": row["competitor_id"], "competitor_name": competitor.get("name"), "signal_type": "verified_citation", "gap_type": "verified_citation_gap", "target_verified_citation": False, "competitor_verified_citation": True, "citation": row, "citation_status": "verified", "business_importance": observation.get("business_importance"), "primary_intent": observation.get("primary_intent"), "status": "observed_single_sample", "eligible_for_comparison": True, "limitation": "本次完成的可见来源审计未观察到目标方已验证引用；不代表从不被引用。"})
                if row["ownership"] == "third_party":
                    for competitor in registry:
                        spans = _mention_spans(str(row["anchor_or_span"]), [str(competitor.get("name", "")), *[str(item) for item in competitor.get("aliases", [])]])
                        if spans:
                            facts.append({**base, "opportunity_key": str(observation["question_id"]), "competitor_id": competitor["competitor_id"], "competitor_name": competitor.get("name"), "signal_type": "third_party_channel", "gap_type": "third_party_channel_gap", "third_party_citation": row, "competitor_mention_spans": spans, "citation_status": "verified", "business_importance": observation.get("business_importance"), "primary_intent": observation.get("primary_intent"), "status": "observed_single_sample", "eligible_for_comparison": True, "limitation": "仅表示同一样本中第三方来源对登记竞品的可追溯覆盖，不推断页面应写什么。"})
        else:
            coverage["verified_citation"]["excluded_observation_count"] += 1
            manual_review.append({**base, "reason": f"citation_not_assessable_{observation.get('citation_status', 'unknown')}", "metric": "verified_citation_gap", "eligibility_state": "not_evaluable", "limitation": "partial/unavailable/rejected 不能写成未引用或零值。"})
        if rank_review:
            manual_review.append(rank_review)

    for fact in facts:
        metric = "third_party_channel" if fact["gap_type"] == "third_party_channel_gap" else fact["signal_type"]
        coverage[metric]["fact_count"] += 1
        fact["metric_eligible"] = True
    coverage["third_party_channel"]["eligible_observation_count"] = coverage["verified_citation"]["eligible_observation_count"]
    coverage["third_party_channel"]["excluded_observation_count"] = coverage["verified_citation"]["excluded_observation_count"]
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for fact in facts:
        grouped[str(fact["question_id"])].append(fact)
    for question_id, group in sorted(grouped.items()):
        representative = group[0]
        observation_ids = sorted({str(item["observation_id"]) for item in group})
        priority = "P0" if representative.get("business_importance") == "high" and len(observation_ids) >= 2 else ("P1" if representative.get("business_importance") == "high" or len(observation_ids) >= 2 else "P2")
        opportunities.append({**lineage, "opportunity_key": question_id, "question_id": question_id, "question_text": questions_by_id.get(question_id, {}).get("exact_question_text"), "gap_types": sorted({str(item["gap_type"]) for item in group}), "signal_types": sorted({str(item["signal_type"]) for item in group}), "business_importance": representative.get("business_importance"), "primary_intent": representative.get("primary_intent"), "intent": representative.get("primary_intent"), "competitor_ids": sorted({str(item["competitor_id"]) for item in group}), "competitor_names": sorted({str(item.get("competitor_name") or item["competitor_id"]) for item in group}), "platforms": sorted({str(item["platform"]) for item in group}), "observation_ids": observation_ids, "evidence_ids": sorted({str(item["evidence_id"]) for item in group}), "valid": True, "eligible_for_comparison": True, "verified_observation_count": len(observation_ids), "priority": priority, "opportunity_tier": "A_competitive_gap", "opportunity_basis": "竞争差距：同一可比回答中已观察到已登记竞品的有效信号。", "step6_eligibility": "eligible", "step6_handoff": "eligible_for_original_structure_brief", "limitation": "单次可比观察；不作因果归因。" if len(observation_ids) == 1 else "重复可比观察；不作因果归因。"})

    # A competitor fact is stronger evidence than a citation whitespace signal.
    # For remaining questions, verified visible citations without a target-domain
    # citation are useful content-planning evidence, but never a competitor claim.
    competitive_questions = set(grouped)
    whitespace_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in observations:
        question_id = str(observation.get("question_id") or "")
        if not question_id or question_id in competitive_questions:
            continue
        if not (observation.get("comparable") and observation.get("citation_eligible") and observation.get("citation_status") == "verified"):
            continue
        citations = ((decisions.get(str(observation.get("observation_id")), {}).get("citation") or {}).get("verified_citations") or [])
        visible_citations = [
            item for item in citations
            if str(item.get("visible_url") or item.get("url") or "").startswith(("http://", "https://"))
            and str(item.get("visible_anchor_text") or item.get("anchor_or_span") or "")
        ]
        if not visible_citations:
            continue
        target_cited = any(
            _domain_owner(str(item.get("visible_url") or item.get("url") or ""), target_domains, registry)[0] == "target"
            for item in visible_citations
        )
        if not target_cited:
            whitespace_groups[question_id].append(observation)
    for question_id, group in sorted(whitespace_groups.items()):
        representative = group[0]
        observation_ids = sorted({str(item["observation_id"]) for item in group})
        priority = "P0" if representative.get("business_importance") == "high" and len(observation_ids) >= 2 else ("P1" if representative.get("business_importance") == "high" or len(observation_ids) >= 2 else "P2")
        opportunities.append({
            **lineage, "opportunity_key": question_id, "question_id": question_id,
            "question_text": questions_by_id.get(question_id, {}).get("exact_question_text"),
            "gap_types": ["citation_whitespace"], "signal_types": ["verified_citation"],
            "business_importance": representative.get("business_importance"),
            "primary_intent": representative.get("primary_intent"), "intent": representative.get("primary_intent"),
            "competitor_ids": [], "competitor_names": [],
            "platforms": sorted({str(item["platform"]) for item in group}),
            "observation_ids": observation_ids,
            "evidence_ids": sorted({str(item["evidence_id"]) for item in group}),
            "valid": True, "eligible_for_comparison": True,
            "verified_observation_count": len(observation_ids), "priority": priority,
            "opportunity_tier": "B_citation_whitespace",
            "opportunity_basis": "引用空位：本次完成的可见来源审计存在外部来源，未观察到目标官方域引用。",
            "step6_eligibility": "eligible", "step6_handoff": "eligible_for_original_structure_brief",
            "limitation": "不代表竞品领先、市场份额或来源页应写什么；不作因果归因。",
        })

    for review in manual_review:
        review.setdefault("opportunity_tier", "C_review_required")
        review.setdefault("step6_eligibility", "manual_review")

    facts_by_observation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sources_by_observation: dict[str, list[dict[str, Any]]] = defaultdict(list)
    opportunities_by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in facts:
        facts_by_observation[str(item.get("observation_id"))].append(item)
    for item in source_topology:
        sources_by_observation[str(item.get("observation_id"))].append(item)
    for item in opportunities:
        opportunities_by_question[str(item.get("question_id"))].append(item)
    workbench: list[dict[str, Any]] = []
    for observation in observations:
        observation_id = str(observation.get("observation_id") or "")
        question = questions_by_id.get(str(observation.get("question_id") or ""), {})
        answer_path = run_dir / "raw" / "observations" / observation_id / "answer.txt"
        answer_text = answer_path.read_text(encoding="utf-8") if answer_path.exists() else None
        answer = answer_text or ""
        aliases = {
            "target": target_aliases,
            **{
                str(item["competitor_id"]): [str(item.get("name", "")), *[str(alias) for alias in item.get("aliases", [])]]
                for item in registry
            },
        }
        ranks = _explicit_overall_ranks(answer, aliases) if answer else {}
        sources = sources_by_observation.get(observation_id, [])
        citation_eligible = bool(observation.get("citation_eligible") and observation.get("citation_status") == "verified")
        evidence_state = "完整可回放" if answer_text and observation.get("comparable") and citation_eligible else (
            "回答可回放，引用不可评估" if answer_text and observation.get("comparable") else "不可评估/待复核"
        )
        workbench.append({
            **lineage,
            "observation_id": observation_id,
            "evidence_id": observation.get("evidence_id"),
            "question_id": observation.get("question_id"),
            "question_text": question.get("exact_question_text"),
            "question_group": question.get("question_group", "未标注"),
            "growth_trigger": bool(question.get("growth_trigger")),
            "platform": observation.get("platform"),
            "business_importance": observation.get("business_importance") or question.get("business_importance"),
            "answer_text": answer_text,
            "answer_artifact_reference": _artifact_reference(run_dir, observation_id),
            "target_brand_mention": bool(observation.get("brand_mention")),
            "mention_eligible": bool(observation.get("mention_eligible")),
            "target_recommendation_rank": ranks.get("target", {}).get("rank", "unranked"),
            "rank_observable": bool(ranks),
            "target_official_citation": any(item.get("ownership") == "target" for item in sources),
            "citation_eligible": citation_eligible,
            "verified_source_count": len(sources),
            "verified_sources": sources,
            "registered_competitor_facts": facts_by_observation.get(observation_id, []),
            "content_opportunity_keys": [item.get("opportunity_key") for item in opportunities_by_question.get(str(observation.get("question_id")), [])],
            "evidence_state": evidence_state,
            "limitation": "不可比：" + "、".join(observation.get("non_comparable_reasons", [])) if not observation.get("comparable") else (
                "引用证据未达到 verified；不可填零。" if not citation_eligible else "单次观察；不作因果归因。"
            ),
        })

    platform_counts: dict[str, dict[str, Any]] = {}
    for observation in observations:
        if not observation.get("comparable"):
            continue
        platform = str(observation.get("platform") or "unknown")
        row = platform_counts.setdefault(platform, {
            "platform": platform, "observation_count": 0, "mention_eligible_count": 0,
            "target_mention_count": 0, "source_audited_observation_count": 0,
            "target_official_citation_observation_count": 0,
            "visible_verified_source_card_count": 0,
            "citation_limited_count": 0, "formal_rank_observable_count": 0,
        })
        row["observation_count"] += 1
        if observation.get("mention_eligible"):
            row["mention_eligible_count"] += 1
            row["target_mention_count"] += int(bool(observation.get("brand_mention")))
        if observation.get("citation_status") == "verified" and observation.get("citation_eligible"):
            row["source_audited_observation_count"] += 1
            verified_citations = ((decisions.get(str(observation.get("observation_id")), {}).get("citation") or {}).get("verified_citations") or [])
            target_cited = False
            for item in verified_citations:
                url = str(item.get("visible_url") or item.get("url") or "")
                anchor = str(item.get("visible_anchor_text") or item.get("anchor_or_span") or "")
                if not url or not anchor:
                    continue
                row["visible_verified_source_card_count"] += 1
                ownership, _ = _domain_owner(url, target_domains, registry)
                target_cited = target_cited or ownership == "target"
            row["target_official_citation_observation_count"] += int(target_cited)
        elif observation.get("citation_status") in {"partial", "unavailable", "rejected"}:
            row["citation_limited_count"] += 1
        row["formal_rank_observable_count"] += int(bool(observation.get("rank_eligible")))
    platform_coverage = [platform_counts[name] for name in sorted(platform_counts)]

    snapshot_seed = _json_value({"lineage": lineage, "facts": facts, "reviews": manual_review, "topology": source_topology, "candidates": candidate_reviews, "workbench": workbench})
    snapshot_id = f"step5-{lineage.get('run_id') or 'run'}-{hashlib.sha256(snapshot_seed.encode('utf-8')).hexdigest()[:12]}"
    for record in [*facts, *opportunities, *manual_review, *source_topology, *candidate_reviews, *workbench]:
        record["snapshot_id"] = snapshot_id
    metric_coverage = {**lineage, "snapshot_id": snapshot_id, "observation_count": len(observations), "metric_families": coverage, "inclusion_rule": "仅 contract-valid、comparable 且对应信号 eligible 的观察进入指标；不可用证据不写作零值。"}
    fact_fields = ("snapshot_id", "project_id", "run_id", "question_catalog_version", "question_set_version", "competitor_catalog_version", "collection_mode", "opportunity_key", "observation_id", "evidence_id", "question_id", "question_text", "question_revision_id", "platform", "parser_version", "adapter_version", "rule_version", "competitor_id", "competitor_name", "signal_type", "gap_type", "target_value", "competitor_value", "target_rank", "competitor_rank", "rank_delta", "citation_status", "competitor_mention_spans", "rank_evidence", "citation", "third_party_citation", "business_importance", "primary_intent", "status", "metric_eligible", "eligible_for_comparison", "signal_definition_version", "artifact_reference", "limitation")
    opportunity_fields = ("snapshot_id", "project_id", "run_id", "question_catalog_version", "question_set_version", "competitor_catalog_version", "collection_mode", "opportunity_key", "question_id", "question_text", "gap_types", "signal_types", "business_importance", "primary_intent", "intent", "competitor_ids", "competitor_names", "platforms", "observation_ids", "evidence_ids", "valid", "eligible_for_comparison", "signal_definition_version", "verified_observation_count", "priority", "opportunity_tier", "opportunity_basis", "step6_eligibility", "step6_handoff", "limitation")
    candidate_fields = ("snapshot_id", "project_id", "run_id", "question_id", "question_text", "platform", "observation_id", "evidence_id", "candidate_name_raw", "candidate_aliases_suggested", "candidate_type", "status", "manual_review_required", "answer_span", "line_number", "reason")
    workbench_fields = ("snapshot_id", "project_id", "run_id", "question_id", "question_text", "question_group", "growth_trigger", "platform", "observation_id", "evidence_id", "business_importance", "target_brand_mention", "mention_eligible", "target_recommendation_rank", "rank_observable", "target_official_citation", "citation_eligible", "verified_source_count", "evidence_state", "answer_artifact_reference", "limitation")
    _write_csv(output_dir / "05_竞品差距_事实表.csv", facts, fact_fields)
    _write_csv(output_dir / "05_引用来源拓扑_已验证表.csv", source_topology, ("snapshot_id", "project_id", "run_id", "parser_version", "adapter_version", "rule_version", "source_domain", "source_publisher", "url", "type", "ownership", "competitor_id", "platform", "question_id", "observation_id", "evidence_id", "anchor_or_span", "access_state", "citation_path_depth"))
    _write_csv(output_dir / "05_内容机会候选表.csv", opportunities, opportunity_fields)
    _write_csv(output_dir / "05_候选竞品复核表.csv", candidate_reviews, candidate_fields)
    _write_csv(output_dir / "05_问题工作台.csv", workbench, workbench_fields)
    _write_csv(output_dir / "05_平台观察覆盖表.csv", platform_coverage, ("platform", "observation_count", "mention_eligible_count", "target_mention_count", "source_audited_observation_count", "target_official_citation_observation_count", "visible_verified_source_card_count", "citation_limited_count", "formal_rank_observable_count"))
    _write_csv(output_dir / "05_不可评估与人工复核表.csv", manual_review, ("snapshot_id", "project_id", "run_id", "question_id", "question_revision_id", "platform", "observation_id", "parser_version", "adapter_version", "rule_version", "reason", "metric", "eligibility_state", "opportunity_tier", "step6_eligibility", "evidence_id", "artifact_reference", "limitation"))
    for filename, records in {"competitor_facts.jsonl": facts, "content_opportunities.jsonl": opportunities, "manual_review.jsonl": manual_review, "verified_source_topology.jsonl": source_topology, "candidate_competitor_review.jsonl": candidate_reviews, "05_问题工作台.jsonl": workbench}.items():
        (output_dir / filename).write_text("".join(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in records), encoding="utf-8")
    snapshot = {**lineage, "snapshot_id": snapshot_id, "observation_count": len(observations), "competitor_facts": facts, "opportunities": opportunities, "manual_review": manual_review, "source_topology": source_topology, "candidate_competitor_review": candidate_reviews, "question_workbench_file": "05_问题工作台.jsonl", "metric_coverage_file": "05_指标口径与覆盖.json"}
    (output_dir / "step5_snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "05_指标口径与覆盖.json").write_text(json.dumps(metric_coverage, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    report = "\n".join(("# 05 竞品差距与内容机会", "", f"- 问题工作台：{len(workbench)} 条已接纳观察。", f"- 可评估竞品事实：{len(facts)} 条。", f"- 内容机会候选：{len(opportunities)} 条；仅交接 Step 6。", f"- 已验证引用来源拓扑：{len(source_topology)} 条。", f"- 候选竞品复核：{len(candidate_reviews)} 条（不进入正式竞品指标）。", f"- 不可评估/人工复核：{len(manual_review)} 条。", "- 未登记品牌、第三方来源域、隐藏 DOM URL 不进入竞品指标。", "- 未观察到正式推荐列表或已验证可见引用时，对应指标保持不可评估，不填零。")) + "\n"
    (output_dir / "05_竞品差距与内容机会.md").write_text(report, encoding="utf-8")
    _render_growth_workbench(
        output_dir, workbench, candidate_reviews, opportunities, metric_coverage,
        str(target_scope.get("canonical_brand") or "目标品牌"),
    )
    return {"competitor_facts": facts, "opportunities": opportunities, "manual_review": manual_review, "source_topology": source_topology, "candidate_competitor_review": candidate_reviews, "question_workbench": workbench, "platform_coverage": platform_coverage}
