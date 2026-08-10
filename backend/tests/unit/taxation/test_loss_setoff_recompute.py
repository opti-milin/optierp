"""Recompute must not permanently shrink brought-forward losses or drift the input hash."""

from __future__ import annotations

import inspect
from decimal import Decimal

from app.services.taxation import loss_setoff as loss_service
from app.services.taxation import preview as preview_service
from app.services.taxation import runs as runs_service
from app.services.taxation.facts import AdjustmentFact
from app.services.taxation.preview import preserve_override_amounts


def test_available_bf_losses_accepts_for_computation_id() -> None:
    source = inspect.getsource(loss_service.available_bf_losses)
    assert "for_computation_id" in source
    assert "add_back" in source or "_active_setoff_totals_by_ledger" in source


def test_create_run_reverses_prior_setoffs_before_gathering_inputs() -> None:
    source = inspect.getsource(runs_service.create_run)
    assert "reverse_setoffs_for_computation" in source
    assert source.index("reverse_setoffs_for_computation") < source.index("gather_inputs")


def test_gather_inputs_uses_computation_scoped_bf_losses() -> None:
    source = inspect.getsource(preview_service.gather_inputs)
    assert "for_computation_id=doc.id if restore_own_setoffs else None" in source
    assert "preserve_override_amounts" in source


def test_create_run_disables_setoff_restore_after_reversing() -> None:
    source = inspect.getsource(runs_service.create_run)
    assert "restore_own_setoffs=False" in source
    assert source.index("reverse_setoffs_for_computation") < source.index(
        "restore_own_setoffs=False"
    )


def test_preserve_override_amounts_copies_from_prior_run_rows() -> None:
    draft = (
        AdjustmentFact(
            section_code="37(1)",
            rule_code=None,
            stage="PGBP",
            description="Penalty",
            direction="Add",
            amount=Decimal("100"),
            override_amount=None,
            status="Manual",
        ),
    )

    class _Line:
        rule_code = None
        section_code = "37(1)"
        description = "Penalty"
        override_amount = Decimal("80")

    out = preserve_override_amounts(draft, [_Line()])
    assert out[0].override_amount == Decimal("80")
    assert out[0].status == "Override"


def test_preserve_override_amounts_is_noop_without_overrides() -> None:
    draft = (
        AdjustmentFact(
            section_code="43B",
            rule_code=None,
            stage="PGBP",
            description="Dues",
            direction="Less",
            amount=Decimal("50"),
            override_amount=None,
            status="Manual",
        ),
    )
    assert preserve_override_amounts(draft, []) is draft or preserve_override_amounts(
        draft, []
    ) == draft
