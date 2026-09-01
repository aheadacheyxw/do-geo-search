"""Brand-neutral local CLI for GEO project setup, processing and comparison."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from .collection_control import append_control_event, next_collection_decision, read_control_events
from .competitor import build_step5_package
from .content_briefs import build_step6_package
from .contracts import sha256_json
from .evidence_package import append_observation, initialize_run_package
from .final_report import build_and_render
from .history import discover_history, discovery_summary
from .io import read_json, write_json, write_jsonl
from .period_report import compare_runs, write_comparison
from .project_questions import freeze_question_set, validate_project_profile, validate_question_catalog
from .source_capture import capture_verified_sources
from .step4 import build_step4_package


def _slug(value: object) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "brand").casefold()).strip("-")
    return text or f"brand-{hashlib.sha256(str(value).encode()).hexdigest()[:8]}"


def _question(question: Mapping[str, Any], index: int, project_id: str) -> dict[str, Any]:
    item = dict(question)
    qid = str(item.get("question_id") or f"{project_id}-q-{index:03d}")
    text = str(item.get("exact_question_text") or item.get("text") or "").strip()
    item.update({
        "question_id": qid,
        "question_revision_id": str(item.get("question_revision_id") or f"{qid}-r1"),
        "exact_question_text": text,
        "primary_intent": str(item.get("primary_intent") or "discover"),
        "question_group": str(item.get("question_group") or "scenario_provider_recommendation"),
        "business_importance": str(item.get("business_importance") or "medium"),
        "set_membership": str(item.get("set_membership") or "core"),
        "growth_trigger": bool(item.get("growth_trigger", True)),
    })
    return item


def init_project(answers_path: Path, output: Path) -> int:
    answers = read_json(answers_path)
    brand = str(answers.get("canonical_brand") or "").strip()
    project_id = str(answers.get("project_id") or _slug(brand))
    profile_version = str(answers.get("profile_version") or f"{project_id}-profile-v1")
    confirmation = dict(answers.get("human_confirmation") or {})
    confirmation.setdefault("decision", "pending")
    confirmation.setdefault("profile_version", profile_version)
    profile = {
        "status": "approved" if confirmation.get("decision") == "approved" else "pending_human_confirmation",
        "project_id": project_id,
        "profile_version": profile_version,
        "target_scope": {
            "canonical_brand": brand,
            "aliases": list(answers.get("aliases") or []),
            "official_domains": list(answers.get("official_domains") or []),
        },
        "in_scope_products_services": list(answers.get("products_services") or []),
        "audiences": list(answers.get("audiences") or []),
        "markets_languages": list(answers.get("markets_languages") or []),
        "target_ai_platforms": list(answers.get("target_ai_platforms") or []),
        "business_goals": list(answers.get("business_goals") or ["growth_acquisition", "brand_factual_accuracy_and_risk"]),
        "success_signals": list(answers.get("success_signals") or ["brand_mention", "explicit_recommendation", "formal_rank", "verified_citation", "sentiment", "factual_risk"]),
        "declared_competitors": list(answers.get("declared_competitors") or []),
        "exclusions": list(answers.get("exclusions") or ["media", "community", "cdn", "unknown_domain"]),
        "fact_sources": list(answers.get("fact_sources") or [{"claim": "品牌及官方域由使用者提交", "status": "supplied_unverified", "reference": "onboarding"}]),
        "measurement_context": dict(answers.get("measurement_context") or {}),
        "human_confirmation": confirmation,
    }
    questions = [_question(item, index, project_id) for index, item in enumerate(answers.get("questions") or [], 1)]
    catalog_version = str(answers.get("question_catalog_version") or f"{project_id}-questions-v1")
    question_set_version = str(answers.get("question_set_version") or f"{project_id}-set-v1")
    profile_hash = sha256_json(profile)
    catalog = {
        "status": "frozen" if confirmation.get("decision") == "approved" and questions else "draft",
        "project_id": project_id,
        "profile_version": profile_version,
        "catalog_version": catalog_version,
        "questions": questions,
    }
    set_manifest = {
        "status": catalog["status"],
        "project_id": project_id,
        "project_profile_version": profile_version,
        "project_profile_sha256": profile_hash,
        "question_catalog_version": catalog_version,
        "question_set_version": question_set_version,
        "question_count": len(questions),
        "ordered_question_ids": [item["question_id"] for item in questions],
    }
    step1, step2 = output / "step1", output / "step2"
    step1.mkdir(parents=True, exist_ok=True)
    step2.mkdir(parents=True, exist_ok=True)
    write_json(step1 / "project_profile.json", profile)
    write_json(step1 / "human_confirmation_receipt.json", confirmation)
    write_json(step2 / "question_catalog.json", catalog)
    write_json(step2 / "question_set_manifest.json", set_manifest)
    write_json(step2 / "human_confirmation_receipt.json", {
        **confirmation, "project_id": project_id, "question_catalog_version": catalog_version,
        "question_set_version": question_set_version,
    })
    write_json(step2 / "source_manifest.json", {
        "sources": list(answers.get("source_permissions") or []),
        "step2_readiness": "ready_business_geo" if catalog["status"] == "frozen" else "draft",
    })
    return 0


def validate_project(project: Path) -> int:
    profile = read_json(project / "step1" / "project_profile.json")
    catalog = read_json(project / "step2" / "question_catalog.json")
    profile_result = validate_project_profile(profile)
    catalog_result = validate_question_catalog(catalog.get("questions", []))
    frozen = freeze_question_set(profile, catalog.get("questions", []))
    result = {
        "ok": profile_result.ok and catalog_result.ok and frozen.status.startswith("ready"),
        "profile": {"ok": profile_result.ok, "codes": list(profile_result.codes)},
        "question_catalog": {"ok": catalog_result.ok, "codes": list(catalog_result.codes)},
        "freeze_status": frozen.status,
    }
    write_json(project / "validation_report.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 2


def prepare_run(project: Path, run_id: str, output_root: Path) -> int:
    """Create a versioned run manifest from one approved project."""

    if validate_project(project) != 0:
        raise ValueError("project profile or question catalog is not approved and frozen")
    profile = read_json(project / "step1" / "project_profile.json")
    catalog = read_json(project / "step2" / "question_catalog.json")
    set_manifest = read_json(project / "step2" / "question_set_manifest.json")
    scope = profile.get("target_scope") or {}
    platforms = []
    for item in profile.get("target_ai_platforms", []):
        record = dict(item) if isinstance(item, Mapping) else {"platform": str(item)}
        record.setdefault("surface_class", "general_ai_web")
        record.setdefault("web_surface", "web_ui")
        platforms.append(record)
    context = dict(profile.get("measurement_context") or {})
    manifest = {
        "run_id": run_id,
        "run_status": "planned",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "step3_contract_version": "step3-v1",
        "project_id": profile["project_id"],
        "profile_version": profile["profile_version"],
        "project_profile_sha256": sha256_json(profile),
        "question_catalog_version": catalog["catalog_version"],
        "question_catalog_sha256": sha256_json(catalog),
        "question_set_version": set_manifest["question_set_version"],
        "question_set_sha256": sha256_json(set_manifest.get("ordered_question_ids", [])),
        "collection_mode": context.get("collection_mode", "standardized_local_UI"),
        "adapter_version": "controlled-web-ui-adapter-v1",
        "parser_version": "answer-boundary-parser-v1",
        "rule_version": "geo-signal-rules-v1",
        "planned_questions": catalog["questions"],
        "planned_platforms": platforms,
        "planned_observation_count": len(catalog["questions"]) * len(platforms),
        "measurement_context_defaults": {
            "market_region": context.get("market_region", "unavailable"),
            "language_locale": context.get("language_locale", "unavailable"),
            "answer_language": context.get("language_locale", "unavailable"),
            "account_session_class": context.get("account_session_class", "unavailable"),
            "web_search_state": context.get("web_search_mode", "record_observed_value"),
            "mode_reasoning_state": context.get("mode_reasoning_state", "record_observed_value"),
            "collection_mode": context.get("collection_mode", "standardized_local_UI"),
        },
        "brand_identity": {
            "canonical_brand": scope.get("canonical_brand"),
            "aliases": scope.get("aliases", []),
            "official_domains": scope.get("official_domains", []),
            "products_services": profile.get("in_scope_products_services", []),
        },
        "evidence_protocol": {
            "capture_sequence": ["wait_for_answer_completion", "capture_initial", "expand_visible_sources_once", "capture_expanded"],
            "required_artifacts": ["answer.txt", "initial.png", "initial.html", "expanded.png", "expanded.html", "source-cards.json", "citation-candidates.json"],
        },
    }
    run_dir = initialize_run_package(output_root, manifest)
    print(str(run_dir))
    return 0


def discover(profile_path: Path, roots: Sequence[Path], output: Path | None = None, exclude_run: Path | None = None) -> int:
    candidates = discover_history(read_json(profile_path), roots, exclude_run=exclude_run)
    result = {"summary": discovery_summary(candidates), "candidates": candidates}
    if output:
        write_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


def ingest_observation(run_dir: Path, package: Path) -> int:
    required = {
        "answer_text": package / "answer.txt", "initial_screenshot": package / "initial.png",
        "initial_answer_source_dom": package / "initial.html", "expanded_screenshot": package / "expanded.png",
        "source_cards_expanded_dom": package / "expanded.html",
    }
    missing = [path.name for path in [package / "observation.json", package / "source-cards.json", package / "citation-candidates.json", *required.values()] if not path.is_file()]
    if missing:
        raise ValueError(f"incomplete evidence package {package.name}: {', '.join(missing)}")
    append_observation(
        run_dir, read_json(package / "observation.json"), required,
        json.loads((package / "source-cards.json").read_text(encoding="utf-8")),
        json.loads((package / "citation-candidates.json").read_text(encoding="utf-8")),
    )
    return 0


def ingest_staged(run_dir: Path, stage_dir: Path) -> int:
    for package in sorted(path for path in stage_dir.iterdir() if path.is_dir()):
        metadata = read_json(package / "observation.json") if (package / "observation.json").exists() else {}
        destination = run_dir / "raw" / "observations" / str(metadata.get("observation_id") or package.name)
        if not destination.exists():
            ingest_observation(run_dir, package)
    return 0


def step4(run_dir: Path, profile_path: Path, output: Path | None) -> int:
    manifest = read_json(run_dir / "manifest.json")
    questions = {item["question_id"]: item for item in manifest.get("planned_questions", [])}
    build_step4_package(run_dir, read_json(profile_path), questions, output)
    return 0


def step5(run_dir: Path, profile_path: Path, output: Path | None) -> int:
    profile = read_json(profile_path)
    build_step5_package(run_dir / "step4", run_dir, profile.get("declared_competitors", []), output, project_profile=profile)
    return 0


def capture_sources(run_dir: Path, output: Path | None) -> int:
    output_dir = output or run_dir / "step6"
    output_dir.mkdir(parents=True, exist_ok=True)
    topology_path = run_dir / "step5" / "verified_source_topology.jsonl"
    topology = [json.loads(line) for line in topology_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    captures = capture_verified_sources(topology, captured_at=datetime.now(timezone.utc).isoformat())
    write_jsonl(output_dir / "source_capture.jsonl", captures)
    return 0


def step6(run_dir: Path, profile_path: Path, step2_manifest_path: Path, output: Path | None) -> int:
    manifest = read_json(run_dir / "manifest.json")
    questions = {item["question_id"]: item for item in manifest.get("planned_questions", [])}
    step2_dir = step2_manifest_path.parent
    def optional(name: str) -> dict[str, Any] | None:
        path = step2_dir / name
        return read_json(path) if path.exists() else None
    build_step6_package(
        run_dir, read_json(profile_path), read_json(step2_manifest_path), questions, output,
        step2_receipt=optional("human_confirmation_receipt.json"),
        step1_receipt=read_json(profile_path.parent / "human_confirmation_receipt.json") if (profile_path.parent / "human_confirmation_receipt.json").exists() else None,
        step2_catalog=optional("question_catalog.json"),
        step2_source_manifest=optional("source_manifest.json"),
        step2_validation_report=optional("validation_report.json"),
    )
    return 0


def compare(profile_path: Path, previous: Path, current: Path, output: Path) -> int:
    profile = read_json(profile_path)
    report = compare_runs(previous, current, profile)
    brand = str((profile.get("target_scope") or {}).get("canonical_brand") or "目标品牌")
    write_comparison(report, output, brand)
    return 0


def report(run_dir: Path, profile_path: Path, output: Path | None) -> int:
    path = build_and_render(run_dir, read_json(profile_path), output or run_dir / "report")
    print(str(path))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="geo-monitor")
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("init"); init.add_argument("--answers", required=True, type=Path); init.add_argument("--output", required=True, type=Path)
    validate = commands.add_parser("validate"); validate.add_argument("--project", required=True, type=Path)
    history = commands.add_parser("discover-history"); history.add_argument("--profile", required=True, type=Path); history.add_argument("--search-root", required=True, action="append", type=Path); history.add_argument("--output", type=Path); history.add_argument("--exclude-run", type=Path)
    prepare = commands.add_parser("prepare-run"); prepare.add_argument("--project", required=True, type=Path); prepare.add_argument("--run-id", required=True); prepare.add_argument("--output", required=True, type=Path)
    init_run = commands.add_parser("init-run"); init_run.add_argument("--manifest", required=True, type=Path); init_run.add_argument("--output", required=True, type=Path)
    ingest = commands.add_parser("ingest-staged"); ingest.add_argument("--run-dir", required=True, type=Path); ingest.add_argument("--stage-dir", required=True, type=Path)
    event = commands.add_parser("record-control-event"); event.add_argument("--run-dir", required=True, type=Path); event.add_argument("--event", required=True, type=Path)
    action = commands.add_parser("next-collection-action"); action.add_argument("--run-dir", required=True, type=Path); action.add_argument("--platform", required=True); action.add_argument("--question-id", required=True); action.add_argument("--now", required=True)
    s4 = commands.add_parser("step4"); s4.add_argument("--run-dir", required=True, type=Path); s4.add_argument("--profile", required=True, type=Path); s4.add_argument("--output", type=Path)
    s5 = commands.add_parser("step5"); s5.add_argument("--run-dir", required=True, type=Path); s5.add_argument("--profile", required=True, type=Path); s5.add_argument("--output", type=Path)
    capture = commands.add_parser("capture-sources"); capture.add_argument("--run-dir", required=True, type=Path); capture.add_argument("--output", type=Path)
    s6 = commands.add_parser("step6"); s6.add_argument("--run-dir", required=True, type=Path); s6.add_argument("--profile", required=True, type=Path); s6.add_argument("--step2-manifest", required=True, type=Path); s6.add_argument("--output", type=Path)
    summary = commands.add_parser("report"); summary.add_argument("--run-dir", required=True, type=Path); summary.add_argument("--profile", required=True, type=Path); summary.add_argument("--output", type=Path)
    comparison = commands.add_parser("compare"); comparison.add_argument("--profile", required=True, type=Path); comparison.add_argument("--previous-run", required=True, type=Path); comparison.add_argument("--current-run", required=True, type=Path); comparison.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.command == "init": return init_project(args.answers, args.output)
    if args.command == "validate": return validate_project(args.project)
    if args.command == "discover-history": return discover(args.profile, args.search_root, args.output, args.exclude_run)
    if args.command == "prepare-run": return prepare_run(args.project, args.run_id, args.output)
    if args.command == "init-run": initialize_run_package(args.output, read_json(args.manifest)); return 0
    if args.command == "ingest-staged": return ingest_staged(args.run_dir, args.stage_dir)
    if args.command == "record-control-event": append_control_event(args.run_dir, read_json(args.event)); return 0
    if args.command == "next-collection-action":
        manifest = read_json(args.run_dir / "manifest.json")
        print(json.dumps(next_collection_decision(manifest["risk_control_policy"], read_control_events(args.run_dir), platform=args.platform, question_id=args.question_id, now=args.now), ensure_ascii=False)); return 0
    if args.command == "step4": return step4(args.run_dir, args.profile, args.output)
    if args.command == "step5": return step5(args.run_dir, args.profile, args.output)
    if args.command == "capture-sources": return capture_sources(args.run_dir, args.output)
    if args.command == "step6": return step6(args.run_dir, args.profile, args.step2_manifest, args.output)
    if args.command == "report": return report(args.run_dir, args.profile, args.output)
    return compare(args.profile, args.previous_run, args.current_run, args.output)


if __name__ == "__main__":
    raise SystemExit(main())
