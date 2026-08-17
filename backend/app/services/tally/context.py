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
from app.models.tally import TallyImport, TallyImportEntity, TallyImportLog, TallyStagingRecord
from app.services.tally.mapping import MappingBook


@dataclass
class EntityCounters:
    total: int = 0
    created: int = 0
    updated: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass
class ImportContext:
    """Everything an importer needs, and everywhere its results go."""

    db: AsyncSession
    company: Company
    user: CurrentUser
    session: TallyImport
    book: MappingBook
    #: dry run: validate and resolve, but never write a document
    dry_run: bool = False
    counters: dict[str, EntityCounters] = field(default_factory=dict)
    logs: list[TallyImportLog] = field(default_factory=list)
    #: current staging row, so helpers can attach messages without plumbing
    current: TallyStagingRecord | None = None
    #: "<party uuid>:<tally bill ref>" -> invoice id, built as invoices import.
    #: This is how a Tally receipt finds the invoice its "Agst Ref" settles.
    bill_index: dict[str, uuid.UUID] = field(default_factory=dict)
    bill_doctype: dict[str, str] = field(default_factory=dict)
    #: casefolded Tally group name -> its parent group, from this file's GROUP
    #: records. Lets any importer walk a name up to its reserved ancestor.
    group_parents: dict[str, str | None] = field(default_factory=dict)

    # -- counters ---------------------------------------------------------------------
    def counter(self, entity_key: str) -> EntityCounters:
        return self.counters.setdefault(entity_key, EntityCounters())

    # -- messages ---------------------------------------------------------------------
    def message(
        self,
        record: TallyStagingRecord | None,
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
        entry = TallyImportLog(
            id=uuid.uuid4(),
            tally_import_id=self.session.id,
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
    async def row(self, record: TallyStagingRecord) -> AsyncIterator[TallyStagingRecord]:
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
        self, record: TallyStagingRecord, text: str, field_name: str | None = None
    ) -> None:
        """Roll back the failed row, then persist the failure on the row itself."""
        # Read the id *before* the rollback: rollback expires every loaded
        # object, and touching an expired attribute afterwards would trigger a
        # lazy refresh from sync context (MissingGreenlet).
        record_id = record.id
        await self.db.rollback()
        # The rollback discarded our in-memory edits along with the document, so
        # re-read the staging row before writing the error onto it.
        target = await self.db.get(TallyStagingRecord, record_id)
        if target is None:  # pragma: no cover - the row is committed at parse time
            return
        target.status = "Error"
        target.target_id = None
        self.message(target, "error", text, field_name)
        await self.db.commit()

    # -- outcomes ---------------------------------------------------------------------
    def created(
        self,
        record: TallyStagingRecord,
        doctype: str,
        target_id: uuid.UUID,
        target_name: str | None = None,
    ) -> None:
        record.target_doctype = doctype
        record.target_id = target_id
        record.target_name = target_name
        record.status = "Warning" if _has_warning(record) else "Imported"
        self.counter(record.entity_key).created += 1

    def reused(
        self,
        record: TallyStagingRecord,
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

    def skip(self, record: TallyStagingRecord, reason: str) -> None:
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


def _has_warning(record: TallyStagingRecord) -> bool:
    return any(m.get("level") == "warning" for m in (record.messages or []))


def apply_counters(context: ImportContext, entities: list[TallyImportEntity]) -> None:
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
