"""Read-only discovery of prior GEO runs for the same brand identity."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _text(value: object) -> str:
    return " ".join(str(value or "").casefold().split())


def _domain(value: object) -> str:
    raw = str(value or "").strip().casefold()
    if not raw:
        return ""
    if "://" in raw:
        raw = urlparse(raw).hostname or ""
    return raw.removeprefix("www.").rstrip(".")


def identity_from_profile(profile: Mapping[str, Any]) -> dict[str, set[str]]:
    scope = profile.get("target_scope") or {}
    brand = _text(scope.get("canonical_brand") or profile.get("canonical_brand"))
    aliases = {_text(item) for item in scope.get("aliases", []) if _text(item)}
    if brand:
        aliases.add(brand)
    raw_domains = scope.get("official_domains") or profile.get("official_domains") or []
    domains = set()
    for item in raw_domains:
        domains.add(_domain(item.get("domain") if isinstance(item, Mapping) else item))
    products = {
        _text(item.get("name") if isinstance(item, Mapping) else item)
        for item in (profile.get("in_scope_products_services") or profile.get("in_scope_products") or [])
    }
    return {
        "brands": {item for item in aliases if item},
        "domains": {item for item in domains if item},
        "products": {item for item in products if item},
    }


def _profile_index(search_root: Path) -> dict[str, list[tuple[Path, dict[str, Any]]]]:
    index: dict[str, list[tuple[Path, dict[str, Any]]]] = {}
    for path in search_root.rglob("project_profile.json"):
        if any(part in {"raw", ".git", "node_modules"} for part in path.parts):
            continue
        profile = _read_json(path)
        project_id = str(profile.get("project_id") or "")
        if project_id:
            index.setdefault(project_id, []).append((path, profile))
    return index


def _run_date(run_dir: Path, manifest: Mapping[str, Any]) -> str:
    for key in ("completed_at", "created_at", "started_at", "generated_at"):
        if manifest.get(key):
            return str(manifest[key])
    match = re.search(r"20\d{2}-\d{2}-\d{2}", str(manifest.get("run_id") or run_dir.name))
    if match:
        return match.group(0)
    return datetime.fromtimestamp(run_dir.stat().st_mtime, timezone.utc).date().isoformat()


def _platforms(normalized_path: Path) -> list[str]:
    values = set()
    try:
        for line in normalized_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                if row.get("platform"):
                    values.add(str(row["platform"]))
    except (OSError, json.JSONDecodeError):
        return []
    return sorted(values)


def discover_history(
    profile: Mapping[str, Any],
    search_roots: Sequence[Path],
    *,
    exclude_run: Path | None = None,
) -> list[dict[str, Any]]:
    """Find prior evidence-bearing snapshots without reading raw answer content."""

    target = identity_from_profile(profile)
    candidates: list[dict[str, Any]] = []
    seen: set[Path] = set()
    for root in search_roots:
        root = root.expanduser().resolve()
        if not root.exists():
            continue
        profiles = _profile_index(root)
        for snapshot_path in root.rglob("step5_snapshot.json"):
            if any(part in {"raw", ".git", "node_modules"} for part in snapshot_path.parts):
                continue
            run_dir = snapshot_path.parent.parent.resolve()
            if run_dir in seen or (exclude_run and run_dir == exclude_run.resolve()):
                continue
            seen.add(run_dir)
            normalized_path = run_dir / "step4" / "normalized_observations.jsonl"
            manifest_path = run_dir / "manifest.json"
            if not normalized_path.is_file() or not manifest_path.is_file():
                continue
            snapshot = _read_json(snapshot_path)
            manifest = _read_json(manifest_path)
            project_id = str(snapshot.get("project_id") or manifest.get("project_id") or "")
            historical_profile = snapshot.get("project_profile") if isinstance(snapshot.get("project_profile"), Mapping) else None
            profile_path = None
            if not historical_profile:
                matched_profiles = profiles.get(project_id, [])
                if matched_profiles:
                    profile_path, historical_profile = matched_profiles[0]
            if not historical_profile:
                identity = snapshot.get("brand_identity") or manifest.get("brand_identity") or {}
                historical_profile = {"target_scope": identity}
            history = identity_from_profile(historical_profile)
            brand_overlap = sorted(target["brands"] & history["brands"])
            domain_overlap = sorted(target["domains"] & history["domains"])
            product_overlap = sorted(target["products"] & history["products"])
            if brand_overlap and domain_overlap:
                match_type = "exact_brand_domain"
            elif brand_overlap:
                match_type = "brand_match"
            elif domain_overlap:
                match_type = "domain_match"
            else:
                continue
            rows = sum(1 for line in normalized_path.read_text(encoding="utf-8").splitlines() if line.strip())
            planned = int(manifest.get("planned_observation_count") or 0)
            synthetic = "synthetic" in _text(manifest.get("trial_scope")) or "synthetic" in _text(manifest.get("run_id"))
            candidates.append({
                "run_id": str(manifest.get("run_id") or run_dir.name),
                "run_dir": str(run_dir),
                "snapshot_id": snapshot.get("snapshot_id"),
                "snapshot_date": _run_date(run_dir, manifest),
                "project_id": project_id,
                "profile_version": snapshot.get("profile_version") or manifest.get("profile_version"),
                "question_set_version": snapshot.get("question_set_version") or manifest.get("question_set_version"),
                "competitor_catalog_version": snapshot.get("competitor_catalog_version"),
                "signal_definition_version": snapshot.get("signal_definition_version"),
                "match_type": match_type,
                "brand_overlap": brand_overlap,
                "domain_overlap": domain_overlap,
                "product_overlap": product_overlap,
                "platforms": _platforms(normalized_path),
                "valid_observation_count": rows,
                "planned_observation_count": planned or None,
                "coverage_rate": rows / planned if planned else None,
                "complete_run": bool(planned and rows == planned),
                "synthetic": synthetic,
                "profile_path": str(profile_path) if profile_path else None,
                "comparison_candidate": bool(snapshot.get("snapshot_id") and rows and not synthetic),
            })
    candidates.sort(key=lambda item: (bool(item["complete_run"]), str(item["snapshot_date"]), str(item["run_id"])), reverse=True)
    return candidates


def discovery_summary(candidates: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    comparable = [row for row in candidates if row.get("comparison_candidate")]
    preferred = [row for row in comparable if row.get("complete_run")] or comparable
    return {
        "history_found": bool(candidates),
        "candidate_count": len(candidates),
        "comparison_candidate_count": len(comparable),
        "match_types": dict(Counter(str(row.get("match_type")) for row in candidates)),
        "recommended_previous_run": preferred[0].get("run_dir") if preferred else None,
        "prompt_user_for_comparison": bool(comparable),
    }
