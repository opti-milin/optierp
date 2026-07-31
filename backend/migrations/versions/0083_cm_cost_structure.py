"""Add cost structure / drivers, explanations, variance runs (CM Cost Driver Framework)."""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0083_cm_cost_structure"
down_revision = "0082_cm_plan_allocations"
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


def upgrade() -> None:
    op.add_column(
        "cm_plans",
        sa.Column("cost_structure_snapshot", JSONB(), nullable=True),
    )
    op.add_column("cm_plan_costs", sa.Column("explanation", JSONB(), nullable=True))
    op.add_column("cm_plan_costs", sa.Column("parent_driver", sa.String(length=60), nullable=True))
    op.add_column(
        "cm_plan_costs",
        sa.Column("is_group", sa.Boolean(), server_default=sa.text("false"), nullable=False),
    )

    op.create_table(
        "cm_cost_structures",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("template_id", sa.String(60)),
        sa.Column("effective_from", sa.Date()),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
    )
    op.create_index(
        "ix_cm_cost_structures_company_active",
        "cm_cost_structures",
        ["company_id", "is_active"],
    )
    op.create_index("ix_cm_cost_structures_company_id", "cm_cost_structures", ["company_id"])
    _rls("cm_cost_structures")

    op.create_table(
        "cm_cost_drivers",
        *_meta_columns(),
        sa.Column(
            "cost_structure_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cm_cost_structures.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("label", sa.String(140), nullable=False),
        sa.Column("parent_code", sa.String(60)),
        sa.Column("is_group", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("cm_class", sa.String(40), nullable=False),
        sa.Column("allocation_method", sa.String(40)),
        sa.Column("allocation_basis", sa.String(40)),
        sa.Column("method_params", JSONB()),
        sa.Column("basis_params", JSONB()),
        sa.Column("source", sa.String(40)),
        sa.Column("scope", sa.String(20), server_default=sa.text("'header'"), nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("is_system", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.UniqueConstraint("cost_structure_id", "code", name="uq_cm_cost_driver_code"),
    )
    op.create_index("ix_cm_cost_drivers_structure", "cm_cost_drivers", ["cost_structure_id"])

    op.create_table(
        "cm_plan_variance_runs",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column(
            "cm_plan_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cm_plans.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("scenario_id", UUID(as_uuid=True)),
        sa.Column("from_date", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("to_date", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("rows", JSONB()),
        sa.Column("warnings", JSONB()),
    )
    op.create_index("ix_cm_plan_variance_plan", "cm_plan_variance_runs", ["cm_plan_id"])
    op.create_index("ix_cm_plan_variance_runs_company_id", "cm_plan_variance_runs", ["company_id"])
    _rls("cm_plan_variance_runs")

    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cm_cost_structures TO erp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cm_cost_drivers TO erp_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON cm_plan_variance_runs TO erp_app")


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS company_isolation ON cm_plan_variance_runs")
    op.drop_table("cm_plan_variance_runs")
    op.drop_table("cm_cost_drivers")
    op.execute("DROP POLICY IF EXISTS company_isolation ON cm_cost_structures")
    op.drop_table("cm_cost_structures")
    op.drop_column("cm_plan_costs", "is_group")
    op.drop_column("cm_plan_costs", "parent_driver")
    op.drop_column("cm_plan_costs", "explanation")
    op.drop_column("cm_plans", "cost_structure_snapshot")
