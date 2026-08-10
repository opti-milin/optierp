"""Pure CBDT JSON builder — maps canonical fields onto nested paths (no I/O)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Any


@dataclass(frozen=True, slots=True)
class FieldMapEntry:
    canonical_field: str
    cbdt_json_path: str
    transform: str | None = None


def apply_transform(value: Any, transform: str | None) -> Any:
    """Apply an optional transform declared on statutory.itr_field_map."""
    if value is None:
        return None
    if transform in (None, "", "identity", "string"):
        if isinstance(value, Decimal):
            return str(value)
        return value
    if transform == "rupees":
        # CBDT schemas expect whole rupees for money fields (ROUND_HALF_UP).
        amount = value if isinstance(value, Decimal) else Decimal(str(value))
        return int(amount.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    if transform == "number":
        if isinstance(value, Decimal):
            return float(value)
        return float(value)
    if transform == "boolean":
        return bool(value)
    return value


def _set_path(root: dict[str, Any], path: str, value: Any) -> None:
    parts = [p for p in path.split(".") if p]
    if not parts:
        return
    cursor: dict[str, Any] = root
    for part in parts[:-1]:
        nxt = cursor.get(part)
        if not isinstance(nxt, dict):
            nxt = {}
            cursor[part] = nxt
        cursor = nxt
    cursor[parts[-1]] = value


def build_cbdt_payload(
    field_maps: list[FieldMapEntry] | tuple[FieldMapEntry, ...],
    values: dict[str, Any],
) -> dict[str, Any]:
    """Nest canonical values under CBDT JSON paths from the statutory field map."""
    root: dict[str, Any] = {}
    for entry in field_maps:
        if entry.canonical_field not in values:
            continue
        raw = values[entry.canonical_field]
        if raw is None:
            continue
        _set_path(root, entry.cbdt_json_path, apply_transform(raw, entry.transform))
    return root


def payload_sha256(payload: dict[str, Any]) -> str:
    """Stable sha256 of canonical JSON (sorted keys, no whitespace)."""
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
