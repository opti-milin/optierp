"""115JB MAT compare — pure kernel (no DB)."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.taxation.kernel.money import ZERO, money, percent_of, q

DEFAULT_MAT_RATE = Decimal("15")


@dataclass(frozen=True, slots=True)
class MatInput:
    book_profit: Decimal
    normal_tax_payable: Decimal
    mat_rate_percent: Decimal = DEFAULT_MAT_RATE
    available_mat_credit: Decimal = ZERO
    apply_cess_on_mat: bool = True
    cess_rate_percent: Decimal = Decimal("4")


@dataclass(frozen=True, slots=True)
class MatResult:
    book_profit: Decimal
    mat_before_cess: Decimal
    mat_tax: Decimal
    normal_tax: Decimal
    tax_before_credit: Decimal
    mat_credit_created: Decimal
    mat_credit_utilised: Decimal
    tax_after_mat: Decimal
    applied_basis: str  # Normal | MAT
    breakdown: dict[str, str]


def compare_mat(inp: MatInput) -> MatResult:
    """Compute MAT on book profit and choose the higher of MAT vs normal tax.

    When MAT applies, excess over normal creates 115JAA credit.
    When normal applies and credit is available, utilise up to the excess of normal over MAT.
    """
    book = q(money(inp.book_profit))
    normal = q(money(inp.normal_tax_payable))
    rate = money(inp.mat_rate_percent)
    mat_before = q(percent_of(book, rate)) if book > ZERO else ZERO
    mat_tax = mat_before
    if inp.apply_cess_on_mat and mat_before > ZERO:
        mat_tax = q(mat_before + percent_of(mat_before, inp.cess_rate_percent))

    available = q(money(inp.available_mat_credit))
    credit_created = ZERO
    credit_utilised = ZERO

    if mat_tax > normal:
        applied = "MAT"
        tax_before_credit = mat_tax
        credit_created = q(mat_tax - normal)
        tax_after = mat_tax
    else:
        applied = "Normal"
        tax_before_credit = normal
        excess = q(normal - mat_tax)
        credit_utilised = min(available, excess) if excess > ZERO and available > ZERO else ZERO
        tax_after = q(normal - credit_utilised)

    return MatResult(
        book_profit=book,
        mat_before_cess=mat_before,
        mat_tax=mat_tax,
        normal_tax=normal,
        tax_before_credit=tax_before_credit,
        mat_credit_created=credit_created,
        mat_credit_utilised=credit_utilised,
        tax_after_mat=tax_after,
        applied_basis=applied,
        breakdown={
            "book_profit": format(book, "f"),
            "mat_rate_percent": format(rate, "f"),
            "mat_tax": format(mat_tax, "f"),
            "normal_tax": format(normal, "f"),
            "applied_basis": applied,
            "mat_credit_created": format(credit_created, "f"),
            "mat_credit_utilised": format(credit_utilised, "f"),
            "tax_after_mat": format(tax_after, "f"),
        },
    )
