"""Tax interest (234A/B/C), advance-tax calendar, compliance reminders — Phase 7."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0090_tax_interest"
down_revision = "0089_mat_setoff_depreciation"
branch_labels = None
depends_on = None


def _meta_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    ]


def _rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY company_isolation ON {table} "
        f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
    )
    op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO erp_app")


def upgrade() -> None:
    op.add_column(
        "tax_computations",
        sa.Column("return_filed_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "tax_computations",
        sa.Column(
            "audit_applicable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "tax_computations",
        sa.Column("itr_due_date_override", sa.Date(), nullable=True),
    )

    op.create_table(
        "tax_compliance_reminders",
        *_meta_columns(),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("ay_code", sa.String(20), nullable=False),
        sa.Column("rule_code", sa.String(80), nullable=False),
        sa.Column("rule_kind", sa.String(40), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("channel", sa.String(40), nullable=False, server_default=sa.text("'email'")),
        sa.Column("status", sa.String(40), nullable=False, server_default=sa.text("'Sent'")),
        sa.Column("shortfall_amount", sa.Numeric(21, 6), nullable=True),
        sa.Column("recipients", JSONB(), nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column("payload", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("error", sa.String(500), nullable=True),
        sa.UniqueConstraint(
            "company_id",
            "ay_code",
            "rule_code",
            "due_date",
            name="uq_tax_compliance_reminder_once",
        ),
    )
    op.create_index(
        "ix_tax_compliance_reminders_company_ay",
        "tax_compliance_reminders",
        ["company_id", "ay_code"],
    )
    _rls("tax_compliance_reminders")

    # Seed advance-tax reminder template (idempotent on name).
    op.execute(
        """
        INSERT INTO notification_templates (id, name, channel, subject, body, is_html, docstatus, creation, modified)
        SELECT gen_random_uuid(),
               'tax_advance_instalment_reminder',
               'email',
               'Advance tax due {{ due_date }} — {{ company_name }}',
               'Advance tax instalment {{ label }} for AY {{ ay_code }} is due on {{ due_date }}.
Required cumulative: {{ required }}
Paid to date: {{ paid }}
Suggested payment: {{ shortfall }}

Please deposit via Tax Challans before the due date to avoid interest under s.234C.',
               false,
               0,
               now(),
               now()
        WHERE NOT EXISTS (
            SELECT 1 FROM notification_templates WHERE name = 'tax_advance_instalment_reminder'
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM notification_templates WHERE name = 'tax_advance_instalment_reminder'"
    )
    op.execute("DROP POLICY IF EXISTS company_isolation ON tax_compliance_reminders")
    op.drop_table("tax_compliance_reminders")
    op.drop_column("tax_computations", "itr_due_date_override")
    op.drop_column("tax_computations", "audit_applicable")
    op.drop_column("tax_computations", "return_filed_date")
