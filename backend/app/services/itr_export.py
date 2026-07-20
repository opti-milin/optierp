"""ITR-6 export pack — Phase 2.

Builds a schedule-oriented JSON (and CSV summary) from a **submitted** Income Tax
Computation for CA / offline-utility handoff. Not a live portal schema — labelled
fields map to the common ITR-6 heads we already compute.
"""

from __future__ import annotations

import csv
import io
import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.models.base import DOCSTATUS_SUBMITTED
from app.schemas.compliance import IncomeTaxSettings, Itr6ExportPack
from app.services.accounts_common import get_company
from app.services.income_tax_computation import get_computation
from app.services.income_tax_settings import get_income_tax_settings
from app.services.financial_reports.statements import balance_sheet, profit_and_loss

ZERO = Decimal("0")
Q2 = Decimal("0.01")


def _q(x: Decimal) -> Decimal:
    return Decimal(x or 0).quantize(Q2)


def advance_tax_instalments(assessment_year: str) -> list[dict]:
    """Statutory corporate advance-tax dates for an AY like '2025-26'.

    Instalments fall in the FY that precedes the AY (FY starts 1 Apr of AY-start-1).
    """
    try:
        start_year = int(assessment_year.split("-")[0]) - 1
    except (ValueError, IndexError) as exc:
        raise ValidationError(
            "assessment_year must look like '2025-26'", field="assessment_year"
        ) from exc
    # 15 Jun, 15 Sep, 15 Dec, 15 Mar of FY
    return [
        {"instalment": 1, "due_date": date(start_year, 6, 15), "cumulative_percent": 15},
        {"instalment": 2, "due_date": date(start_year, 9, 15), "cumulative_percent": 45},
        {"instalment": 3, "due_date": date(start_year, 12, 15), "cumulative_percent": 75},
        {"instalment": 4, "due_date": date(start_year + 1, 3, 15), "cumulative_percent": 100},
    ]


async def build_itr_pack(
    db: AsyncSession, doc_id: uuid.UUID, company_id: uuid.UUID
) -> Itr6ExportPack:
    """Assemble an entity ITR handoff pack (ITR-6 / ITR-3 / ITR-5) from a submitted computation."""
    doc = await get_computation(db, doc_id, company_id)
    if doc.docstatus != DOCSTATUS_SUBMITTED:
        raise ValidationError(
            "Only a submitted computation can be exported", field="docstatus"
        )
    company = await get_company(db, company_id)
    settings = await get_income_tax_settings(db, company_id)
    form = entity_form_for(settings)

    pl = await profit_and_loss(
        db, company_id, from_date=doc.from_date, to_date=doc.to_date
    )
    bs = await balance_sheet(db, company_id, as_of=doc.to_date)

    adjustments = [
        {
            "idx": ln.idx,
            "category_id": str(ln.category_id) if ln.category_id else None,
            "description": ln.description,
            "direction": ln.direction,
            "amount": str(_q(ln.amount)),
        }
        for ln in doc.adjustments
    ]

    payload: dict = {
        "form": form,
        "entity_type": settings.entity_type,
        "assessment_year": doc.assessment_year,
        "filing_regime": settings.filing_regime,
        "company": {
            "name": company.company_name,
            "pan": company.pan,
            "tan": company.tan,
            "gstin": company.tax_id,
            "abbr": company.abbr,
        },
        "period": {
            "from_date": doc.from_date.isoformat(),
            "to_date": doc.to_date.isoformat(),
        },
        "computation_ref": doc.name,
        "part_a_bs": {
            "total_assets": str(_q(bs["total_assets"])),
            "total_liabilities": str(_q(bs["total_liabilities"])),
            "total_equity": str(_q(bs["total_equity"])),
            "provisional_profit_loss": str(_q(bs["provisional_profit_loss"])),
        },
        "part_a_pl": {
            "total_income": str(_q(pl["total_income"])),
            "total_expense": str(_q(pl["total_expense"])),
            "net_profit": str(_q(pl["net_profit"])),
        },
        "schedule_bp": {
            "book_profit": str(_q(doc.book_profit)),
            "net_adjustments": str(_q(doc.net_adjustments)),
            "taxable_income": str(_q(doc.taxable_income)),
            "adjustments": adjustments,
        },
        "part_b_tti": {
            "tax_amount": str(_q(doc.tax_amount)),
            "surcharge_amount": str(_q(doc.surcharge_amount)),
            "cess_amount": str(_q(doc.cess_amount)),
            "total_tax": str(_q(doc.total_tax)),
            "tds_credit": str(_q(doc.tds_credit)),
            "advance_tax_paid": str(_q(doc.advance_tax_paid)),
            "tax_payable": str(_q(doc.tax_payable)),
        },
        "advance_tax_calendar": [
            {
                "instalment": i["instalment"],
                "due_date": i["due_date"].isoformat(),
                "cumulative_percent": i["cumulative_percent"],
            }
            for i in advance_tax_instalments(doc.assessment_year)
        ],
        "notes": [
            f"OptiReach {form} handoff pack — not the Income-tax portal schema.",
            "Hand to CA / offline utility, or use sandbox e-file for a stub acknowledgement.",
        ],
    }

    # Phase 4 lean entity worksheets (heads for CA mapping; not full portal schedules).
    if form == "ITR-3":
        payload["business_income"] = {
            "pgbp_from_books": str(_q(pl["net_profit"])),
            "note": "Proprietor PGBP seeded from P&L net profit; refine with CA before filing.",
        }
    elif form == "ITR-5":
        payload["partner_share"] = {
            "partners": [],
            "note": "Firm/LLP partner profit-share allocation — enter with CA before filing.",
        }

    return Itr6ExportPack(
        computation_id=doc.id,
        assessment_year=doc.assessment_year,
        form=form,
        payload=payload,
    )


