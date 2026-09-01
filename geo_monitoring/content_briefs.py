"""Step 6: evidence-bounded, per-opportunity content planning skeletons."""

from __future__ import annotations

import csv
import html
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from .project_questions import validate_project_profile, validate_question_catalog


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]], fields: Sequence[str]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else value
                for key, value in row.items()
            })


def _platforms(sources: Sequence[Mapping[str, Any]]) -> list[str]:
    """Use only concrete, verified cited-source platforms, capped deterministically."""

    scores: dict[str, tuple[int, int]] = defaultdict(lambda: (0, 0))
    for source in sources:
        if not source.get("verified") or source.get("ownership") not in {"target", "third_party"}:
            continue
        name = source.get("platform")
        if not name:
            continue
        current = scores[str(name)]
        scores[str(name)] = (
            current[0] + int(source.get("cited_source_page_count", 1)),
            current[1] + int(source.get("covered_question_count", 1)),
        )
    return [name for name, _ in sorted(scores.items(), key=lambda item: (-item[1][0], -item[1][1], item[0]))[:5]]


def _content_shape(intent: str, question_text: str, observed_structure: Sequence[str] = ()) -> tuple[str, list[str]]:
    """Return only an original title direction and H2 headings, never source prose."""

    question = question_text.rstrip("？?") or "目标问题"
    by_intent = {
        "understand": ("定义与服务说明", ["该问题涉及的概念与业务边界", "适用对象与典型使用场景", "服务能力应如何理解", "选择前需要确认的信息", "常见问题"]),
        "discover": ("选型指南", ["业务场景与决策目标", "可选方案的适用边界", "关键评估维度", "按团队情况完成选择", "上线前核验清单"]),
        "solve": ("问题解决指南", ["问题现象与适用边界", "排查前需要确认的信息", "解决路径与配置步骤", "验证稳定性与合规性的检查项", "常见问题"]),
        "evaluate": ("对比与评估指南", ["业务需求与决策边界", "方案对比的核心维度", "不同场景下的选择原则", "落地前的验证清单", "常见问题"]),
        "transact": ("采购准备指南", ["采购需求与使用边界", "服务方案的评估维度", "询价与验收前需确认的信息", "上线与交付核验清单", "常见问题"]),
    }
    content_type, headings = by_intent.get(intent, by_intent["understand"])
    headings = list(headings)
    # Only normalized component categories influence the proposed structure.
    # No source title, sentence, table value, or CTA is reproduced here.
    if "选项比较" in observed_structure:
        headings.insert(-1, "可选方案与关键差异对比表")
    if "步骤" in observed_structure:
        headings.insert(-1, "实施步骤与上线验证")
    if "图示" in observed_structure:
        headings.insert(-1, "用决策路径图呈现选型流程")
    return content_type, [f"{question}：{content_type}", *headings]


def _normalize_gap_reason(opportunity: Mapping[str, Any]) -> tuple[str, str]:
    """Describe observed signal states without claiming why an AI made a choice."""

    if opportunity.get("opportunity_tier") == "B_citation_whitespace":
        return (
            "verified_citation_whitespace_target_not_observed",
            "引用空位：本次完成的可见来源审计存在外部来源，未观察到目标官方域引用；不代表竞品领先或因果关系。",
        )
    gap_types = set(opportunity.get("gap_types") or ([opportunity["gap_type"]] if opportunity.get("gap_type") else []))
    if "verified_citation_gap" in gap_types:
        return "verified_competitor_citation_target_not_observed", "同一可比回答的已验证可见来源中观察到登记竞品引用，未观察到目标官方域引用。"
    if "formal_rank_gap" in gap_types:
        return "explicit_overall_list_competitor_ahead", "同一可比回答的明确整体推荐列表中，登记竞品显示位置领先目标方或目标方未入列。"
    if "third_party_channel_gap" in gap_types:
        return "third_party_source_mentions_registered_competitor", "同一可比回答的已验证第三方来源中出现登记竞品的可追溯覆盖。"
    if "mention_gap" in gap_types:
        return "registered_competitor_mentioned_target_not_mentioned", "同一可比回答正文提及登记竞品，未提及目标品牌。"
    return "signal_gap_needs_manual_review", "机会信号无法归入既定类别，需人工复核；不作原因或因果推断。"


