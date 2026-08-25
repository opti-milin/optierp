"""Number meetings per book, not per entity.

``create_meeting`` allocates the serial as ``MAX(serial_no) + 1`` **within a meeting
type**, which is right: an entity keeps a separate book for board meetings, AGMs, EGMs
and each committee, and "the 5th Board Meeting" and "the 5th Annual General Meeting" are
different meetings that both legitimately carry the number 5.

The constraint `0096` shipped was ``unique(entity_id, serial_no)``, which cannot express
that — the first AGM of an entity that has ever held a board meeting collides on serial 1.
The comment inside ``create_meeting`` already names the intended constraint as
``unique(entity_id, meeting_type, serial_no)``; only the DDL disagreed.

Nothing had exercised it until an entity held two kinds of meeting in the same tenant,
which is why it survived Phase 3. Committee meetings are covered by the same key: they
share the ``committee`` type, so two committees of one entity still share a sequence —
consistent with ``ss_dates.scope_for``, which is where a per-committee book would have to
be introduced if it is ever wanted.
"""

from __future__ import annotations

from alembic import op

revision = "0101_meeting_serial_per_type"
down_revision = "0100_secretarial_capital"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_constraint("uq_secretarial_meeting_serial", "secretarial_meetings", type_="unique")
    op.create_unique_constraint(
        "uq_secretarial_meeting_serial",
        "secretarial_meetings",
        ["entity_id", "meeting_type", "serial_no"],
    )


def downgrade() -> None:
    # Narrowing back can fail on data the wider key allowed, which is the point of the
    # change; a downgrade on a populated database has to reconcile the duplicates first.
    op.drop_constraint("uq_secretarial_meeting_serial", "secretarial_meetings", type_="unique")
    op.create_unique_constraint(
        "uq_secretarial_meeting_serial", "secretarial_meetings", ["entity_id", "serial_no"]
    )