async def build_itr6_pack(
    db: AsyncSession, doc_id: uuid.UUID, company_id: uuid.UUID
) -> Itr6ExportPack:
    """Company ITR-6 pack. Requires ``entity_type=Company``."""
    settings = await get_income_tax_settings(db, company_id)
    if settings.entity_type != "Company":
        raise ValidationError(
            f"ITR-6 export is for Company entities; this tenant is '{settings.entity_type}'. "
            "Use GET …/itr for the entity-matched pack (ITR-3/5).",
            field="entity_type",
        )
    return await build_itr_pack(db, doc_id, company_id)


def itr6_csv(pack: Itr6ExportPack) -> str:
    """Flat CSV summary of the pack's money heads (for spreadsheet handoff)."""
    p = pack.payload
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["section", "field", "value"])
    company = p.get("company", {})
    for k in ("name", "pan", "tan", "gstin"):
        w.writerow(["company", k, company.get(k, "")])
    w.writerow(["meta", "assessment_year", pack.assessment_year])
    w.writerow(["meta", "form", pack.form])
    w.writerow(["meta", "entity_type", p.get("entity_type", "")])
    for section in ("part_a_pl", "schedule_bp", "part_b_tti", "business_income", "partner_share"):
        block = p.get(section)
        if not isinstance(block, dict):
            continue
        for k, v in block.items():
            if k == "adjustments":
                continue
            w.writerow([section, k, v])
    for adj in (p.get("schedule_bp") or {}).get("adjustments") or []:
        w.writerow(
            [
                "adjustment",
                adj.get("description", ""),
                f"{adj.get('direction')}:{adj.get('amount')}",
            ]
        )
    return buf.getvalue()


def entity_form_for(settings: IncomeTaxSettings) -> str:
    """Map entity_type → ITR form code (Phase 4)."""
    return {
        "Company": "ITR-6",
        "Proprietor": "ITR-3",
        "Firm": "ITR-5",
        "LLP": "ITR-5",
    }.get(settings.entity_type, "ITR-6")
