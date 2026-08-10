"""Generate AY 2025-26 and 2026-27 packs from 2024-25 with corrected rates."""

from __future__ import annotations

import copy
import json
from pathlib import Path

BASE = Path(__file__).resolve().parents[1] / "data" / "statutory" / "in"


def main() -> None:
    p24 = json.loads((BASE / "2024-25" / "pack.json").read_text(encoding="utf-8"))

    p25 = copy.deepcopy(p24)
    p25["ay"] = {
        "code": "2025-26",
        "ay_start": "2025-04-01",
        "ay_end": "2026-03-31",
        "fy_start": "2024-04-01",
        "fy_end": "2025-03-31",
        "prev_ay_code": "2024-25",
    }
    p25["finance_act_version"] = {
        "version": "FA2024",
        "enacted_on": "2024-08-16",
        "source_ref": "Finance (No. 2) Act 2024",
        "is_current": True,
    }
    for s in p25["rate_schedules"]:
        if s["code"] == "IND_115BAC":
            s["remarks"] = "Finance (No.2) Act 2024 new regime slabs"
            s["bands"] = [
                {"seq": 1, "lower": "0", "upper": "300000", "rate_percent": "0", "fixed_amount": "0"},
                {"seq": 2, "lower": "300000", "upper": "700000", "rate_percent": "5", "fixed_amount": "0"},
                {"seq": 3, "lower": "700000", "upper": "1000000", "rate_percent": "10", "fixed_amount": "0"},
                {"seq": 4, "lower": "1000000", "upper": "1200000", "rate_percent": "15", "fixed_amount": "0"},
                {"seq": 5, "lower": "1200000", "upper": "1500000", "rate_percent": "20", "fixed_amount": "0"},
                {"seq": 6, "lower": "1500000", "upper": None, "rate_percent": "30", "fixed_amount": "0"},
            ]
        if s["code"] in ("SPEC_LTCG_112A", "CO_SPEC_LTCG_112A"):
            s["bands"][0]["rate_percent"] = "12.5"
        if s["code"] in ("SPEC_STCG_111A", "CO_SPEC_STCG_111A"):
            s["bands"][0]["rate_percent"] = "20"
    (BASE / "2025-26" / "pack.json").write_text(json.dumps(p25, indent=2) + "\n", encoding="utf-8")

    p26 = copy.deepcopy(p25)
    p26["ay"] = {
        "code": "2026-27",
        "ay_start": "2026-04-01",
        "ay_end": "2027-03-31",
        "fy_start": "2025-04-01",
        "fy_end": "2026-03-31",
        "prev_ay_code": "2025-26",
    }
    p26["finance_act_version"] = {
        "version": "FA2025",
        "enacted_on": "2025-03-29",
        "source_ref": "Finance Act 2025",
        "is_current": True,
    }
    for s in p26["rate_schedules"]:
        if s["code"] == "IND_115BAC":
            s["remarks"] = "Finance Act 2025 new regime slabs"
            s["bands"] = [
                {"seq": 1, "lower": "0", "upper": "400000", "rate_percent": "0", "fixed_amount": "0"},
                {"seq": 2, "lower": "400000", "upper": "800000", "rate_percent": "5", "fixed_amount": "0"},
                {"seq": 3, "lower": "800000", "upper": "1200000", "rate_percent": "10", "fixed_amount": "0"},
                {"seq": 4, "lower": "1200000", "upper": "1600000", "rate_percent": "15", "fixed_amount": "0"},
                {"seq": 5, "lower": "1600000", "upper": "2000000", "rate_percent": "20", "fixed_amount": "0"},
                {"seq": 6, "lower": "2000000", "upper": "2400000", "rate_percent": "25", "fixed_amount": "0"},
                {"seq": 7, "lower": "2400000", "upper": None, "rate_percent": "30", "fixed_amount": "0"},
            ]
    for r in p26["rebate_rules"]:
        if r["code"] == "87A_115BAC":
            r["max_taxable_income"] = "1200000"
            r["max_rebate_amount"] = "60000"
            r["marginal_relief_enabled"] = True
    (BASE / "2026-27" / "pack.json").write_text(json.dumps(p26, indent=2) + "\n", encoding="utf-8")
    print("wrote 2025-26 and 2026-27 packs")


if __name__ == "__main__":
    main()
