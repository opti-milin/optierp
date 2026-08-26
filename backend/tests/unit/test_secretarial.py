"""Secretarial engine — date maths, Rule 5 gate, applicability AST, block trees,
the s.186 ceiling and the share-transfer state machine."""

from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.services.secretarial import applicability, blocks, capital, eligibility, s186, ss_dates
from app.services.secretarial.applicability import Verdict


def test_agm_notice_uses_clear_days():
    # 21 clear days + the two excluded endpoints.
    assert ss_dates.notice_due_on(date(2026, 9, 30), "agm") == date(2026, 9, 7)


def test_board_notice_is_seven_days():
    assert ss_dates.notice_due_on(date(2026, 9, 15), "board") == date(2026, 9, 8)


def test_minutes_deadlines():
    draft, signed = ss_dates.minutes_deadlines(date(2026, 9, 15))
    assert draft == date(2026, 9, 30)
    assert signed == date(2026, 10, 15)


def test_shorter_notice_is_flagged_not_as_a_breach():
    result = ss_dates.notice_compliance(
        scheduled_on=date(2026, 9, 15),
        sent_on=date(2026, 9, 14),
        meeting_type="board",
        shorter_notice=True,
    )
    assert result["status"] == "shorter_notice"


def test_rule5_blocks_financial_statements():
    result = eligibility.check(
        "Approval of financial statements",
        "RESOLVED THAT the audited financial statements and the Board's Report be approved.",
    )
    assert result["eligible"] is False
    assert result["blocked_matters"]


def test_rule5_allows_ordinary_commercial_contract():
    result = eligibility.check(
        "Engagement of interior consultant",
        "RESOLVED THAT the Company engage Kala Studio to design the reception.",
    )
    assert result["eligible"] is True
    assert result["blocked_matters"] == []


def test_applicability_legacy_kinds():
    ctx = {"entity": {"kind": "company", "class": "private", "listed": False}, "facts": {}}
    verdict, _ = applicability.evaluate({"kinds": ["company"]}, ctx)
    assert verdict == Verdict.APPLIES
    verdict, _ = applicability.evaluate({"kinds": ["llp"]}, ctx)
    assert verdict == Verdict.NOT_APPLICABLE


def test_applicability_unknown_when_facts_missing():
    ctx = {
        "entity": {"kind": "company", "class": "private", "listed": False},
        "facts": {"net_profit": None},
    }
    verdict, reasons = applicability.evaluate({"gte": ["facts.net_profit", 50_000_000]}, ctx)
    assert verdict == Verdict.UNKNOWN
    assert reasons


def test_applicability_csr_threshold():
    ctx = {
        "entity": {"kind": "company", "class": "private", "listed": False},
        "facts": {"net_profit": 60_000_000, "turnover": 1_000_000, "net_worth": 1_000_000},
    }
    predicate = {
        "all": [
            {"eq": ["entity.kind", "company"]},
            {
                "any": [
                    {"gte": ["facts.net_worth", 5_000_000_000]},
                    {"gte": ["facts.turnover", 10_000_000_000]},
                    {"gte": ["facts.net_profit", 50_000_000]},
                ]
            },
        ]
    }
    verdict, _ = applicability.evaluate(predicate, ctx)
    assert verdict == Verdict.APPLIES


def test_block_tree_resolves_placeholders():
    tree = [
        {"type": "heading", "level": 2, "text": "Resolution — {{ appointee_name }}"},
        {"type": "paragraph", "text": "DIN {{ appointee_din }}"},
    ]
    validated = blocks.validate_tree(tree, where="test")
    resolved = blocks.resolve(validated, {"appointee_name": "Rahul Mehta", "appointee_din": "08234567"})
    assert "Rahul Mehta" in resolved[0]["text"]
    assert "08234567" in resolved[1]["text"]


# --- Phase 5: capital and s.186 ---------------------------------------------------


def test_s186_limit_takes_the_higher_of_the_two_tests():
    # 60% of (10L + 185L + 15L) = 126L; 100% of (185L + 15L) = 200L. s.186(2) says
    # "whichever is more", so the second test governs.
    out = s186.compute_limit(
        paid_up_capital=Decimal(1_000_000),
        free_reserves=Decimal(18_500_000),
        securities_premium=Decimal(1_500_000),
    )
    assert out["limit_sixty_pct"] == Decimal("12600000.00")
    assert out["limit_hundred_pct"] == Decimal(20_000_000)
    assert out["effective_limit"] == Decimal(20_000_000)


def test_s186_limit_is_unknowable_without_free_reserves():
    # Not zero. A company with unrecorded free reserves has no computable ceiling, and
    # treating the gap as nil would report a breach that may not exist.
    out = s186.compute_limit(
        paid_up_capital=Decimal(1_000_000), free_reserves=None, securities_premium=None
    )
    assert out["effective_limit"] is None
    assert any("Free reserves" in gap for gap in out["gaps"])


def test_s186_securities_premium_absent_is_read_as_nil_but_said_out_loud():
    out = s186.compute_limit(
        paid_up_capital=Decimal(1_000_000),
        free_reserves=Decimal(4_000_000),
        securities_premium=None,
    )
    assert out["effective_limit"] == Decimal(4_000_000)
    assert any("Securities premium" in gap for gap in out["gaps"])


def test_wholly_owned_subsidiary_is_exempt_from_the_ceiling():
    # s.186(11): the entry still belongs on the register, but it consumes no headroom.
    assert "wholly_owned_subsidiary" in s186.EXEMPT_RELATIONS


@pytest.mark.parametrize(
    ("state", "target", "allowed"),
    [
        ("draft", "board_approved", True),
        ("draft", "issued_posted", False),  # shares cannot move before the board approves
        ("board_approved", "issued_posted", True),
        ("issued_posted", "reverted", True),
        ("reverted", "issued_posted", False),  # a reversal is final
        ("issued_posted", "board_approved", False),
    ],
)
def test_transfer_state_machine(state, target, allowed):
    if allowed:
        capital._check_transition(state, target)
    else:
        with pytest.raises(ValidationError):
            capital._check_transition(state, target)


def test_transfer_refusal_names_both_states():
    # The message has to be actionable: "invalid transition" tells a user nothing about
    # what to do next.
    with pytest.raises(ValidationError) as exc:
        capital._check_transition("draft", "issued_posted")
    message = str(exc.value.args[0])
    assert "draft" in message and "issued_posted" in message and "board_approved" in message
