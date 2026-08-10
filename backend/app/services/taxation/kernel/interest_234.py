"""Interest 234A / 234B / 234C — pure kernel (no DB)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from app.services.taxation.kernel.money import ZERO, money, percent_of, q

# Advance-tax / 234B/C not attracted when assessed tax ≤ this (s.208).
ADVANCE_TAX_THRESHOLD = Decimal("10000")
# 234B: advance tax must be ≥ 90% of assessed tax.
SECTION_234B_THRESHOLD_PERCENT = Decimal("90")


@dataclass(frozen=True, slots=True)
class AdvanceInstalment:
    seq: int
    due_date: date
    cumulative_percent: Decimal
    code: str = ""
    label: str = ""


@dataclass(frozen=True, slots=True)
class ChallanPayment:
    deposit_date: date
    amount: Decimal
    challan_type: str  # AdvanceTax | SelfAssessment | RegularAssessment | …


@dataclass(frozen=True, slots=True)
class Interest234Input:
    assessed_tax: Decimal
    """Tax liability before TDS/advance credits (post MAT if applied)."""
    tds_tcs_credit: Decimal = ZERO
    advance_payments: tuple[ChallanPayment, ...] = ()
    instalments: tuple[AdvanceInstalment, ...] = ()
    itr_due_date: date | None = None
    return_filed_date: date | None = None
    as_of_date: date | None = None
    """End date for open 234A/B when return not yet filed (defaults to today)."""
    rate_234a_percent: Decimal = Decimal("1")
    rate_234b_percent: Decimal = Decimal("1")
    rate_234c_percent: Decimal = Decimal("1")
    ay_start: date | None = None
    """Assessment year start (1 Apr of AY) — 234B runs from this date."""


@dataclass(frozen=True, slots=True)
class Interest234Result:
    interest_234a: Decimal
    interest_234b: Decimal
    interest_234c: Decimal
    total_interest: Decimal
    assessed_tax: Decimal
    tax_after_tds: Decimal
    advance_tax_paid: Decimal
    breakdown: dict[str, object]


def months_or_part(start: date, end: date) -> int:
    """Months or part of a month from the day after ``start`` through ``end``.

    If ``end`` ≤ ``start``, returns 0. A part of a month counts as a full month.
    """
    if end <= start:
        return 0
    period_start = _add_days(start, 1)
    if end < period_start:
        return 0
    return _month_parts(period_start, end)


def _add_days(d: date, n: int) -> date:
    from datetime import timedelta

    return d + timedelta(days=n)


def _month_parts(period_start: date, period_end: date) -> int:
    """Months or part thereof between two inclusive dates."""
    if period_end < period_start:
        return 0
    years = period_end.year - period_start.year
    months = period_end.month - period_start.month
    total = years * 12 + months
    if period_end.day >= period_start.day:
        total += 1
    else:
        total += 1
    return max(total, 1)

def _sum_advance_by(
    payments: tuple[ChallanPayment, ...], *, on_or_before: date
) -> Decimal:
    total = ZERO
    for p in payments:
        if p.challan_type != "AdvanceTax":
            continue
        if p.deposit_date <= on_or_before:
            total = q(total + money(p.amount))
    return total


def _sum_all_advance(payments: tuple[ChallanPayment, ...]) -> Decimal:
    return _sum_advance_by(
        payments,
        on_or_before=date(9999, 12, 31),
    )


def compute_interest_234(inp: Interest234Input) -> Interest234Result:
    assessed = q(money(inp.assessed_tax))
    tds = q(money(inp.tds_tcs_credit))
    tax_after_tds = q(assessed - tds)
    if tax_after_tds < ZERO:
        tax_after_tds = ZERO

    advance_paid = _sum_all_advance(inp.advance_payments)
    as_of = inp.as_of_date or date.today()
    filed = inp.return_filed_date

    i_a = ZERO
    i_b = ZERO
    i_c = ZERO
    detail_a: dict[str, object] = {}
    detail_b: dict[str, object] = {}
    detail_c: list[dict[str, object]] = []

    # --- 234A ---
    # Base ≈ tax after TDS less advance tax (self-assessment unpaid).
    base_a = q(tax_after_tds - advance_paid)
    if base_a < ZERO:
        base_a = ZERO
    if inp.itr_due_date is not None and base_a > ZERO:
        end_a = filed if filed is not None else as_of
        months_a = months_or_part(inp.itr_due_date, end_a)
        if months_a > 0 and (filed is None or filed > inp.itr_due_date):
            i_a = q(percent_of(base_a, inp.rate_234a_percent) * months_a)
        detail_a = {
            "base": format(base_a, "f"),
            "due_date": inp.itr_due_date.isoformat(),
            "end_date": end_a.isoformat(),
            "months": months_a,
            "rate_percent": format(money(inp.rate_234a_percent), "f"),
            "interest": format(i_a, "f"),
        }

    # --- 234B ---
    # Attracted when advance tax paid < 90% of assessed tax (after TDS relief on assessed).
    # Interest from AY start (1 Apr) to filing / as_of on (assessed − advance).
    assessed_for_adv = tax_after_tds  # practical: tax net of TDS
    if assessed_for_adv > ADVANCE_TAX_THRESHOLD and inp.ay_start is not None:
        required_90 = q(percent_of(assessed_for_adv, SECTION_234B_THRESHOLD_PERCENT))
        if advance_paid < required_90:
            shortfall_b = q(assessed_for_adv - advance_paid)
            if shortfall_b < ZERO:
                shortfall_b = ZERO
            end_b = filed if filed is not None else as_of
            # 234B from 1 April of AY (= ay_start) — first day of AY starts interest
            # Period: from ay_start to end_b (months or part from 1 Apr).
            months_b = _month_parts(inp.ay_start, end_b) if end_b >= inp.ay_start else 0
            if months_b > 0 and shortfall_b > ZERO:
                i_b = q(percent_of(shortfall_b, inp.rate_234b_percent) * months_b)
            detail_b = {
                "assessed_for_advance": format(assessed_for_adv, "f"),
                "required_90_percent": format(required_90, "f"),
                "advance_paid": format(advance_paid, "f"),
                "shortfall": format(shortfall_b, "f"),
                "from": inp.ay_start.isoformat(),
                "to": end_b.isoformat(),
                "months": months_b,
                "interest": format(i_b, "f"),
            }
        else:
            detail_b = {"attracted": False, "advance_paid": format(advance_paid, "f")}

    # --- 234C ---
    if assessed_for_adv > ADVANCE_TAX_THRESHOLD and inp.instalments:
        for inst in inp.instalments:
            required = q(percent_of(assessed_for_adv, inst.cumulative_percent))
            paid_by = _sum_advance_by(inp.advance_payments, on_or_before=inst.due_date)
            shortfall = q(required - paid_by)
            if shortfall <= ZERO:
                detail_c.append(
                    {
                        "seq": inst.seq,
                        "due_date": inst.due_date.isoformat(),
                        "required": format(required, "f"),
                        "paid": format(paid_by, "f"),
                        "shortfall": "0",
                        "interest": "0",
                    }
                )
                continue
            # Last instalment (Mar 15): interest for 1 month; earlier: 3 months.
            is_last = inst.seq == max(i.seq for i in inp.instalments)
            months_c = 1 if is_last else 3
            part = q(percent_of(shortfall, inp.rate_234c_percent) * months_c)
            i_c = q(i_c + part)
            detail_c.append(
                {
                    "seq": inst.seq,
                    "code": inst.code,
                    "due_date": inst.due_date.isoformat(),
                    "required_percent": format(inst.cumulative_percent, "f"),
                    "required": format(required, "f"),
                    "paid": format(paid_by, "f"),
                    "shortfall": format(shortfall, "f"),
                    "months": months_c,
                    "interest": format(part, "f"),
                }
            )

    total = q(i_a + i_b + i_c)
    return Interest234Result(
        interest_234a=i_a,
        interest_234b=i_b,
        interest_234c=i_c,
        total_interest=total,
        assessed_tax=assessed,
        tax_after_tds=tax_after_tds,
        advance_tax_paid=advance_paid,
        breakdown={
            "234a": detail_a,
            "234b": detail_b,
            "234c": detail_c,
            "total_interest": format(total, "f"),
        },
    )


@dataclass(frozen=True, slots=True)
class InstalmentProjection:
    seq: int
    code: str
    label: str
    due_date: date
    cumulative_percent: Decimal
    required_cumulative: Decimal
    paid_to_date: Decimal
    shortfall: Decimal
    suggested_payment: Decimal
    status: str  # Paid | Short | Upcoming | Overdue


def project_advance_tax(
    *,
    estimated_tax: Decimal,
    tds_tcs_credit: Decimal,
    payments: tuple[ChallanPayment, ...],
    instalments: tuple[AdvanceInstalment, ...],
    as_of: date | None = None,
) -> tuple[InstalmentProjection, ...]:
    """Per-instalment shortfall projection for the compliance calendar."""
    today = as_of or date.today()
    net = q(money(estimated_tax) - money(tds_tcs_credit))
    if net < ZERO:
        net = ZERO
    out: list[InstalmentProjection] = []
    for inst in instalments:
        required = q(percent_of(net, inst.cumulative_percent))
        paid = _sum_advance_by(payments, on_or_before=min(inst.due_date, today))
        # For upcoming instalments, count all advance paid so far (even after prior due).
        if inst.due_date >= today:
            paid = _sum_advance_by(payments, on_or_before=today)
        shortfall = q(required - paid)
        if shortfall < ZERO:
            shortfall = ZERO
        if shortfall == ZERO and paid >= required:
            status = "Paid"
        elif inst.due_date < today:
            status = "Overdue" if shortfall > ZERO else "Paid"
        elif inst.due_date == today:
            status = "Short" if shortfall > ZERO else "Paid"
        else:
            status = "Upcoming"
        out.append(
            InstalmentProjection(
                seq=inst.seq,
                code=inst.code,
                label=inst.label or inst.code,
                due_date=inst.due_date,
                cumulative_percent=inst.cumulative_percent,
                required_cumulative=required,
                paid_to_date=paid,
                shortfall=shortfall,
                suggested_payment=shortfall,
                status=status,
            )
        )
    return tuple(out)
