"""Module 13 Phases 2-4 — documents, governance, portal tokens, facts, filings.

Three phases in one migration because their tables interlock: a CTC references a
meeting *and* a document, a filing references a meeting *and* a compliance item, and
splitting them would mean circular FKs across revisions.

DB-level guards worth noting — each mirrors a rule the service layer also enforces,
because an evidence table that can be talked into an impossible state is not evidence:
a signed minute must carry its book numbers, a superseded CTC must say why, and a
circular that was blocked must record what blocked it.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0096_secretarial_governance"
down_revision = "0095_secretarial_registers"
branch_labels = None
depends_on = None

RLS_TABLES = (
    "secretarial_documents",
    "secretarial_meetings",
    "secretarial_agenda_items",
    "secretarial_attendance",
    "secretarial_minutes_book_seq",
    "secretarial_circulars",
    "secretarial_consent_responses",
    "secretarial_circulations",
    "secretarial_circulation_recipients",
    "secretarial_portal_tokens",
    "secretarial_portal_events",
    "secretarial_ctcs",
    "secretarial_financial_facts",
    "secretarial_filings",
    "secretarial_status_history",
)


def _meta() -> list[sa.Column]:
    return [
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("creation", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("modified", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("docstatus", sa.SmallInteger(), nullable=False, server_default=sa.text("0")),
        sa.Column("owner", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("modified_by", UUID(as_uuid=True), sa.ForeignKey("users.id"), nullable=True),
    ]


def _company() -> sa.Column:
    return sa.Column(
        "company_id", UUID(as_uuid=True), sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False
    )


def _entity(nullable: bool = False) -> sa.Column:
    return sa.Column(
        "entity_id",
        UUID(as_uuid=True),
        sa.ForeignKey("secretarial_entities.id", ondelete="CASCADE"),
        nullable=nullable,
    )


def _person(name: str = "person_id", nullable: bool = True, ondelete: str = "SET NULL") -> sa.Column:
    return sa.Column(
        name, UUID(as_uuid=True), sa.ForeignKey("secretarial_persons.id", ondelete=ondelete), nullable=nullable
    )


def upgrade() -> None:
    # --- Phase 2: documents --------------------------------------------------------
    op.create_table(
        "secretarial_documents",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("document_type", sa.String(60), nullable=False),
        sa.Column("fragment", sa.String(40), nullable=False),
        sa.Column("pack_code", sa.String(80)),
        sa.Column("pack_version", sa.Integer()),
        sa.Column(
            "pack_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_content_packs.id", ondelete="SET NULL"),
        ),
        sa.Column("generation_inputs", JSONB()),
        sa.Column("resolved_blocks", JSONB()),
        sa.Column("compliance_meta", JSONB()),
        sa.Column("source_doctype", sa.String(100)),
        sa.Column("source_id", UUID(as_uuid=True)),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("document_date", sa.Date()),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("root_id", UUID(as_uuid=True)),
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column(
            "supersedes_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_documents.id", ondelete="SET NULL"),
        ),
        sa.Column("is_current", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("change_summary", sa.Text()),
        sa.Column(
            "signed_file_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_files.id", ondelete="SET NULL"),
        ),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "status IN ('draft', 'final', 'issued', 'superseded')", name="ck_secretarial_document_status"
        ),
        sa.UniqueConstraint("root_id", "version", name="uq_secretarial_document_version"),
    )
    op.create_index("ix_secretarial_documents_company_id", "secretarial_documents", ["company_id"])
    op.create_index("ix_secretarial_documents_entity", "secretarial_documents", ["entity_id", "document_type"])
    op.create_index("ix_secretarial_documents_source", "secretarial_documents", ["source_doctype", "source_id"])
    op.create_index("ix_secretarial_documents_chain", "secretarial_documents", ["root_id", "version"])

    # --- Phase 3: meetings ---------------------------------------------------------
    op.create_table(
        "secretarial_meetings",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("meeting_type", sa.String(20), nullable=False),
        sa.Column(
            "committee_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_committees.id", ondelete="SET NULL"),
        ),
        sa.Column("serial_no", sa.Integer()),
        sa.Column("title", sa.String(250)),
        sa.Column("fy", sa.String(9), nullable=False),
        sa.Column("scheduled_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("held_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("venue", sa.Text()),
        sa.Column("mode", sa.String(20), nullable=False, server_default=sa.text("'physical'")),
        _person("chairperson_id"),
        sa.Column("quorum_required", sa.Integer()),
        sa.Column("quorum_met", sa.Boolean()),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("notice_days_required", sa.Integer(), nullable=False, server_default=sa.text("7")),
        sa.Column("notice_due_on", sa.Date()),
        sa.Column("notice_sent_on", sa.Date()),
        sa.Column("shorter_notice", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("minutes_draft_due_on", sa.Date()),
        sa.Column("minutes_signed_due_on", sa.Date()),
        sa.Column("minutes_draft_on", sa.Date()),
        sa.Column("minutes_signed_on", sa.Date()),
        sa.Column("minutes_entry_no", sa.Integer()),
        sa.Column("minutes_page_from", sa.Integer()),
        sa.Column("minutes_page_to", sa.Integer()),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "meeting_type IN ('board', 'agm', 'egm', 'committee', 'partners')",
            name="ck_secretarial_meeting_type",
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'scheduled', 'circulated', 'held', 'minutes_draft', "
            "'minutes_signed', 'closed', 'cancelled')",
            name="ck_secretarial_meeting_status",
        ),
        # Signed minutes without their book numbers would defeat the whole point of
        # consecutive numbering.
        sa.CheckConstraint(
            "status NOT IN ('minutes_signed', 'closed') OR minutes_entry_no IS NOT NULL",
            name="ck_secretarial_meeting_minutes_numbered",
        ),
        sa.UniqueConstraint("entity_id", "serial_no", name="uq_secretarial_meeting_serial"),
    )
    op.create_index("ix_secretarial_meetings_company_id", "secretarial_meetings", ["company_id"])
    op.create_index(
        "ix_secretarial_meetings_entity", "secretarial_meetings", ["entity_id", "meeting_type", "scheduled_at"]
    )
    op.create_index("ix_secretarial_meetings_status", "secretarial_meetings", ["company_id", "status"])

    op.create_table(
        "secretarial_circulars",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("resolution_text", sa.Text(), nullable=False),
        sa.Column("reference_no", sa.String(80)),
        sa.Column("fy", sa.String(9)),
        sa.Column("consent_rule", sa.String(20), nullable=False, server_default=sa.text("'majority'")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("circulated_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("decided_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("eligibility_checked_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("eligibility_result", JSONB()),
        sa.Column(
            "ratified_meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_meetings.id", ondelete="SET NULL"),
        ),
        sa.Column("ratified_on", sa.Date()),
        sa.Column("cancelled_reason", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "consent_rule IN ('majority', 'unanimous', 'two_thirds')", name="ck_secretarial_circular_rule"
        ),
        sa.CheckConstraint(
            "status IN ('draft', 'circulating', 'passed', 'failed', 'expired', 'ratified', 'cancelled')",
            name="ck_secretarial_circular_status",
        ),
        # A circular can only leave draft once Rule 5 has actually been evaluated.
        sa.CheckConstraint(
            "status = 'draft' OR eligibility_checked_at IS NOT NULL",
            name="ck_secretarial_circular_eligibility_checked",
        ),
        sa.UniqueConstraint("entity_id", "reference_no", name="uq_secretarial_circular_ref"),
    )
    op.create_index("ix_secretarial_circulars_company_id", "secretarial_circulars", ["company_id"])
    op.create_index("ix_secretarial_circulars_entity", "secretarial_circulars", ["entity_id", "status"])
    op.create_index("ix_secretarial_circulars_expiry", "secretarial_circulars", ["status", "expires_at"])

    op.create_table(
        "secretarial_agenda_items",
        *_meta(),
        _company(),
        sa.Column(
            "meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(400), nullable=False),
        sa.Column("body", sa.Text()),
        sa.Column("resolution_text", sa.Text()),
        sa.Column("resolution_kind", sa.String(20)),
        sa.Column("source", sa.String(20), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("pack_code", sa.String(80)),
        sa.Column(
            "circular_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_circulars.id", ondelete="SET NULL"),
        ),
        sa.Column("is_passed", sa.Boolean()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("meeting_id", "seq", name="uq_secretarial_agenda_seq"),
    )
    op.create_index("ix_secretarial_agenda_items_company_id", "secretarial_agenda_items", ["company_id"])
    op.create_index("ix_secretarial_agenda_meeting", "secretarial_agenda_items", ["meeting_id", "seq"])

    op.create_table(
        "secretarial_attendance",
        *_meta(),
        _company(),
        sa.Column(
            "meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_meetings.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _person(nullable=False, ondelete="RESTRICT"),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'present'")),
        sa.Column("joined_via", sa.String(40)),
        sa.Column("is_chairperson", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("remarks", sa.Text()),
        sa.CheckConstraint(
            "status IN ('present', 'absent', 'leave_of_absence', 'video')",
            name="ck_secretarial_attendance_status",
        ),
        sa.UniqueConstraint("meeting_id", "person_id", name="uq_secretarial_attendance"),
    )
    op.create_index("ix_secretarial_attendance_company_id", "secretarial_attendance", ["company_id"])

    op.create_table(
        "secretarial_minutes_book_seq",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("scope", sa.String(80), nullable=False),
        sa.Column("next_entry_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("next_page_no", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("entity_id", "scope", name="uq_secretarial_minutes_scope"),
    )
    op.create_index("ix_secretarial_minutes_book_seq_company_id", "secretarial_minutes_book_seq", ["company_id"])

    op.create_table(
        "secretarial_consent_responses",
        *_meta(),
        _company(),
        sa.Column(
            "circular_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_circulars.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _person(nullable=False, ondelete="RESTRICT"),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("is_interested", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("viewed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("responded_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("responded_ip", sa.String(64)),
        sa.Column("comments", sa.Text()),
        sa.CheckConstraint(
            "status IN ('pending', 'viewed', 'consented', 'declined', 'abstained')",
            name="ck_secretarial_consent_status",
        ),
        # A recorded decision always carries the moment it was made.
        sa.CheckConstraint(
            "status NOT IN ('consented', 'declined', 'abstained') OR responded_at IS NOT NULL",
            name="ck_secretarial_consent_timestamped",
        ),
        sa.UniqueConstraint("circular_id", "person_id", name="uq_secretarial_consent"),
    )
    op.create_index("ix_secretarial_consent_responses_company_id", "secretarial_consent_responses", ["company_id"])
    op.create_index("ix_secretarial_consent_circular", "secretarial_consent_responses", ["circular_id", "status"])

    op.create_table(
        "secretarial_circulations",
        *_meta(),
        _company(),
        sa.Column(
            "meeting_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_meetings.id", ondelete="CASCADE"),
        ),
        _entity(),
        sa.Column("subject", sa.String(300), nullable=False),
        sa.Column("message", sa.Text()),
        sa.Column("document_ids", JSONB()),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True)),
    )
    op.create_index("ix_secretarial_circulations_company_id", "secretarial_circulations", ["company_id"])
    op.create_index("ix_secretarial_circulations_meeting", "secretarial_circulations", ["meeting_id"])

    op.create_table(
        "secretarial_circulation_recipients",
        *_meta(),
        _company(),
        sa.Column(
            "circulation_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_circulations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        _person(nullable=False, ondelete="RESTRICT"),
        sa.Column("email", sa.String(140)),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'pending'")),
        sa.Column("sent_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("viewed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("acknowledged_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("resend_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "status IN ('pending', 'viewed', 'acknowledged')", name="ck_secretarial_circ_recipient_status"
        ),
        sa.UniqueConstraint("circulation_id", "person_id", name="uq_secretarial_circ_recipient"),
    )
    op.create_index(
        "ix_secretarial_circulation_recipients_company_id", "secretarial_circulation_recipients", ["company_id"]
    )

    # --- Portal tokens -------------------------------------------------------------
    op.create_table(
        "secretarial_portal_tokens",
        *_meta(),
        _company(),
        sa.Column("purpose", sa.String(30), nullable=False),
        sa.Column("token_hash", sa.String(64), nullable=False),
        _entity(),
        sa.Column(
            "person_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_persons.id", ondelete="CASCADE"),
        ),
        sa.Column("target_doctype", sa.String(60), nullable=False),
        sa.Column("target_id", UUID(as_uuid=True), nullable=False),
        sa.Column("expires_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("revoked_reason", sa.String(200)),
        sa.Column("last_used_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("use_count", sa.Integer(), nullable=False, server_default=sa.text("0")),
        sa.CheckConstraint(
            "purpose IN ('circulation_view', 'consent_respond', 'client_view')",
            name="ck_secretarial_token_purpose",
        ),
        sa.UniqueConstraint("token_hash", name="uq_secretarial_portal_token"),
    )
    op.create_index("ix_secretarial_portal_tokens_company_id", "secretarial_portal_tokens", ["company_id"])
    op.create_index(
        "ix_secretarial_portal_tokens_subject", "secretarial_portal_tokens", ["purpose", "person_id"]
    )

    op.create_table(
        "secretarial_portal_events",
        *_meta(),
        _company(),
        sa.Column(
            "token_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_portal_tokens.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("event", sa.String(40), nullable=False),
        sa.Column("ip_address", sa.String(64)),
        sa.Column("user_agent", sa.String(300)),
        sa.Column("payload", JSONB()),
    )
    op.create_index("ix_secretarial_portal_events_company_id", "secretarial_portal_events", ["company_id"])
    op.create_index("ix_secretarial_portal_events_token", "secretarial_portal_events", ["token_id", "creation"])

    # --- Certified true copies -----------------------------------------------------
    op.create_table(
        "secretarial_ctcs",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("issuance_no", sa.Integer(), nullable=False),
        sa.Column("passage_mode", sa.String(20), nullable=False),
        sa.Column(
            "meeting_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
        ),
        sa.Column(
            "circular_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
        ),
        sa.Column("resolution_text", sa.Text(), nullable=False),
        sa.Column("passed_on", sa.Date()),
        sa.Column("certified_on", sa.Date(), nullable=False),
        sa.Column("place", sa.String(140)),
        sa.Column("issued_to", sa.String(250)),
        sa.Column("purpose", sa.Text()),
        sa.Column("signatories", JSONB()),
        sa.Column(
            "document_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_documents.id", ondelete="SET NULL")
        ),
        sa.Column("issued_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column(
            "supersedes_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_ctcs.id", ondelete="SET NULL")
        ),
        sa.Column("superseded_reason", sa.Text()),
        sa.CheckConstraint(
            "passage_mode IN ('board', 'circular', 'agm', 'egm', 'committee', 'other')",
            name="ck_secretarial_ctc_mode",
        ),
        # Replacing a certified copy always says why. A silent reissue is worthless.
        sa.CheckConstraint(
            "supersedes_id IS NULL OR superseded_reason IS NOT NULL",
            name="ck_secretarial_ctc_supersede_reason",
        ),
        sa.UniqueConstraint("entity_id", "issuance_no", name="uq_secretarial_ctc_no"),
    )
    op.create_index("ix_secretarial_ctcs_company_id", "secretarial_ctcs", ["company_id"])
    op.create_index("ix_secretarial_ctcs_entity", "secretarial_ctcs", ["entity_id", "issued_at"])

    # --- Phase 4: facts, filings, history -----------------------------------------
    op.create_table(
        "secretarial_financial_facts",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("fy", sa.String(9), nullable=False),
        sa.Column("turnover", sa.Numeric(21, 2)),
        sa.Column("net_profit", sa.Numeric(21, 2)),
        sa.Column("net_worth", sa.Numeric(21, 2)),
        sa.Column("paid_up_capital", sa.Numeric(21, 2)),
        sa.Column("free_reserves", sa.Numeric(21, 2)),
        sa.Column("securities_premium", sa.Numeric(21, 2)),
        sa.Column("borrowings", sa.Numeric(21, 2)),
        sa.Column("deposits", sa.Numeric(21, 2)),
        sa.Column("source", sa.String(10), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("computed_at", sa.TIMESTAMP(timezone=True)),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint("source IN ('auto', 'manual')", name="ck_secretarial_facts_source"),
        sa.UniqueConstraint("entity_id", "fy", name="uq_secretarial_facts_period"),
    )
    op.create_index("ix_secretarial_financial_facts_company_id", "secretarial_financial_facts", ["company_id"])

    op.create_table(
        "secretarial_filings",
        *_meta(),
        _company(),
        _entity(),
        sa.Column(
            "compliance_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_compliance_items.id", ondelete="SET NULL"),
        ),
        sa.Column("form_code", sa.String(40), nullable=False),
        sa.Column("fy", sa.String(9)),
        sa.Column("srn", sa.String(40)),
        sa.Column("filed_on", sa.Date()),
        sa.Column("filing_fee", sa.Numeric(21, 2)),
        sa.Column("additional_fee", sa.Numeric(21, 2)),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'prepared'")),
        sa.Column(
            "meeting_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
        ),
        sa.Column(
            "circular_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
        ),
        sa.Column(
            "document_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_documents.id", ondelete="SET NULL")
        ),
        sa.Column(
            "challan_file_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_files.id", ondelete="SET NULL")
        ),
        sa.Column("filed_by", sa.String(200)),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(
            "status IN ('prepared', 'filed', 'approved', 'resubmission', 'rejected')",
            name="ck_secretarial_filing_status",
        ),
        # "Filed" means there is an SRN and a date to point at.
        sa.CheckConstraint(
            "status = 'prepared' OR (srn IS NOT NULL AND filed_on IS NOT NULL)",
            name="ck_secretarial_filing_evidence",
        ),
    )
    op.create_index("ix_secretarial_filings_company_id", "secretarial_filings", ["company_id"])
    op.create_index("ix_secretarial_filings_entity", "secretarial_filings", ["entity_id", "filed_on"])
    op.create_index("ix_secretarial_filings_item", "secretarial_filings", ["compliance_item_id"])

    op.create_table(
        "secretarial_status_history",
        *_meta(),
        _company(),
        sa.Column(
            "item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_compliance_items.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("from_status", sa.String(20)),
        sa.Column("to_status", sa.String(20), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.Column("changed_by", UUID(as_uuid=True), sa.ForeignKey("users.id", ondelete="SET NULL")),
    )
    op.create_index("ix_secretarial_status_history_company_id", "secretarial_status_history", ["company_id"])
    op.create_index("ix_secretarial_status_history_item", "secretarial_status_history", ["item_id", "creation"])

    for table in RLS_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(
            f"CREATE POLICY company_isolation ON {table} "
            f"USING (company_id = NULLIF(current_setting('app.company_id', true), '')::uuid)"
        )
        op.execute(f"GRANT SELECT, INSERT, UPDATE, DELETE ON {table} TO erp_app")


def downgrade() -> None:
    for table in RLS_TABLES:
        op.execute(f"DROP POLICY IF EXISTS company_isolation ON {table}")
    for table in (
        "secretarial_status_history",
        "secretarial_filings",
        "secretarial_financial_facts",
        "secretarial_ctcs",
        "secretarial_portal_events",
        "secretarial_portal_tokens",
        "secretarial_circulation_recipients",
        "secretarial_circulations",
        "secretarial_consent_responses",
        "secretarial_minutes_book_seq",
        "secretarial_attendance",
        "secretarial_agenda_items",
        "secretarial_circulars",
        "secretarial_meetings",
        "secretarial_documents",
    ):
        op.drop_table(table)
