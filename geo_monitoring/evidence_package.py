"""Append-only Step 3 evidence-package storage."""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
import shutil
from typing import Any, Mapping, Sequence

from .contracts import sha256_json
from .collection_control import default_risk_policy
from .evidence_audit import audit_candidates, derive_visible_source_cards
from .validation import validate_observation_metadata, validate_raw_evidence_package


ARTIFACT_FILES = {
    "answer_text": "answer.txt",
    "initial_screenshot": "initial.png",
    "initial_answer_source_dom": "initial_answer_source_dom.html",
    "expanded_screenshot": "expanded.png",
    "source_cards_expanded_dom": "expanded_answer_source_dom.html",
}

RUN_INHERITED_FIELDS = (
    "step3_contract_version",
    "profile_version",
    "project_profile_sha256",
    "question_catalog_version",
    "question_catalog_sha256",
    "question_set_version",
    "question_set_sha256",
    "adapter_version",
    "parser_version",
    "rule_version",
)


def initialize_run_package(root: Path, manifest: Mapping[str, Any]) -> Path:
    """Create an empty v2 run package without overwriting an existing run."""

    run_id = str(manifest["run_id"])
    run_dir = root / run_id
    if run_dir.exists():
        raise FileExistsError(f"run package already exists: {run_id}")
    (run_dir / "raw" / "observations").mkdir(parents=True)
    (run_dir / "validation").mkdir()
    (run_dir / "control").mkdir()
    manifest_record = dict(manifest)
    manifest_record.setdefault("risk_control_policy", default_risk_policy())
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return run_dir


def _append_jsonl(path: Path, record: Mapping[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(dict(record), ensure_ascii=False, sort_keys=True) + "\n")


def _file_hash(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def append_observation(
    run_dir: Path,
    observation: Mapping[str, Any],
    artifact_paths: Mapping[str, Path],
    source_cards: Sequence[Mapping[str, Any]],
    candidates: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Copy one immutable Web UI observation and create its raw indexes."""

    observation_id = str(observation["observation_id"])
    manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
    if observation.get("run_id") != manifest["run_id"]:
        raise ValueError("observation run_id does not match package")
    observation_dir = run_dir / "raw" / "observations" / observation_id
    if observation_dir.exists():
        raise FileExistsError(f"observation already exists: {observation_id}")
    observation_dir.mkdir(parents=True)

    artifact_index_path = run_dir / "raw" / "artifact_index.jsonl"
    for artifact_type, filename in ARTIFACT_FILES.items():
        source = artifact_paths.get(artifact_type)
        if source is None or not source.is_file():
            continue
        destination = observation_dir / filename
        shutil.copyfile(source, destination)
        _append_jsonl(artifact_index_path, {
            "artifact_id": f"{observation_id}:{artifact_type}",
            "observation_id": observation_id,
            "artifact_type": artifact_type,
            "relative_path": str(destination.relative_to(run_dir)),
            "sha256": _file_hash(destination),
            "capture_status": "complete",
        })

    raw_record = dict(observation)
    for field in RUN_INHERITED_FIELDS:
        if field in manifest:
            raw_record.setdefault(field, manifest[field])
    raw_record.setdefault("evidence_id", f"{observation_id}:evidence")
    raw_record["frozen_question_sha256"] = sha256(str(observation.get("frozen_question_text", "")).encode()).hexdigest()
    raw_record["actual_sent_sha256"] = sha256(str(observation.get("actual_sent_text", "")).encode()).hexdigest()
    visible = observation.get("platform_visible_query_text")
    raw_record["platform_visible_query_sha256"] = sha256(str(visible).encode()).hexdigest() if visible is not None else None
    raw_record.setdefault("comparable", True)
    raw_record.setdefault("non_comparable_reasons", [])
    if (
        observation.get("prompt_integrity_state") == "observable_transform"
        and observation.get("actual_sent_text") != observation.get("frozen_question_text")
    ):
        raw_record["comparable"] = False
        raw_record["non_comparable_reasons"] = sorted({
            *raw_record["non_comparable_reasons"],
            "prompt_transform_without_new_revision",
        })
    raw_record["raw_observation_sha256"] = sha256_json(raw_record)
    (observation_dir / "observation.json").write_text(
        json.dumps(raw_record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )

    captured_source_cards = [dict(card) for card in source_cards]
    # A collector may preserve a numbered card label while the DOM parser sees
    # the same visible card without its display number.  URL identity is the
    # stable evidence key within a single expanded-source capture.
    existing_urls = {
        str(card.get("visible_url", "")).strip()
        for card in captured_source_cards
        if str(card.get("visible_url", "")).strip()
    }
    expanded_dom_path = artifact_paths.get("source_cards_expanded_dom")
    if expanded_dom_path and expanded_dom_path.is_file():
        for card in derive_visible_source_cards(expanded_dom_path.read_text(encoding="utf-8"), observation_id):
            url = str(card.get("visible_url", "")).strip()
            if url and url not in existing_urls:
                captured_source_cards.append(card)
                existing_urls.add(url)
    for index, source_card in enumerate(captured_source_cards, 1):
        _append_jsonl(run_dir / "raw" / "source_cards.jsonl", {
            **source_card,
            "source_card_id": source_card.get("source_card_id", f"{observation_id}:card:{index}"),
            "observation_id": observation_id,
            "source_card_status": source_card.get("source_card_status", "complete"),
        })
    derived_candidates = [
        {"candidate_origin": "visible_source_card", "url": card.get("visible_url"), "anchor_or_span": card.get("visible_anchor_text"), "kind": "visible_source_card"}
        for card in captured_source_cards
        if str(card.get("visible_url", "")).startswith(("http://", "https://")) and card.get("visible_anchor_text")
    ]
    verified_candidates, rejected_candidates = audit_candidates([*candidates, *derived_candidates])
    for index, candidate in enumerate([*verified_candidates, *rejected_candidates], 1):
        _append_jsonl(run_dir / "raw" / "citation_candidates.jsonl", {
            **candidate,
            "candidate_id": candidate.get("candidate_id", f"{observation_id}:candidate:{index}"),
            "observation_id": observation_id,
        })

    evidence_input = {
        "answer_dom_artifact": artifact_paths.get("initial_answer_source_dom"),
        "initial_screenshot": artifact_paths.get("initial_screenshot"),
        "expanded_screenshot": artifact_paths.get("expanded_screenshot"),
        "source_cards_expanded_dom": artifact_paths.get("source_cards_expanded_dom"),
    }
    evidence_result = validate_raw_evidence_package(evidence_input)
    metadata_result = validate_observation_metadata(raw_record)
    admission = "admitted" if evidence_result.ok and metadata_result.ok else "partial_only"
    validation = {
        "observation_id": observation_id,
        "schema_valid": metadata_result.ok,
        "lineage_valid": metadata_result.ok,
        "prompt_integrity_valid": bool(observation.get("prompt_integrity_state")),
        "answer_evidence_valid": evidence_result.ok,
        "source_evidence_valid": evidence_result.ok,
        "replay_valid": evidence_result.ok,
        "step4_admission": admission,
        "exclusion_reasons": sorted({*evidence_result.codes, *metadata_result.codes}),
    }
    _append_jsonl(run_dir / "validation" / "observation_validation.jsonl", validation)
    return validation
