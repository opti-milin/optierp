"""Ordered income-tax calculation pipeline."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from app.core.exceptions import ValidationError
from app.services.income_tax_engine.cess import apply_cess
from app.services.income_tax_engine.context import (
    ResolvedTaxRules,
    TaxEngineInput,
    TaxEngineResult,
    ZERO,
    q,
)
from app.services.income_tax_engine.rebate import apply_rebate
from app.services.income_tax_engine.strategies.flat_rate import flat_tax
from app.services.income_tax_engine.strategies.rule_based import ordinary_tax, special_income_tax
from app.services.income_tax_engine.strategies.slab_based import tax_from_slabs
from app.services.income_tax_engine.surcharge import apply_surcharge


def net_adjustments_of(lines: list[tuple[str, Decimal]]) -> Decimal:
    total = ZERO
    for direction, amount in lines:
        amt = q(amount)
        if direction == "Add":
            total += amt
        elif direction == "Deduct":
            total -= amt
        else:
            raise ValidationError(f"Invalid adjustment direction '{direction}'", field="direction")
    return q(total)


def compute_from_resolved(
    inp: TaxEngineInput,
    rules: ResolvedTaxRules,
) -> TaxEngineResult:
    """
    Pipeline:
      taxable income (book + adjustments)
      → base tax (flat / slab / rule-based with special rates)
      → rebate
      → surcharge + marginal relief
      → cess
      → less TDS/TCS/advance tax
    """
    net_adj = net_adjustments_of(inp.adjustments)
    book_taxable = q(inp.book_profit) + net_adj

    method = rules.computation_method
    special_tax = ZERO
    special_details: list[dict[str, Any]] = []
    special_income_total = ZERO
    slab_steps: list[dict] = []

    if method == "RuleBased" or inp.special_income:
        special_tax, special_details, special_income_total = special_income_tax(inp.special_income)
        if inp.special_income_in_book:
            # Entity books: special winnings already sit inside book profit.
            ordinary = max(q(book_taxable - special_income_total), ZERO)
            taxable = q(book_taxable)
        else:
            # Individual heads: special lines are entered separately and additive.
            ordinary = max(q(book_taxable), ZERO)
            taxable = q(book_taxable + special_income_total)
        taxable_for_tax = max(taxable, ZERO)
        if method == "RuleBased":
            ordinary_method = rules.ordinary_method
        elif rules.computation_method == "SlabBased":
            ordinary_method = "SlabBased"
        else:
            ordinary_method = "FlatRate"
        base_tax, slab_steps = ordinary_tax(ordinary, rules, method=ordinary_method)
        effective_method = (
            "RuleBased" if (method == "RuleBased" or special_income_total > ZERO) else method
        )
        ordinary_income = ordinary
    elif method == "SlabBased":
        taxable = q(book_taxable)
        taxable_for_tax = max(taxable, ZERO)
        base_tax, slab_steps = tax_from_slabs(taxable_for_tax, rules.slabs)
        effective_method = "SlabBased"
        ordinary_income = taxable_for_tax
    else:
        taxable = q(book_taxable)
        taxable_for_tax = max(taxable, ZERO)
        base_tax = flat_tax(taxable_for_tax, rules.flat_tax_rate)
        effective_method = "FlatRate"
        ordinary_income = taxable_for_tax

    tax_before_rebate = q(base_tax)
    rebate, tax_after_rebate = apply_rebate(
        taxable_income=taxable_for_tax,
        tax_before_rebate=tax_before_rebate,
        rules=rules,
    )

    surcharge_after, surcharge_before, relief = apply_surcharge(
        tax_after_rebate=tax_after_rebate,
        taxable_income=taxable_for_tax,
        brackets=rules.surcharge_brackets,
        marginal_relief_enabled=rules.marginal_relief_enabled,
        special_tax=special_tax,
    )

    cess = apply_cess(
        tax_after_rebate=tax_after_rebate,
        surcharge_after_relief=surcharge_after,
        cess_rate=rules.cess_rate,
        cess_base=rules.cess_base,
        special_tax=special_tax,
    )

    total = q(tax_after_rebate + surcharge_after + cess + special_tax)
    credits = q(inp.tds_credit) + q(inp.tcs_credit) + q(inp.advance_tax_paid)
    payable = q(total - credits)

    breakdown = {
        "computation_method": effective_method,
        "ordinary_method": rules.ordinary_method,
        "policy_id": str(rules.policy_id) if rules.policy_id else None,
        "entity_type": rules.entity_type,
        "filing_regime": rules.filing_regime,
        "flat_tax_rate": str(rules.flat_tax_rate),
        "slab_steps": slab_steps,
        "tax_before_rebate": str(tax_before_rebate),
        "rebate": {
            "section": rules.rebate_section,
            "amount": str(rebate),
            "max_taxable": str(rules.rebate_max_taxable),
            "max_amount": str(rules.rebate_max_amount),
        },
        "surcharge": {
            "before_relief": str(surcharge_before),
            "after_relief": str(surcharge_after),
            "marginal_relief": str(relief),
            "marginal_relief_enabled": rules.marginal_relief_enabled,
        },
        "cess": {
            "rate": str(rules.cess_rate),
            "base": rules.cess_base,
            "amount": str(cess),
        },
        "special_income": special_details,
        "credits": {
            "tds": str(q(inp.tds_credit)),
            "tcs": str(q(inp.tcs_credit)),
            "advance_tax": str(q(inp.advance_tax_paid)),
        },
    }

    return TaxEngineResult(
        net_adjustments=net_adj,
        taxable_income=q(taxable),
        ordinary_income=q(ordinary_income),
        special_tax=q(special_tax),
        tax_amount=q(tax_after_rebate),
        tax_before_rebate=tax_before_rebate,
        surcharge_before_relief=surcharge_before,
        marginal_relief_amount=relief,
        surcharge_amount=surcharge_after,
        rebate_amount=rebate,
        cess_amount=cess,
        total_tax=total,
        tax_payable=payable,
        computation_method=effective_method,
        policy_id=rules.policy_id,
        rate_table_id=rules.rate_table_id,
        special_line_taxes=special_details,
        breakdown=breakdown,
    )


def run_pipeline(inp: TaxEngineInput, rules: ResolvedTaxRules) -> TaxEngineResult:
    """Public alias for ``compute_from_resolved``."""
    return compute_from_resolved(inp, rules)
