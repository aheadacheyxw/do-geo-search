"""Deterministic local JSON and JSONL read/write helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable

from .contracts import canonical_json


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.write_text(canonical_json(value) + "\n", encoding="utf-8")


def write_jsonl(path: Path, records: Iterable[Any]) -> None:
    path.write_text("".join(canonical_json(record) + "\n" for record in records), encoding="utf-8")
