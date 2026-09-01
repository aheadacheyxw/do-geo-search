"""Shared deterministic values and serialization helpers for GEO v2."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import json
from typing import Any, Mapping, Tuple


@dataclass(frozen=True)
class ValidationResult:
    """A stable validation outcome suitable for machine-readable reports."""

    ok: bool
    codes: Tuple[str, ...] = ()


def canonical_json(value: Any) -> str:
    """Serialize a JSON-compatible value with stable key and Unicode handling."""

    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def sha256_json(value: Any) -> str:
    """Return the SHA-256 hash of canonical JSON."""

    return sha256(canonical_json(value).encode("utf-8")).hexdigest()


def require_fields(record: Mapping[str, Any], fields: Tuple[str, ...]) -> ValidationResult:
    """Check fields for non-empty values and return sorted stable issue codes."""

    missing = tuple(sorted(field for field in fields if not record.get(field)))
    return ValidationResult(ok=not missing, codes=missing)