def create_brief(
    opportunity: Mapping[str, Any], question: Mapping[str, Any], sources: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Create one H2-only brief without copying or claiming source-page content."""

    if not opportunity.get("valid", True) or not opportunity.get("intent"):
        return {
            "opportunity_key": opportunity.get("opportunity_key"), "status": "manual_review",
            "h2_structure": [], "human_verification_required": True,
        }
    question_text = str(question.get("exact_question_text", "目标问题"))
    intent = str(opportunity.get("intent") or opportunity.get("primary_intent"))
    accessible_direct = [
        source for source in sources
        if source.get("access_state") == "accessible" and source.get("direct")
        and source.get("capture_id") and source.get("observed_structure")
    ]
    if accessible_direct:
        source_status = "已核验可访问来源页"
        source_evidence_state = "verified_source_page_captured"
        structure_basis = "observed_structure_and_intent"
        confidence = "limited"
        action = "先在官网撰写原创选型指南；加入对比表与上线核验清单；逐一核验列示第三方平台的投稿/发布入口后再排期；人工核验所有我方事实。"
        observed_structure = sorted({
            str(component)
            for source in accessible_direct for component in source.get("observed_structure", [])
            if component
        })
    elif sources:
        source_status = "来源页待人工核验"
        source_evidence_state = "verified_source_page_unreadable"
        structure_basis = "intent_and_gap_only"
        confidence = "limited"
        action = "人工核验来源页；补充并核验我方事实素材；再撰写原创内容骨架。"
        observed_structure = "unknown"
    else:
        source_status = "无已验证来源页（需补采集）"
        source_evidence_state = "no_verified_source_page"
        structure_basis = "intent_and_gap_only"
        confidence = "limited"
        action = "补采集本问题的可见来源卡证据（URL + 锚文本/答案片段）；人工核验来源页；补充并核验我方事实素材；再撰写原创内容骨架。"
        observed_structure = "unknown"
    content_type, outline = _content_shape(
        intent, question_text,
        observed_structure if isinstance(observed_structure, list) else (),
    )
    importance = opportunity.get("business_importance", "medium")
    repeated = int(opportunity.get("verified_observation_count", 1)) >= 2
    priority = "P0" if importance == "high" and repeated else ("P1" if importance == "high" or repeated else "P2")
    normalized_reason_code, normalized_reason = _normalize_gap_reason(opportunity)
    opportunity_tier = str(opportunity.get("opportunity_tier") or "A_competitive_gap")
    opportunity_basis = str(opportunity.get("opportunity_basis") or (
        "引用空位：本次完成的可见来源审计存在外部来源，未观察到目标官方域引用。"
        if opportunity_tier == "B_citation_whitespace"
        else "竞争差距：同一可比回答中已观察到已登记竞品的有效信号。"
    ))
    return {
        "opportunity_key": opportunity["opportunity_key"],
        "question_id": opportunity.get("question_id"),
        "question": question_text,
        "main_title": outline[0],
        "h2_structure": outline[1:],
        "content_type": content_type,
        "priority": priority,
        "publication_platforms": _platforms(sources),
        "action_advice": action,
        "source_page_verification_status": source_status,
        "source_evidence_state": source_evidence_state,
        "normalized_reason_code": normalized_reason_code,
        "normalized_reason": normalized_reason,
        "opportunity_tier": opportunity_tier,
        "opportunity_basis": opportunity_basis,
        "structure_basis": structure_basis,
        "confidence": confidence,
        "observed_structure": observed_structure,
        "status": "ready_for_human_content_planning",
        "human_verification_required": True,
    }


def audit_step1_to_step5(
    run_dir: Path,
    project_profile: Mapping[str, Any],
    step2_manifest: Mapping[str, Any],
    step2_receipt: Mapping[str, Any] | None = None,
    step1_receipt: Mapping[str, Any] | None = None,
    step2_catalog: Mapping[str, Any] | None = None,
    step2_source_manifest: Mapping[str, Any] | None = None,
    step2_validation_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate only handoff identities and files; do not reinterpret observations."""

    errors: list[str] = []
    limitations: list[str] = []
    manifest_path = run_dir / "manifest.json"
    step4_summary_path = run_dir / "step4" / "initial_pattern_summary.json"
    snapshot_path = run_dir / "step5" / "step5_snapshot.json"
    if not manifest_path.exists() or not step4_summary_path.exists() or not snapshot_path.exists():
        return {"status": "blocked", "errors": ["required_handoff_file_missing"], "limitations": []}
    manifest = _read_json(manifest_path)
    step4_summary = _read_json(step4_summary_path)
    snapshot = _read_json(snapshot_path)
    profile_result = validate_project_profile(project_profile)
    step_results: dict[str, dict[str, Any]] = {
        "step1": {"status": "ready" if profile_result.ok else "blocked", "checks": {"profile_contract_valid": profile_result.ok}, "errors": list(profile_result.codes)},
        "step2": {"status": "not_inspected", "checks": {}, "errors": []},
        "step3": {"status": "not_inspected", "checks": {}, "errors": []},
        "step4": {"status": "not_inspected", "checks": {}, "errors": []},
        "step5": {"status": "not_inspected", "checks": {}, "errors": []},
    }
    if not profile_result.ok:
        errors.extend(profile_result.codes)
    profile_receipt = step1_receipt or {}
    step1_receipt_errors = [
        field for field, expected in (("decision", "approved"), ("project_id", project_profile.get("project_id")), ("profile_version", project_profile.get("profile_version")))
        if profile_receipt and profile_receipt.get(field) != expected
    ]
    if step1_receipt and step1_receipt_errors:
        errors.extend(f"step1_human_confirmation_{field}_mismatch" for field in step1_receipt_errors)
    if not step1_receipt:
        limitations.append("Step 1 外部 human_confirmation receipt 未提供给交接审计；已仅核验档案内批准记录。")
    step_results["step1"] = {
        "status": "blocked" if not profile_result.ok or step1_receipt_errors else "ready",
        "checks": {"profile_contract_valid": profile_result.ok, "external_receipt_checked": bool(step1_receipt), "external_receipt_approved": profile_receipt.get("decision") == "approved" if step1_receipt else None},
        "errors": [*list(profile_result.codes), *[f"human_confirmation_{field}_mismatch" for field in step1_receipt_errors]],
    }
    if step2_manifest.get("status") != "frozen":
        errors.append("step2_question_set_not_frozen")
    if step2_manifest.get("project_id") != project_profile.get("project_id"):
        errors.append("step2_project_id_mismatch")
    if step2_manifest.get("project_profile_version") != project_profile.get("profile_version"):
        errors.append("step2_profile_version_mismatch")
    receipt = step2_receipt or {}
    if receipt.get("decision") != "approved":
        errors.append("step2_human_confirmation_not_approved")
    for field in ("project_id", "profile_version", "question_catalog_version", "question_set_version"):
        source_value = project_profile.get("profile_version") if field == "profile_version" else step2_manifest.get(field)
        if receipt.get(field) != source_value:
            errors.append(f"step2_human_confirmation_{field}_mismatch")
    step_results["step2"] = {
        "status": "ready" if not any(code.startswith("step2_") for code in errors) else "blocked",
        "checks": {"question_set_frozen": step2_manifest.get("status") == "frozen", "human_confirmation_approved": receipt.get("decision") == "approved"},
        "errors": [code for code in errors if code.startswith("step2_")],
    }
    if step2_catalog is not None:
        catalog_questions = step2_catalog.get("questions", [])
        catalog_result = validate_question_catalog(catalog_questions)
        catalog_errors: list[str] = []
        if step2_catalog.get("status") != "frozen":
            catalog_errors.append("catalog_not_frozen")
        if step2_catalog.get("catalog_version") != step2_manifest.get("question_catalog_version"):
            catalog_errors.append("catalog_version_mismatch")
        if not catalog_result.ok:
            catalog_errors.extend(catalog_result.codes)
        if catalog_errors:
            errors.extend(f"step2_{code}" for code in catalog_errors)
            step_results["step2"]["errors"].extend(catalog_errors)
            step_results["step2"]["status"] = "blocked"
        step_results["step2"]["checks"].update({"catalog_checked": True, "catalog_question_count": len(catalog_questions), "catalog_contract_valid": catalog_result.ok})
    else:
        limitations.append("Step 2 question_catalog 未提供给交接审计；未复核完整问题目录。")
    if step2_source_manifest is not None:
        ready = step2_source_manifest.get("step2_readiness") in {"ready_business_geo", "ready_seo_geo_hybrid"}
        step_results["step2"]["checks"].update({"source_manifest_checked": True, "source_manifest_readiness": step2_source_manifest.get("step2_readiness")})
        if not ready:
            errors.append("step2_source_manifest_not_ready")
            step_results["step2"]["errors"].append("source_manifest_not_ready")
            step_results["step2"]["status"] = "blocked"
    else:
        limitations.append("Step 2 source_manifest 未提供给交接审计；未复核来源授权与 readiness。")
    if step2_validation_report is not None:
        valid = step2_validation_report.get("status") in {"ready_business_geo", "ready_seo_geo_hybrid"}
        step_results["step2"]["checks"].update({"validation_report_checked": True, "validation_status": step2_validation_report.get("status")})
        if not valid:
            errors.append("step2_validation_report_not_ready")
            step_results["step2"]["errors"].append("validation_report_not_ready")
            step_results["step2"]["status"] = "blocked"
    else:
        limitations.append("Step 2 validation_report 未提供给交接审计；未复核问题集质量门。")
    expected = {
        "project_id": project_profile.get("project_id"),
        "profile_version": project_profile.get("profile_version"),
        "question_catalog_version": step2_manifest.get("question_catalog_version"),
        "question_set_version": step2_manifest.get("question_set_version"),
    }
    for field, expected_value in expected.items():
        if expected_value and manifest.get(field) != expected_value:
            errors.append(f"manifest_{field}_mismatch")
    for field in ("project_id", "question_catalog_version", "question_set_version", "collection_mode"):
        if not snapshot.get(field):
            errors.append(f"step5_snapshot_{field}_missing")
        elif manifest.get(field) and snapshot.get(field) != manifest.get(field):
            errors.append(f"step5_snapshot_{field}_mismatch")
    normalized_count = len(_read_jsonl(run_dir / "step4" / "normalized_observations.jsonl"))
    if normalized_count != int(step4_summary.get("included_observation_count", -1)):
        errors.append("step4_included_observation_count_mismatch")
    planned_count = manifest.get("planned_observation_count")
    if isinstance(planned_count, int) and planned_count != normalized_count:
        limitations.append(f"planned_observation_count={planned_count}，Step 4 纳入={normalized_count}；本期存在平台/范围偏差，不能表述为完整计划样本。")
    if manifest.get("run_status") == "planned" and normalized_count:
        limitations.append("run_status=planned，但已存在 Step 4 纳入观察；运行状态未回写，不能将 manifest 状态表述为已完成。")
    validation_path = run_dir / "validation" / "observation_validation.jsonl"
    raw_root = run_dir / "raw" / "observations"
    if validation_path.exists() and raw_root.exists():
        validations = _read_jsonl(validation_path)
        admitted = [item for item in validations if item.get("step4_admission") == "admitted"]
        required_artifacts = ("answer.txt", "initial.png", "initial_answer_source_dom.html", "expanded.png", "expanded_answer_source_dom.html", "observation.json")
        missing_artifacts = [
            f"{item.get('observation_id')}:{artifact}"
            for item in admitted for artifact in required_artifacts
            if not (raw_root / str(item.get("observation_id")) / artifact).exists()
        ]
        step_results["step3"] = {
            "status": "blocked" if missing_artifacts else "ready",
            "checks": {"validated_observation_count": len(validations), "step4_admitted_count": len(admitted), "required_artifact_count_per_observation": len(required_artifacts)},
            "errors": missing_artifacts,
        }
        if missing_artifacts:
            errors.append("step3_required_artifact_missing")
        source_card_path = run_dir / "raw" / "source_cards.jsonl"
        candidate_path = run_dir / "raw" / "citation_candidates.jsonl"
        raw_audit_errors = [name for name, path in (("source_cards", source_card_path), ("citation_candidates", candidate_path)) if not path.exists()]
        if raw_audit_errors:
            errors.extend(f"step3_{name}_audit_missing" for name in raw_audit_errors)
            step_results["step3"]["status"] = "blocked"
            step_results["step3"]["errors"].extend(f"{name}_audit_missing" for name in raw_audit_errors)
        step_results["step3"]["checks"].update({
            "source_card_record_count": len(_read_jsonl(source_card_path)),
            "citation_candidate_record_count": len(_read_jsonl(candidate_path)),
        })
    else:
        limitations.append("Step 3 原始证据或准入记录未提供给交接审计；该步骤标记为 not_inspected。")
    decisions_path = run_dir / "step4" / "signal_decisions.jsonl"
    quality_path = run_dir / "step4" / "quality_and_exclusions.jsonl"
    if decisions_path.exists() and quality_path.exists():
        decisions = _read_jsonl(decisions_path)
        quality = _read_jsonl(quality_path)
        step4_errors: list[str] = []
        if len(decisions) != normalized_count:
            step4_errors.append("signal_decision_count_mismatch")
        if len(quality) < normalized_count:
            step4_errors.append("quality_record_count_mismatch")
        evidence_audit_path = run_dir / "step4" / "evidence_audit.jsonl"
        evidence_audit_count = len(_read_jsonl(evidence_audit_path)) if evidence_audit_path.exists() else 0
        if not evidence_audit_path.exists() or evidence_audit_count != normalized_count:
            step4_errors.append("evidence_audit_count_mismatch")
        step_results["step4"] = {
            "status": "blocked" if step4_errors else "ready",
            "checks": {"normalized_observation_count": normalized_count, "signal_decision_count": len(decisions), "quality_record_count": len(quality), "evidence_audit_count": evidence_audit_count},
            "errors": step4_errors,
        }
        if step4_errors:
            errors.extend(f"step4_{item}" for item in step4_errors)
    else:
        limitations.append("Step 4 信号决策或质量记录未提供给交接审计；该步骤标记为 not_inspected。")
    opportunity_records = _read_jsonl(run_dir / "step5" / "content_opportunities.jsonl")
    fact_records = _read_jsonl(run_dir / "step5" / "competitor_facts.jsonl")
    step5_errors: list[str] = []
    if not snapshot.get("snapshot_id"):
        step5_errors.append("snapshot_id_missing")
    if any(item.get("snapshot_id") != snapshot.get("snapshot_id") for item in [*opportunity_records, *fact_records]):
        step5_errors.append("record_snapshot_id_mismatch")
    if any(not item.get("opportunity_key") or not item.get("evidence_ids") for item in opportunity_records):
        step5_errors.append("opportunity_traceability_incomplete")
    step_results["step5"] = {
        "status": "blocked" if step5_errors else "ready",
        "checks": {"snapshot_id_present": bool(snapshot.get("snapshot_id")), "opportunity_count": len(opportunity_records), "fact_count": len(fact_records)},
        "errors": step5_errors,
    }
    if step5_errors:
        errors.extend(f"step5_{item}" for item in step5_errors)
    return {
        "status": "blocked" if errors else ("ready_with_limitations" if limitations else "ready"),
        "errors": sorted(set(errors)),
        "limitations": limitations,
        "project_id": manifest.get("project_id"), "run_id": manifest.get("run_id"),
        "profile_version": project_profile.get("profile_version"),
        "question_catalog_version": manifest.get("question_catalog_version"),
        "question_set_version": manifest.get("question_set_version"),
        "step4_included_observation_count": normalized_count,
        "step4_excluded_observation_count": step4_summary.get("excluded_observation_count"),
        "step5_snapshot_id": snapshot.get("snapshot_id"),
        "step_results": step_results,
    }


def _source_platforms(
    topology: Sequence[Mapping[str, Any]], captures: Sequence[Mapping[str, Any]] = (),
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]]]:
    by_question: dict[str, list[dict[str, Any]]] = defaultdict(list)
    aggregate: dict[str, dict[str, Any]] = {}
    seen_pages: set[tuple[str, str, str]] = set()
    capture_by_url = {str(item.get("url")): item for item in captures if item.get("url")}
    for row in topology:
        if row.get("access_state") != "visible_verified" or not row.get("source_domain"):
            continue
        # The visible source card's publisher is a concrete platform label.
        # Fall back to the domain only when the UI did not expose one.
        platform = str(row.get("source_publisher") or row["source_domain"])
        question_id = str(row.get("question_id"))
        url = str(row.get("url") or "")
        page_key = (question_id, platform, url)
        if page_key in seen_pages:
            continue
        seen_pages.add(page_key)
        capture = capture_by_url.get(url, {})
        source_access = str(capture.get("source_access") or "metadata_only")
        source = {
            "platform": platform, "verified": True, "access_state": source_access,
            "direct": source_access == "accessible", "capture_id": capture.get("capture_id"),
            "observed_structure": capture.get("observed_structure", []),
            "ownership": row.get("ownership"), "cited_source_page_count": 1,
            "covered_question_count": 1,
        }
        by_question[question_id].append(source)
        # A competitor citation is evidence of a gap, never a publishing
        # channel for the target.  Unknown ownership is likewise excluded.
        if row.get("ownership") not in {"target", "third_party"}:
            continue
        entry = aggregate.setdefault(platform, {"发布平台": platform, "已验证被引用来源页数": 0, "覆盖问题数": set(), "对应内容机会数": set(), "可参考内容类型": set()})
        entry["已验证被引用来源页数"] += 1
        entry["覆盖问题数"].add(question_id)
    return by_question, list(aggregate.values())


