"""Ordered evaluation of adjustment rules → line results."""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.compliance import TaxAdjustmentProvision
from app.services.tax_adjustment_engine.adapters import gather_facts
from app.services.tax_adjustment_engine.context import (
    AdjustmentEngineInput,
    AdjustmentEngineResult,
    AdjustmentLineResult,
    ZERO,
    q,
)
from app.services.tax_adjustment_engine.methods import get_method
from app.services.tax_adjustment_engine.resolve import resolve_adjustment_pack


def net_from_results(lines: list[AdjustmentLineResult]) -> Decimal:
    total = ZERO
    for ln in lines:
        if ln.direction == "Add":
            total += q(ln.final_amount)
        elif ln.direction == "Deduct":
            total -= q(ln.final_amount)
    return q(total)


def adjustment_snapshot(result: AdjustmentEngineResult) -> list[dict]:
    return [
        {
            "section_code": ln.section_code,
            "stage": ln.stage,
            "direction": ln.direction,
            "final_amount": str(ln.final_amount),
            "status": ln.status,
            "rule_id": str(ln.rule_id) if ln.rule_id else None,
            "provision_id": str(ln.provision_id) if ln.provision_id else None,
            "explanation": ln.explanation,
        }
        for ln in result.lines
    ]


def _mode_applies(applies: str, mode: str) -> bool:
    if applies in ("Both", mode):
        return True
    # provisions store "EntityBooks" / "IndividualHeads" / "Both"
    return False


def _topo_sort(rules: list) -> list:
    """Order by depends_on section/rule codes, falling back to sequence."""
    by_code = {r.rule_code: r for r in rules}
    by_section: dict[str, list] = {}
    for r in rules:
        by_section.setdefault(r.section_code, []).append(r)
    visited: set[str] = set()
    order: list = []

    def visit(code: str) -> None:
        if code in visited:
            return
        visited.add(code)
        r = by_code.get(code)
        if r is None:
            return
        for dep in r.depends_on or []:
            if dep in by_code:
                visit(dep)
            for alt in by_section.get(dep, []):
                visit(alt.rule_code)
        order.append(r)

    for r in sorted(rules, key=lambda x: x.sequence):
        visit(r.rule_code)
    return order


