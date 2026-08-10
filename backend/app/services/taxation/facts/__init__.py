"""Decimal-only fact adapters for the taxation pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.taxation.kernel.money import ZERO, money, q


@dataclass(frozen=True, slots=True)
class IncomeFact:
    head: str
    income_character_code: str
    gross: Decimal
    deductions: Decimal
    net: Decimal
    sub_ref: str | None = None


@dataclass(frozen=True, slots=True)
class AdjustmentFact:
    """Worksheet adjustment before / during a run."""

    section_code: str
    rule_code: str | None
    stage: str
    description: str | None
    direction: str
    amount: Decimal
    override_amount: Decimal | None
    status: str

    @property
    def override_key(self) -> str:
        return f"{self.rule_code or ''}|{self.section_code}|{self.description or ''}"

    @property
    def applied_amount(self) -> Decimal:
        if self.override_amount is not None:
            return q(money(self.override_amount))
        return q(money(self.amount))


def income_facts_from_lines(rows: list) -> tuple[IncomeFact, ...]:
    """Build facts from ORM income lines (attributes accessed, no float)."""
    out: list[IncomeFact] = []
    for row in rows:
        gross = money(row.gross)
        deductions = money(row.deductions)
        net = money(row.net) if row.net is not None else q(gross - deductions)
        out.append(
            IncomeFact(
                head=str(row.head or "PGBP"),
                income_character_code=str(row.income_character_code or "ORDINARY"),
                gross=q(gross),
                deductions=q(deductions),
                net=q(net),
                sub_ref=row.sub_ref,
            )
        )
    return tuple(out)


def adjustment_facts_from_lines(rows: list, *, draft_only: bool = True) -> tuple[AdjustmentFact, ...]:
    """Manual / draft worksheet lines (run_id is NULL) become adjustment facts."""
    out: list[AdjustmentFact] = []
    for row in rows:
        if draft_only and getattr(row, "run_id", None) is not None:
            continue
        out.append(
            AdjustmentFact(
                section_code=str(row.section_code or ""),
                rule_code=row.rule_code,
                stage=str(row.stage or "PGBP"),
                description=row.description,
                direction=str(row.direction or "Add"),
                amount=money(row.amount or 0),
                override_amount=(
                    money(row.override_amount) if row.override_amount is not None else None
                ),
                status=str(row.status or "Manual"),
            )
        )
    return tuple(out)


def net_by_character(facts: tuple[IncomeFact, ...]) -> dict[str, Decimal]:
    totals: dict[str, Decimal] = {}
    for f in facts:
        totals[f.income_character_code] = q(totals.get(f.income_character_code, ZERO) + f.net)
    return totals


def apply_adjustments(base: Decimal, adjustments: tuple[AdjustmentFact, ...]) -> Decimal:
    total = money(base)
    for adj in adjustments:
        amt = adj.applied_amount
        if adj.direction.lower() in ("deduct", "less", "deduction"):
            total = q(total - amt)
        else:
            total = q(total + amt)
    return total
