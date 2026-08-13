"""Preview overlay must ignore the engine's own audit rows.

Each saved computation run writes its evaluated adjustments back as rows carrying a
``run_id``. The workspace editor reads the whole list, so it can echo those rows back in
the preview overlay. If they were honoured, one adjustment would be counted once per
historic run and the live result rail would drift further from the truth on every save.
"""

from __future__ import annotations

import uuid
from decimal import Decimal

from app.schemas.taxation import TaxComputationAdjustmentLineIn
from app.services.taxation.preview import adjustment_facts_from_overlay


def _line(
    section_code: str, amount: str, *, run_id: uuid.UUID | None = None, direction: str = "Add"
) -> TaxComputationAdjustmentLineIn:
    return TaxComputationAdjustmentLineIn(
        run_id=run_id,
        section_code=section_code,
        stage="PGBP",
        description=f"Adjustment {section_code}",
        direction=direction,
        amount=Decimal(amount),
    )


def test_manual_rows_are_kept() -> None:
    facts = adjustment_facts_from_overlay([_line("37(1)", "48500"), _line("43B", "345000")])

    assert len(facts) == 2
    assert sum(f.amount for f in facts) == Decimal("393500")


def test_run_scoped_rows_are_dropped() -> None:
    run = uuid.uuid4()
    facts = adjustment_facts_from_overlay(
        [
            _line("37(1)", "48500"),
            _line("37(1)", "48500", run_id=run),
        ]
    )

    assert len(facts) == 1
    assert facts[0].amount == Decimal("48500")


def test_the_same_adjustment_is_never_counted_once_per_run() -> None:
    """The exact shape the workspace sent before the fix: manual rows plus two runs."""
    run_one, run_two = uuid.uuid4(), uuid.uuid4()
    manual = [_line("37(1)", "48500"), _line("43B", "345000"), _line("43B", "210000", direction="Less")]
    replayed = [
        _line(line.section_code, format(line.amount, "f"), run_id=run, direction=line.direction)
        for run in (run_one, run_two)
        for line in manual
    ]

    facts = adjustment_facts_from_overlay([*manual, *replayed])

    assert len(facts) == len(manual)
    added = sum(f.amount for f in facts if f.direction == "Add")
    deducted = sum(f.amount for f in facts if f.direction == "Less")
    assert added - deducted == Decimal("183500")


def test_run_id_is_optional_so_older_clients_still_work() -> None:
    line = TaxComputationAdjustmentLineIn(section_code="37(1)", amount=Decimal("100"))

    assert line.run_id is None
    assert len(adjustment_facts_from_overlay([line])) == 1