async def run_adjustment_engine(
    db: AsyncSession,
    inp: AdjustmentEngineInput,
) -> AdjustmentEngineResult:
    pack = await resolve_adjustment_pack(
        db,
        company_id=inp.company_id,
        assessment_year=inp.assessment_year,
        entity_type=inp.entity_type,
        filing_regime=inp.filing_regime,
        as_of=inp.to_date,
    )
    if pack is None:
        # No pack — honour existing manual lines only
        lines = _lines_from_existing(inp.existing_lines)
        return AdjustmentEngineResult(
            lines=lines,
            net_adjustments=net_from_results(lines),
            pack_id=None,
        )

    extras = {
        "salary_income": inp.salary_income,
        "adjusted_gross_total_income": inp.book_profit,
        "chapter_via_deduction": inp.chapter_via_deduction,
        "standard_deduction": inp.standard_deduction,
    }
    # Preserve schedule amounts from existing lines' inputs
    schedule_inputs: dict[str, Decimal] = {}
    override_by_section: dict[str, dict] = {}
    for raw in inp.existing_lines:
        sc = raw.get("section_code") or ""
        if raw.get("override_amount") is not None:
            override_by_section[sc] = raw
        inputs = raw.get("inputs") or {}
        if "schedule_amount" in inputs:
            schedule_inputs[sc] = q(inputs["schedule_amount"])
        if raw.get("amount") and raw.get("status") in ("Manual", "Overridden"):
            schedule_inputs.setdefault(sc, q(raw.get("final_amount") or raw.get("amount") or 0))

    facts = await gather_facts(
        db,
        company_id=inp.company_id,
        assessment_year=inp.assessment_year,
        from_date=inp.from_date,
        to_date=inp.to_date,
        book_profit=inp.book_profit,
        schedule_inputs=schedule_inputs,
        extras=extras,
    )

    prov_modes: dict = {}
    if pack.rules:
        pids = {r.provision_id for r in pack.rules}
        rows = (
            await db.execute(
                select(TaxAdjustmentProvision).where(TaxAdjustmentProvision.id.in_(pids))
            )
        ).scalars().all()
        prov_modes = {p.id: p.applies_to_modes for p in rows}

    applicable = []
    for r in pack.rules:
        modes = prov_modes.get(r.provision_id, "Both")
        if not _mode_applies(modes, inp.assessee_mode):
            continue
        # entity_types filter in parameters
        et = (r.parameters or {}).get("entity_types")
        if et and inp.entity_type not in et:
            continue
        mode_list = (r.parameters or {}).get("modes")
        if mode_list and inp.assessee_mode not in mode_list:
            continue
        applicable.append(r)

    ordered = _topo_sort(applicable)
    prior_results: dict[str, AdjustmentLineResult] = {}
    lines: list[AdjustmentLineResult] = []

    for rule in ordered:
        fn = get_method(rule.evaluation_method)
        result = fn(rule, facts, prior_results)

        # Apply preserved override
        existing = override_by_section.get(rule.section_code)
        if existing and rule.allow_manual_override:
            ov = existing.get("override_amount")
            if ov is None and existing.get("status") == "Overridden":
                ov = existing.get("final_amount") or existing.get("amount")
            if ov is not None:
                result.override_amount = q(ov)
                result.final_amount = q(ov)
                result.status = "Overridden"
                result.explanation = {
                    **result.explanation,
                    "overridden": True,
                    "computed_before_override": str(result.computed_amount),
                }

        # Also preserve fully manual custom amounts entered for seed lines
        if (
            existing
            and result.status == "NeedsInput"
            and q(existing.get("final_amount") or existing.get("amount") or 0) != ZERO
        ):
            amt = q(existing.get("final_amount") or existing.get("amount") or 0)
            result.computed_amount = amt
            result.final_amount = amt
            result.amount  # property
            result.status = "Manual"
            result.explanation = {**result.explanation, "manual_entry": True}

        # Skip zero non-seed lines unless force_seed / include
        if (
            result.final_amount == ZERO
            and result.status in ("NeedsInput", "Skipped", "Manual")
            and not rule.include_in_seed_lines
            and not inp.force_seed
        ):
            prior_results[rule.rule_code] = result
            prior_results[rule.section_code] = result
            continue

        if (
            result.final_amount == ZERO
            and result.status == "Skipped"
            and not rule.include_in_seed_lines
        ):
            prior_results[rule.rule_code] = result
            continue

        lines.append(result)
        prior_results[rule.rule_code] = result
        prior_results[rule.section_code] = result

    # Append custom existing lines without section/rule (free-form)
    for raw in inp.existing_lines:
        if raw.get("section_code") or raw.get("rule_id") or raw.get("provision_id"):
            continue
        if not raw.get("description") and q(raw.get("amount") or 0) == ZERO:
            continue
        amt = q(raw.get("final_amount") or raw.get("amount") or 0)
        lines.append(
            AdjustmentLineResult(
                provision_id=None,
                rule_id=None,
                section_code="",
                stage=str(raw.get("stage") or "PGBP"),
                description=str(raw.get("description") or "Custom"),
                direction=str(raw.get("direction") or "Add"),
                base_amount=amt,
                computed_amount=amt,
                override_amount=None,
                final_amount=amt,
                status="Manual",
                explanation={"method": "Manual", "custom": True},
                inputs={},
                source_refs={},
                category_id=raw.get("category_id"),
            )
        )

    stage_nets: dict[str, Decimal] = {}
    for ln in lines:
        stage_nets[ln.stage] = q(stage_nets.get(ln.stage, ZERO) + (
            ln.final_amount if ln.direction == "Add" else -ln.final_amount
        ))

    return AdjustmentEngineResult(
        lines=lines,
        net_adjustments=net_from_results(lines),
        pack_id=pack.pack_id,
        stage_nets=stage_nets,
    )


def _lines_from_existing(existing: list[dict]) -> list[AdjustmentLineResult]:
    out: list[AdjustmentLineResult] = []
    for raw in existing:
        amt = q(raw.get("final_amount") or raw.get("amount") or 0)
        out.append(
            AdjustmentLineResult(
                provision_id=raw.get("provision_id"),
                rule_id=raw.get("rule_id"),
                section_code=str(raw.get("section_code") or ""),
                stage=str(raw.get("stage") or "PGBP"),
                description=str(raw.get("description") or ""),
                direction=str(raw.get("direction") or "Add"),
                base_amount=q(raw.get("base_amount") or amt),
                computed_amount=q(raw.get("computed_amount") or amt),
                override_amount=q(raw["override_amount"])
                if raw.get("override_amount") is not None
                else None,
                final_amount=amt,
                status=str(raw.get("status") or "Manual"),
                explanation=dict(raw.get("explanation") or {}),
                inputs=dict(raw.get("inputs") or {}),
                source_refs=dict(raw.get("source_refs") or {}),
                category_id=raw.get("category_id"),
            )
        )
    return out
