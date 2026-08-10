"""Frozen ResolvedRuleSet — hashed for run reproducibility."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal

from app.services.taxation.kernel.types import RateBand, RebateSpec, SurchargeSpec


def _dec(v: Decimal | None) -> str | None:
    if v is None:
        return None
    return format(v, "f")


@dataclass(frozen=True, slots=True)
class ResolvedSchedule:
    code: str
    income_character_code: str | None
    schedule_kind: str
    bands: tuple[RateBand, ...]


@dataclass(frozen=True, slots=True)
class ResolvedRuleSet:
    """Law snapshot for one (company, AY, class, regime) resolution."""

    finance_act_version_id: uuid.UUID
    ay_code: str
    assessee_class_code: str
    regime_code: str
    schedules: tuple[ResolvedSchedule, ...]
    surcharge: SurchargeSpec | None
    rebate: RebateSpec | None
    cess_rate_percent: Decimal
    overrides_applied: tuple[str, ...] = field(default_factory=tuple)
    default_character_code: str = "ORDINARY"

    def canonical_dict(self) -> dict:
        schedules = []
        for s in self.schedules:
            schedules.append(
                {
                    "code": s.code,
                    "income_character_code": s.income_character_code,
                    "schedule_kind": s.schedule_kind,
                    "bands": [
                        {
                            "lower": _dec(b.lower),
                            "upper": _dec(b.upper),
                            "rate_percent": _dec(b.rate_percent),
                            "fixed_amount": _dec(b.fixed_amount),
                        }
                        for b in s.bands
                    ],
                }
            )
        surcharge = None
        if self.surcharge is not None:
            surcharge = {
                "marginal_relief": self.surcharge.marginal_relief,
                "default_cap_percent": _dec(self.surcharge.default_cap_percent),
                "capped_characters": sorted(self.surcharge.capped_characters),
                "bands": [
                    {
                        "lower": _dec(b.lower),
                        "upper": _dec(b.upper),
                        "rate_percent": _dec(b.rate_percent),
                    }
                    for b in self.surcharge.bands
                ],
            }
        rebate = None
        if self.rebate is not None:
            rebate = {
                "max_taxable_income": _dec(self.rebate.max_taxable_income),
                "max_rebate_amount": _dec(self.rebate.max_rebate_amount),
                "marginal_relief_enabled": self.rebate.marginal_relief_enabled,
                "excluded_characters": sorted(self.rebate.excluded_characters),
            }
        return {
            "finance_act_version_id": str(self.finance_act_version_id),
            "ay_code": self.ay_code,
            "assessee_class_code": self.assessee_class_code,
            "regime_code": self.regime_code,
            "schedules": schedules,
            "surcharge": surcharge,
            "rebate": rebate,
            "cess_rate_percent": _dec(self.cess_rate_percent),
            "overrides_applied": list(self.overrides_applied),
            "default_character_code": self.default_character_code,
        }

    def hash(self) -> str:
        payload = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def ruleset_as_debug(ruleset: ResolvedRuleSet) -> dict:
    """JSON-safe dump for explain endpoints (not used for hashing)."""
    raw = asdict(ruleset)
    raw["finance_act_version_id"] = str(ruleset.finance_act_version_id)
    raw["ruleset_hash"] = ruleset.hash()
    return raw
