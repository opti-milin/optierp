"""Shop floor (Phase 2): Operation/Workstation/Routing, BOM/WO ops, Job Card,
Material Consumption for Manufacture.

Revision ID: 0072_shop_floor
Revises: 0071_item_alternative
Create Date: 2026-07-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision: str = "0072_shop_floor"
down_revision: Union[str, None] = "0071_item_alternative"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _meta_columns() -> list[sa.Column]:
    return [
        sa.Column("id", pg.UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", pg.UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    ]


def _company_column() -> sa.Column:
    return sa.Column(
        "company_id",
        pg.UUID(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=False,
    )


def _enable_rls(table: str) -> None:
    op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
    op.execute(
        f"CREATE POLICY company_isolation ON {table} "
        f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
    )


def upgrade() -> None:
    # --- Engine masters (RLS) -------------------------------------------------
    op.create_table(
        "operations",
        *_meta_columns(),
        _company_column(),
        sa.Column("operation_name", sa.String(140), nullable=False),
        sa.Column("default_hour_rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "operation_name", name="uq_operation_name"),
    )
    op.create_index("ix_operations_company", "operations", ["company_id"])
    _enable_rls("operations")

    op.create_table(
        "workstations",
        *_meta_columns(),
        _company_column(),
        sa.Column("workstation_name", sa.String(140), nullable=False),
        sa.Column("hour_rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("working_hours", sa.Numeric(21, 6), nullable=False, server_default=sa.text("8")),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "workstation_name", name="uq_workstation_name"),
    )
    op.create_index("ix_workstations_company", "workstations", ["company_id"])
    _enable_rls("workstations")

    op.create_table(
        "routings",
        *_meta_columns(),
        _company_column(),
        sa.Column("routing_name", sa.String(140), nullable=False),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "routing_name", name="uq_routing_name"),
    )
    op.create_index("ix_routings_company", "routings", ["company_id"])
    _enable_rls("routings")

    op.create_table(
        "routing_operations",
        *_meta_columns(),
        sa.Column(
            "routing_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("routings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("operation_id", pg.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("workstation_id", pg.UUID(as_uuid=True), sa.ForeignKey("workstations.id"), nullable=True),
        sa.Column("time_in_mins", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.UniqueConstraint("routing_id", "idx", name="uq_routing_operation_idx"),
    )
    op.create_index("ix_routing_operations_routing", "routing_operations", ["routing_id"])

    # --- BOM operations (bespoke child, no RLS) --------------------------------
    op.add_column(
        "boms",
        sa.Column("routing_id", pg.UUID(as_uuid=True), sa.ForeignKey("routings.id"), nullable=True),
    )
    op.create_table(
        "bom_operations",
        *_meta_columns(),
        sa.Column(
            "bom_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("boms.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("operation_id", pg.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("workstation_id", pg.UUID(as_uuid=True), sa.ForeignKey("workstations.id"), nullable=True),
        sa.Column("time_in_mins", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("hour_rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("operating_cost", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_bom_operations_bom", "bom_operations", ["bom_id"])

    # --- Work Order operations -------------------------------------------------
    op.create_table(
        "work_order_operations",
        *_meta_columns(),
        sa.Column(
            "work_order_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("work_orders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("operation_id", pg.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("workstation_id", pg.UUID(as_uuid=True), sa.ForeignKey("workstations.id"), nullable=True),
        sa.Column("time_in_mins", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("hour_rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("planned_operating_cost", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'Pending'")),
        sa.Column("description", sa.Text(), nullable=True),
    )
    op.create_index("ix_work_order_operations_wo", "work_order_operations", ["work_order_id"])
    op.create_index(
        "ix_work_order_operations_workstation",
        "work_order_operations",
        ["workstation_id"],
    )

    # --- Job Card + time logs --------------------------------------------------
    op.create_table(
        "job_cards",
        *_meta_columns(),
        _company_column(),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column(
            "work_order_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("work_orders.id"),
            nullable=False,
        ),
        sa.Column(
            "work_order_operation_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("work_order_operations.id"),
            nullable=False,
        ),
        sa.Column("operation_id", pg.UUID(as_uuid=True), sa.ForeignKey("operations.id"), nullable=False),
        sa.Column("workstation_id", pg.UUID(as_uuid=True), sa.ForeignKey("workstations.id"), nullable=True),
        sa.Column("for_quantity", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("total_completed_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("time_in_mins", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("status", sa.String(30), nullable=False, server_default=sa.text("'Open'")),
        sa.Column("remarks", sa.Text(), nullable=True),
        sa.UniqueConstraint("company_id", "name", name="uq_job_card_name"),
        sa.UniqueConstraint(
            "work_order_operation_id",
            name="uq_job_card_wo_operation",
        ),
    )
    op.create_index("ix_job_cards_company_status", "job_cards", ["company_id", "status"])
    op.create_index("ix_job_cards_work_order", "job_cards", ["work_order_id"])

    op.create_table(
        "job_card_time_logs",
        *_meta_columns(),
        sa.Column(
            "job_card_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("job_cards.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("from_time", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("to_time", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("time_in_mins", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("completed_qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
    )
    op.create_index("ix_job_card_time_logs_jc", "job_card_time_logs", ["job_card_id"])


def downgrade() -> None:
    op.drop_index("ix_job_card_time_logs_jc", table_name="job_card_time_logs")
    op.drop_table("job_card_time_logs")
    op.drop_index("ix_job_cards_work_order", table_name="job_cards")
    op.drop_index("ix_job_cards_company_status", table_name="job_cards")
    op.drop_table("job_cards")

    op.drop_index("ix_work_order_operations_workstation", table_name="work_order_operations")
    op.drop_index("ix_work_order_operations_wo", table_name="work_order_operations")
    op.drop_table("work_order_operations")

    op.drop_index("ix_bom_operations_bom", table_name="bom_operations")
    op.drop_table("bom_operations")
    op.drop_column("boms", "routing_id")

    op.drop_index("ix_routing_operations_routing", table_name="routing_operations")
    op.drop_table("routing_operations")
    op.execute("DROP POLICY IF EXISTS company_isolation ON routings")
    op.drop_index("ix_routings_company", table_name="routings")
    op.drop_table("routings")

    op.execute("DROP POLICY IF EXISTS company_isolation ON workstations")
    op.drop_index("ix_workstations_company", table_name="workstations")
    op.drop_table("workstations")

    op.execute("DROP POLICY IF EXISTS company_isolation ON operations")
    op.drop_index("ix_operations_company", table_name="operations")
    op.drop_table("operations")
