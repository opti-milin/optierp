"""Unit tests for statutory pack validation (no DB)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.services.taxation.catalogue.pack_validator import PackValidationError, validate_pack

PACK_ROOT = Path(__file__).resolve().parents[4] / "data" / "statutory" / "in"


def _common() -> dict:
    return json.loads((PACK_ROOT / "_common.json").read_text(encoding="utf-8"))


def _pack(ay: str) -> dict:
    return json.loads((PACK_ROOT / ay / "pack.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("ay", ["2024-25", "2025-26", "2026-27"])
def test_shipped_packs_validate(ay: str) -> None:
    if not (PACK_ROOT / ay / "pack.json").exists():
        pytest.skip(f"pack {ay} not generated yet")
    validate_pack(_pack(ay), common=_common())


def test_company_normal_surcharge_has_7_and_12() -> None:
    pack = _pack("2024-25")
    sur = next(s for s in pack["surcharge_schedules"] if s["code"] == "CO_NORMAL_SUR")
    rates = [b["rate_percent"] for b in sur["bands"]]
    assert "7" in rates
    assert "12" in rates


def test_new_regime_slabs_differ_by_ay() -> None:
    if not (PACK_ROOT / "2025-26" / "pack.json").exists():
        pytest.skip("2025-26 pack missing")
    if not (PACK_ROOT / "2026-27" / "pack.json").exists():
        pytest.skip("2026-27 pack missing")

    def bac_bands(ay: str) -> list[tuple[str, str | None, str]]:
        pack = _pack(ay)
        sched = next(s for s in pack["rate_schedules"] if s["code"] == "IND_115BAC")
        return [(b["lower"], b["upper"], b["rate_percent"]) for b in sched["bands"]]

    assert bac_bands("2024-25") != bac_bands("2025-26")
    assert bac_bands("2025-26") != bac_bands("2026-27")
    # AY 2026-27 starts with ₹4L nil band (Budget 2025)
    assert bac_bands("2026-27")[0] == ("0", "400000", "0")


def test_gap_in_bands_rejected() -> None:
    pack = _pack("2024-25")
    common = _common()
    sched = next(s for s in pack["rate_schedules"] if s["code"] == "IND_OLD_GENERAL")
    sched["bands"][1]["lower"] = "260000"  # gap after 250000
    with pytest.raises(PackValidationError, match="gap|overlap"):
        validate_pack(pack, common=common)


def test_dangling_provision_rejected() -> None:
    pack = _pack("2024-25")
    common = _common()
    pack["rule_packs"][0]["rules"][0]["provision_section_code"] = "NO-SUCH-SECTION"
    with pytest.raises(PackValidationError, match="unknown provision"):
        validate_pack(pack, common=common)
