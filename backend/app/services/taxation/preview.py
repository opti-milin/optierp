"""Non-persisting dry-run compute for the Income Tax workspace result rail.

INVARIANT: nothing in this module writes. ``preview_computation`` performs no
``db.add``, no ``db.flush``, no ``db.commit`` and never assigns to a persisted ORM
attribute; the unsaved editor overlay is turned into throwaway value objects
instead. The append-only run/result chain stays the only source of persisted
numbers, so a CA can experiment without polluting the audit trail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import cast

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.models.tax_computation import TaxComputation, TaxComputationRun
from app.schemas.taxation import (
    TaxComputationAdjustmentLineIn,
    TaxComputationIncomeLineIn,
    TaxPreviewLineOut,
    TaxPreviewOut,
    TaxPreviewRequest,
)
from app.services.taxation import credits as credit_service
from app.services.taxation import depreciation as dep_service
from app.services.taxation import interest as interest_service
from app.services.taxation import loss_setoff as loss_service
from app.services.taxation import mat_credit as mat_service
from app.services.taxation.facts import (
    AdjustmentFact,
    IncomeFact,
    adjustment_facts_from_lines,
    apply_adjustments,
    income_facts_from_lines,
)
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.kernel.setoff import BroughtForwardLoss
from app.services.taxation.pipeline import ENGINE_VERSION, input_hash, run_pipeline
from app.services.taxation.resolve import resolve_ruleset
from app.services.taxation.resolve.types import ResolvedRuleSet

DEDUCT_DIRECTIONS = frozenset({"deduct", "less", "deduction"})


@dataclass(frozen=True, slots=True)
class InterestSubject:
    """Read-only stand-in for a ``TaxComputation`` passed to the interest service.

    ``interest.compute_for_computation`` only reads attributes. Building this
    instead of assigning overlay values onto the ORM row is what keeps preview
    free of writes.
    """

    id: uuid.UUID
    company_id: uuid.UUID
    ay_code: str
    assessee_class_code: str
    audit_applicable: bool
    return_filed_date: date | None
    itr_due_date_override: date | None


@dataclass(frozen=True, slots=True)
class PreviewInputs:
    """Everything the pipeline needs, resolved from persisted rows + overlay."""

    ruleset: ResolvedRuleSet
    income_facts: tuple[IncomeFact, ...]
    adjustment_facts: tuple[AdjustmentFact, ...]
    bf_losses: tuple[BroughtForwardLoss, ...]
    bf_fingerprint: str
    tax_depreciation_total: Decimal
    available_mat_credit: Decimal
    book_profit_115jb: Decimal | None
    ruleset_hash: str
    input_hash: str
    notes: tuple[str, ...] = ()


def income_facts_from_overlay(lines: list[TaxComputationIncomeLineIn]) -> tuple[IncomeFact, ...]:
    """Unsaved income rows become facts; ``net`` derives from gross − deductions."""
    out: list[IncomeFact] = []
    for line in lines:
        gross = q(money(line.gross))
        deductions = q(money(line.deductions))
        net = q(money(line.net)) if line.net is not None else q(gross - deductions)
        out.append(
            IncomeFact(
                head=line.head or "PGBP",
                income_character_code=line.income_character_code or "ORDINARY",
                gross=gross,
                deductions=deductions,
                net=net,
                sub_ref=line.sub_ref,
            )
        )
    return tuple(out)


def adjustment_facts_from_overlay(
    lines: list[TaxComputationAdjustmentLineIn],
) -> tuple[AdjustmentFact, ...]:
    """Unsaved adjustment rows become facts.

    Rows carrying a ``run_id`` are the engine's own audit record of an earlier saved run.
    A client that echoes the whole list back would otherwise have the same adjustment
    counted once per historic run, so they are dropped here exactly as
    ``adjustment_facts_from_lines(draft_only=True)`` drops them on the persisted path.
    """
    return tuple(
        AdjustmentFact(
            section_code=line.section_code or "",
            rule_code=line.rule_code,
            stage=line.stage or "PGBP",
            description=line.description,
            direction=line.direction or "Add",
            amount=q(money(line.amount)),
            override_amount=(
                q(money(line.override_amount)) if line.override_amount is not None else None
            ),
            status=line.status or "Manual",
        )
        for line in lines
        if line.run_id is None
    )


def preserve_override_amounts(
    draft: tuple[AdjustmentFact, ...],
    prior_run_lines: list,
) -> tuple[AdjustmentFact, ...]:
    """Copy override_amount from the current run's audit rows onto matching draft facts.

    Shared by the live preview and ``runs.create_run`` so a saved run and a fresh
    fingerprint always hash the same worksheet.
    """
    by_key: dict[str, Decimal] = {}
    for line in prior_run_lines:
        if getattr(line, "override_amount", None) is None:
            continue
        key = (
            f"{getattr(line, 'rule_code', None) or ''}|"
            f"{getattr(line, 'section_code', None) or ''}|"
            f"{getattr(line, 'description', None) or ''}"
        )
        by_key[key] = money(line.override_amount)
    if not by_key:
        return draft
    out: list[AdjustmentFact] = []
    for fact in draft:
        ov = by_key.get(fact.override_key)
        if ov is not None and fact.override_amount is None:
            out.append(
                AdjustmentFact(
                    section_code=fact.section_code,
                    rule_code=fact.rule_code,
                    stage=fact.stage,
                    description=fact.description,
                    direction=fact.direction,
                    amount=fact.amount,
                    override_amount=ov,
                    status="Override",
                )
            )
        else:
            out.append(fact)
    return tuple(out)


def bf_loss_fingerprint(losses: tuple[BroughtForwardLoss, ...]) -> str:
    return "|".join(
        f"{loss.ledger_id}:{format(loss.amount_remaining, 'f')}:{loss.loss_kind}" for loss in losses
    )


def gross_total_income_from_facts(
    income_facts: tuple[IncomeFact, ...], adjustment_facts: tuple[AdjustmentFact, ...]
) -> Decimal:
    """Head-wise net income after tax adjustments — before depreciation and set-off."""
    base = q(sum((fact.net for fact in income_facts), ZERO))
    return apply_adjustments(base, adjustment_facts)


def build_preview_lines(
    *,
    gross_total_income: Decimal,
    losses_set_off: Decimal,
    tax_depreciation_claimed: Decimal,
    total_income: Decimal,
    tax_on_total_income: Decimal,
    rebate_amount: Decimal,
    surcharge_before_relief: Decimal,
    marginal_relief_amount: Decimal,
    cess_amount: Decimal,
    cess_rate_percent: Decimal,
    tax_normal: Decimal,
    tax_mat: Decimal | None,
    mat_credit_utilised: Decimal,
    interest_234a: Decimal,
    interest_234b: Decimal,
    interest_234c: Decimal,
    credits_total: Decimal,
    net_payable: Decimal,
    refund_due: Decimal,
) -> list[TaxPreviewLineOut]:
    """The drill-down the result rail renders, in reading order, in plain English."""
    cess_rate = format(q(money(cess_rate_percent)).normalize(), "f")
    specs: list[tuple[str, str, Decimal, str, str | None, str]] = [
        (
            "gross_total_income",
            "Gross Total Income",
            gross_total_income,
            "income",
            None,
            "Head-wise net income after adding back disallowed items and deducting "
            "items allowable only for tax.",
        ),
        (
            "losses_set_off",
            "Less: Brought Forward Losses Set Off",
            losses_set_off,
            "income",
            "Sections 70–74",
            "Brought forward losses applied against this year's income of the same class.",
        ),
        (
            "tax_depreciation",
            "Less: Depreciation under the Income-tax Act",
            tax_depreciation_claimed,
            "income",
            "Section 32",
            "Block-wise depreciation on the written-down value register for this year.",
        ),
        (
            "total_income",
            "Total Income",
            total_income,
            "total",
            "Section 288A",
            "Gross Total Income less set-off and depreciation, rounded to the nearest ten rupees.",
        ),
        (
            "tax_on_total_income",
            "Tax on Total Income",
            tax_on_total_income,
            "tax",
            None,
            "Total Income charged at the rates in force for this entity class and regime.",
        ),
        (
            "rebate",
            "Less: Rebate",
            rebate_amount,
            "tax",
            "Section 87A",
            "Rebate allowed where total income is within the statutory limit.",
        ),
        (
            "surcharge",
            "Add: Surcharge",
            surcharge_before_relief,
            "tax",
            None,
            "Surcharge on tax after rebate at the rate for this income slab.",
        ),
        (
            "marginal_relief",
            "Less: Marginal Relief",
            marginal_relief_amount,
            "tax",
            None,
            "Relief so that the extra tax does not exceed the income above the surcharge threshold.",
        ),
        (
            "cess",
            "Add: Health and Education Cess",
            cess_amount,
            "tax",
            None,
            f"{cess_rate}% of tax after rebate plus surcharge.",
        ),
        (
            "tax_normal",
            "Tax Payable under Normal Provisions",
            tax_normal,
            "tax",
            None,
            "Tax after rebate, surcharge, marginal relief and cess.",
        ),
        (
            "tax_mat",
            "Tax Payable under Minimum Alternate Tax",
            tax_mat if tax_mat is not None else ZERO,
            "tax",
            "Section 115JB",
            "Book profit charged at the Minimum Alternate Tax rate plus cess; the higher of "
            "this and tax under normal provisions is payable.",
        ),
        (
            "mat_credit_utilised",
            "Less: Minimum Alternate Tax Credit Set Off",
            mat_credit_utilised,
            "credit",
            "Section 115JAA",
            "Credit from earlier years when Minimum Alternate Tax exceeded normal tax, set off "
            "against this year's higher normal tax.",
        ),
        (
            "interest_234a",
            "Add: Interest for Late Filing of Return",
            interest_234a,
            "tax",
            "Section 234A",
            "One per cent a month on unpaid tax from the return due date to the date of filing.",
        ),
        (
            "interest_234b",
            "Add: Interest for Short Payment of Advance Tax",
            interest_234b,
            "tax",
            "Section 234B",
            "One per cent a month where advance tax paid is less than ninety per cent of the "
            "assessed tax.",
        ),
        (
            "interest_234c",
            "Add: Interest for Deferment of Advance Tax Instalments",
            interest_234c,
            "tax",
            "Section 234C",
            "One per cent a month on the shortfall in each advance tax instalment.",
        ),
        (
            "credits_total",
            "Less: Taxes Already Paid and Deducted at Source",
            credits_total,
            "credit",
            None,
            "Advance tax, self-assessment tax and tax deducted or collected at source claimed "
            "for this year.",
        ),
        (
            "net_payable",
            "Net Tax Payable",
            net_payable,
            "total",
            None,
            "Tax and interest payable less the taxes already paid.",
        ),
        (
            "refund_due",
            "Refund Due",
            refund_due,
            "total",
            None,
            "Taxes already paid in excess of the tax and interest payable.",
        ),
    ]
    emphasis = {
        "total_income": "subtotal",
        "tax_normal": "subtotal",
        "net_payable": "total",
        "refund_due": "total",
    }
    return [
        TaxPreviewLineOut(
            key=key,
            label=label,
            amount=q(money(amount)),
            kind=kind,
            statutory_ref=ref,
            explain=explain,
            emphasis=emphasis.get(key, "normal"),
        )
        for key, label, amount, kind, ref, explain in specs
    ]


async def _load_computation(
    db: AsyncSession, company_id: uuid.UUID, computation_id: uuid.UUID
) -> TaxComputation:
    doc = await db.scalar(
        select(TaxComputation)
        .where(
            TaxComputation.id == computation_id,
            TaxComputation.company_id == company_id,
        )
        .options(
            selectinload(TaxComputation.income_lines),
            selectinload(TaxComputation.adjustment_lines),
        )
    )
    if doc is None:
        raise NotFoundError("Tax computation not found")
    return doc


async def gather_inputs(
    db: AsyncSession,
    doc: TaxComputation,
    overlay: TaxPreviewRequest | None = None,
    *,
    restore_own_setoffs: bool = True,
) -> PreviewInputs:
    """Assemble exactly the inputs ``runs.create_run`` assembles, overlay applied.

    ``restore_own_setoffs`` adds back this worksheet's active set-off applications so a
    fingerprint after ``persist_setoff_applications`` still matches the hash stamped on
    the run. Pass ``False`` from ``create_run`` after it has already reversed those
    applications — otherwise the pool would be counted twice.
    """
    ruleset = await resolve_ruleset(
        db,
        company_id=doc.company_id,
        ay_code=doc.ay_code,
        assessee_class_code=doc.assessee_class_code,
        regime_code=doc.regime_code,
    )
    notes: list[str] = []

    if overlay is not None and overlay.income_lines is not None:
        income_facts = income_facts_from_overlay(overlay.income_lines)
        notes.append("Income figures taken from unsaved editor rows, not from the saved worksheet.")
    else:
        income_facts = income_facts_from_lines(list(doc.income_lines))

    prior_run_lines: list = []
    if doc.current_run_id:
        prior_run_lines = [
            ln for ln in doc.adjustment_lines if ln.run_id == doc.current_run_id
        ]

    if overlay is not None and overlay.adjustment_lines is not None:
        adjustment_facts = preserve_override_amounts(
            adjustment_facts_from_overlay(overlay.adjustment_lines),
            prior_run_lines,
        )
        notes.append("Adjustments taken from unsaved editor rows, not from the saved worksheet.")
    else:
        adjustment_facts = preserve_override_amounts(
            adjustment_facts_from_lines(list(doc.adjustment_lines), draft_only=True),
            prior_run_lines,
        )

    bf_losses = tuple(
        await loss_service.available_bf_losses(
            db,
            doc.company_id,
            doc.ay_code,
            for_computation_id=doc.id if restore_own_setoffs else None,
        )
    )

    if overlay is not None and overlay.tax_depreciation_total is not None:
        dep_total = q(money(overlay.tax_depreciation_total))
        notes.append("Depreciation under the Income-tax Act overridden for this preview.")
    else:
        dep_total = await dep_service.total_depreciation(db, doc.company_id, doc.ay_code)

    mat_available = await mat_service.available_mat_credit(
        db, doc.company_id, current_ay=doc.ay_code
    )

    if overlay is not None and overlay.apply_book_profit:
        book_profit = (
            q(money(overlay.book_profit_115jb)) if overlay.book_profit_115jb is not None else None
        )
        if book_profit is None:
            notes.append(
                "Book profit cleared for this preview, so Minimum Alternate Tax is not compared."
            )
    else:
        book_profit = (
            q(money(doc.book_profit_115jb)) if doc.book_profit_115jb is not None else None
        )

    rhash = ruleset.hash()
    fingerprint = bf_loss_fingerprint(bf_losses)
    # Brought-forward remaining is mutated by the run itself (set-off persistence). Including
    # it in the input hash made every saved run look "stale" the moment it finished, and
    # made Review permanently block Submit. The worksheet + ruleset are what the CA edits;
    # the BF pool is captured in the run breakdown for audit instead.
    ihash = input_hash(
        income_facts=income_facts,
        adjustment_facts=adjustment_facts,
        ruleset_hash=rhash,
        tax_depreciation_total=dep_total,
        book_profit_115jb=book_profit,
        bf_loss_fingerprint="",
    )
    return PreviewInputs(
        ruleset=ruleset,
        income_facts=income_facts,
        adjustment_facts=adjustment_facts,
        bf_losses=bf_losses,
        bf_fingerprint=fingerprint,
        tax_depreciation_total=dep_total,
        available_mat_credit=mat_available,
        book_profit_115jb=book_profit,
        ruleset_hash=rhash,
        input_hash=ihash,
        notes=tuple(notes),
    )


async def current_fingerprint(db: AsyncSession, doc: TaxComputation) -> tuple[str, str]:
    """(ruleset_hash, input_hash) for the persisted state — used to detect a stale run."""
    inputs = await gather_inputs(db, doc, None)
    return inputs.ruleset_hash, inputs.input_hash


async def preview_computation(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    computation_id: uuid.UUID,
    overlay: TaxPreviewRequest | None = None,
) -> TaxPreviewOut:
    """Compute the same figures a real run would, and persist nothing at all."""
    doc = await _load_computation(db, company_id, computation_id)
    inputs = await gather_inputs(db, doc, overlay)

    state = run_pipeline(
        income_facts=inputs.income_facts,
        adjustment_facts=inputs.adjustment_facts,
        ruleset=inputs.ruleset,
        bf_losses=inputs.bf_losses,
        current_ay=doc.ay_code,
        tax_depreciation_total=inputs.tax_depreciation_total,
        book_profit_115jb=inputs.book_profit_115jb,
        available_mat_credit=inputs.available_mat_credit,
    )
    if state.kernel_result is None:
        raise ValidationError("Pipeline produced no kernel result", code="pipeline_empty")
    kr = state.kernel_result

    tax_normal = q(money(kr.tax_payable))
    mat = state.mat_result
    gross_tax = q(money(mat.tax_after_mat)) if mat is not None else tax_normal

    subject = InterestSubject(
        id=doc.id,
        company_id=doc.company_id,
        ay_code=doc.ay_code,
        assessee_class_code=doc.assessee_class_code,
        audit_applicable=(
            overlay.audit_applicable
            if overlay is not None and overlay.audit_applicable is not None
            else bool(doc.audit_applicable)
        ),
        return_filed_date=(
            overlay.return_filed_date
            if overlay is not None and overlay.return_filed_date is not None
            else doc.return_filed_date
        ),
        itr_due_date_override=(
            overlay.itr_due_date_override
            if overlay is not None and overlay.itr_due_date_override is not None
            else doc.itr_due_date_override
        ),
    )
    interest = await interest_service.compute_for_computation(
        db,
        cast(TaxComputation, subject),
        assessed_tax=gross_tax,
        as_of_date=overlay.as_of_date if overlay is not None else None,
    )
    credits_total = await credit_service.claimed_credits_total(
        db, doc.company_id, ay_code=doc.ay_code, computation_id=doc.id
    )

    total_tax = q(gross_tax + interest.total_interest)
    net_payable = q(total_tax - credits_total)
    refund_due = q(credits_total - total_tax)
    if net_payable < ZERO:
        net_payable = ZERO
    if refund_due < ZERO:
        refund_due = ZERO

    losses_set_off = (
        q(money(state.setoff_result.total_set_off)) if state.setoff_result is not None else ZERO
    )
    gti = gross_total_income_from_facts(inputs.income_facts, inputs.adjustment_facts)

    current_run = await _current_run(db, doc)
    matches = (
        current_run is not None
        and current_run.input_hash == inputs.input_hash
        and current_run.ruleset_hash == inputs.ruleset_hash
    )

    breakdown = dict(kr.breakdown)
    breakdown["ruleset_hash"] = inputs.ruleset_hash
    breakdown["character_nets"] = {k: format(v, "f") for k, v in state.character_nets.items()}
    breakdown["tax_depreciation_total"] = format(inputs.tax_depreciation_total, "f")
    breakdown["pipeline_notes"] = list(state.notes)
    breakdown["interest_234"] = interest.breakdown
    breakdown["credits_total"] = format(credits_total, "f")
    if mat is not None:
        breakdown["mat"] = mat.breakdown

    notes = list(inputs.notes)
    if not matches:
        notes.append(
            "These figures are not yet saved as a computation run — record a run to keep them."
        )

    out = TaxPreviewOut(
        ay_code=doc.ay_code,
        gross_total_income=gti,
        total_income=q(money(kr.total_income)),
        losses_set_off=losses_set_off,
        tax_depreciation_claimed=inputs.tax_depreciation_total,
        tax_on_total_income=q(money(kr.tax_before_rebate)),
        rebate_amount=q(money(kr.rebate_amount)),
        surcharge_before_relief=q(money(kr.surcharge_before_relief)),
        marginal_relief_amount=q(money(kr.marginal_relief_amount)),
        surcharge_amount=q(money(kr.surcharge_amount)),
        cess_amount=q(money(kr.cess_amount)),
        tax_normal=tax_normal,
        tax_mat=q(money(mat.mat_tax)) if mat is not None else None,
        tax_applied_basis=mat.applied_basis if mat is not None else "Normal",
        mat_credit_created=q(money(mat.mat_credit_created)) if mat is not None else ZERO,
        mat_credit_utilised=q(money(mat.mat_credit_utilised)) if mat is not None else ZERO,
        interest_234a=q(money(interest.interest_234a)),
        interest_234b=q(money(interest.interest_234b)),
        interest_234c=q(money(interest.interest_234c)),
        total_interest=q(money(interest.total_interest)),
        credits_total=credits_total,
        total_tax=total_tax,
        net_payable=net_payable,
        refund_due=refund_due,
        breakdown=breakdown,
        engine_version=ENGINE_VERSION,
        ruleset_hash=inputs.ruleset_hash,
        input_hash=inputs.input_hash,
        persisted=False,
        matches_current_run=matches,
        notes=notes,
    )
    out.lines = build_preview_lines(
        gross_total_income=out.gross_total_income,
        losses_set_off=out.losses_set_off,
        tax_depreciation_claimed=out.tax_depreciation_claimed,
        total_income=out.total_income,
        tax_on_total_income=out.tax_on_total_income,
        rebate_amount=out.rebate_amount,
        surcharge_before_relief=out.surcharge_before_relief,
        marginal_relief_amount=out.marginal_relief_amount,
        cess_amount=out.cess_amount,
        cess_rate_percent=inputs.ruleset.cess_rate_percent,
        tax_normal=out.tax_normal,
        tax_mat=out.tax_mat,
        mat_credit_utilised=out.mat_credit_utilised,
        interest_234a=out.interest_234a,
        interest_234b=out.interest_234b,
        interest_234c=out.interest_234c,
        credits_total=out.credits_total,
        net_payable=out.net_payable,
        refund_due=out.refund_due,
    )

    # Defensive: discard any accidental autoflush state so a preview can never leak a write.
    await db.rollback()
    return out


async def _current_run(db: AsyncSession, doc: TaxComputation) -> TaxComputationRun | None:
    if doc.current_run_id is None:
        return None
    return await db.scalar(
        select(TaxComputationRun).where(
            TaxComputationRun.id == doc.current_run_id,
            TaxComputationRun.company_id == doc.company_id,
        )
    )
