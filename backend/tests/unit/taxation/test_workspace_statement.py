"""Unified workspace — section status derivation and Statement of Total Income rows.

Exercises the pure parts of ``services/taxation/workspace.py`` and
``services/taxation/statement.py`` with hand-built stand-ins, so the badge logic and
the statement layout are covered without a database.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from app.schemas.taxation import (
    TaxPreviewOut,
    TaxValidationIssueOut,
    TaxValidationOut,
)
from app.services.taxation import statement as statement_service
from app.services.taxation import workspace as workspace_service
from app.services.taxation.templates import is_concessional, mat_applies

D = Decimal


def _facts(**overrides: Any) -> SimpleNamespace:
    """A ``WorkspaceFacts``-shaped object carrying only what the builders read."""
    computation = SimpleNamespace(
        id=uuid.uuid4(),
        name="TAXC-0001",
        ay_code="2025-26",
        assessee_class_code="Company",
        regime_code="NORMAL",
        filing_type="Original",
        audit_applicable=True,
        docstatus=0,
        book_profit_115jb=None,
        current_run_id=None,
    )
    base: dict[str, Any] = {
        "computation": computation,
        "income_lines": [],
        "adjustment_lines": [],
        "registration": SimpleNamespace(pan="AAACE1234F"),
        "election": None,
        "assessee_class": SimpleNamespace(
            code="Company", title="Company", default_itr_form="ITR-6"
        ),
        "regime": SimpleNamespace(code="NORMAL", title="Normal provisions"),
        "assessment_year": SimpleNamespace(
            code="2025-26",
            prev_ay_code="2024-25",
            fy_start=date(2024, 4, 1),
            fy_end=date(2025, 3, 31),
        ),
        "runs": [],
        "current_run": None,
        "result": None,
        "registers": [],
        "depreciation_total": D("0.00"),
        "losses": [],
        "setoff_entries": [],
        "mat_credits": [],
        "mat_credit_available": D("0.00"),
        "credits": [],
        "challans": [],
        "reconciliations": [],
        "filings": [],
        "advance_tax": None,
        "asset_count": 0,
        "return_due_date": date(2025, 10, 31),
        "mat_applicable": True,
        "concessional_regime": False,
        "forfeited_incentives": [],
        "today": date(2025, 6, 1),
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def _income_line(head: str, net: str, seq: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        seq=seq,
        head=head,
        income_character_code="ORDINARY",
        sub_ref=None,
        gross=Decimal(net),
        deductions=D("0"),
        net=Decimal(net),
    )


def _adjustment(amount: str, direction: str = "Add") -> SimpleNamespace:
    return SimpleNamespace(
        id=uuid.uuid4(),
        run_id=None,
        section_code="37(1)",
        stage="PGBP",
        description="Penalty disallowed",
        direction=direction,
        amount=Decimal(amount),
        override_amount=None,
    )


def _ok_validation() -> TaxValidationOut:
    return TaxValidationOut(ok=True, blocking_count=0, advisory_count=0, issues=[])


# --- section status ----------------------------------------------------------


def test_untouched_year_reports_sections_not_started() -> None:
    sections = workspace_service.build_sections(_facts(), _ok_validation())
    by_key = {s.key: s for s in sections}

    assert by_key["income"].status == "not-started"
    assert by_key["summary"].status == "not-started"
    # Nothing to record is a complete state, not an unfinished one.
    assert by_key["adjustments"].status == "complete"
    assert by_key["losses"].status == "complete"


def test_income_section_summarises_heads_and_total() -> None:
    facts = _facts(income_lines=[_income_line("PGBP", "1000000"), _income_line("OS", "50000", 1)])
    section = next(s for s in workspace_service.build_sections(facts, _ok_validation()) if s.key == "income")

    assert section.status == "complete"
    assert section.row_count == 2
    assert section.summary is not None
    assert "2 heads of income" in section.summary
    assert "₹10,50,000" in section.summary


def test_blocking_issue_overrides_a_complete_section() -> None:
    facts = _facts(income_lines=[_income_line("PGBP", "1000000")])
    validation = TaxValidationOut(
        ok=False,
        blocking_count=1,
        advisory_count=0,
        issues=[
            TaxValidationIssueOut(
                severity="blocking",
                code="income_negative",
                message="Income under this head cannot be negative.",
                section="income",
            )
        ],
    )
    sections = workspace_service.build_sections(facts, validation)
    by_key = {s.key: s for s in sections}

    assert by_key["income"].status == "has-errors"
    assert by_key["income"].blocking_count == 1
    # The Review section always mirrors the validation totals.
    assert by_key["review"].status == "has-errors"
    assert by_key["review"].blocking_count == 1


def test_loss_summary_never_reports_a_negative_carry_forward() -> None:
    """``amount_remaining`` is already net of the set-off, so it must not be netted twice."""
    facts = _facts(
        losses=[SimpleNamespace(id=uuid.uuid4(), amount_remaining=D("0.00"))],
        setoff_entries=[SimpleNamespace(amount_set_off=D("1435000.00"))],
    )
    section = next(s for s in workspace_service.build_sections(facts, _ok_validation()) if s.key == "losses")

    assert section.summary == "₹14,35,000 set off this year, ₹0 carried forward"
    assert "-" not in section.summary


def test_loss_summary_reports_what_actually_remains() -> None:
    facts = _facts(
        losses=[
            SimpleNamespace(id=uuid.uuid4(), amount_remaining=D("250000.00")),
            SimpleNamespace(id=uuid.uuid4(), amount_remaining=D("100000.00")),
        ],
        setoff_entries=[SimpleNamespace(amount_set_off=D("500000.00"))],
    )
    section = next(s for s in workspace_service.build_sections(facts, _ok_validation()) if s.key == "losses")

    assert section.summary == "₹5,00,000 set off this year, ₹3,50,000 carried forward"


def test_section_summaries_group_rupees_the_indian_way() -> None:
    """Section navigation summaries are read by a Chartered Accountant, not a machine."""
    assert workspace_service._rupees(D("12135000.00")) == "₹1,21,35,000"
    assert workspace_service._rupees(D("2012100.00")) == "₹20,12,100"
    assert workspace_service._rupees(D("180964.05")) == "₹1,80,964.05"
    assert workspace_service._rupees(D("999.00")) == "₹999"
    assert workspace_service._rupees(D("-41000.00")) == "-₹41,000"
    assert workspace_service._rupees(None) == "₹0"


def test_mat_section_hidden_when_not_applicable_and_no_ledger() -> None:
    facts = _facts(mat_applicable=False, mat_credits=[])
    keys = [s.key for s in workspace_service.build_sections(facts, _ok_validation())]
    assert "mat" not in keys


def test_mat_section_kept_when_a_credit_ledger_exists() -> None:
    facts = _facts(mat_applicable=False, mat_credits=[SimpleNamespace(id=uuid.uuid4())])
    keys = [s.key for s in workspace_service.build_sections(facts, _ok_validation())]
    assert "mat" in keys


def test_next_action_points_at_the_first_unfinished_section() -> None:
    sections = workspace_service.build_sections(_facts(), _ok_validation())
    action, section_key = workspace_service._next_action(sections, 0)

    assert section_key == "income"
    assert "Statement of Income" in action


def test_next_action_prefers_a_section_with_errors() -> None:
    facts = _facts(income_lines=[_income_line("PGBP", "1000000")])
    validation = TaxValidationOut(
        ok=False,
        blocking_count=1,
        advisory_count=0,
        issues=[
            TaxValidationIssueOut(
                severity="blocking",
                code="challan_unposted",
                message="A challan has not been posted.",
                section="challans",
            )
        ],
    )
    sections = workspace_service.build_sections(facts, validation)
    action, section_key = workspace_service._next_action(sections, 0)

    assert section_key == "challans"
    assert action.startswith("Resolve issues in")


# --- workspace context -------------------------------------------------------


def test_context_labels_are_business_terms() -> None:
    ctx = workspace_service.build_context(_facts())

    assert ctx.ay_label.startswith("Assessment Year")
    assert ctx.financial_year_label == "Financial Year 2024-25"
    assert ctx.entity_class_label == "Company"
    assert ctx.itr_form_code == "ITR-6"
    assert ctx.is_draft is True
    assert ctx.mat_applicable is True


def test_context_reports_forfeited_incentives_under_a_concessional_election() -> None:
    facts = _facts(
        concessional_regime=True,
        mat_applicable=False,
        forfeited_incentives=["Additional depreciation under Section 32(1)(iia)"],
    )
    ctx = workspace_service.build_context(facts)

    assert ctx.concessional_regime is True
    assert ctx.mat_applicable is False
    assert ctx.forfeited_incentives


def test_mat_applies_only_to_companies_outside_the_concessional_regimes() -> None:
    assert mat_applies("Company", "NORMAL") is True
    assert mat_applies("Company", "115BAA") is False
    assert mat_applies("Company", "115BAB") is False
    assert mat_applies("Individual", "NORMAL") is False
    assert is_concessional("115BAA") is True
    assert is_concessional("NORMAL") is False


# --- statement ---------------------------------------------------------------


def _preview(**overrides: Any) -> TaxPreviewOut:
    base: dict[str, Any] = {
        "ay_code": "2025-26",
        "gross_total_income": D("1000000.00"),
        "total_income": D("900000.00"),
        "losses_set_off": D("50000.00"),
        "tax_depreciation_claimed": D("50000.00"),
        "tax_on_total_income": D("225000.00"),
        "tax_normal": D("234000.00"),
        "cess_amount": D("9000.00"),
        "credits_total": D("100000.00"),
        "total_tax": D("234000.00"),
        "net_payable": D("134000.00"),
        "tax_applied_basis": "Normal",
    }
    base.update(overrides)
    return TaxPreviewOut(**base)


def test_income_section_ends_with_gross_total_income() -> None:
    facts = _facts(income_lines=[_income_line("PGBP", "800000"), _income_line("OS", "200000", 1)])
    section = statement_service.build_income_section(facts)

    assert section.title == "Computation of Total Income"
    last = section.rows[-1]
    assert last.label == "Gross Total Income"
    assert last.amount == D("1000000.00")
    assert last.emphasis == "subtotal"
    # Heads are spelled out, never abbreviated.
    assert any("Profits and Gains of Business or Profession" == r.label for r in section.rows)


def test_income_section_group_header_has_no_amount_when_details_follow() -> None:
    """Multiple lines under one head: head is a label only; details carry amounts."""
    facts = _facts(
        income_lines=[
            _income_line("PGBP", "500000", 0),
            _income_line("PGBP", "300000", 1),
        ]
    )
    section = statement_service.build_income_section(facts)
    head = next(r for r in section.rows if r.key == "income.head.PGBP")
    details = [r for r in section.rows if r.key.startswith("income.PGBP.")]

    assert head.amount is None
    assert head.indent == 1
    assert len(details) == 2
    assert all(d.indent == 2 and d.amount is not None for d in details)


def test_adjustments_section_separates_add_backs_from_deductions() -> None:
    facts = _facts(adjustment_lines=[_adjustment("40000"), _adjustment("15000", "Deduct")])
    section = statement_service.build_adjustments_section(facts)
    by_key = {r.key: r for r in section.rows}

    assert by_key["adj.add.total"].amount == D("40000.00")
    assert by_key["adj.less.total"].amount == D("15000.00")
    assert by_key["adj.net"].amount == D("25000.00")


def test_adjustments_section_ignores_engine_written_rows() -> None:
    engine_line = _adjustment("99999")
    engine_line.run_id = uuid.uuid4()
    facts = _facts(adjustment_lines=[engine_line])
    section = statement_service.build_adjustments_section(facts)

    assert section.rows[0].key == "adj.empty"


def test_total_income_section_reads_in_statutory_order() -> None:
    section = statement_service.build_total_income_section(_preview())
    keys = [row.key for row in section.rows]

    assert keys == ["ti.gross", "ti.depreciation", "ti.setoff", "ti.total"]
    total = section.rows[-1]
    assert total.emphasis == "total"
    assert total.statutory_ref == "Section 288A"


def test_mat_section_is_omitted_where_section_115jb_does_not_apply() -> None:
    facts = _facts(mat_applicable=False)
    assert statement_service.build_mat_section(facts, _preview()) is None


def test_mat_section_states_which_basis_won() -> None:
    facts = _facts(mat_applicable=True)
    facts.computation.book_profit_115jb = D("5000000")
    section = statement_service.build_mat_section(
        facts, _preview(tax_mat=D("750000.00"), tax_applied_basis="MAT")
    )

    assert section is not None
    verdict = next(r for r in section.rows if r.key == "mat.basis")
    assert verdict.note is not None
    assert "Minimum Alternate Tax applies" in verdict.note


def test_net_section_shows_a_refund_only_when_one_is_due() -> None:
    without = statement_service.build_net_section(_preview())
    assert not any(row.key == "net.refund" for row in without.rows)

    with_refund = statement_service.build_net_section(
        _preview(net_payable=D("0.00"), refund_due=D("25000.00"))
    )
    assert any(row.key == "net.refund" for row in with_refund.rows)


def test_interest_section_labels_every_charge_in_plain_english() -> None:
    section = statement_service.build_interest_section(
        _preview(interest_234a=D("1000"), interest_234b=D("2000"), total_interest=D("3000"))
    )
    labels = {row.key: row.label for row in section.rows}

    assert labels["interest.234a"] == "Interest for Late Filing of Return"
    assert labels["interest.234b"] == "Interest for Short Payment of Advance Tax"
    assert labels["interest.234c"] == "Interest for Deferment of Advance Tax Instalments"


def test_head_and_character_labels_never_leak_a_code() -> None:
    assert statement_service.head_label("HP") == "Income from House Property"
    assert statement_service.head_label("PGBP") == "Profits and Gains of Business or Profession"
    assert (
        statement_service.character_label("LTCG_112A")
        == "Long Term Capital Gains (Section 112A)"
    )
    # An unknown code degrades to something readable rather than raising.
    assert statement_service.head_label("SOMETHING_NEW") == "Something New"
