"""Module 13 (Secretarial) Phase 0 — entities, persons, appointments, delegation.

Two things here are not the usual pattern and are deliberate:

* ``secretarial_engagements`` is bilateral — the granting client and the grantee firm
  both have to see it — so its RLS policy matches *either* side rather than the single
  ``company_id`` the other tables use.
* ``audit_logs`` gains ``acting_company_id`` so a delegated action records three facts
  (who acted, from which firm, on whose data) instead of two.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0094_secretarial_spine"
down_revision = "0093_tally_import"
branch_labels = None
depends_on = None

# Standard single-tenant isolation.
RLS_TABLES = (
    "secretarial_entities",
    "secretarial_settings",
    "secretarial_persons",
    "secretarial_appointments",
    "secretarial_practice_clients",
)

# Readable without a tenant context (system catalogue rows have company_id NULL).
OPEN_TABLES = ("secretarial_content_packs",)

ALL_TABLES = (*RLS_TABLES, *OPEN_TABLES, "secretarial_engagements")


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


def _entity_col(name: str = "entity_id", nullable: bool = False, ondelete: str = "CASCADE") -> sa.Column:
    return sa.Column(
        name,
        UUID(as_uuid=True),
        sa.ForeignKey("secretarial_entities.id", ondelete=ondelete),
        nullable=nullable,
    )


def upgrade() -> None:
    # --- The aggregate root -------------------------------------------------------
    op.create_table(
        "secretarial_entities",
        *_meta_columns(),
        _company_col(),
        sa.Column("entity_name", sa.String(200), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False, server_default=sa.text("'company'")),
        sa.Column("entity_class", sa.String(20), nullable=False, server_default=sa.text("'private'")),
        sa.Column("cin", sa.String(21)),
        sa.Column("llpin", sa.String(8)),
        sa.Column("pan", sa.String(10)),
        sa.Column("tan", sa.String(10)),
        sa.Column("gstin", sa.String(15)),
        sa.Column("is_listed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("incorporated_on", sa.Date()),
        sa.Column("fy_end_mmdd", sa.String(4), nullable=False, server_default=sa.text("'0331'")),
        sa.Column("registered_office", JSONB()),
        sa.Column("email", sa.String(140)),
        sa.Column("phone", sa.String(40)),
        sa.Column("website", sa.String(200)),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("notes", sa.Text()),
        sa.Column(
            "linked_company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="SET NULL"),
        ),
        sa.CheckConstraint("kind IN ('company', 'llp')", name="ck_secretarial_entity_kind"),
        sa.CheckConstraint(
            "status IN ('active', 'dormant', 'struck_off', 'amalgamated', 'closed')",
            name="ck_secretarial_entity_status",
        ),
        sa.UniqueConstraint("company_id", "cin", name="uq_secretarial_entity_cin"),
        sa.UniqueConstraint("company_id", "llpin", name="uq_secretarial_entity_llpin"),
    )
    op.create_index("ix_secretarial_entities_company_id", "secretarial_entities", ["company_id"])
    op.create_index("ix_secretarial_entities_linked", "secretarial_entities", ["linked_company_id"])
    op.create_index(
        "ix_secretarial_entities_company_status", "secretarial_entities", ["company_id", "status"]
    )

    # --- Per-tenant settings (which shell the module opens in) --------------------
    op.create_table(
        "secretarial_settings",
        *_meta_columns(),
        _company_col(),
        sa.Column("profile", sa.String(10), nullable=False, server_default=sa.text("'business'")),
        sa.Column("practice_name", sa.String(200)),
        sa.Column("practice_registration_no", sa.String(40)),
        sa.Column(
            "default_financial_access",
            sa.String(20),
            nullable=False,
            server_default=sa.text("'ledger_read'"),
        ),
        sa.Column("reminder_offsets", JSONB()),
        sa.Column("settings", JSONB()),
        sa.CheckConstraint("profile IN ('practice', 'business')", name="ck_secretarial_profile"),
        sa.UniqueConstraint("company_id", name="uq_secretarial_settings_company"),
    )
    op.create_index("ix_secretarial_settings_company_id", "secretarial_settings", ["company_id"])

    # --- Person graph -------------------------------------------------------------
    op.create_table(
        "secretarial_persons",
        *_meta_columns(),
        _company_col(),
        sa.Column("full_name", sa.String(200), nullable=False),
        sa.Column("din", sa.String(8)),
        sa.Column("pan", sa.String(10)),
        sa.Column("is_body_corporate", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("fathers_name", sa.String(200)),
        sa.Column("date_of_birth", sa.Date()),
        sa.Column("gender", sa.String(20)),
        sa.Column("nationality", sa.String(60)),
        sa.Column("occupation", sa.String(140)),
        sa.Column("qualification", sa.String(200)),
        sa.Column("email", sa.String(140)),
        sa.Column("mobile", sa.String(40)),
        sa.Column("address", JSONB()),
        sa.Column("kyc_status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("kyc_verified_on", sa.Date()),
        sa.Column("kyc", JSONB()),
        sa.Column("is_disqualified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("disqualification_note", sa.Text()),
        sa.Column("contact_id", UUID(as_uuid=True), sa.ForeignKey("contacts.id", ondelete="SET NULL")),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("company_id", "full_name", "pan", name="uq_secretarial_person_identity"),
    )
    op.create_index("ix_secretarial_persons_company_id", "secretarial_persons", ["company_id"])
    op.create_index(
        "ix_secretarial_persons_company_name", "secretarial_persons", ["company_id", "full_name"]
    )
    # DIN is unique per tenant, but most persons have none — a plain UNIQUE would be
    # satisfied by multiple NULLs anyway; the partial index documents the intent.
    op.create_index(
        "uq_secretarial_person_din",
        "secretarial_persons",
        ["company_id", "din"],
        unique=True,
        postgresql_where=sa.text("din IS NOT NULL"),
    )

    op.create_table(
        "secretarial_appointments",
        *_meta_columns(),
        _company_col(),
        _entity_col(),
        sa.Column(
            "person_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_persons.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("role_type", sa.String(30), nullable=False),
        sa.Column("designation", sa.String(140)),
        sa.Column("appointed_on", sa.Date(), nullable=False),
        sa.Column("ceased_on", sa.Date()),
        sa.Column("cessation_reason", sa.String(200)),
        sa.Column("appointment_mode", sa.String(30)),
        sa.Column("is_signing", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("is_chairperson", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "role_type IN ('director', 'designated_partner', 'partner', 'kmp', 'auditor', 'secretary')",
            name="ck_secretarial_appointment_role",
        ),
        sa.CheckConstraint(
            "ceased_on IS NULL OR ceased_on >= appointed_on",
            name="ck_secretarial_appointment_dates",
        ),
    )
    op.create_index("ix_secretarial_appointments_company_id", "secretarial_appointments", ["company_id"])
    op.create_index(
        "ix_secretarial_appointments_entity", "secretarial_appointments", ["entity_id", "role_type"]
    )
    op.create_index("ix_secretarial_appointments_person", "secretarial_appointments", ["person_id"])
    op.create_index(
        "ix_secretarial_appointments_active",
        "secretarial_appointments",
        ["entity_id"],
        postgresql_where=sa.text("ceased_on IS NULL"),
    )

    # --- Delegation ---------------------------------------------------------------
    op.create_table(
        "secretarial_engagements",
        *_meta_columns(),
        sa.Column(
            "client_company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "firm_company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _entity_col(),
        # Identity snapshot so the grantee firm can render its roster without
        # reading another tenant's rows (RLS would hide them).
        sa.Column("entity_name", sa.String(200), nullable=False),
        sa.Column("entity_kind", sa.String(10), nullable=False, server_default=sa.text("'company'")),
        sa.Column("entity_registration_no", sa.String(21)),
        sa.Column("client_name", sa.String(200)),
        sa.Column("firm_name", sa.String(200)),
        sa.Column("secretarial_access", sa.String(10), nullable=False, server_default=sa.text("'write'")),
        sa.Column(
            "financial_access", sa.String(20), nullable=False, server_default=sa.text("'ledger_read'")
        ),
        sa.Column("include_banking", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("starts_on", sa.Date()),
        sa.Column("ends_on", sa.Date()),
        sa.Column("accepted_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("ended_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("ended_reason", sa.Text()),
        sa.Column("granted_user_ids", JSONB()),
        sa.Column("projected_roles", JSONB()),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "financial_access IN ('none', 'derived_only', 'reports_read', 'ledger_read')",
            name="ck_secretarial_engagement_financial",
        ),
        sa.CheckConstraint(
            "secretarial_access IN ('read', 'write')", name="ck_secretarial_engagement_access"
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'active', 'suspended', 'ended')",
            name="ck_secretarial_engagement_status",
        ),
        # An ended engagement must say why — a revocation is never silent.
        sa.CheckConstraint(
            "status <> 'ended' OR ended_reason IS NOT NULL",
            name="ck_secretarial_engagement_end_reason",
        ),
        sa.CheckConstraint(
            "client_company_id <> firm_company_id", name="ck_secretarial_engagement_distinct"
        ),
    )
    op.create_index(
        "uq_secretarial_engagement_active",
        "secretarial_engagements",
        ["entity_id", "firm_company_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'active', 'suspended')"),
    )
    op.create_index(
        "ix_secretarial_engagements_firm", "secretarial_engagements", ["firm_company_id", "status"]
    )
    op.create_index(
        "ix_secretarial_engagements_client", "secretarial_engagements", ["client_company_id", "status"]
    )

    op.create_table(
        "secretarial_practice_clients",
        *_meta_columns(),
        _company_col(),
        sa.Column("entity_id", UUID(as_uuid=True), nullable=False),
        sa.Column(
            "owner_company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "engagement_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_engagements.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "relationship_type", sa.String(20), nullable=False, server_default=sa.text("'managed'")
        ),
        sa.Column("entity_name", sa.String(200), nullable=False),
        sa.Column("entity_kind", sa.String(10), nullable=False, server_default=sa.text("'company'")),
        sa.Column("registration_no", sa.String(21)),
        sa.Column("onboarding_state", sa.String(20), nullable=False, server_default=sa.text("'active'")),
        sa.Column("billable", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("active_from", sa.Date()),
        sa.Column("active_to", sa.Date()),
        sa.Column(
            "assigned_to_user_id", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")
        ),
        sa.Column("next_due_on", sa.Date()),
        sa.Column("overdue_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("open_item_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.Column("last_activity_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("refreshed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("metrics", JSONB()),
        sa.CheckConstraint(
            "relationship_type IN ('own', 'managed', 'delegated')",
            name="ck_practice_client_relationship",
        ),
        sa.CheckConstraint(
            "onboarding_state IN ('prospect', 'onboarding', 'active', 'dormant', 'exited')",
            name="ck_practice_client_state",
        ),
    )
    op.create_index(
        "ix_secretarial_practice_clients_company_id", "secretarial_practice_clients", ["company_id"]
    )
    op.create_index(
        "uq_practice_client_entity",
        "secretarial_practice_clients",
        ["company_id", "entity_id"],
        unique=True,
    )
    op.create_index(
        "ix_practice_clients_state", "secretarial_practice_clients", ["company_id", "onboarding_state"]
    )
    op.create_index(
        "ix_practice_clients_due", "secretarial_practice_clients", ["company_id", "next_due_on"]
    )

    # --- Legal-content layer ------------------------------------------------------
    op.create_table(
        "secretarial_content_packs",
        *_meta_columns(),
        _company_col(nullable=True),
        sa.Column("code", sa.String(80), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("event_type", sa.String(60), nullable=False),
        sa.Column("applies_to_kinds", JSONB()),
        sa.Column("fragments", JSONB()),
        sa.Column("variables", JSONB()),
        sa.Column("compliance_meta", JSONB()),
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
            sa.ForeignKey("secretarial_content_packs.id", ondelete="SET NULL"),
        ),
        sa.Column("superseded_on", sa.Date()),
        sa.Column("is_system", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "review_status IN ('draft', 'reviewed', 'approved', 'published', 'retired')",
            name="ck_secretarial_pack_review",
        ),
        # Publishing without a named reviewer is the one thing this table exists to
        # prevent (plan §2.12).
        sa.CheckConstraint(
            "review_status <> 'published' OR (reviewer_name IS NOT NULL AND reviewed_on IS NOT NULL)",
            name="ck_secretarial_pack_reviewer",
        ),
        sa.UniqueConstraint("code", "version", "company_id", name="uq_secretarial_pack_version"),
    )
    op.create_index(
        "ix_secretarial_packs_lookup", "secretarial_content_packs", ["code", "review_status"]
    )
    op.create_index("ix_secretarial_packs_event", "secretarial_content_packs", ["event_type"])

    # --- Delegated-action provenance on the audit trail ---------------------------
    op.add_column("audit_logs", sa.Column("acting_company_id", UUID(as_uuid=True)))
    op.create_index("ix_audit_logs_acting_company", "audit_logs", ["acting_company_id"])

    # --- RLS ----------------------------------------------------------------------
    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY company_isolation ON {table} "
            f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
        )

    # Bilateral: an engagement is visible to the client who granted it and the firm
    # that holds it. Neither side can see engagements it is not a party to.
    op.execute("ALTER TABLE secretarial_engagements ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY engagement_parties ON secretarial_engagements "
        "USING ("
        "  client_company_id = NULLIF(current_setting('app.company_id', true), '')::uuid"
        "  OR firm_company_id = NULLIF(current_setting('app.company_id', true), '')::uuid"
        ")"
    )

    # System catalogue rows (company_id NULL) are readable by every tenant; tenant
    # rows only by their owner.
    op.execute("ALTER TABLE secretarial_content_packs ENABLE ROW LEVEL SECURITY")
    op.execute(
        "CREATE POLICY content_pack_visibility ON secretarial_content_packs "
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
    op.execute("DROP POLICY IF EXISTS engagement_parties ON secretarial_engagements")
    op.execute("DROP POLICY IF EXISTS content_pack_visibility ON secretarial_content_packs")

    op.drop_index("ix_audit_logs_acting_company", table_name="audit_logs")
    op.drop_column("audit_logs", "acting_company_id")

    op.drop_table("secretarial_content_packs")
    op.drop_table("secretarial_practice_clients")
    op.drop_table("secretarial_engagements")
    op.drop_table("secretarial_appointments")
    op.drop_table("secretarial_persons")
    op.drop_table("secretarial_settings")
    op.drop_table("secretarial_entities")
