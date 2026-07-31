"""Add Contribution Margin Plan tables + shipping_rule_id on QTN/SO.

Revision ID: 0081_cm_planning
Revises: 0080_shipping_transit_days
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0081_cm_planning"
down_revision = "0080_shipping_transit_days"
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


def _drop_rls(table: str) -> None:
    op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")


def upgrade() -> None:
    op.add_column(
        "quotations",
        sa.Column("shipping_rule_id", UUID(as_uuid=True), sa.ForeignKey("shipping_rules.id", ondelete="SET NULL")),
    )
    op.add_column(
        "sales_orders",
        sa.Column("shipping_rule_id", UUID(as_uuid=True), sa.ForeignKey("shipping_rules.id", ondelete="SET NULL")),
    )

    op.create_table(
        "cm_plans",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("quotation_id", UUID(as_uuid=True), sa.ForeignKey("quotations.id", ondelete="SET NULL")),
        sa.Column("sales_order_id", UUID(as_uuid=True), sa.ForeignKey("sales_orders.id", ondelete="SET NULL")),
        sa.Column("template_id", sa.String(60)),
        sa.Column("target_cm1_pct", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("target_cm2_pct", sa.Numeric(8, 4)),
        sa.Column("min_cm1_pct", sa.Numeric(8, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("submit_policy", sa.String(20), nullable=False, server_default=sa.text("'warn'")),
        sa.Column("remarks", sa.Text()),
        sa.Column("warnings", JSONB()),
        sa.UniqueConstraint("company_id", "name", name="uq_cm_plan_name"),
    )
    op.create_index("ix_cm_plans_company_id", "cm_plans", ["company_id"])
    op.create_index("ix_cm_plans_company_docstatus", "cm_plans", ["company_id", "docstatus"])
    op.create_index("ix_cm_plans_quotation", "cm_plans", ["quotation_id"])
    op.create_index("ix_cm_plans_sales_order", "cm_plans", ["sales_order_id"])
    # One draft plan per quotation
    op.execute(
        "CREATE UNIQUE INDEX uq_cm_plans_draft_quotation "
        "ON cm_plans (quotation_id) "
        "WHERE quotation_id IS NOT NULL AND docstatus = 0"
    )
    _rls("cm_plans")

    op.create_table(
        "cm_plan_scenarios",
        *_meta_columns(),
        sa.Column("cm_plan_id", UUID(as_uuid=True), sa.ForeignKey("cm_plans.id", ondelete="CASCADE"), nullable=False),
        sa.Column("name", sa.String(140), nullable=False),
        sa.Column("is_baseline", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("overrides", JSONB()),
        sa.Column("customer_id", UUID(as_uuid=True), sa.ForeignKey("customers.id", ondelete="SET NULL")),
        sa.Column("sales_partner_id", UUID(as_uuid=True), sa.ForeignKey("sales_partners.id", ondelete="SET NULL")),
        sa.Column("shipping_rule_id", UUID(as_uuid=True), sa.ForeignKey("shipping_rules.id", ondelete="SET NULL")),
        sa.Column("revenue", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("material", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("labor", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("freight", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("commission", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("packaging", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("variable_cost", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("product_channel_fixed", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("segment_bu_fixed", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("corporate_overhead", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("cm1", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("cm2", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("cm3", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("operating_profit", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("cm1_pct", sa.Numeric(8, 4)),
        sa.Column("cm2_pct", sa.Numeric(8, 4)),
        sa.Column("cm3_pct", sa.Numeric(8, 4)),
        sa.Column("operating_profit_pct", sa.Numeric(8, 4)),
        sa.Column("min_selling_total", sa.Numeric(21, 6)),
        sa.Column("applied_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("ix_cm_plan_scenarios_plan", "cm_plan_scenarios", ["cm_plan_id"])
    op.execute(
        "CREATE UNIQUE INDEX uq_cm_plan_baseline "
        "ON cm_plan_scenarios (cm_plan_id) WHERE is_baseline = true"
    )

    op.create_table(
        "cm_plan_items",
        *_meta_columns(),
        sa.Column(
            "cm_plan_scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cm_plan_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("line_key", sa.String(64), nullable=False),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="SET NULL")),
        sa.Column("item_code", sa.String(140)),
        sa.Column("item_name", sa.String(140), nullable=False),
        sa.Column("qty", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("selling_rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("selling_amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("quotation_item_id", UUID(as_uuid=True)),
        sa.Column("sales_order_item_id", UUID(as_uuid=True)),
        sa.Column("material", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("labor", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("freight", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("packaging", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("variable_cost", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("cm1", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("min_selling_rate", sa.Numeric(21, 6)),
    )
    op.create_index("ix_cm_plan_items_scenario", "cm_plan_items", ["cm_plan_scenario_id"])

    op.create_table(
        "cm_plan_costs",
        *_meta_columns(),
        sa.Column(
            "cm_plan_scenario_id",
            UUID(as_uuid=True),
            sa.ForeignKey("cm_plan_scenarios.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("cm_plan_item_id", UUID(as_uuid=True), sa.ForeignKey("cm_plan_items.id", ondelete="CASCADE")),
        sa.Column("idx", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("driver", sa.String(40), nullable=False),
        sa.Column("cm_class", sa.String(40), nullable=False),
        sa.Column("amount", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("source", sa.String(40), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("rate", sa.Numeric(21, 6)),
        sa.Column("qty", sa.Numeric(21, 6)),
        sa.Column("notes", sa.String(255)),
    )
    op.create_index("ix_cm_plan_costs_scenario", "cm_plan_costs", ["cm_plan_scenario_id"])

    op.create_table(
        "cm_cost_rates",
        *_meta_columns(),
        sa.Column("company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False),
        sa.Column("rate_name", sa.String(140), nullable=False),
        sa.Column("driver", sa.String(40), nullable=False, server_default=sa.text("'packaging'")),
        sa.Column("item_id", UUID(as_uuid=True), sa.ForeignKey("items.id", ondelete="CASCADE")),
        sa.Column("item_group_id", UUID(as_uuid=True), sa.ForeignKey("item_groups.id", ondelete="CASCADE")),
        sa.Column("rate", sa.Numeric(21, 6), nullable=False, server_default=sa.text("0")),
        sa.Column("uom", sa.String(140)),
        sa.Column("disabled", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.UniqueConstraint("company_id", "rate_name", name="uq_cm_cost_rate_name"),
    )
    op.create_index("ix_cm_cost_rates_company", "cm_cost_rates", ["company_id"])
    _rls("cm_cost_rates")


def downgrade() -> None:
    _drop_rls("cm_cost_rates")
    op.drop_index("ix_cm_cost_rates_company", table_name="cm_cost_rates")
    op.drop_table("cm_cost_rates")
    op.drop_index("ix_cm_plan_costs_scenario", table_name="cm_plan_costs")
    op.drop_table("cm_plan_costs")
    op.drop_index("ix_cm_plan_items_scenario", table_name="cm_plan_items")
    op.drop_table("cm_plan_items")
    op.execute("DROP INDEX IF EXISTS uq_cm_plan_baseline")
    op.drop_index("ix_cm_plan_scenarios_plan", table_name="cm_plan_scenarios")
    op.drop_table("cm_plan_scenarios")
    op.execute("DROP INDEX IF EXISTS uq_cm_plans_draft_quotation")
    _drop_rls("cm_plans")
    op.drop_index("ix_cm_plans_sales_order", table_name="cm_plans")
    op.drop_index("ix_cm_plans_quotation", table_name="cm_plans")
    op.drop_index("ix_cm_plans_company_docstatus", table_name="cm_plans")
    op.drop_index("ix_cm_plans_company_id", table_name="cm_plans")
    op.drop_table("cm_plans")
    op.drop_column("sales_orders", "shipping_rule_id")
    op.drop_column("quotations", "shipping_rule_id")
