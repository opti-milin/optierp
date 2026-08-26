"""Shared state for one import run.

Every importer takes an :class:`ImportContext`. It carries the things that are
constant for a run (db session, company, user, the resolved mapping book) and
the things that accumulate (per-entity counters, per-row messages, the log).

Two behaviours worth knowing:

* **Nothing throws out of a row.** :meth:`ImportContext.row` is a context
  manager that catches whatever an importer raises, marks that staging row as
  ``Error`` with the message, and moves to the next row. One bad voucher out of
  4,000 must not lose the other 3,999.
* **One transaction per row.** Each row commits on success and rolls back on
  failure. That is not just for isolation: the document services this module
  calls (``create_sales_invoice``, ``submit_payment_entry``, ...) commit
  internally, so a row *is* the unit of work whether we like it or not. Making
  that explicit means a failed row leaves nothing half-written, and a run that
  dies at row 8,000 keeps the 7,999 documents before it.
"""

from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Any, AsyncIterator

from sqlalchemy import inspect as sa_inspect
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import DuplicateError, NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.core import Company
from app.models.migration import (
    MigrationImport,
    MigrationImportedDocument,
    MigrationImportEntity,
    MigrationImportLog,
    MigrationStagingRecord,
)
from app.services.migration.catalogue import ENTITY_BY_KEY, STAGE_VOUCHER
from app.services.migration.mapping import MappingBook


@dataclass
class EntityCounters:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(frozen=True)
class ImportedIdentity:
    """A Tally record this company has already turned into a document.

    A plain frozen copy, never a live ORM handle: a failed row rolls the session
    back and expires every attached object, and this lookup has to stay readable
    across that. (Same reason ``MappingBook`` holds copies — see its docstring.)
    """

    source_guid: str
    entity_key: str
    target_doctype: str
    target_id: uuid.UUID
    target_name: str | None
    voucher_number: str | None
    alter_id: int | None
    #: The session that created it. A session re-run must recognise its own work
    #: rather than mistaking it for someone else's import.
    import_id: uuid.UUID | None
    import_name: str | None


async def load_imported_guids(
    db: AsyncSession, company_id: uuid.UUID
) -> dict[str, ImportedIdentity]:
    """Every Tally GUID this company has already imported, keyed by GUID."""
    from sqlalchemy import select

    from app.models.migration import MigrationImport as _TallyImport

    stmt = (
        select(MigrationImportedDocument, _TallyImport.name)
        .outerjoin(_TallyImport, MigrationImportedDocument.migration_import_id == _TallyImport.id)
        .where(MigrationImportedDocument.company_id == company_id)
    )
    rows = (await db.execute(stmt)).all()
    return {
        doc.source_guid: ImportedIdentity(
            source_guid=doc.source_guid,
            entity_key=doc.entity_key,
            target_doctype=doc.target_doctype,
            target_id=doc.target_id,
            target_name=doc.target_name,
            voucher_number=doc.voucher_number,
            alter_id=doc.alter_id,
            import_id=doc.migration_import_id,
            import_name=import_name,
        )
        for doc, import_name in rows
    }


#: A record we have already imported, seen again in a new export.
REPEAT_UNCHANGED = "duplicate"  # same voucher, same version — just skip it
REPEAT_AMENDED = "amendment"  # edited in Tally since we imported it


def classify_repeat(record: MigrationStagingRecord, prior: "ImportedIdentity") -> str:
    """Is this the same voucher again, or an edited version of it?

    Tally bumps ``ALTERID`` on every edit, so a higher one for a GUID we already
    hold means the voucher changed in Tally after we imported it. That is a very
    different thing from an overlapping export, and it must not be reported as
    "already imported, nothing to do" — the books have genuinely diverged.

    Both outcomes still skip. We never silently rewrite a posted document from a
    file: the correction has to be a decision someone makes, with the reversing
    entries that implies.
    """
    incoming = (record.raw or {}).get("alter_id")
    if incoming is None or prior.alter_id is None:
        # Nothing to compare — CSV exports and older Tally versions carry no
        # ALTERID. Treat it as unchanged rather than inventing a conflict.
        return REPEAT_UNCHANGED
    return REPEAT_AMENDED if int(incoming) > int(prior.alter_id) else REPEAT_UNCHANGED


def is_transactional(entity_key: str) -> bool:
    """True for vouchers/openings — the records that can double-post.

    Masters are excluded deliberately: the mapping book already resolves an
    existing ledger or item to the record it made last time, so re-importing one
    reuses it instead of creating a second.
    """
    spec = ENTITY_BY_KEY.get(entity_key)
    return spec is not None and spec.stage >= STAGE_VOUCHER


