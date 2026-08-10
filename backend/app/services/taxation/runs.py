"""Append-only tax computation runs — never delete results or re-insert over history."""

from __future__ import annotations

import time
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.tax_computation import (
    TaxComputation,
    TaxComputationAdjustmentLine,
    TaxComputationResult,
    TaxComputationRun,
)
from app.services.taxation import credits as credit_service
from app.services.taxation import interest as interest_service
from app.services.taxation import loss_setoff as loss_service
from app.services.taxation import mat_credit as mat_service
from app.services.taxation.facts import AdjustmentFact
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.pipeline import ENGINE_VERSION, run_pipeline


async def _get_computation(
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
            selectinload(TaxComputation.runs),
        )
    )
    if doc is None:
        raise NotFoundError("Tax computation not found")
    return doc


def _preserve_overrides(
    draft: tuple[AdjustmentFact, ...],
    prior_run_lines: list[TaxComputationAdjustmentLine],
) -> tuple[AdjustmentFact, ...]:
    """Deprecated shim — prefer ``preview.preserve_override_amounts``."""
    from app.services.taxation.preview import preserve_override_amounts

    return preserve_override_amounts(draft, prior_run_lines)


async def create_run(
    db: AsyncSession,
    *,
    computation_id: uuid.UUID,
    user: CurrentUser,
    trigger: str = "manual",
) -> TaxComputationRun:
    """Evaluate the computation, append a run + result, never delete prior runs."""
    from app.services.taxation import preview as preview_service

    if user.company_id is None:
        raise ValidationError("company_id required", code="company_required")
    doc = await _get_computation(db, user.company_id, computation_id)
    if doc.docstatus == 2:
        raise ValidationError("Cancelled computation cannot be recomputed", code="cancelled")

    t0 = time.perf_counter()

    # Restore ledgers for this worksheet's prior set-offs so the new run sees the same
    # pool and can replace applications instead of stacking them to zero.
    await loss_service.reverse_setoffs_for_computation(
        db,
        company_id=doc.company_id,
        computation_id=doc.id,
        user_id=user.id,
    )
    await db.flush()
    # Reload lines after flush so gather_inputs sees a consistent session state.
    await db.refresh(doc, attribute_names=["adjustment_lines", "income_lines", "current_run_id"])

    inputs = await preview_service.gather_inputs(db, doc, None, restore_own_setoffs=False)
    income_facts = inputs.income_facts
    adj_facts = inputs.adjustment_facts
    bf_tuple = inputs.bf_losses
    dep_total = inputs.tax_depreciation_total
    mat_avail = inputs.available_mat_credit
    rhash = inputs.ruleset_hash
    ihash = inputs.input_hash
    ruleset = inputs.ruleset

    # Pin must match creation snapshot if present.
    if doc.finance_act_version_id != ruleset.finance_act_version_id:
        # Still compute against the pinned FA if catalogue has it; prefer pin.
        # Phase 4: warn via note in breakdown — re-resolve not supported without pack history.
        pass

    state = run_pipeline(
        income_facts=income_facts,
        adjustment_facts=adj_facts,
        ruleset=ruleset,
        bf_losses=bf_tuple,
        current_ay=doc.ay_code,
        tax_depreciation_total=dep_total,
        book_profit_115jb=inputs.book_profit_115jb,
        available_mat_credit=mat_avail,
    )
    if state.kernel_result is None:
        raise ValidationError("Pipeline produced no kernel result", code="pipeline_empty")
    kr = state.kernel_result

    next_no = await db.scalar(
        select(func.coalesce(func.max(TaxComputationRun.run_no), 0)).where(
            TaxComputationRun.computation_id == doc.id
        )
    )
    assert next_no is not None
    run_no = int(next_no) + 1

    # Supersede previous current run (stamp only — never delete).
    if doc.current_run_id:
        prev = await db.get(TaxComputationRun, doc.current_run_id)
        if prev is not None and prev.superseded_at is None:
            prev.superseded_at = datetime.now(UTC)

    duration_ms = int((time.perf_counter() - t0) * 1000)
    run = TaxComputationRun(
        company_id=doc.company_id,
        computation_id=doc.id,
        run_no=run_no,
        trigger=trigger,
        engine_version=ENGINE_VERSION,
        finance_act_version_id=doc.finance_act_version_id,
        ruleset_hash=rhash,
        input_hash=ihash,
        duration_ms=duration_ms,
        user_id=user.id,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(run)
    await db.flush()

    breakdown = dict(kr.breakdown)
    breakdown["pipeline_notes"] = list(state.notes)
    breakdown["ruleset_hash"] = rhash
    breakdown["character_nets"] = {
        k: format(v, "f") for k, v in state.character_nets.items()
    }
    breakdown["tax_depreciation_total"] = format(dep_total, "f")
    if state.mat_result is not None:
        breakdown["mat"] = state.mat_result.breakdown
    if state.setoff_result is not None:
        breakdown["setoff_total"] = format(state.setoff_result.total_set_off, "f")
        breakdown["setoff_applications"] = [
            {
                "ledger_id": a.ledger_id,
                "loss_kind": a.loss_kind,
                "against": a.against_character,
                "amount": format(a.amount, "f"),
                "seq": a.sequence,
            }
            for a in state.setoff_result.applications
        ]

    # Tax liability after MAT compare (if any), then interest 234, then credits.
    tax_normal = q(money(kr.tax_payable))
    tax_mat = state.mat_result.mat_tax if state.mat_result else None
    applied_basis = state.mat_result.applied_basis if state.mat_result else "Normal"
    gross_tax = (
        q(money(state.mat_result.tax_after_mat)) if state.mat_result else tax_normal
    )

    interest = await interest_service.compute_for_computation(
        db, doc, assessed_tax=gross_tax
    )
    breakdown["interest_234"] = interest.breakdown

    credits_total = await credit_service.claimed_credits_total(
        db, doc.company_id, ay_code=doc.ay_code, computation_id=doc.id
    )
    net_payable = q(gross_tax + interest.total_interest - credits_total)
    if net_payable < ZERO:
        net_payable = ZERO
    breakdown["credits_total"] = format(credits_total, "f")

    result = TaxComputationResult(
        company_id=doc.company_id,
        run_id=run.id,
        taxable_income=kr.total_income,
        tax_normal=tax_normal,
        tax_mat=tax_mat,
        tax_applied_basis=applied_basis,
        tax_before_rebate=kr.tax_before_rebate,
        rebate_amount=kr.rebate_amount,
        surcharge_before_relief=kr.surcharge_before_relief,
        marginal_relief_amount=kr.marginal_relief_amount,
        surcharge_amount=kr.surcharge_amount,
        cess_amount=kr.cess_amount,
        interest_234a=interest.interest_234a,
        interest_234b=interest.interest_234b,
        interest_234c=interest.interest_234c,
        credits_total=credits_total,
        total_tax=q(gross_tax + interest.total_interest),
        net_payable=net_payable,
        breakdown=breakdown,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(result)

    # Persist set-off applications + MAT credit ledger (append-only).
    if state.setoff_result and state.setoff_result.applications:
        await loss_service.persist_setoff_applications(
            db,
            company_id=doc.company_id,
            ay_code=doc.ay_code,
            computation_id=doc.id,
            run_id=run.id,
            applications=state.setoff_result.applications,
            user_id=user.id,
        )
    if state.mat_result is not None:
        await mat_service.persist_mat_result(
            db,
            company_id=doc.company_id,
            ay_code=doc.ay_code,
            computation_id=doc.id,
            run_id=run.id,
            mat=state.mat_result,
            user_id=user.id,
        )

    # Append evaluated adjustment lines for this run (do not delete draft or prior).
    for fact in adj_facts:
        applied = fact.applied_amount
        db.add(
            TaxComputationAdjustmentLine(
                company_id=doc.company_id,
                computation_id=doc.id,
                run_id=run.id,
                rule_code=fact.rule_code,
                section_code=fact.section_code,
                stage=fact.stage,
                description=fact.description,
                direction=fact.direction,
                amount=q(fact.amount),
                override_amount=fact.override_amount,
                final_amount=applied,
                status=fact.status if fact.override_amount is None else "Override",
                explanation={"source": "pipeline", "run_no": run_no},
                owner=user.id,
                modified_by=user.id,
            )
        )

    doc.current_run_id = run.id
    doc.status = "Computed"
    doc.modified_by = user.id
    await db.commit()
    await db.refresh(run)
    return run


async def list_runs(
    db: AsyncSession, company_id: uuid.UUID, computation_id: uuid.UUID
) -> list[TaxComputationRun]:
    await _get_computation(db, company_id, computation_id)
    result = await db.scalars(
        select(TaxComputationRun)
        .where(
            TaxComputationRun.computation_id == computation_id,
            TaxComputationRun.company_id == company_id,
        )
        .options(selectinload(TaxComputationRun.result))
        .order_by(TaxComputationRun.run_no.desc())
    )
    return list(result.all())


async def get_run(
    db: AsyncSession,
    company_id: uuid.UUID,
    computation_id: uuid.UUID,
    run_id: uuid.UUID,
) -> TaxComputationRun:
    run = await db.scalar(
        select(TaxComputationRun)
        .where(
            TaxComputationRun.id == run_id,
            TaxComputationRun.computation_id == computation_id,
            TaxComputationRun.company_id == company_id,
        )
        .options(selectinload(TaxComputationRun.result))
    )
    if run is None:
        raise NotFoundError("Tax computation run not found")
    return run
