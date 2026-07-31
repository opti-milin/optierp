"""Contribution Margin report — classify P&L balances into CM1 / CM2 / CM3.

Reuses ``_balances`` / account tree from the financial-reports helpers.
Statutory P&L is unchanged; this is an additive managerial view.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.accounts import GLEntry
from app.schemas.accounts import (
    ContributionMarginReport,
    ContributionMarginSection,
    FinancialStatementRow,
)
from app.services.cm_classification import get_cm_settings
from app.services.financial_reports._helpers import ZERO, _account_map, _balances

CM_SECTION_ORDER: tuple[str, ...] = (
    "revenue",
    "variable_cost",
    "product_channel_fixed",
    "segment_bu_fixed",
    "corporate_overhead",
)

CM_SECTION_LABELS: dict[str, str] = {
    "revenue": "Revenue",
    "variable_cost": "Variable costs",
    "product_channel_fixed": "Product / Channel fixed costs",
    "segment_bu_fixed": "Segment / Business Unit fixed costs",
    "corporate_overhead": "Corporate overhead",
    "unclassified": "Unclassified",
}


def _signed_amount(cm_class: str | None, debit: Decimal, credit: Decimal) -> Decimal:
    """Normalize GL net to a positive contribution for the section.

    Revenue (income-like): credits increase the section → ``credit - debit``.
    Cost classes (expense-like): debits increase the section → ``debit - credit``.
    """
    net = debit - credit
    if cm_class == "revenue":
        return -net
    return net


def _pct(part: Decimal, whole: Decimal) -> Decimal | None:
    if whole == ZERO:
        return None
    return (part * Decimal("100") / whole).quantize(Decimal("0.01"))


def build_cm_waterfall(
    *,
    from_date: date,
    to_date: date,
    cost_center_id: uuid.UUID | None,
    account_meta: dict[uuid.UUID, dict[str, Any]],
    leaf_dc: dict[uuid.UUID, tuple[Decimal, Decimal]],
    unclassified_policy: str = "bucket",
    show_zero_rows: bool = False,
    warnings: list[str] | None = None,
) -> ContributionMarginReport:
    """Pure waterfall builder (unit-testable without DB).

    ``account_meta`` maps account_id → {
        account_name, root_type, report_type, is_group, path, cm_class
    }.
    ``leaf_dc`` maps account_id → (debit, credit) for the period.
    """
    warnings = list(warnings or [])
    buckets: dict[str | None, list[FinancialStatementRow]] = {c: [] for c in CM_SECTION_ORDER}
    buckets[None] = []
    totals: dict[str | None, Decimal] = {c: ZERO for c in CM_SECTION_ORDER}
    totals[None] = ZERO

    for account_id, (debit, credit) in leaf_dc.items():
        meta = account_meta.get(account_id)
        if meta is None:
            continue
        if meta.get("is_group"):
            continue
        if meta.get("report_type") != "Profit and Loss":
            continue
        cm_class = meta.get("cm_class")
        amount = _signed_amount(cm_class, Decimal(debit), Decimal(credit))
        if amount == ZERO and not show_zero_rows:
            continue
        row = FinancialStatementRow(
            account_id=account_id,
            account_name=meta["account_name"],
            root_type=meta.get("root_type"),
            is_group=False,
            indent=str(meta.get("path", "")).count("."),
            amount=amount,
        )
        key: str | None = cm_class if cm_class in CM_SECTION_ORDER else None
        buckets[key].append(row)
        totals[key] += amount

    for key in list(buckets.keys()):
        buckets[key].sort(key=lambda r: (r.indent, r.account_name))

    unclassified_total = totals[None]
    if unclassified_total != ZERO:
        if unclassified_policy == "error":
            names = [r.account_name for r in buckets[None][:10]]
            raise ValidationError(
                "Unclassified P&L activity blocks Contribution Margin: "
                + ", ".join(names)
                + ("…" if len(buckets[None]) > 10 else ""),
                code="CM_UNCLASSIFIED",
            )
        if unclassified_policy == "exclude":
            warnings.append(
                f"Excluded {len(buckets[None])} unclassified P&L account(s) "
                f"totalling {unclassified_total} — report may understate costs/revenue."
            )
            buckets[None] = []
            unclassified_total = ZERO
        else:
            warnings.append(
                f"{len(buckets[None])} unclassified P&L account(s) "
                f"({unclassified_total}) — apply a CM template or set cm_class on accounts."
            )

    revenue = totals["revenue"]
    variable = totals["variable_cost"]
    prod_fixed = totals["product_channel_fixed"]
    seg_fixed = totals["segment_bu_fixed"]
    corp = totals["corporate_overhead"]

    cm1 = revenue - variable
    cm2 = cm1 - prod_fixed
    cm3 = cm2 - seg_fixed
    operating = cm3 - corp

    sections: list[ContributionMarginSection] = []
    for cm_class in CM_SECTION_ORDER:
        total = totals[cm_class]
        if cm_class == "revenue":
            pct = Decimal("100.00") if revenue != ZERO else None
        else:
            pct = _pct(total, revenue)
        sections.append(
            ContributionMarginSection(
                cm_class=cm_class,
                label=CM_SECTION_LABELS[cm_class],
                rows=buckets[cm_class],
                total=total,
                pct_of_revenue=pct,
            )
        )
    if buckets[None]:
        sections.append(
            ContributionMarginSection(
                cm_class=None,
                label=CM_SECTION_LABELS["unclassified"],
                rows=buckets[None],
                total=unclassified_total,
                pct_of_revenue=_pct(unclassified_total, revenue),
            )
        )

    return ContributionMarginReport(
        from_date=from_date,
        to_date=to_date,
        cost_center_id=cost_center_id,
        sections=sections,
        revenue=revenue,
        variable_cost=variable,
        product_channel_fixed=prod_fixed,
        segment_bu_fixed=seg_fixed,
        corporate_overhead=corp,
        cm1=cm1,
        cm2=cm2,
        cm3=cm3,
        operating_profit=operating,
        cm1_pct=_pct(cm1, revenue),
        cm2_pct=_pct(cm2, revenue),
        cm3_pct=_pct(cm3, revenue),
        unclassified_total=unclassified_total,
        warnings=warnings,
    )


async def contribution_margin(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    from_date: date,
    to_date: date,
    cost_center_id: uuid.UUID | None = None,
) -> ContributionMarginReport:
    if from_date > to_date:
        raise ValidationError("from_date must be before to_date")

    settings = await get_cm_settings(db, company_id)
    accounts = await _account_map(db, company_id)
    period = await _balances(
        db,
        company_id,
        from_date=from_date,
        to_date=to_date,
        cost_center_id=cost_center_id,
    )

    warnings: list[str] = []
    if cost_center_id is not None:
        null_cc = await db.scalar(
            select(func.count())
            .select_from(GLEntry)
            .where(
                GLEntry.company_id == company_id,
                GLEntry.posting_date >= from_date,
                GLEntry.posting_date <= to_date,
                GLEntry.cost_center_id.is_(None),
            )
        )
        if null_cc:
            warnings.append(
                f"{null_cc} GL posting(s) in this period have no cost center "
                "and are excluded from the filtered report."
            )

    meta: dict[uuid.UUID, dict[str, Any]] = {
        a_id: {
            "account_name": a.account_name,
            "root_type": a.root_type,
            "report_type": a.report_type,
            "is_group": a.is_group,
            "path": a.path,
            "cm_class": a.cm_class,
        }
        for a_id, a in accounts.items()
    }

    return build_cm_waterfall(
        from_date=from_date,
        to_date=to_date,
        cost_center_id=cost_center_id,
        account_meta=meta,
        leaf_dc=period,
        unclassified_policy=settings.unclassified_policy,
        show_zero_rows=settings.show_zero_rows,
        warnings=warnings,
    )