def build_step6_package(
    run_dir: Path,
    project_profile: Mapping[str, Any],
    step2_manifest: Mapping[str, Any],
    questions: Mapping[str, Mapping[str, Any]],
    output_dir: Path | None = None,
    step2_receipt: Mapping[str, Any] | None = None,
    step1_receipt: Mapping[str, Any] | None = None,
    step2_catalog: Mapping[str, Any] | None = None,
    step2_source_manifest: Mapping[str, Any] | None = None,
    step2_validation_report: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create auditable, one-opportunity-per-question Step 6 planning outputs."""

    output_dir = output_dir or run_dir / "step6"
    output_dir.mkdir(parents=True, exist_ok=True)
    audit = audit_step1_to_step5(
        run_dir, project_profile, step2_manifest, step2_receipt,
        step1_receipt, step2_catalog, step2_source_manifest, step2_validation_report,
    )
    opportunities = _read_jsonl(run_dir / "step5" / "content_opportunities.jsonl")
    topology = _read_jsonl(run_dir / "step5" / "verified_source_topology.jsonl")
    source_captures = _read_jsonl(output_dir / "source_capture.jsonl")
    source_by_question, distribution = _source_platforms(topology, source_captures)
    briefs: list[dict[str, Any]] = []
    review: list[dict[str, Any]] = []
    if audit["status"] != "blocked":
        for opportunity in opportunities:
            question_id = str(opportunity.get("question_id"))
            question = questions.get(question_id)
            expected_versions = (audit.get("project_id"), audit.get("run_id"), audit.get("question_set_version"))
            observed_versions = (opportunity.get("project_id"), opportunity.get("run_id"), opportunity.get("question_set_version"))
            if not question or not opportunity.get("valid") or not opportunity.get("intent") or opportunity.get("step6_eligibility", "eligible") != "eligible" or expected_versions != observed_versions:
                review.append({"opportunity_key": opportunity.get("opportunity_key"), "question_id": question_id, "reason": "step5_opportunity_or_lineage_incomplete", "status": "manual_review"})
                continue
            brief = create_brief(opportunity, question, source_by_question.get(question_id, []))
            brief.update({
                "run_id": opportunity.get("run_id"), "project_id": opportunity.get("project_id"), "snapshot_id": opportunity.get("snapshot_id"),
                "evidence_ids": opportunity.get("evidence_ids", []), "observation_ids": opportunity.get("observation_ids", []),
                "gap_types": opportunity.get("gap_types", opportunity.get("gap_type", [])), "signal_definition_version": opportunity.get("signal_definition_version"),
            })
            briefs.append(brief)
    else:
        review.append({"reason": "upstream_handoff_blocked", "status": "manual_review", "errors": audit["errors"]})
    for platform in distribution:
        platform["覆盖问题数"] = len(platform["覆盖问题数"])
        platform["对应内容机会数"] = sum(1 for brief in briefs if platform["发布平台"] in brief["publication_platforms"])
        platform["可参考内容类型"] = "；".join(sorted({brief["content_type"] for brief in briefs if platform["发布平台"] in brief["publication_platforms"]}))
    team_rows = [{
        "问题": brief["question"], "主标题": brief["main_title"], "H2内容结构": "\n".join(brief["h2_structure"]),
        "机会等级": brief["opportunity_tier"], "机会依据": brief["opportunity_basis"], "优先级": brief["priority"], "建议发布平台": "、".join(brief["publication_platforms"]) or "无（本期无已验证被引用来源页）",
        "简要行动建议": brief["action_advice"], "来源页核验状态": brief["source_page_verification_status"],
    } for brief in briefs]
    reason_rows = [{
        "opportunity_key": brief["opportunity_key"], "question_id": brief["question_id"],
        "归一原因代码": brief["normalized_reason_code"], "归一原因说明": brief["normalized_reason"],
        "信号类型": "、".join(brief.get("gap_types", [])) if isinstance(brief.get("gap_types"), list) else brief.get("gap_types"),
        "来源证据状态": brief["source_evidence_state"], "证据ID": brief.get("evidence_ids", []),
        "归因边界": "仅描述同一可比观察的信号差异；不声称 AI 推荐、引用或排名的因果原因。",
    } for brief in briefs]
    _write_csv(output_dir / "06_内容结构总表.csv", team_rows, ("问题", "主标题", "H2内容结构", "机会等级", "机会依据", "优先级", "建议发布平台", "简要行动建议", "来源页核验状态"))
    _write_csv(output_dir / "06_发布平台引用分布.csv", distribution, ("发布平台", "已验证被引用来源页数", "覆盖问题数", "对应内容机会数", "可参考内容类型"))
    _write_csv(output_dir / "06_原因归一表.csv", reason_rows, ("opportunity_key", "question_id", "归一原因代码", "归一原因说明", "信号类型", "来源证据状态", "证据ID", "归因边界"))
    _write_csv(output_dir / "06_人工复核队列.csv", review, ("opportunity_key", "question_id", "reason", "status", "errors"))
    (output_dir / "06_内容结构明细.jsonl").write_text("".join(json.dumps(brief, ensure_ascii=False, sort_keys=True) + "\n" for brief in briefs), encoding="utf-8")
    (output_dir / "06_上游交接校验.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (output_dir / "06_全步骤交接与质量校验.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    step_statuses = "；".join(f"{name}={item['status']}" for name, item in audit["step_results"].items())
    report = "\n".join(("# 06 内容规划骨架", "", f"- 全步骤交接状态：{audit['status']}（{step_statuses}）。", f"- 有效内容机会：{len(briefs)} 条；一条机会对应一份骨架，未按意图合并。", f"- 已验证引用来源平台：{len(distribution)} 个。", f"- 人工复核：{len(review)} 条。", "- `06_原因归一表` 只归一可观察信号，不声称 AI 产生差距的因果原因。", "- 本步骤不生成正文、不判断 create/update、不发布；所有品牌事实均需人工核验。", *[f"- 限制：{item}" for item in audit["limitations"]])) + "\n"
    (output_dir / "06_内容规划骨架.md").write_text(report, encoding="utf-8")
    html_fields = ("问题", "主标题", "H2内容结构", "机会等级", "机会依据", "优先级", "建议发布平台", "简要行动建议", "来源页核验状态")
    html_rows = "".join("<tr>" + "".join(f"<td>{html.escape(str(row[field]))}</td>" for field in html_fields) + "</tr>" for row in team_rows) or "<tr><td colspan=\"9\">无可排期内容机会</td></tr>"
    html_report = "<!doctype html><html lang=\"zh-CN\"><meta charset=\"utf-8\"><title>06 内容规划骨架</title><style>body{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC',sans-serif;margin:32px;color:#18202a}table{border-collapse:collapse;width:100%}th,td{border:1px solid #d9dee7;padding:8px;white-space:pre-line;text-align:left;vertical-align:top}th{background:#f4f7fb}</style><h1>06 内容规划骨架</h1><p>上游交接状态：" + html.escape(audit["status"]) + "</p><table><thead><tr>" + "".join(f"<th>{html.escape(field)}</th>" for field in html_fields) + "</tr></thead><tbody>" + html_rows + "</tbody></table></html>"
    (output_dir / "06_内容规划骨架.html").write_text(html_report, encoding="utf-8")
    return {"audit": audit, "briefs": briefs, "review": review, "platform_distribution": distribution}