@dataclass
class ImportContext:
    """Everything an importer needs, and everywhere its results go."""

    db: AsyncSession
    company: Company
    user: CurrentUser
    session: MigrationImport
    book: MappingBook
    #: dry run: validate and resolve, but never write a document
    dry_run: bool = False
    counters: dict[str, EntityCounters] = field(default_factory=dict)
    logs: list[MigrationImportLog] = field(default_factory=list)
    #: current staging row, so helpers can attach messages without plumbing
    current: MigrationStagingRecord | None = None
    #: "<party uuid>:<tally bill ref>" -> invoice id, built as invoices import.
    #: This is how a Tally receipt finds the invoice its "Agst Ref" settles.
    bill_index: dict[str, uuid.UUID] = field(default_factory=dict)
    bill_doctype: dict[str, str] = field(default_factory=dict)
    #: casefolded Tally group name -> its parent group, from this file's GROUP
    #: records. Lets any importer walk a name up to its reserved ancestor.
    group_parents: dict[str, str | None] = field(default_factory=dict)
    #: Tally GUIDs this company has already imported, from every previous
    #: session. Loaded once per run; see :func:`load_imported_guids`.
    imported_guids: dict[str, "ImportedIdentity"] = field(default_factory=dict)
    #: GUID this row added to ``imported_guids``, so a failure can take it back
    #: out — the database rollback discards the row, and the cache must agree.
    _identity_added: str | None = field(default=None, repr=False)

    # -- counters ---------------------------------------------------------------------
    def counter(self, entity_key: str) -> EntityCounters:
        return self.counters.setdefault(entity_key, EntityCounters())

    # -- messages ---------------------------------------------------------------------
    def message(
        self,
        record: MigrationStagingRecord | None,
        level: str,
        text: str,
        field_name: str | None = None,
    ) -> None:
        target = record or self.current
        if target is None:
            return
        messages = list(target.messages or [])
        messages.append({"level": level, "message": text, "field": field_name})
        target.messages = messages

    def warn(self, text: str, field_name: str | None = None) -> None:
        self.message(self.current, "warning", text, field_name)

    def info(self, text: str) -> None:
        self.message(self.current, "info", text)

    def log(
        self,
        phase: str,
        message: str,
        *,
        level: str = "info",
        entity_key: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> None:
        entry = MigrationImportLog(
            id=uuid.uuid4(),
            migration_import_id=self.session.id,
            phase=phase,
            entity_key=entity_key,
            level=level,
            message=message,
            context=context,
            owner=self.user.id,
            modified_by=self.user.id,
        )
        self.db.add(entry)
        self.logs.append(entry)

    # -- per-row execution ------------------------------------------------------------
    @asynccontextmanager
    async def row(self, record: MigrationStagingRecord) -> AsyncIterator[MigrationStagingRecord]:
        """Run one staging row as its own transaction, capturing any failure.

        On success the caller has already set ``record.status`` and we commit.
        On failure everything the row touched is rolled back, then the row alone
        is re-marked ``Error`` and committed, so the tester sees the reason in
        the UI even though the document never existed.
        """
        # A previous row's rollback expires every object the session holds — the
        # rest of this batch, the import session, the company. Reload whatever is
        # stale before any column is touched, or the first read would try to
        # lazy-load from sync context and blow up with MissingGreenlet.
        await self._reload_if_expired(record, self.session, self.company)
        self.current = record
        self._identity_added = None
        counters = self.counter(record.entity_key)
        counters.total += 1
        try:
            yield record
            await self.db.flush()
            await self.db.commit()
        except (ValidationError, NotFoundError, DuplicateError) as exc:
            await self._fail(record, str(exc), getattr(exc, "field", None))
            counters.failed += 1
        except SQLAlchemyError as exc:
            await self._fail(record, f"Database rejected this record: {exc.__class__.__name__}")
            counters.failed += 1
        except Exception as exc:  # noqa: BLE001 - one bad row must not stop the import
            await self._fail(record, f"{exc.__class__.__name__}: {exc}")
            counters.failed += 1
        finally:
            self.current = None

    async def _reload_if_expired(self, *objects: Any) -> None:
        for obj in objects:
            if obj is not None and sa_inspect(obj).expired:
                await self.db.refresh(obj)

    async def _fail(
        self, record: MigrationStagingRecord, text: str, field_name: str | None = None
    ) -> None:
        """Roll back the failed row, then persist the failure on the row itself."""
        # Read the id *before* the rollback: rollback expires every loaded
        # object, and touching an expired attribute afterwards would trigger a
        # lazy refresh from sync context (MissingGreenlet).
        record_id = record.id
        # The rollback discards the identity row this attempt added, so drop it
        # from the cache too — otherwise a later row with the same GUID would be
        # skipped as "already imported" when nothing was ever committed.
        if self._identity_added is not None:
            self.imported_guids.pop(self._identity_added, None)
            self._identity_added = None
        await self.db.rollback()
        # The rollback discarded our in-memory edits along with the document, so
        # re-read the staging row before writing the error onto it.
        target = await self.db.get(MigrationStagingRecord, record_id)
        if target is None:  # pragma: no cover - the row is committed at parse time
            return
        target.status = "Error"
        target.target_id = None
        self.message(target, "error", text, field_name)
        await self.db.commit()

    # -- outcomes ---------------------------------------------------------------------
    def created(
        self,
        record: MigrationStagingRecord,
        doctype: str,
        target_id: uuid.UUID,
        target_name: str | None = None,
    ) -> None:
        record.target_doctype = doctype
        record.target_id = target_id
        record.target_name = target_name
        record.status = "Warning" if _has_warning(record) else "Imported"
        self.counter(record.entity_key).created += 1
        self.remember_identity(record, doctype, target_id, target_name)

    def remember_identity(
        self,
        record: MigrationStagingRecord,
        doctype: str,
        target_id: uuid.UUID,
        target_name: str | None = None,
    ) -> None:
        """Record "this Tally record is now this document", for future imports.

        Written inside the row's own transaction, so the identity lands if and
        only if the document does. Recording it anywhere later would leave a
        window where a document exists with nothing marking it as imported.
        """
        if self.dry_run or not record.source_guid:
            return
        if not is_transactional(record.entity_key):
            return
        if record.source_guid in self.imported_guids:
            return  # already tracked; the unique constraint would reject a second
        raw = record.raw or {}
        identity = MigrationImportedDocument(
            id=uuid.uuid4(),
            company_id=self.company_id,
            migration_import_id=self.session.id,
            source_guid=record.source_guid,
            entity_key=record.entity_key,
            alter_id=raw.get("alter_id"),
            vch_key=raw.get("vch_key"),
            voucher_number=record.voucher_number,
            posting_date=record.posting_date,
            target_doctype=doctype,
            target_id=target_id,
            target_name=target_name,
            owner=self.user.id,
            modified_by=self.user.id,
        )
        self.db.add(identity)
        self.imported_guids[record.source_guid] = ImportedIdentity(
            source_guid=record.source_guid,
            entity_key=record.entity_key,
            target_doctype=doctype,
            target_id=target_id,
            target_name=target_name,
            voucher_number=record.voucher_number,
            alter_id=raw.get("alter_id"),
            import_id=self.session.id,
            import_name=self.session.name,
        )
        self._identity_added = record.source_guid

    def reused(
        self,
        record: MigrationStagingRecord,
        doctype: str,
        target_id: uuid.UUID,
        target_name: str | None = None,
    ) -> None:
        """The record already existed here — matched, not created."""
        record.target_doctype = doctype
        record.target_id = target_id
        record.target_name = target_name
        record.status = "Warning" if _has_warning(record) else "Imported"
        self.counter(record.entity_key).updated += 1

    def skip(self, record: MigrationStagingRecord, reason: str) -> None:
        record.status = "Skipped"
        self.message(record, "info", reason)
        self.counter(record.entity_key).skipped += 1

    # -- convenience ------------------------------------------------------------------
    @property
    def company_id(self) -> uuid.UUID:
        return self.company.id

    @property
    def opening_date(self) -> date | None:
        return self.session.opening_date or self.session.from_date

    def option(self, key: str, default: Any = None) -> Any:
        return (self.session.options or {}).get(key, default)


def _has_warning(record: MigrationStagingRecord) -> bool:
    return any(m.get("level") == "warning" for m in (record.messages or []))


def apply_counters(context: ImportContext, entities: list[MigrationImportEntity]) -> None:
    """Copy the run's counters onto the session's per-entity progress rows."""
    by_key = {e.entity_key: e for e in entities}
    for key, counters in context.counters.items():
        entity = by_key.get(key)
        if entity is None:
            continue
        entity.total = counters.total
        entity.created = counters.created
        entity.updated = counters.updated
        entity.skipped = counters.skipped
        entity.failed = counters.failed
