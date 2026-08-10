"""Ordered taxation pipeline — pure stages over (state, ruleset)."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, replace
from decimal import Decimal

from app.services.taxation.facts import (
    AdjustmentFact,
    IncomeFact,
    apply_adjustments,
    net_by_character,
)
from app.services.taxation.kernel.compute import compute
from app.services.taxation.kernel.mat import MatInput, MatResult, compare_mat
from app.services.taxation.kernel.money import ZERO, money, q
from app.services.taxation.kernel.setoff import BroughtForwardLoss, SetoffResult, apply_loss_setoff
from app.services.taxation.kernel.types import IncomeComponent, KernelInput, KernelResult
from app.services.taxation.resolve.types import ResolvedRuleSet, ResolvedSchedule

ENGINE_VERSION = "0.6.0"


@dataclass(frozen=True, slots=True)
class PipelineState:
    income_facts: tuple[IncomeFact, ...] = ()
    adjustment_facts: tuple[AdjustmentFact, ...] = ()
    character_nets: dict[str, Decimal] = field(default_factory=dict)
    taxable_income: Decimal = ZERO
    kernel_result: KernelResult | None = None
    bf_losses: tuple[BroughtForwardLoss, ...] = ()
    current_ay: str = ""
    tax_depreciation_total: Decimal = ZERO
    book_profit_115jb: Decimal | None = None
    available_mat_credit: Decimal = ZERO
    setoff_result: SetoffResult | None = None
    mat_result: MatResult | None = None
    notes: tuple[str, ...] = ()

    def with_note(self, note: str) -> PipelineState:
        return replace(self, notes=(*self.notes, note))


def _schedule_for_character(
    ruleset: ResolvedRuleSet, character: str
) -> ResolvedSchedule:
    exact = [s for s in ruleset.schedules if s.income_character_code == character]
    if exact:
        return exact[0]
    ordinary = [
        s
        for s in ruleset.schedules
        if s.income_character_code in (None, "", ruleset.default_character_code)
    ]
    if ordinary:
        return ordinary[0]
    return ruleset.schedules[0]


def _character_cap(ruleset: ResolvedRuleSet, character: str) -> Decimal | None:
    if ruleset.surcharge and character in ruleset.surcharge.capped_characters:
        return ruleset.surcharge.default_cap_percent
    return None


def stage_gather_income(state: PipelineState, ruleset: ResolvedRuleSet) -> PipelineState:
    nets = net_by_character(state.income_facts)
    if not nets and state.income_facts:
        nets = {ruleset.default_character_code: ZERO}
    return replace(state, character_nets=nets)


def stage_apply_adjustments(state: PipelineState, _ruleset: ResolvedRuleSet) -> PipelineState:
    base = q(sum(state.character_nets.values(), ZERO))
    taxable = apply_adjustments(base, state.adjustment_facts)
    # Distribute taxable back proportionally across characters for kernel slices.
    if base <= ZERO or not state.character_nets:
        char = {(_ruleset.default_character_code): taxable}
        return replace(state, taxable_income=taxable, character_nets=char)
    factor = taxable / base if base != ZERO else Decimal("1")
    scaled = {k: q(money(v) * factor) for k, v in state.character_nets.items()}
    # Fix rounding drift on the largest character.
    drift = q(taxable - sum(scaled.values(), ZERO))
    if drift != ZERO and scaled:
        key = max(scaled, key=lambda k: scaled[k])
        scaled[key] = q(scaled[key] + drift)
    return replace(state, taxable_income=taxable, character_nets=scaled)


def stage_tax_depreciation(state: PipelineState, ruleset: ResolvedRuleSet) -> PipelineState:
    """Deduct IT Act block depreciation from ordinary character net."""
    dep = q(money(state.tax_depreciation_total))
    if dep <= ZERO:
        return state
    nets = dict(state.character_nets)
    key = "ORDINARY" if "ORDINARY" in nets else ruleset.default_character_code
    before = q(money(nets.get(key, ZERO)))
    after = q(before - dep)
    nets[key] = after
    taxable = q(sum(nets.values(), ZERO))
    return replace(
        state,
        character_nets=nets,
        taxable_income=taxable,
    ).with_note(f"tax_depreciation: deducted {format(dep, 'f')} from {key}")


def stage_loss_setoff(state: PipelineState, _ruleset: ResolvedRuleSet) -> PipelineState:
    if not state.bf_losses:
        return state.with_note("loss_setoff: no brought-forward losses")
    ay = state.current_ay or _ruleset.ay_code
    result = apply_loss_setoff(state.character_nets, state.bf_losses, current_ay=ay)
    taxable = q(sum(result.character_nets.values(), ZERO))
    note = f"loss_setoff: applied {format(result.total_set_off, 'f')} across {len(result.applications)} entries"
    return replace(
        state,
        character_nets=result.character_nets,
        taxable_income=taxable,
        setoff_result=result,
    ).with_note(note)


def stage_chapter_via_stub(state: PipelineState, _ruleset: ResolvedRuleSet) -> PipelineState:
    return state.with_note("chapter_via: deferred to later phase")


def stage_kernel_tax(state: PipelineState, ruleset: ResolvedRuleSet) -> PipelineState:
    components: list[IncomeComponent] = []
    for character, amount in state.character_nets.items():
        amt = money(amount)
        if amt == ZERO:
            continue
        # Negative character nets become zero for tax (loss handled via CF ledger).
        if amt < ZERO:
            continue
        sched = _schedule_for_character(ruleset, character)
        rebate_ok = True
        if ruleset.rebate and character in ruleset.rebate.excluded_characters:
            rebate_ok = False
        components.append(
            IncomeComponent(
                character_code=character,
                amount=amt,
                bands=sched.bands,
                surcharge_cap_percent=_character_cap(ruleset, character),
                rebate_eligible=rebate_ok,
            )
        )
    if not components:
        components.append(
            IncomeComponent(
                character_code=ruleset.default_character_code,
                amount=ZERO,
                bands=_schedule_for_character(ruleset, ruleset.default_character_code).bands,
            )
        )
    result = compute(
        KernelInput(
            components=tuple(components),
            surcharge=ruleset.surcharge,
            rebate=ruleset.rebate,
            cess_rate_percent=ruleset.cess_rate_percent,
            apply_288a=True,
            apply_288b=True,
        )
    )
    return replace(state, kernel_result=result, taxable_income=result.total_income)


def stage_mat_compare(state: PipelineState, ruleset: ResolvedRuleSet) -> PipelineState:
    if state.book_profit_115jb is None:
        return state.with_note("mat_compare: skipped (no book_profit_115jb)")
    if state.kernel_result is None:
        return state.with_note("mat_compare: skipped (no kernel result)")
    mat = compare_mat(
        MatInput(
            book_profit=state.book_profit_115jb,
            normal_tax_payable=state.kernel_result.tax_payable,
            available_mat_credit=state.available_mat_credit,
            cess_rate_percent=ruleset.cess_rate_percent,
        )
    )
    return replace(state, mat_result=mat).with_note(
        f"mat_compare: basis={mat.applied_basis} tax_after={format(mat.tax_after_mat, 'f')}"
    )


def stage_credits_interest_stub(state: PipelineState, _ruleset: ResolvedRuleSet) -> PipelineState:
    return state.with_note(
        "credits_interest: TDS/credits + 234A/B/C applied in runs service (challan-backed)"
    )


PIPELINE_STAGES = (
    stage_gather_income,
    stage_apply_adjustments,
    stage_tax_depreciation,
    stage_loss_setoff,
    stage_chapter_via_stub,
    stage_kernel_tax,
    stage_mat_compare,
    stage_credits_interest_stub,
)


def run_pipeline(
    *,
    income_facts: tuple[IncomeFact, ...],
    adjustment_facts: tuple[AdjustmentFact, ...],
    ruleset: ResolvedRuleSet,
    bf_losses: tuple[BroughtForwardLoss, ...] = (),
    current_ay: str | None = None,
    tax_depreciation_total: Decimal = ZERO,
    book_profit_115jb: Decimal | None = None,
    available_mat_credit: Decimal = ZERO,
) -> PipelineState:
    state = PipelineState(
        income_facts=income_facts,
        adjustment_facts=adjustment_facts,
        bf_losses=bf_losses,
        current_ay=current_ay or ruleset.ay_code,
        tax_depreciation_total=q(money(tax_depreciation_total)),
        book_profit_115jb=(
            q(money(book_profit_115jb)) if book_profit_115jb is not None else None
        ),
        available_mat_credit=q(money(available_mat_credit)),
    )
    for stage in PIPELINE_STAGES:
        state = stage(state, ruleset)
    return state


def input_hash(
    *,
    income_facts: tuple[IncomeFact, ...],
    adjustment_facts: tuple[AdjustmentFact, ...],
    ruleset_hash: str,
    tax_depreciation_total: Decimal = ZERO,
    book_profit_115jb: Decimal | None = None,
    bf_loss_fingerprint: str = "",
) -> str:
    payload = {
        "ruleset_hash": ruleset_hash,
        "tax_depreciation_total": format(q(money(tax_depreciation_total)), "f"),
        "book_profit_115jb": (
            format(q(money(book_profit_115jb)), "f") if book_profit_115jb is not None else None
        ),
        "bf_losses": bf_loss_fingerprint,
        "income": [
            {
                "head": f.head,
                "character": f.income_character_code,
                "gross": format(f.gross, "f"),
                "deductions": format(f.deductions, "f"),
                "net": format(f.net, "f"),
                "sub_ref": f.sub_ref,
            }
            for f in income_facts
        ],
        "adjustments": [
            {
                "section": a.section_code,
                "rule": a.rule_code,
                "direction": a.direction,
                "amount": format(a.amount, "f"),
                "override": format(a.override_amount, "f") if a.override_amount is not None else None,
                "status": a.status,
            }
            for a in adjustment_facts
        ],
    }
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
