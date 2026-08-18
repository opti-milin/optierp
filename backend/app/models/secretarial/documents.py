"""Generated documents — stored as their inputs, not as bytes.

Plan §2.5. A document row keeps the resolved block tree, the inputs it came from, and
the exact pack version that produced it. Re-downloading re-renders from those, so the
same document produced today still renders identically in five years even after the
Act is amended and the pack has moved on — which is what makes an old minute book
defensible.

Regeneration never overwrites. It writes a new version pointing at the old one, so
"what changed since the board saw it" stays answerable.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CompanyScopedMixin, DocumentMixin

DOCUMENT_STATUSES = ("draft", "final", "issued", "superseded")


class SecretarialDocument(Base, DocumentMixin, CompanyScopedMixin):
    """One rendered artefact: a notice, minutes, a resolution, a letter.

    ``source_doctype``/``source_id`` point back at whatever caused it — a meeting, a
    circular, an event form — so a document is never an orphan on a shared drive.
    """

    __tablename__ = "secretarial_documents"
    __table_args__ = (
        Index("ix_secretarial_documents_entity", "entity_id", "document_type"),
        Index("ix_secretarial_documents_source", "source_doctype", "source_id"),
        Index("ix_secretarial_documents_chain", "root_id", "version"),
        UniqueConstraint("root_id", "version", name="uq_secretarial_document_version"),
    )

    entity_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_entities.id", ondelete="CASCADE"), nullable=False
    )

    title: Mapped[str] = mapped_column(String(250), nullable=False)
    document_type: Mapped[str] = mapped_column(String(60), nullable=False)
    fragment: Mapped[str] = mapped_column(String(40), nullable=False)

    # The pack that produced it, pinned by version — never "whatever is current".
    pack_code: Mapped[str | None] = mapped_column(String(80))
    pack_version: Mapped[int | None] = mapped_column(Integer)
    pack_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_content_packs.id", ondelete="SET NULL")
    )

    # Everything needed to reproduce the render, byte for byte.
    generation_inputs: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    resolved_blocks: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONB)
    compliance_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB)

    source_doctype: Mapped[str | None] = mapped_column(String(100))
    source_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))

    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default=text("'draft'"))
    document_date: Mapped[date | None] = mapped_column(Date)
    issued_at: Mapped[datetime | None] = mapped_column(TIMESTAMP(timezone=True))

    # --- Version chain ------------------------------------------------------------
    # root_id is the first version's id and never changes, so the whole history of a
    # document is one indexed lookup rather than a recursive walk.
    root_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_documents.id", ondelete="SET NULL")
    )
    is_current: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    change_summary: Mapped[str | None] = mapped_column(Text)

    # Set when a signed scan has been uploaded against this document.
    signed_file_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("secretarial_files.id", ondelete="SET NULL")
    )
    notes: Mapped[str | None] = mapped_column(Text)
