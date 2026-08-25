"""Module 13 Phase 5 — share transfers, certificates, capital events, s.186 register.

One migration for the whole phase because the tables interlock: a transfer detail names
the certificate it surrendered and the one it issued, a certificate names the capital
event that created it, and splitting them would mean circular FKs across revisions. The
two self-and-cross references are added with ``ALTER TABLE`` after both tables exist,
which is the same shape ``0096`` used for the CTC/document pair.

The database checks here are not belt-and-braces on top of service validation — they are
the half that survives a bad migration, a psql session or a future refactor:

* a reverted transfer must say why (a silent reversal is the entry an inspection asks about)
* a cancelled certificate must carry its date and reason
* every distinctive range must be non-empty and ordered
* a share movement must actually move shares
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0100_secretarial_capital"
down_revision = "0099_tally_import_heartbeat"
branch_labels = None
depends_on = None

RLS_TABLES = (
    "secretarial_share_certificates",
    "secretarial_share_transfer_details",
    "secretarial_distinctive_seq",
    "secretarial_capital_events",
    "secretarial_s186_limits",
    "secretarial_s186_entries",
)

TRANSFER_STATUSES = ("draft", "board_approved", "issued_posted", "reverted")
CERTIFICATE_ISSUE_TYPES = ("original", "duplicate", "renewed", "split", "consolidation")
CERTIFICATE_STATUSES = ("issued", "cancelled", "surrendered")
CAPITAL_EVENT_TYPES = (
    "right_issue",
    "private_placement",
    "preferential_allotment",
    "esop_grant",
    "bonus_issue",
    "buyback",
    "dividend",
)
CAPITAL_EVENT_STATUSES = ("draft", "approved", "allotted", "cancelled")
S186_ENTRY_TYPES = ("loan", "guarantee", "security", "investment")
S186_ENTRY_STATUSES = ("outstanding", "repaid", "invoked", "written_off")


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


def _entity() -> sa.Column:
    return sa.Column(
        "entity_id",
        UUID(as_uuid=True),
        sa.ForeignKey("secretarial_entities.id", ondelete="CASCADE"),
        nullable=False,
    )


def _meeting(name: str) -> sa.Column:
    return sa.Column(
        name, UUID(as_uuid=True), sa.ForeignKey("secretarial_meetings.id", ondelete="SET NULL")
    )


def _in(column: str, values: tuple[str, ...]) -> str:
    joined = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({joined})"


def upgrade() -> None:
    # --- Capital events ------------------------------------------------------------
    # First, because certificates reference them.
    op.create_table(
        "secretarial_capital_events",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("event_type", sa.String(30), nullable=False),
        sa.Column("title", sa.String(250), nullable=False),
        sa.Column("fy", sa.String(9)),
        _meeting("board_meeting_id"),
        _meeting("general_meeting_id"),
        sa.Column(
            "circular_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
        ),
        sa.Column("share_class", sa.String(60)),
        sa.Column("shares_offered", sa.Numeric(21, 4)),
        sa.Column("shares_allotted", sa.Numeric(21, 4)),
        sa.Column("face_value", sa.Numeric(21, 4)),
        sa.Column("price_per_share", sa.Numeric(21, 4)),
        sa.Column("premium_per_share", sa.Numeric(21, 4)),
        sa.Column("total_amount", sa.Numeric(21, 4)),
        sa.Column("dividend_per_share", sa.Numeric(21, 4)),
        sa.Column("offer_on", sa.Date()),
        sa.Column("record_on", sa.Date()),
        sa.Column("closes_on", sa.Date()),
        sa.Column("allotted_on", sa.Date()),
        sa.Column("paid_up_before", sa.Numeric(21, 4)),
        sa.Column("paid_up_after", sa.Numeric(21, 4)),
        sa.Column("authorised_capital", sa.Numeric(21, 4)),
        sa.Column("allottees", JSONB()),
        sa.Column("solvency_check", JSONB()),
        sa.Column(
            "filing_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_filings.id", ondelete="SET NULL")
        ),
        sa.Column(
            "document_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_documents.id", ondelete="SET NULL")
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(_in("event_type", CAPITAL_EVENT_TYPES), name="ck_secretarial_capital_event_type"),
        sa.CheckConstraint(_in("status", CAPITAL_EVENT_STATUSES), name="ck_secretarial_capital_event_status"),
        # An allotment that allotted nothing on no date is a draft that lied.
        sa.CheckConstraint(
            "status <> 'allotted' OR (allotted_on IS NOT NULL)",
            name="ck_secretarial_capital_event_allotment",
        ),
    )
    op.create_index("ix_secretarial_capital_events_company_id", "secretarial_capital_events", ["company_id"])
    op.create_index(
        "ix_secretarial_capital_events_entity",
        "secretarial_capital_events",
        ["entity_id", "event_type", "status"],
    )

    # --- Share certificates --------------------------------------------------------
    op.create_table(
        "secretarial_share_certificates",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("certificate_no", sa.Integer(), nullable=False),
        sa.Column(
            "member_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_members.id", ondelete="SET NULL")
        ),
        sa.Column("holder_name", sa.String(200), nullable=False),
        sa.Column("folio_no", sa.String(40)),
        sa.Column("share_class", sa.String(60), nullable=False, server_default=sa.text("'Equity'")),
        sa.Column("no_of_shares", sa.Numeric(21, 4), nullable=False),
        sa.Column("face_value", sa.Numeric(21, 4)),
        sa.Column("amount_paid_up", sa.Numeric(21, 4)),
        sa.Column("distinctive_from", sa.Integer(), nullable=False),
        sa.Column("distinctive_to", sa.Integer(), nullable=False),
        sa.Column("issue_type", sa.String(20), nullable=False, server_default=sa.text("'original'")),
        sa.Column("issued_on", sa.Date()),
        sa.Column("deferred", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'issued'")),
        sa.Column("cancelled_on", sa.Date()),
        sa.Column("cancelled_reason", sa.Text()),
        sa.Column("supersedes_id", UUID(as_uuid=True)),
        sa.Column(
            "capital_event_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_capital_events.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "document_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_documents.id", ondelete="SET NULL")
        ),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("entity_id", "certificate_no", name="uq_secretarial_certificate_no"),
        sa.CheckConstraint(
            _in("issue_type", CERTIFICATE_ISSUE_TYPES), name="ck_secretarial_certificate_issue_type"
        ),
        sa.CheckConstraint(_in("status", CERTIFICATE_STATUSES), name="ck_secretarial_certificate_status"),
        sa.CheckConstraint("no_of_shares > 0", name="ck_secretarial_certificate_shares"),
        # The range has to be non-empty, ordered, and hold exactly the shares claimed.
        sa.CheckConstraint(
            "distinctive_to >= distinctive_from AND distinctive_from > 0",
            name="ck_secretarial_certificate_range",
        ),
        sa.CheckConstraint(
            "distinctive_to - distinctive_from + 1 = no_of_shares",
            name="ck_secretarial_certificate_range_size",
        ),
        # A cancellation without a date and a reason is not a record of anything.
        sa.CheckConstraint(
            "status <> 'cancelled' OR (cancelled_on IS NOT NULL AND cancelled_reason IS NOT NULL)",
            name="ck_secretarial_certificate_cancellation",
        ),
    )
    op.create_index(
        "ix_secretarial_share_certificates_company_id", "secretarial_share_certificates", ["company_id"]
    )
    op.create_index(
        "ix_secretarial_certificates_entity", "secretarial_share_certificates", ["entity_id", "status"]
    )
    op.create_index("ix_secretarial_certificates_member", "secretarial_share_certificates", ["member_id"])
    op.create_index(
        "ix_secretarial_certificates_range",
        "secretarial_share_certificates",
        ["entity_id", "share_class", "distinctive_from"],
    )
    op.create_foreign_key(
        "fk_secretarial_certificate_supersedes",
        "secretarial_share_certificates",
        "secretarial_share_certificates",
        ["supersedes_id"],
        ["id"],
        ondelete="SET NULL",
    )

    # No two live certificates of a class may claim overlapping distinctive numbers.
    # A range is the identity of the shares themselves, so an overlap means two people
    # hold paper for the same shares. Exclusion beats a trigger here: the database
    # refuses the write rather than detecting it afterwards.
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.execute(
        "ALTER TABLE secretarial_share_certificates ADD CONSTRAINT ex_secretarial_certificate_overlap "
        "EXCLUDE USING gist ("
        "  entity_id WITH =, share_class WITH =, "
        "  int4range(distinctive_from, distinctive_to, '[]') WITH &&"
        ") WHERE (status = 'issued')"
    )

    # --- Distinctive-number counter ------------------------------------------------
    op.create_table(
        "secretarial_distinctive_seq",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("share_class", sa.String(60), nullable=False),
        sa.Column("next_number", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.UniqueConstraint("entity_id", "share_class", name="uq_secretarial_distinctive_scope"),
        sa.CheckConstraint("next_number > 0", name="ck_secretarial_distinctive_next"),
    )
    op.create_index(
        "ix_secretarial_distinctive_seq_company_id", "secretarial_distinctive_seq", ["company_id"]
    )

    # --- SH-4 transfer details -----------------------------------------------------
    op.create_table(
        "secretarial_share_transfer_details",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("instrument_no", sa.Integer(), nullable=False),
        sa.Column(
            "share_transfer_id", UUID(as_uuid=True), sa.ForeignKey("share_transfers.id", ondelete="SET NULL")
        ),
        sa.Column(
            "transferor_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_members.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "transferee_member_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_members.id", ondelete="SET NULL"),
        ),
        sa.Column("transferor_name", sa.String(200), nullable=False),
        sa.Column("transferee_name", sa.String(200), nullable=False),
        sa.Column("transferor_folio", sa.String(40)),
        sa.Column("transferee_folio", sa.String(40)),
        sa.Column("share_class", sa.String(60), nullable=False, server_default=sa.text("'Equity'")),
        sa.Column("no_of_shares", sa.Numeric(21, 4), nullable=False),
        sa.Column("face_value", sa.Numeric(21, 4)),
        sa.Column("consideration", sa.Numeric(21, 4), nullable=False, server_default=sa.text("0")),
        sa.Column("stamp_duty", sa.Numeric(21, 4)),
        sa.Column("executed_on", sa.Date(), nullable=False),
        sa.Column("lodged_on", sa.Date()),
        sa.Column("distinctive_from", sa.Integer()),
        sa.Column("distinctive_to", sa.Integer()),
        _meeting("board_meeting_id"),
        sa.Column(
            "board_agenda_item_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_agenda_items.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "circular_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_circulars.id", ondelete="SET NULL")
        ),
        sa.Column("approved_on", sa.Date()),
        sa.Column(
            "surrendered_certificate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_share_certificates.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "issued_certificate_id",
            UUID(as_uuid=True),
            sa.ForeignKey("secretarial_share_certificates.id", ondelete="SET NULL"),
        ),
        sa.Column(
            "document_id", UUID(as_uuid=True), sa.ForeignKey("secretarial_documents.id", ondelete="SET NULL")
        ),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'draft'")),
        sa.Column("posted_on", sa.Date()),
        sa.Column("reverted_on", sa.Date()),
        sa.Column("reverted_reason", sa.Text()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("entity_id", "instrument_no", name="uq_secretarial_sh4_no"),
        sa.CheckConstraint(_in("status", TRANSFER_STATUSES), name="ck_secretarial_sh4_status"),
        sa.CheckConstraint("no_of_shares > 0", name="ck_secretarial_sh4_shares"),
        # A reversal has to name its reason and its date. This is the constraint the
        # whole revert-with-reason feature exists for.
        sa.CheckConstraint(
            "status <> 'reverted' OR (reverted_reason IS NOT NULL AND reverted_on IS NOT NULL)",
            name="ck_secretarial_sh4_revert_reason",
        ),
        # Board approval means there is a board decision to point at.
        sa.CheckConstraint(
            "status IN ('draft', 'reverted') "
            "OR board_meeting_id IS NOT NULL OR circular_id IS NOT NULL",
            name="ck_secretarial_sh4_authority",
        ),
    )
    op.create_index(
        "ix_secretarial_share_transfer_details_company_id",
        "secretarial_share_transfer_details",
        ["company_id"],
    )
    op.create_index(
        "ix_secretarial_sh4_entity", "secretarial_share_transfer_details", ["entity_id", "executed_on"]
    )
    op.create_index("ix_secretarial_sh4_status", "secretarial_share_transfer_details", ["company_id", "status"])

    # --- s.186 limits and register -------------------------------------------------
    op.create_table(
        "secretarial_s186_limits",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("fy", sa.String(9), nullable=False),
        sa.Column("paid_up_capital", sa.Numeric(21, 4)),
        sa.Column("free_reserves", sa.Numeric(21, 4)),
        sa.Column("securities_premium", sa.Numeric(21, 4)),
        sa.Column("limit_sixty_pct", sa.Numeric(21, 4)),
        sa.Column("limit_hundred_pct", sa.Numeric(21, 4)),
        sa.Column("effective_limit", sa.Numeric(21, 4)),
        _meeting("special_resolution_meeting_id"),
        sa.Column("special_resolution_on", sa.Date()),
        sa.Column("source", sa.String(10), nullable=False, server_default=sa.text("'manual'")),
        sa.Column("computed_on", sa.Date()),
        sa.Column("gaps", JSONB()),
        sa.Column("notes", sa.Text()),
        sa.UniqueConstraint("entity_id", "fy", name="uq_secretarial_s186_limit_fy"),
        sa.CheckConstraint("source IN ('ledger', 'manual')", name="ck_secretarial_s186_limit_source"),
    )
    op.create_index("ix_secretarial_s186_limits_company_id", "secretarial_s186_limits", ["company_id"])

    op.create_table(
        "secretarial_s186_entries",
        *_meta(),
        _company(),
        _entity(),
        sa.Column("entry_type", sa.String(20), nullable=False),
        sa.Column("fy", sa.String(9)),
        sa.Column("party_name", sa.String(200), nullable=False),
        sa.Column("party_cin", sa.String(30)),
        sa.Column("party_relation", sa.String(60)),
        sa.Column("amount", sa.Numeric(21, 4), nullable=False),
        sa.Column("rate_of_interest", sa.Numeric(9, 4)),
        sa.Column("purpose", sa.Text()),
        sa.Column("security_details", sa.Text()),
        sa.Column("made_on", sa.Date(), nullable=False),
        sa.Column("due_on", sa.Date()),
        sa.Column("repaid_on", sa.Date()),
        _meeting("board_meeting_id"),
        _meeting("special_resolution_meeting_id"),
        sa.Column("limit_check", JSONB()),
        sa.Column("status", sa.String(20), nullable=False, server_default=sa.text("'outstanding'")),
        sa.Column("notes", sa.Text()),
        sa.CheckConstraint(_in("entry_type", S186_ENTRY_TYPES), name="ck_secretarial_s186_entry_type"),
        sa.CheckConstraint(_in("status", S186_ENTRY_STATUSES), name="ck_secretarial_s186_entry_status"),
        sa.CheckConstraint("amount > 0", name="ck_secretarial_s186_entry_amount"),
        sa.CheckConstraint(
            "status <> 'repaid' OR repaid_on IS NOT NULL", name="ck_secretarial_s186_entry_repaid"
        ),
    )
    op.create_index("ix_secretarial_s186_entries_company_id", "secretarial_s186_entries", ["company_id"])
    op.create_index(
        "ix_secretarial_s186_entries_entity", "secretarial_s186_entries", ["entity_id", "status", "made_on"]
    )

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
        "secretarial_s186_entries",
        "secretarial_s186_limits",
        "secretarial_share_transfer_details",
        "secretarial_distinctive_seq",
        "secretarial_share_certificates",
        "secretarial_capital_events",
    ):
        op.drop_table(table)
