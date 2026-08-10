"""Resolve (company, AY, class, regime) → frozen hashed ResolvedRuleSet."""

from __future__ import annotations

import uuid
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models import statutory as stat
from app.services.taxation.catalogue import accessors as cat
from app.services.taxation.kernel.money import money
from app.services.taxation.kernel.types import RateBand, RebateSpec, SurchargeBand, SurchargeSpec
from app.services.taxation.registration import get_election, list_overrides
from app.services.taxation.resolve.types import ResolvedRuleSet, ResolvedSchedule

ENGINE_DEFAULT_CHARACTER = "ORDINARY"


def _bands_from_schedule(sched: stat.RateSchedule) -> tuple[RateBand, ...]:
    ordered = sorted(sched.bands, key=lambda b: b.seq)
    return tuple(
        RateBand(
            lower=money(b.lower),
            upper=money(b.upper) if b.upper is not None else None,
            rate_percent=money(b.rate_percent),
            fixed_amount=money(b.fixed_amount or 0),
        )
        for b in ordered
    )


def _surcharge_spec(sched: stat.SurchargeSchedule | None) -> SurchargeSpec | None:
    if sched is None:
        return None
    ordered = sorted(sched.bands, key=lambda b: b.seq)
    bands = tuple(
        SurchargeBand(
            lower=money(b.lower),
            upper=money(b.upper) if b.upper is not None else None,
            rate_percent=money(b.rate_percent),
        )
        for b in ordered
    )
    capped = frozenset(str(c) for c in (sched.capped_characters or []))
    return SurchargeSpec(
        bands=bands,
        marginal_relief=sched.marginal_relief_method != "None",
        capped_characters=capped,
        default_cap_percent=Decimal("15"),
    )


def _rebate_spec(
    rules: list[stat.StatutoryRebateRule],
    *,
    assessee_class_code: str,
    regime_code: str,
) -> RebateSpec | None:
    match = [
        r
        for r in rules
        if r.assessee_class_code == assessee_class_code and r.regime_code == regime_code
    ]
    if not match:
        match = [r for r in rules if r.assessee_class_code == assessee_class_code]
    if not match:
        return None
    r = match[0]
    excluded = frozenset(str(c) for c in (r.excluded_characters or []))
    return RebateSpec(
        max_taxable_income=money(r.max_taxable_income),
        max_rebate_amount=money(r.max_rebate_amount),
        marginal_relief_enabled=bool(r.marginal_relief_enabled),
        excluded_characters=excluded,
    )


async def resolve_ruleset(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    ay_code: str,
    assessee_class_code: str,
    regime_code: str | None = None,
) -> ResolvedRuleSet:
    """Build a frozen rule set from the statutory catalogue + tenant overrides."""
    fav = await cat.get_current_finance_act(db, ay_code=ay_code)
    if fav is None:
        raise ValidationError(
            f"No current Finance Act version for AY {ay_code}",
            code="missing_finance_act",
            field="ay_code",
        )

    resolved_regime = regime_code
    resolved_class = assessee_class_code
    if not resolved_regime:
        election = await get_election(db, company_id, ay_code)
        if election is not None:
            resolved_regime = election.regime_code
            resolved_class = election.assessee_class_code or resolved_class
        else:
            regimes = await cat.list_regimes(db, assessee_class_code=resolved_class)
            default = next((r for r in regimes if r.is_default), regimes[0] if regimes else None)
            if default is None:
                raise ValidationError(
                    f"No tax regime for class {resolved_class}",
                    code="missing_regime",
                    field="regime_code",
                )
            resolved_regime = default.code

    schedules_raw = await cat.list_rate_schedules(
        db,
        ay_code=ay_code,
        assessee_class_code=resolved_class,
        regime_code=resolved_regime,
    )
    if not schedules_raw:
        raise ValidationError(
            f"No rate schedules for {resolved_class}/{resolved_regime} in AY {ay_code}",
            code="missing_rate_schedule",
            field="ay_code",
        )

    overrides = await list_overrides(db, company_id, ay_code=ay_code)
    active_overrides = [o for o in overrides if not o.disabled]
    pin_codes = {
        o.target_code
        for o in active_overrides
        if o.override_kind in ("pin_rate_schedule", "rate_schedule")
    }
    disabled_codes = {
        o.target_code
        for o in active_overrides
        if o.override_kind in ("disable_rule", "disable_schedule")
    }

    if pin_codes:
        schedules_raw = [s for s in schedules_raw if s.code in pin_codes] or schedules_raw
    if disabled_codes:
        schedules_raw = [s for s in schedules_raw if s.code not in disabled_codes]
        if not schedules_raw:
            raise ValidationError(
                "All rate schedules disabled by policy overrides",
                code="schedules_disabled",
            )

    schedules = tuple(
        ResolvedSchedule(
            code=s.code,
            income_character_code=s.income_character_code,
            schedule_kind=s.schedule_kind,
            bands=_bands_from_schedule(s),
        )
        for s in schedules_raw
    )

    surcharge_rows = await cat.list_surcharge_schedules(
        db, ay_code=ay_code, assessee_class_code=resolved_class
    )
    surcharge_match = next(
        (s for s in surcharge_rows if s.regime_code in (None, "", resolved_regime)),
        surcharge_rows[0] if surcharge_rows else None,
    )
    surcharge = _surcharge_spec(surcharge_match)

    rebate_rows = await cat.list_rebate_rules(db, ay_code=ay_code)
    rebate = _rebate_spec(
        rebate_rows, assessee_class_code=resolved_class, regime_code=resolved_regime
    )

    cess_rows = await cat.list_cess_rules(db, ay_code=ay_code)
    cess_rate = money(cess_rows[0].rate_percent) if cess_rows else Decimal("4")

    characters = await cat.list_income_characters(db)
    default_char = ENGINE_DEFAULT_CHARACTER
    if characters and not any(c.code == default_char for c in characters):
        default_char = characters[0].code

    return ResolvedRuleSet(
        finance_act_version_id=fav.id,
        ay_code=ay_code,
        assessee_class_code=resolved_class,
        regime_code=resolved_regime,
        schedules=schedules,
        surcharge=surcharge,
        rebate=rebate,
        cess_rate_percent=cess_rate,
        overrides_applied=tuple(
            f"{o.override_kind}:{o.target_code}" for o in active_overrides
        ),
        default_character_code=default_char,
    )
