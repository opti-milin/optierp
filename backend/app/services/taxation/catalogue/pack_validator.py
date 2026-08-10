"""Validate statutory JSON packs before load.

Ensures every (version, class, regime) rate schedule has gapless non-overlapping
bands, no dangling FK references, and every rule's provision exists.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any


class PackValidationError(ValueError):
    pass


def _d(value: Any) -> Decimal:
    if value is None:
        return Decimal("Infinity")
    return Decimal(str(value))


def _validate_bands(label: str, bands: list[dict[str, Any]]) -> None:
    if not bands:
        raise PackValidationError(f"{label}: empty bands")
    ordered = sorted(bands, key=lambda b: int(b["seq"]))
    prev_upper: Decimal | None = None
    for i, band in enumerate(ordered):
        lower = _d(band["lower"])
        upper = None if band.get("upper") is None else _d(band["upper"])
        if upper is not None and upper <= lower:
            raise PackValidationError(f"{label} seq={band['seq']}: upper <= lower")
        if i == 0 and lower != 0:
            raise PackValidationError(f"{label}: first band must start at 0")
        if prev_upper is not None and lower != prev_upper:
            raise PackValidationError(
                f"{label} seq={band['seq']}: gap/overlap — expected lower={prev_upper}, got {lower}"
            )
        prev_upper = upper
    if prev_upper is not None:
        raise PackValidationError(f"{label}: last band must be open-ended (upper=null)")


def validate_common(common: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    class_codes = {c["code"] for c in common.get("assessee_classes", [])}
    if not class_codes:
        errors.append("common: no assessee_classes")
    char_codes = {c["code"] for c in common.get("income_characters", [])}
    if "ORDINARY" not in char_codes:
        errors.append("common: ORDINARY income_character required")
    prov_codes = {p["section_code"] for p in common.get("provisions", [])}
    if not prov_codes:
        errors.append("common: no provisions")
    return errors


def validate_pack(pack: dict[str, Any], *, common: dict[str, Any] | None = None) -> None:
    errors: list[str] = []
    if common is not None:
        errors.extend(validate_common(common))
        class_codes = {c["code"] for c in common["assessee_classes"]}
        char_codes = {c["code"] for c in common["income_characters"]}
        prov_codes = {p["section_code"] for p in common["provisions"]}
    else:
        class_codes = set()
        char_codes = set()
        prov_codes = set()

    ay = pack.get("ay") or {}
    if not ay.get("code"):
        errors.append("pack: ay.code required")

    fav = pack.get("finance_act_version") or {}
    if not fav.get("version"):
        errors.append("pack: finance_act_version.version required")

    for regime in pack.get("regimes", []):
        if class_codes and regime["assessee_class_code"] not in class_codes:
            errors.append(f"regime {regime['code']}: unknown class {regime['assessee_class_code']}")

    for sched in pack.get("rate_schedules", []):
        label = f"rate_schedule {sched.get('code')}"
        if class_codes and sched["assessee_class_code"] not in class_codes:
            errors.append(f"{label}: unknown class")
        ic = sched.get("income_character_code")
        if ic and char_codes and ic not in char_codes:
            errors.append(f"{label}: unknown income_character {ic}")
        try:
            _validate_bands(label, sched.get("bands") or [])
        except PackValidationError as exc:
            errors.append(str(exc))

    for sched in pack.get("surcharge_schedules", []):
        label = f"surcharge_schedule {sched.get('code')}"
        try:
            _validate_bands(label, sched.get("bands") or [])
        except PackValidationError as exc:
            errors.append(str(exc))
        for ch in sched.get("capped_characters") or []:
            if char_codes and ch not in char_codes:
                errors.append(f"{label}: unknown capped character {ch}")

    for rp in pack.get("rule_packs", []):
        for rule in rp.get("rules") or []:
            sec = rule.get("provision_section_code")
            if prov_codes and sec not in prov_codes:
                errors.append(f"rule {rule.get('rule_code')}: unknown provision {sec}")

    if errors:
        raise PackValidationError("; ".join(errors))
