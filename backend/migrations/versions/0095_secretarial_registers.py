"""Module 13 (Secretarial) Phase 1 — statutory registers + the compliance calendar.

Registers are effective-dated everywhere: rows close with a date, they do not get
deleted, because a register has to answer "who held this office in FY 2024-25".
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0095_secretarial_registers"
down_revision = "0094_secretarial_spine"
branch_labels = None
depends_on = None

RLS_TABLES = (
    "secretarial_members",
    "secretarial_committees",
    "secretarial_committee_members",
    "secretarial_group_links",
    "secretarial_related_parties",
    "secretarial_beneficial_owners",
    "secretarial_auditors",
    "secretarial_charges",
    "secretarial_dscs",
    "secretarial_files",
    "secretarial_compliance_items",
    "secretarial_compliance_reminders",
)

OPEN_TABLES = ("secretarial_compliance_rules",)

ALL_TABLES = (*RLS_TABLES, *OPEN_TABLES)


def _meta_columns() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    ]


def _company_col(nullable: bool = False) -> sa.Column:
    return sa.Column(
        "company_id",
        UUID(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="CASCADE"),
        nullable=nullable,
    )


def _entity_col(nullable: bool = False) -> sa.Column:
    return sa.Column(
        "entity_id",
        UUID(as_uuid=True),
        sa.ForeignKey("secretarial_entities.id", ondelete="CASCADE"),
        nullable=nullable,
    )


def _person_col(name: str = "person_id", nullable: bool = True, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(
        name,
        UUID(as_uuid=True),
        sa.ForeignKey("secretarial_persons.id", ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    # --- Register of members ------------------------------------------------------
    op.create_table(
        "secretarial_members",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column("member_name", sa.String(200), nullable=False),
        sa.Column("folio_no", sa.String(40), nullable=False),
        sa.Column("member_type", sa.String(20), nullable=False, server_default=sa.text("'individual'")),
        _person_col(),
        sa.Column(
            "shareholder_id", UUID(as_uuid=True), sa.ForeignKey("shareholders.id", ondelete="SET NULL")
        ),
        sa.Column("pan", sa.String(10)),
        sa.Column("email", sa.String(140)),
        sa.Column("nationality", sa.String(60)),
        sa.Column("address", JSONB()),
        sa.Column("share_class", sa.String(60)),
        sa.Column("shares_held", sa.Numeric(21, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("nominal_value", sa.Numeric(21, 4)),
        sa.Column("holding_as_on", sa.Date()),
        sa.Column("joined_on", sa.Date()),
        sa.Column("ceased_on", sa.Date()),
        sa.Column("is_beneficial_owner", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("entity_id", "folio_no", name="uq_secretarial_member_folio"),
    )
    op.create_index("ix_secretarial_members_company_id", "secretarial_members", ["company_id"])
    op.create_index("ix_secretarial_members_entity", "secretarial_members", ["entity_id", "ceased_on"])
    op.create_index("ix_secretarial_members_name", "secretarial_members", ["entity_id", "member_name"])

    # --- Committees ---------------------------------------------------------------
    op.create_table(
        "secretarial_committees",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column("committee_name", sa.String(140), nullable=False),
        sa.Column("committee_type", sa.String(60)),
        sa.Column("constituted_on", sa.Date()),
        sa.Column("dissolved_on", sa.Date()),
        sa.Column("terms_of_reference", sa.Text()),
        sa.Column("quorum", sa.Integer()),
        sa.UniqueConstraint("entity_id", "committee_name", name="uq_secretarial_committee_name"),
    )
    op.create_index("ix_secretarial_committees_company_id", "secretarial_committees", ["company_id"])

    op.create_table(
        "secretarial_committee_members",
        *_meta_columns(),
        _company_col(),
        sa.Column(
            "committee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_committees.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _person_col(nullable=False, ondelete="RESTRICT"),
        sa.Column("is_chair", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
    )
    op.create_index(
        "ix_secretarial_committee_members_company_id", "secretarial_committee_members", ["company_id"]
    )
    op.create_index(
        "ix_secretarial_committee_members", "secretarial_committee_members", ["committee_id", "valid_to"]
    )

    # --- Group structure ----------------------------------------------------------
    op.create_table(
        "secretarial_group_links",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column(
            "related_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_entities.id", ondelete="SET NULL"),
        ),
        sa.Column("related_entity_name", sa.String(200), nullable=False),
        sa.Column("related_cin", sa.String(21)),
        sa.Column("relation", sa.String(30), nullable=False),
        sa.Column("shareholding_pct", sa.Numeric(9, 4)),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "relation IN ('holding', 'subsidiary', 'associate', 'joint_venture', 'fellow_subsidiary')",
            name="ck_secretarial_group_relation",
        ),
    )
    op.create_index("ix_secretarial_group_links_company_id", "secretarial_group_links", ["company_id"])
    op.create_index(
        "ix_secretarial_group_links_entity", "secretarial_group_links", ["entity_id", "valid_to"]
    )

    # --- Related parties ----------------------------------------------------------
    op.create_table(
        "secretarial_related_parties",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column("party_name", sa.String(200), nullable=False),
        sa.Column("basis", sa.String(20), nullable=False),
        sa.Column("relationship_note", sa.String(300)),
        _person_col(),
        sa.Column(
            "related_entity_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_entities.id", ondelete="SET NULL"),
        ),
        sa.Column("is_manual", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("valid_from", sa.Date()),
        sa.Column("valid_to", sa.Date()),
        sa.CheckConstraint(
            "basis IN ('director', 'kmp', 'member', 'group', 'relative', 'manual')",
            name="ck_secretarial_rp_basis",
        ),
        sa.UniqueConstraint("entity_id", "party_name", "basis", name="uq_secretarial_related_party"),
    )
    op.create_index(
        "ix_secretarial_related_parties_company_id", "secretarial_related_parties", ["company_id"]
    )
    op.create_index(
        "ix_secretarial_related_parties_entity", "secretarial_related_parties", ["entity_id", "basis"]
    )

    # --- Beneficial owners --------------------------------------------------------
    op.create_table(
        "secretarial_beneficial_owners",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        _person_col(),
        sa.Column("person_name", sa.String(200), nullable=False),
        sa.Column("classification", sa.String(10), nullable=False),
        sa.Column("holding_pct", sa.Numeric(9, 4)),
        sa.Column("nature_of_interest", sa.String(300)),
        sa.Column("declaration_ref", sa.String(80)),
        sa.Column("declared_on", sa.Date()),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_to", sa.Date()),
        sa.CheckConstraint(
            "classification IN ('bo', 'sbo', 'ubo')", name="ck_secretarial_bo_classification"
        ),
    )
    op.create_index(
        "ix_secretarial_beneficial_owners_company_id", "secretarial_beneficial_owners", ["company_id"]
    )
    op.create_index(
        "ix_secretarial_bo_entity", "secretarial_beneficial_owners", ["entity_id", "valid_from", "valid_to"]
    )

    # --- Auditors -----------------------------------------------------------------
    op.create_table(
        "secretarial_auditors",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column("firm_name", sa.String(200), nullable=False),
        sa.Column("registration_no", sa.String(40)),
        sa.Column("auditor_type", sa.String(30), nullable=False, server_default=sa.text("'statutory'")),
        sa.Column("appointed_on", sa.Date()),
        sa.Column("appointment_mode", sa.String(40)),
        sa.Column("term_from_fy", sa.String(9)),
        sa.Column("term_to_fy", sa.String(9)),
        sa.Column("ceased_on", sa.Date()),
        sa.Column("cessation_reason", sa.String(200)),
        sa.Column("adt1_filed_on", sa.Date()),
        sa.Column("email", sa.String(140)),
        sa.Column("notes", sa.Text()),
    )
    op.create_index("ix_secretarial_auditors_company_id", "secretarial_auditors", ["company_id"])
    op.create_index("ix_secretarial_auditors_entity", "secretarial_auditors", ["entity_id", "ceased_on"])

    # --- Charges ------------------------------------------------------------------
    op.create_table(
        "secretarial_charges",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column("charge_id_no", sa.String(40)),
        sa.Column("holder_name", sa.String(200), nullable=False),
        sa.Column("charge_type", sa.String(60)),
        sa.Column("amount_secured", sa.Numeric(21, 2)),
        sa.Column("property_description", sa.Text()),
        sa.Column("created_on", sa.Date()),
        sa.Column("modified_on", sa.Date()),
        sa.Column("satisfied_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'open'")),
        sa.Column("srn", sa.String(40)),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "status IN ('open', 'satisfied', 'modified')", name="ck_secretarial_charge_status"
        ),
    )
    op.create_index("ix_secretarial_charges_company_id", "secretarial_charges", ["company_id"])
    op.create_index("ix_secretarial_charges_entity", "secretarial_charges", ["entity_id", "status"])

    # --- DSC register -------------------------------------------------------------
    op.create_table(
        "secretarial_dscs",
        *_meta_columns(),
        _company_col(),
        _entity_col(nullable=True),
        _person_col(),
        sa.Column("holder_name", sa.String(200), nullable=False),
        sa.Column("serial_no", sa.String(80)),
        sa.Column("issuing_authority", sa.String(140)),
        sa.Column("issued_on", sa.Date()),
        sa.Column("expires_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("custodian", sa.String(200)),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "status IN ('active', 'expired', 'revoked')", name="ck_secretarial_dsc_status"
        ),
    )
    op.create_index("ix_secretarial_dscs_company_id", "secretarial_dscs", ["company_id"])
    op.create_index("ix_secretarial_dscs_expiry", "secretarial_dscs", ["company_id", "expires_on"])

    # --- Uploaded evidence --------------------------------------------------------
    op.create_table(
        "secretarial_files",
        *_meta_columns(),
        _company_col(),
        _entity_col(nullable=True),
        sa.Column("file_name", sa.String(255), nullable=False),
        sa.Column(
            "content_type",
            sa.String(120),
            nullable=False,
            server_default=sa.text("'application/octet-stream'"),
        ),
        sa.Column("file_size", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("sha256", sa.String(64)),
        sa.Column("storage_backend", sa.String(20), nullable=False, server_default=sa.text("'db'")),
        sa.Column("storage_ref", sa.String(500)),
        sa.Column("content", sa.LargeBinary()),
        sa.Column("reference_doctype", sa.String(100)),
        sa.Column("reference_id", UUID(as_uuid=True)),
        sa.Column("category", sa.String(60)),
        sa.Column("description", sa.Text()),
    )
    op.create_index("ix_secretarial_files_company_id", "secretarial_files", ["company_id"])
    op.create_index(
        "ix_secretarial_files_ref", "secretarial_files", ["reference_doctype", "reference_id"]
    )
    op.create_index("ix_secretarial_files_entity", "secretarial_files", ["entity_id"])

    # --- Compliance rule catalogue ------------------------------------------------
    op.create_table(
        "secretarial_compliance_rules",
        *_meta_columns(),
        _company_col(nullable=True),
        sa.Column("code", sa.String(60), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("act", sa.String(140)),
        sa.Column("section", sa.String(60)),
        sa.Column("form_code", sa.String(40)),
        sa.Column("authority", sa.String(40), nullable=False, server_default=sa.text("'MCA'")),
        sa.Column("basis", sa.String(20), nullable=False, server_default=sa.text("'fy'")),
        sa.Column("due_formula", JSONB()),
        sa.Column("applicability", JSONB()),
        sa.Column("reminder_offsets", JSONB()),
        sa.Column("penalty_note", sa.Text()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("source_ref", sa.String(300)),
        sa.Column("source_excerpt", sa.Text()),
        sa.Column("review_status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("reviewed_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("reviewed_on", sa.Date()),
        sa.Column("reviewer_name", sa.String(200)),
        sa.Column("reviewer_credential", sa.String(140)),
        sa.Column("published_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("effective_from", sa.Date()),
        sa.Column(
            "superseded_by_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_compliance_rules.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint("basis IN ('fy', 'event', 'recurring')", name="ck_secretarial_rule_basis"),
        sa.CheckConstraint(
            "review_status IN ('draft', 'reviewed', 'approved', 'published', 'retired')",
            name="ck_secretarial_rule_review",
        ),
        sa.CheckConstraint(
            "review_status <> 'published' OR (reviewer_name IS NOT NULL AND reviewed_on IS NOT NULL)",
            name="ck_secretarial_rule_reviewer",
        ),
        sa.UniqueConstraint("code", "version", "company_id", name="uq_secretarial_rule_version"),
    )
    op.create_index(
        "ix_secretarial_rules_lookup", "secretarial_compliance_rules", ["code", "review_status"]
    )

    # --- Compliance items ---------------------------------------------------------
    op.create_table(
        "secretarial_compliance_items",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column(
            "rule_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_compliance_rules.id", ondelete="SET NULL"),
        ),
        sa.Column("rule_code", sa.String(60), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("form_code", sa.String(40)),
        sa.Column("act_section", sa.String(200)),
        sa.Column("fy", sa.String(9), nullable=False),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("due_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'upcoming'")),
        sa.Column(
            "assigned_to_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("completed_on", sa.Date()),
        sa.Column("srn", sa.String(40)),
        sa.Column("filed_on", sa.Date()),
        sa.Column(
            "evidence_file_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_files.id", ondelete="SET NULL"),
        ),
        sa.Column("waived_reason", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "status IN ('not_applicable', 'upcoming', 'due', 'in_progress', 'pending_review', "
            "'filed', 'completed', 'overdue', 'waived')",
            name="ck_secretarial_item_status",
        ),
        # Waiving an obligation always carries a reason (plan §5).
        sa.CheckConstraint(
            "status <> 'waived' OR waived_reason IS NOT NULL", name="ck_secretarial_item_waive_reason"
        ),
        sa.UniqueConstraint("entity_id", "rule_code", "fy", name="uq_secretarial_item_period"),
    )
    op.create_index(
        "ix_secretarial_compliance_items_company_id", "secretarial_compliance_items", ["company_id"]
    )
    op.create_index(
        "ix_secretarial_items_due", "secretarial_compliance_items", ["company_id", "due_on", "status"]
    )
    op.create_index("ix_secretarial_items_entity", "secretarial_compliance_items", ["entity_id", "fy"])
    op.create_index(
        "ix_secretarial_items_assignee",
        "secretarial_compliance_items",
        ["assigned_to_user_id", "status"],
    )

    # --- Reminder dispatch log (idempotency for the daily job) --------------------
    op.create_table(
        "secretarial_compliance_reminders",
        *_meta_columns(),
        _company_col(),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_compliance_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("offset_days", sa.Integer(), nullable=False),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("sent_to", JSONB()),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'sent'")),
        sa.Column("error_message", sa.Text()),
        sa.UniqueConstraint("item_id", "offset_days", name="uq_secretarial_reminder_offset"),
    )
    op.create_index(
        "ix_secretarial_compliance_reminders_company_id",
        "secretarial_compliance_reminders",
        ["company_id"],
    )
    op.create_index("ix_secretarial_reminders_item", "secretarial_compliance_reminders", ["item_id"])

    # --- RLS ----------------------------------------------------------------------
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY company_isolation ON {table} "
            f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
        )

    op.execute("ALTER TABLE secretarial_compliance_rules ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY rule_visibility ON secretarial_compliance_rules "
        "USING ("
        "  company_id IS NULL"
        "  OR company_id = NULLIF(current_setting('app.company_id', true), '')::uuid"
        ")"
    )

    for table in ALL_TABLES:
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO erp_app")


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
    op.execute("DROP POLICY IF EXISTS rule_visibility ON secretarial_compliance_rules")

    op.drop_table("secretarial_compliance_reminders")
    op.drop_table("secretarial_compliance_items")
    op.drop_table("secretarial_compliance_rules")
    op.drop_table("secretarial_files")
    op.drop_table("secretarial_dscs")
    op.drop_table("secretarial_charges")
    op.drop_table("secretarial_auditors")
    op.drop_table("secretarial_beneficial_owners")
    op.drop_table("secretarial_related_parties")
    op.drop_table("secretarial_group_links")
    op.drop_table("secretarial_committee_members")
    op.drop_table("secretarial_committees")
    op.drop_table("secretarial_members")
