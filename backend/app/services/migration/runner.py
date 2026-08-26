"""The import pipeline: upload -> parse -> map -> validate -> run -> (rollback).

Each phase is a separate API call, because a real migration is not one click:
the tester uploads, looks at what was found, fixes a handful of mappings, dry
runs, compares the numbers against Tally, and only then commits. Every phase is
re-runnable.

    create_import(file)      -> Draft
    parse_import()           -> Parsed      staging rows exist, nothing written
    automap_import()         -> Mapped      every Tally name has a proposal
    validate_import()        -> Validated   dry run; reports what would happen
    run_import()             -> Imported    documents created, GL/stock posted
    rollback_import()        -> Rolled Back documents cancelled, staging kept

Ordering inside a run is not negotiable and lives in :data:`ENTITY_ORDER`:
trees before leaves, masters before vouchers, invoices before the payments that
settle them.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Sequence

from sqlalchemy import delete, func, select, update
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import FiscalYear
from app.models.core import Company
from app.models.migration import (
    MigrationImport,
    MigrationImportedDocument,
    MigrationImportEntity,
    MigrationImportLog,
    MigrationStagingRecord,
)
from app.services.migration import sources
from app.services.migration.sources import ir as parser
from app.services.migration.sources.profiles import SourceProfile
from app.services.migration.catalogue import (
    ENTITIES,
    ENTITY_BY_KEY,
    STAGE_VOUCHER,
    SUPPORT_NONE,
    SUPPORT_REFERENCE,
    EntitySpec,
)
from app.services.migration.context import (
    REPEAT_AMENDED,
    ImportContext,
    apply_counters,
    classify_repeat,
    is_transactional,
    load_imported_guids,
)
from app.services.migration.importers import contacts as contact_importers
from app.services.migration.importers import masters as master_importers
from app.services.migration.importers import setup as setup_importers
from app.services.migration.importers import openings as opening_importers
from app.services.migration.importers import vouchers as voucher_importers
from app.services.migration.mapping import (
    ENTITY_TARGETS,
    MappingBook,
    auto_map_entity,
    list_mappings,
)

#: The order entities are imported in. Masters first (trees before leaves), then
#: vouchers with invoices ahead of the payments that reference them.
ENTITY_ORDER: tuple[str, ...] = (
    # masters
    "currency",
    "unit",
    # Payment terms come before the parties that default to them: a Customer
    # carries payment_terms_template_id, so the template has to exist first.
    "payment_terms",
    "group",
    "ledger",
    "customer",
    "supplier",
    # Addresses and contacts hang off a Customer or Supplier and are nothing
    # without one.
    "address",
    "contact",
    # Both name ledger accounts, so they follow the chart of accounts.
    "bank_account",
    "tax_template",
    "cost_category",
    "cost_centre",
    "stock_group",
    "stock_category",
    "godown",
    "stock_item",
    "price_list",
    "voucher_type",
    "budget",
    "asset",
    # openings
    "opening_ledger",
    "opening_stock",
    # vouchers — quotations and orders, then goods movements, then invoices,
    # then journals, then payments (which allocate against the invoices above).
    "voucher_quotation",
    "voucher_sales_order",
    "voucher_purchase_order",
    "voucher_delivery_note",
    "voucher_receipt_note",
    "voucher_rejections_in",
    "voucher_rejections_out",
    "voucher_sales",
    "voucher_purchase",
    "voucher_credit_note",
    "voucher_debit_note",
    "voucher_stock_journal",
    "voucher_manufacturing_journal",
    "voucher_physical_stock",
    "voucher_journal",
    "voucher_reversing_journal",
    "voucher_receipt",
    "voucher_payment",
    "voucher_contra",
    "voucher_memorandum",
    "voucher_payroll",
    # Last of all: a statement line is matched against the payments above, so
    # they have to exist before the reconciliation tool is handed anything.
    "bank_transaction",
)

NAMING_SERIES = "MIGRATION-.YYYY.-"


# --------------------------------------------------------------------------------------
# Session lifecycle
# --------------------------------------------------------------------------------------


async def _require_company(db: AsyncSession, company_id: uuid.UUID | None) -> Company:
    if company_id is None:
        raise ValidationError("An active company is required")
    company = await db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company not found")
    return company


async def get_import(
    db: AsyncSession, import_id: uuid.UUID, company_id: uuid.UUID | None
) -> MigrationImport:
    session = await db.get(MigrationImport, import_id)
    if session is None or session.company_id != company_id:
        raise NotFoundError("Tally import not found")
    return session


async def list_imports(
    db: AsyncSession, company_id: uuid.UUID | None, *, limit: int = 50
) -> list[MigrationImport]:
    stmt = (
        select(MigrationImport)
        .where(MigrationImport.company_id == company_id)
        .order_by(MigrationImport.creation.desc())
        .limit(limit)
    )
    return list((await db.execute(stmt)).scalars().all())


async def create_import(
    db: AsyncSession,
    user: CurrentUser,
    *,
    raw: bytes,
    file_name: str | None,
    title: str | None = None,
    opening_date: Any = None,
    options: dict[str, Any] | None = None,
    saved_profiles: tuple[SourceProfile, ...] = (),
) -> MigrationImport:
    """Store an upload and parse it immediately — a session always has content."""
    company = await _require_company(db, user.company_id)
    if not raw:
        raise ValidationError("The uploaded file is empty")

    digest = hashlib.sha256(raw).hexdigest()
    duplicate = (
        await db.execute(
            select(MigrationImport).where(
                MigrationImport.company_id == company.id,
                MigrationImport.file_hash == digest,
                MigrationImport.status.in_(("Imported", "Partially Imported")),
            )
        )
    ).scalars().first()

    name = await get_next_name(db, NAMING_SERIES, company.id)
    session = MigrationImport(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        title=title or file_name or name,
        file_name=file_name,
        file_size=len(raw),
        file_hash=digest,
        opening_date=opening_date,
        status="Draft",
        options=options or {},
        owner=user.id,
        modified_by=user.id,
    )
    db.add(session)
    await db.flush()

    if duplicate is not None:
        _log(
            db, session, user, "parse",
            f"This exact file was already imported as {duplicate.name}. Importing "
            "it again will create duplicate documents unless you roll that one back.",
            level="warning",
        )

    await parse_import(db, session, user, raw=raw, saved_profiles=saved_profiles)
    await db.commit()
    return session


async def parse_import(
    db: AsyncSession,
    session: MigrationImport,
    user: CurrentUser,
    *,
    raw: bytes | None = None,
    profile: SourceProfile | None = None,
    saved_profiles: tuple[SourceProfile, ...] = (),
) -> MigrationImport:
    """Read the file into staging rows. Safe to re-run — it replaces the staging set.

    ``profile`` forces a spreadsheet mapping instead of detecting one; that is
    what re-parsing after a mapping edit does, and why the raw upload is kept on
    the session rather than only its staging rows.
    """
    if raw is None:
        raw = sources.decode_payload(session.payload, session.payload_encoding)
    if session.status in ("Importing",):
        raise ValidationError("This import is currently running")
    if session.status in ("Imported", "Partially Imported"):
        raise ValidationError(
            "This import has already been run. Roll it back before re-parsing."
        )

    parsed, source_type, text = sources.parse(
        raw,
        file_name=session.file_name,
        profile=profile,
        saved_profiles=saved_profiles,
    )

    await db.execute(
        delete(MigrationStagingRecord).where(MigrationStagingRecord.migration_import_id == session.id)
    )
    await db.execute(
        delete(MigrationImportEntity).where(MigrationImportEntity.migration_import_id == session.id)
    )

    session.source_type = source_type
    session.payload, session.payload_encoding = sources.encode_payload(raw, source_type, text)
    session.source_app = parsed.source_app or session.source_app or "Tally"
    session.source_profile = parsed.source_profile
    session.sheet_map = parsed.sheet_map
    session.source_company_name = parsed.company_name
    # The Tally company UUID is what makes a *second* export from the same
    # company recognisable as such — it survives renames, and every mapping
    # decision made here is only safely reusable within the same company.
    session.source_company_guid = parsed.company_guid
    session.from_date = parsed.from_date
    session.to_date = parsed.to_date
    session.opening_date = session.opening_date or parsed.from_date
    session.total_records = parsed.total
    session.imported_count = 0
    session.skipped_count = 0
    session.error_count = 0
    session.status = "Parsed"
    session.modified_by = user.id

    for entity_key, records in parsed.records.items():
        spec = ENTITY_BY_KEY.get(entity_key)
        if spec is None:
            continue
        db.add(
            MigrationImportEntity(
                id=uuid.uuid4(),
                migration_import_id=session.id,
                entity_key=entity_key,
                label=spec.label,
                target_doctype=spec.target,
                module=spec.module,
                stage=spec.stage,
                support=spec.support,
                # Even unsupported entities stay on: they only ever produce
                # Skipped rows, and reporting the gap is the point.
                selected=True,
                total=len(records),
                owner=user.id,
                modified_by=user.id,
            )
        )
        for record in records:
            db.add(_staging_row(session, user, spec, record))

    # Party rows are the same Tally ledgers seen a second time, so the wizard can
    # show "412 ledgers -> 118 customers, 63 suppliers" instead of one opaque line.
    _add_party_entities(db, session, user, parsed)

    for warning in parsed.warnings:
        _log(db, session, user, "parse", warning, level="warning")
    # A sheet we chose not to import is a different fact from one we did not
    # recognise, and the tester needs the reason for the first kind — "no HR
    # module yet" is actionable, "unsupported" is not.
    for sheet, (count, reason) in parsed.skipped_sheets.items():
        _log(
            db, session, user, "parse",
            f"'{sheet}' ({count} row(s)) was read but not imported: {reason}",
            level="info",
            context={"sheet": sheet, "count": count, "reason": reason},
        )
    for tag, count in parsed.unknown_tags.items():
        if tag in parsed.skipped_sheets:
            continue
        _log(
            db, session, user, "parse",
            f"{count} '{tag}' record(s) in the file are not part of the supported "
            "entity set and were not staged.",
            level="warning",
            context={"tag": tag, "count": count},
        )
    _log(
        db, session, user, "parse",
        f"Parsed {parsed.total} record(s) from {session.file_name or 'the upload'}.",
        context={"entities": {k: len(v) for k, v in parsed.records.items()}},
    )
    await db.flush()
    return session


def _staging_row(
    session: MigrationImport, user: CurrentUser, spec: EntitySpec, record: dict[str, Any]
) -> MigrationStagingRecord:
    is_voucher = spec.stage >= STAGE_VOUCHER
    # A spreadsheet adapter can already know a row is unimportable — a rebuilt
    # double entry that will not balance, say. Carrying that verdict onto the
    # staging row means the dry run reports it *before* the run, instead of the
    # run discovering it document by document.
    messages = [m for m in (record.get("_messages") or []) if isinstance(m, dict)]
    blocked = any(m.get("level") == "error" for m in messages)
    return MigrationStagingRecord(
        id=uuid.uuid4(),
        migration_import_id=session.id,
        entity_key=spec.key,
        stage=spec.stage,
        sequence=int(record.get("_sequence") or 0),
        source_guid=(record.get("guid") or None),
        source_name=(record.get("name") or record.get("party") or None),
        source_parent=record.get("parent"),
        source_voucher_type=record.get("voucher_type"),
        voucher_number=record.get("voucher_number"),
        posting_date=record.get("date") if is_voucher else None,
        raw=_jsonable(record),
        status="Error" if blocked else "Pending",
        messages=messages or None,
        owner=user.id,
        modified_by=user.id,
    )


def _add_party_entities(
    db: AsyncSession, session: MigrationImport, user: CurrentUser, parsed: parser.ParsedFile
) -> None:
    """Stage Customer/Supplier rows for the ledgers that are parties.

    A party ledger is imported twice, on purpose: once as a Chart of Accounts
    leaf (Tally posts to the ledger) and once as a Customer or Supplier (we post
    to a party). They get separate staging rows so each has its own status,
    target and error message — "the ledger imported but the customer failed" is
    a real outcome a tester needs to see.
    """
    from app.services.migration.catalogue import resolve_group_spec

    group_parents = {
        (g.get("name") or "").strip().casefold(): g.get("parent")
        for g in parsed.records.get("group", [])
    }
    buckets: dict[str, list[dict[str, Any]]] = {"customer": [], "supplier": []}
    for record in parsed.records.get("ledger", []):
        spec = resolve_group_spec(record.get("parent"), group_parents)
        if spec is None or spec.party_type is None:
            continue
        buckets["customer" if spec.party_type == "Customer" else "supplier"].append(record)

    for key, records in buckets.items():
        if not records:
            continue
        entity = ENTITY_BY_KEY[key]
        db.add(
            MigrationImportEntity(
                id=uuid.uuid4(),
                migration_import_id=session.id,
                entity_key=key,
                label=entity.label,
                target_doctype=entity.target,
                module=entity.module,
                stage=entity.stage,
                support=entity.support,
                selected=True,
                total=len(records),
                owner=user.id,
                modified_by=user.id,
            )
        )
        for record in records:
            db.add(_staging_row(session, user, entity, record))
    session.total_records += sum(len(records) for records in buckets.values())


def _jsonable(value: Any) -> Any:
    """Dates and Decimals do not survive JSONB directly — stringify them."""
    from datetime import date as _date
    from decimal import Decimal as _Decimal

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_date, datetime)):
        return value.isoformat()
    if isinstance(value, _Decimal):
        return str(value)
    return value


def _log(
    db: AsyncSession,
    session: MigrationImport,
    user: CurrentUser,
    phase: str,
    message: str,
    *,
    level: str = "info",
    entity_key: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    db.add(
        MigrationImportLog(
            id=uuid.uuid4(),
            migration_import_id=session.id,
            phase=phase,
            entity_key=entity_key,
            level=level,
            message=message,
            context=_jsonable(context) if context else None,
            owner=user.id,
            modified_by=user.id,
        )
    )


# --------------------------------------------------------------------------------------
# Auto-mapping
# --------------------------------------------------------------------------------------


async def automap_import(
    db: AsyncSession, session: MigrationImport, user: CurrentUser
) -> dict[str, Any]:
    """Propose a mapping for every Tally name in the session."""
    if session.status == "Draft":
        raise ValidationError("Parse the file before mapping")
    company_id = session.company_id
    summary: dict[str, Any] = {}

    for entity_key in ENTITY_ORDER:
        if entity_key not in ENTITY_TARGETS:
            continue
        rows = await _staging_for(db, session, entity_key)
        if not rows:
            continue
        result = await auto_map_entity(
            db, company_id, user, entity_key,
            ((r.source_name or "", r.source_guid, r.source_parent) for r in rows),
        )
        summary[entity_key] = {
            "matched": result.matched,
            "to_create": result.to_create,
            "locked": result.locked,
            "low_confidence": result.low_confidence,
        }

    session.status = "Mapped" if session.status in ("Parsed", "Mapped") else session.status
    session.modified_by = user.id
    _log(db, session, user, "automap", "Auto-mapping complete.", context=summary)
    await db.commit()
    return summary


async def _staging_for(
    db: AsyncSession, session: MigrationImport, entity_key: str,
    *, limit: int | None = None, offset: int = 0,
) -> list[MigrationStagingRecord]:
    stmt = (
        select(MigrationStagingRecord)
        .where(
            MigrationStagingRecord.migration_import_id == session.id,
            MigrationStagingRecord.entity_key == entity_key,
        )
        .order_by(MigrationStagingRecord.sequence)
    )
    if limit is not None:
        stmt = stmt.limit(limit).offset(offset)
    return list((await db.execute(stmt)).scalars().all())


#: Rows pulled into memory at once for a transactional entity. Each row carries
#: two JSONB payloads, so a full year of vouchers read in one go is a large spike
#: for no benefit — they are processed one at a time regardless.
VOUCHER_BATCH = 500


async def _heartbeat(db: AsyncSession, session: MigrationImport) -> None:
    """Mark the run as still alive.

    Written straight through rather than via the ORM object: a per-row rollback
    may have expired ``session``, and a heartbeat must never be the thing that
    raises inside a run.
    """
    await db.execute(
        update(MigrationImport)
        .where(MigrationImport.id == session.id)
        .values(heartbeat_at=datetime.now(timezone.utc))
    )
    await db.commit()


async def _count_staging(
    db: AsyncSession, session: MigrationImport, entity_key: str
) -> int:
    return int(
        await db.scalar(
            select(func.count())
            .select_from(MigrationStagingRecord)
            .where(
                MigrationStagingRecord.migration_import_id == session.id,
                MigrationStagingRecord.entity_key == entity_key,
            )
        )
        or 0
    )


# --------------------------------------------------------------------------------------
# Validate (dry run) + run
# --------------------------------------------------------------------------------------


async def validate_import(
    db: AsyncSession, session: MigrationImport, user: CurrentUser
) -> dict[str, Any]:
    """Check what would happen without writing a single document.

    A dry run answers the two questions a tester actually has: how many
    documents will I get, and which names are still unresolved?
    """
    company = await _require_company(db, session.company_id)
    book = await MappingBook.load(db, company.id)
    entities = await _entities(db, session)
    selected = {e.entity_key for e in entities if e.selected}

    # What this company has already imported. Reporting overlap here is the
    # whole point of a dry run: the tester finds out *before* posting that 312
    # of these 900 vouchers are ones they already have.
    already = await load_imported_guids(db, company.id)

    unresolved: dict[str, list[str]] = {}
    planned: dict[str, int] = {}
    blockers: list[str] = []
    duplicates: list[dict[str, Any]] = []
    amendments: list[dict[str, Any]] = []
    #: Cancelled/optional vouchers present in the file that the run will skip.
    not_posting = 0
    #: entity_key -> casefolded names this run will have created by voucher time
    will_exist: dict[str, set[str]] = {}

    for entity_key in ENTITY_ORDER:
        spec = ENTITY_BY_KEY.get(entity_key)
        if spec is None or entity_key not in selected:
            continue
        rows = await _staging_for(db, session, entity_key)
        if not rows:
            continue
        if spec.support in (SUPPORT_REFERENCE, SUPPORT_NONE):
            planned[entity_key] = 0
            continue
        # Anything already imported will be skipped by the run, so it is not
        # "planned" — counting it would overstate what the tester is about to get.
        if is_transactional(entity_key):
            for row in rows:
                prior = already.get(row.source_guid or "")
                if prior is None:
                    continue
                entry = {
                    "entity_key": entity_key,
                    "voucher_number": row.voucher_number,
                    "posting_date": row.posting_date.isoformat() if row.posting_date else None,
                    "target_doctype": prior.target_doctype,
                    "target_name": prior.target_name,
                    "imported_by": prior.import_name,
                }
                if classify_repeat(row, prior) == REPEAT_AMENDED:
                    # Same voucher, higher ALTERID — Tally's copy changed after
                    # we imported it. Reported separately because it needs a
                    # decision, not just an acknowledgement.
                    amendments.append({
                        **entry,
                        "imported_alter_id": prior.alter_id,
                        "file_alter_id": (row.raw or {}).get("alter_id"),
                    })
                else:
                    duplicates.append(entry)
            rows = [r for r in rows if (r.source_guid or "") not in already]
            # Tally keeps cancelled and "optional" vouchers in the export and the
            # run skips both, so counting them here promises documents that will
            # never appear — "2 would be created" for a file holding one live
            # voucher and one cancelled one.
            skipped_in_file = [r for r in rows if _will_be_skipped(r)]
            if skipped_in_file:
                not_posting += len(skipped_in_file)
                rows = [r for r in rows if r not in skipped_in_file]
        # A row the adapter already rejected is not going to be created either,
        # and its reason is already on the row for the tester to read.
        blocked_rows = [r for r in rows if _blocked_at_parse(r)]
        if blocked_rows:
            rows = [r for r in rows if r not in blocked_rows]
            blockers.append(
                f"{len(blocked_rows)} {spec.label} record(s) cannot be imported: their "
                "amounts do not add up in the source file. Open the Records tab and "
                "filter to Error for the difference on each."
            )
        planned[entity_key] = len(rows)

        if spec.stage < STAGE_VOUCHER:
            # Masters land before vouchers in the same run, so a name this
            # import is about to create counts as resolvable — reporting it as
            # unresolved would send a tester hunting for a problem that the run
            # itself fixes a moment later.
            will_exist.setdefault(entity_key, set()).update(
                (r.source_name or "").strip().casefold() for r in rows if r.source_name
            )
            continue
        # Vouchers can only post if the ledgers/items they name are mapped.
        for row in rows:
            for name, kind in _referenced_names(row):
                if name.strip().casefold() in will_exist.get(kind, set()):
                    continue
                if book.resolve(kind, name) is None:
                    unresolved.setdefault(kind, [])
                    if name not in unresolved[kind]:
                        unresolved[kind].append(name)

    if not any(e.selected for e in entities):
        blockers.append("No entities are selected for import.")
    if company.default_receivable_account_id is None:
        blockers.append(
            "This company has no default receivable account — seed a Chart of "
            "Accounts before importing sales vouchers."
        )

    total_planned = sum(planned.values())
    session.status = "Validated" if not blockers else session.status
    session.modified_by = user.id
    summary = {
        "planned": planned,
        "total_planned": total_planned,
        "unresolved": {k: v[:50] for k, v in unresolved.items()},
        "unresolved_count": sum(len(v) for v in unresolved.values()),
        "blockers": blockers,
        "duplicates": duplicates[:50],
        "duplicate_count": len(duplicates),
        "amendments": amendments[:50],
        "amendment_count": len(amendments),
        "not_posting_count": not_posting,
    }
    headline = f"Dry run: {total_planned} document(s) planned."
    if duplicates:
        headline += (
            f" {len(duplicates)} record(s) in this file were already imported into "
            "this company and will be skipped."
        )
    if amendments:
        headline += (
            f" {len(amendments)} were edited in Tally after they were imported — "
            "review those before relying on either copy."
        )
    _log(db, session, user, "validate", headline,
         level="warning" if (blockers or duplicates or amendments) else "info",
         context=summary)
    await db.commit()
    return summary


def _blocked_at_parse(row: MigrationStagingRecord) -> bool:
    """True when the *file itself* made this row unimportable.

    Distinct from a row that failed in an earlier run — that one is retried, and
    must be, because the usual fix is to correct a mapping and run again. This is
    the other case: a document whose own totals and line amounts disagree in the
    source, which no amount of re-running will resolve. Attempting it anyway would
    replace the adapter's precise reason ("out by 504.33") with whatever the
    posting service happens to say.
    """
    return any(
        isinstance(message, dict) and message.get("level") == "error"
        for message in (row.raw or {}).get("_messages") or []
    )


def _will_be_skipped(row: MigrationStagingRecord) -> bool:
    """Cancelled or optional in Tally — staged and reported, never posted.

    Mirrors ``_skip_cancelled`` in the voucher importer; the dry run has to agree
    with the run about what is actually going to be created.
    """
    raw = row.raw or {}
    return bool(raw.get("is_cancelled") or raw.get("is_optional"))


def _referenced_names(row: MigrationStagingRecord) -> list[tuple[str, str]]:
    """(name, entity_key) pairs a voucher row depends on, for the dry run."""
    raw = row.raw or {}
    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    names: list[tuple[str, str]] = []
    for key in ("ALLLEDGERENTRIES.LIST", "LEDGERENTRIES.LIST"):
        for node in parser.as_list(data.get(key)):
            if isinstance(node, dict):
                ledger = parser.text_of(node, "LEDGERNAME")
                if ledger:
                    names.append((ledger.strip(), "ledger"))
    for key in (
        "ALLINVENTORYENTRIES.LIST",
        "SOURCEALLINVENTORYENTRIES.LIST",
        "DESTINATIONALLINVENTORYENTRIES.LIST",
    ):
        for node in parser.as_list(data.get(key)):
            if not isinstance(node, dict):
                continue
            item = parser.text_of(node, "STOCKITEMNAME")
            if item:
                names.append((item.strip(), "stock_item"))
            # An item line carries its own income/expense ledger nested one level
            # down. Missing it did not fail the voucher — an unmapped ledger
            # resolves to None and the line quietly falls back to the default
            # account — so the import "succeeded" while booking the purchase
            # somewhere Tally never put it. Silent, and worse than a failure.
            for allocation in parser.as_list(node.get("ACCOUNTINGALLOCATIONS.LIST")):
                if isinstance(allocation, dict):
                    ledger = parser.text_of(allocation, "LEDGERNAME")
                    if ledger:
                        names.append((ledger.strip(), "ledger"))
    return names


async def sync_state(db: AsyncSession, company_id: uuid.UUID) -> dict[str, Any]:
    """What this company has taken from Tally so far, per Tally company.

    The useful number is the high-water ``ALTERID``: Tally bumps that counter on
    every edit, so "everything above N" is exactly the set of vouchers created or
    changed since the last import. That turns the next export from "the whole
    year again, and hope the dedupe catches it" into a narrow one.

    Derived from the imported-document ledger rather than kept as its own
    counter — a stored watermark can drift out of step with what was actually
    imported (a rollback, a failed run), and a derived one cannot.
    """
    stmt = (
        select(
            MigrationImport.source_company_guid,
            func.max(MigrationImport.source_company_name),
            func.max(MigrationImportedDocument.alter_id),
            func.count(MigrationImportedDocument.id),
            func.max(MigrationImportedDocument.creation),
            func.min(MigrationImportedDocument.posting_date),
            func.max(MigrationImportedDocument.posting_date),
        )
        .select_from(MigrationImportedDocument)
        .outerjoin(MigrationImport, MigrationImportedDocument.migration_import_id == MigrationImport.id)
        .where(MigrationImportedDocument.company_id == company_id)
        .group_by(MigrationImport.source_company_guid)
    )
    companies = [
        {
            "source_company_guid": guid,
            "source_company_name": name,
            "last_alter_id": last_alter,
            "documents_imported": int(count or 0),
            "last_imported_at": last_at,
            "earliest_voucher_date": first_date,
            "latest_voucher_date": last_date,
        }
        for guid, name, last_alter, count, last_at, first_date, last_date in (
            await db.execute(stmt)
        ).all()
    ]
    return {
        "companies": companies,
        "total_documents_imported": sum(c["documents_imported"] for c in companies),
    }


def assert_runnable(session: MigrationImport) -> None:
    """Raise if this session cannot be started.

    Checked in the request, before the background task is created: a caller who
    double-clicks Run should get a plain error, not a second 202 and a failure
    they have to go hunting for in the log.
    """
    if session.status == "Importing":
        raise ValidationError("This import is already running")
    if session.status in ("Imported", "Partially Imported"):
        raise ValidationError(
            "This import has already been run. Roll it back first if you want to redo it."
        )


async def import_progress(db: AsyncSession, session: MigrationImport) -> dict[str, Any]:
    """Cheap progress snapshot for polling while a run is in flight.

    Read entirely from the database, so it is correct from any process — the one
    running the import or any other.
    """
    entities = await _entities(db, session)
    done = sum(e.created + e.updated + e.skipped + e.failed for e in entities)
    planned = sum(e.total for e in entities if e.selected) or session.total_records
    return {
        "id": session.id,
        "status": session.status,
        "is_running": session.status == "Importing",
        "total_records": session.total_records,
        "processed": done,
        "imported_count": session.imported_count,
        "skipped_count": session.skipped_count,
        "error_count": session.error_count,
        # Whole percent; planned can be 0 for an empty file, so guard the divide.
        "percent": min(100, round(done * 100 / planned)) if planned else 100,
        "started_at": session.started_at,
        "heartbeat_at": session.heartbeat_at,
        "finished_at": session.finished_at,
        "error_message": session.error_message,
        "current_entity": next(
            (e.entity_key for e in entities if e.selected and e.total and not (
                e.created + e.updated + e.skipped + e.failed
            )),
            None,
        ),
    }


async def run_import(
    db: AsyncSession, session: MigrationImport, user: CurrentUser
) -> MigrationImport:
    """Create the documents. Every row is its own transaction (see ImportContext)."""
    if session.status in ("Imported", "Partially Imported"):
        raise ValidationError(
            "This import has already been run. Roll it back first if you want to redo it."
        )
    company = await _require_company(db, session.company_id)

    # The API marks the session "Importing" in the request, so a caller that
    # tries to start it twice gets an error instead of a second 202. Called
    # directly (a job, a test), we do it here instead.
    if session.status != "Importing":
        session.status = "Importing"
        session.started_at = datetime.now(timezone.utc)
        session.heartbeat_at = session.started_at
        session.error_message = None
        session.modified_by = user.id
        await db.commit()

    book = await MappingBook.load(db, company.id)
    context = ImportContext(
        db=db, company=company, user=user, session=session, book=book,
        # Everything this company has already imported, from every prior session.
        # Loaded once: the run consults it per row and adds to it as it goes.
        imported_guids=await load_imported_guids(db, company.id),
    )
    context.group_parents = {
        (g.source_name or "").strip().casefold(): g.source_parent
        for g in await _staging_for(db, session, "group")
    }
    entities = await _entities(db, session)
    selected = {e.entity_key for e in entities if e.selected}

    try:
        await ensure_fiscal_years(db, session, user)
        await db.commit()
        for entity_key in ENTITY_ORDER:
            if entity_key not in selected:
                continue
            # Masters are read whole: they are few, and the tree importers need
            # the whole set to place a child under a parent that arrives later in
            # the file. Vouchers are read in batches — there can be tens of
            # thousands, each carrying two JSONB payloads, and they are processed
            # one row at a time anyway.
            if is_transactional(entity_key):
                total = await _count_staging(db, session, entity_key)
                offset = 0
                while offset < total:
                    batch = await _staging_for(
                        db, session, entity_key, limit=VOUCHER_BATCH, offset=offset,
                    )
                    if not batch:
                        break
                    offset += len(batch)
                    # "Warning" means imported-with-a-note; both are done.
                    pending = [
                        r
                        for r in batch
                        if r.status not in ("Imported", "Warning") and not _blocked_at_parse(r)
                    ]
                    pending = await _skip_already_imported(context, pending)
                    if pending:
                        await _dispatch(context, entity_key, pending)
                        await db.commit()
                    await _heartbeat(db, session)
                continue

            rows = [
                r
                for r in await _staging_for(db, session, entity_key)
                if r.status not in ("Imported", "Warning") and not _blocked_at_parse(r)
            ]
            if not rows:
                continue
            rows = await _skip_already_imported(context, rows)
            if not rows:
                continue
            await _dispatch(context, entity_key, rows)
            await db.commit()
            await _heartbeat(db, session)
    except Exception as exc:  # noqa: BLE001 - a phase-level failure must be reported, not lost
        await db.rollback()
        session_row = await db.get(MigrationImport, session.id)
        if session_row is not None:
            session_row.status = "Failed"
            session_row.error_message = f"{exc.__class__.__name__}: {exc}"
            session_row.finished_at = datetime.now(timezone.utc)
            await db.commit()
        raise

    entities = await _entities(db, session)
    apply_counters(context, entities)
    await _refresh_totals(db, session)
    # A saved workbook shape that has actually carried an import is worth offering
    # ahead of one somebody tried once and abandoned.
    if session.source_profile:
        from app.services.migration import workbooks

        await workbooks.note_use(db, session.company_id, session.source_profile)
    session.status = "Partially Imported" if session.error_count else "Imported"
    session.finished_at = datetime.now(timezone.utc)
    session.modified_by = user.id
    _log(
        db, session, user, "run",
        f"Import finished: {session.imported_count} imported, "
        f"{session.skipped_count} skipped, {session.error_count} failed.",
        level="warning" if session.error_count else "info",
    )
    if book.unresolved:
        _log(
            db, session, user, "run",
            f"{len(book.unresolved)} Tally name(s) could not be resolved to a record.",
            level="warning",
            context={"names": sorted(f"{k}:{n}" for k, n in list(book.unresolved)[:100])},
        )
    await db.commit()
    return session


async def _skip_already_imported(
    context: ImportContext, rows: list[MigrationStagingRecord]
) -> list[MigrationStagingRecord]:
    """Drop rows this company already imported, and say so on each one.

    Skipping rather than failing the whole run is deliberate: overlapping
    exports are normal (a customer re-exports a wider period), and the useful
    outcome is "the 588 new vouchers landed, the 312 you already had were left
    alone" — not "nothing imported, fix your file".
    """
    remaining: list[MigrationStagingRecord] = []
    skipped = 0
    amended = 0
    entity_key = rows[0].entity_key if rows else None
    for row in rows:
        prior = context.imported_guids.get(row.source_guid or "")
        if prior is None or not is_transactional(row.entity_key):
            remaining.append(row)
            continue
        if prior.import_id == context.session.id:
            # This session's own work, seen again on a re-run. Leave the row
            # exactly as it was: overwriting its "Imported"/"Warning" status with
            # "Skipped" would erase the only marker rollback uses to find what to
            # cancel, stranding a posted document with nothing pointing at it.
            continue
        # Same per-row transaction as any other outcome, so the entity counters
        # add up and the skip is committed even if a later row blows up.
        async with context.row(row):
            where = f" by import {prior.import_name}" if prior.import_name else ""
            document = f"{prior.target_doctype} {prior.target_name or prior.target_id}"
            if classify_repeat(row, prior) == REPEAT_AMENDED:
                # Edited in Tally since we imported it. Do not rewrite the posted
                # document from a file — say so, and let someone decide.
                context.skip(
                    row,
                    f"Changed in Tally since it was imported{where} as {document}. "
                    "The existing document was left untouched — compare the two and, "
                    "if the Tally version is right, amend or cancel and re-enter it here.",
                )
                context.message(row, "warning", "Amended in Tally after import.")
                amended += 1
            else:
                context.skip(
                    row,
                    f"Already imported{where} as {document}. "
                    "Skipped so it is not posted twice.",
                )
            # Point at the document that already exists, so the tester can open it.
            row.target_doctype = prior.target_doctype
            row.target_id = prior.target_id
            row.target_name = prior.target_name
        skipped += 1
    if skipped:
        note = f"Skipped {skipped} record(s) already imported into this company."
        if amended:
            note += (
                f" {amended} of them were edited in Tally after import and need "
                "a decision — the documents here were left untouched."
            )
        context.log("run", note, level="warning", entity_key=entity_key)
        await context.db.commit()
    return remaining


async def _dispatch(
    context: ImportContext, entity_key: str, rows: list[MigrationStagingRecord]
) -> None:
    """Route an entity's staging rows to the importer that handles it."""
    spec = ENTITY_BY_KEY.get(entity_key)
    if spec is None:
        return
    if spec.support == SUPPORT_NONE:
        await voucher_importers.skip_entity(
            context, rows,
            f"{spec.label}: {spec.notes or 'no target module in OptiERP yet'}",
        )
        return
    if spec.support == SUPPORT_REFERENCE:
        await voucher_importers.skip_entity(
            context, rows, spec.notes or f"{spec.label} is kept for reference only."
        )
        return

    match entity_key:
        case "group":
            await master_importers.import_groups(context, rows)
        case "ledger":
            await master_importers.import_ledgers(context, rows)
        case "customer":
            await master_importers.import_parties(context, rows, party_type="Customer")
        case "supplier":
            await master_importers.import_parties(context, rows, party_type="Supplier")
        case "unit":
            await master_importers.import_units(context, rows)
        case "stock_group":
            await master_importers.import_stock_groups(context, rows)
        case "godown":
            await master_importers.import_godowns(context, rows)
        case "stock_item":
            await master_importers.import_stock_items(context, rows)
        case "cost_centre":
            await master_importers.import_cost_centres(context, rows)
        case "opening_ledger":
            await opening_importers.import_opening_ledgers(context, rows)
        case "opening_stock":
            await opening_importers.import_opening_stock(context, rows)
        case "price_list":
            await master_importers.import_price_lists(context, rows)
        case "payment_terms":
            await setup_importers.import_payment_terms(context, rows)
        case "currency":
            await setup_importers.import_currencies(context, rows)
        case "tax_template":
            await setup_importers.import_tax_templates(context, rows)
        case "bank_account":
            await setup_importers.import_bank_accounts(context, rows)
        case "bank_transaction":
            await setup_importers.import_bank_transactions(context, rows)
        case "budget":
            await setup_importers.import_budgets(context, rows)
        case "asset":
            await setup_importers.import_assets(context, rows)
        case "address":
            await contact_importers.import_addresses(context, rows)
        case "contact":
            await contact_importers.import_contacts(context, rows)
        case "voucher_sales" | "voucher_credit_note":
            await voucher_importers.import_invoices(context, rows, kind="Sales")
        case "voucher_purchase" | "voucher_debit_note":
            await voucher_importers.import_invoices(context, rows, kind="Purchase")
        case "voucher_receipt" | "voucher_payment" | "voucher_contra":
            await voucher_importers.import_payments(context, rows)
        case "voucher_journal" | "voucher_reversing_journal":
            await voucher_importers.import_journals(context, rows)
        case "voucher_delivery_note" | "voucher_rejections_in":
            await voucher_importers.import_stock_notes(context, rows, kind="Delivery")
        case "voucher_receipt_note" | "voucher_rejections_out":
            await voucher_importers.import_stock_notes(context, rows, kind="Receipt")
        case "voucher_quotation":
            await voucher_importers.import_orders(context, rows, kind="Quotation")
        case "voucher_sales_order":
            await voucher_importers.import_orders(context, rows, kind="Sales")
        case "voucher_purchase_order":
            await voucher_importers.import_orders(context, rows, kind="Purchase")
        case "voucher_stock_journal" | "voucher_manufacturing_journal":
            await voucher_importers.import_stock_journals(context, rows)
        case "voucher_physical_stock":
            await voucher_importers.import_physical_stock(context, rows)
        case _:
            await voucher_importers.skip_entity(
                context, rows,
                f"No importer is wired for '{entity_key}' yet; the rows were staged.",
            )


async def ensure_fiscal_years(
    db: AsyncSession, session: MigrationImport, user: CurrentUser
) -> list[str]:
    """Create the fiscal years the imported period needs.

    A migration is historical by definition: a company created today has one
    fiscal year, and every 2023 voucher in the Tally file would be rejected with
    "no fiscal year covers this date". Rather than fail thousands of rows for a
    setup detail, we open the missing years up front and log which ones — they
    are ordinary Fiscal Year records the accountant can rename or close.
    """
    company = await _require_company(db, session.company_id)
    start = session.from_date
    end = session.to_date
    if start is None or end is None:
        return []

    existing = list(
        (
            await db.execute(
                select(FiscalYear).where(FiscalYear.company_id == company.id)
            )
        ).scalars().all()
    )
    india = (company.country_code or "").upper() == "IN"
    created: list[str] = []

    # Walk year by year across the imported period, in the company's convention.
    cursor = start
    while cursor <= end:
        if india:
            first = cursor.year if cursor.month >= 4 else cursor.year - 1
            year_start, year_end = date(first, 4, 1), date(first + 1, 3, 31)
            label = f"{first}-{first + 1}"
        else:
            year_start, year_end = date(cursor.year, 1, 1), date(cursor.year, 12, 31)
            label = str(cursor.year)

        covered = any(
            fy.year_start_date <= year_start and fy.year_end_date >= year_end
            for fy in existing
        )
        if not covered and not any(fy.year == label for fy in existing):
            fiscal_year = FiscalYear(
                id=uuid.uuid4(),
                company_id=company.id,
                year=label,
                year_start_date=year_start,
                year_end_date=year_end,
                auto_created=True,
                owner=user.id,
                modified_by=user.id,
            )
            db.add(fiscal_year)
            existing.append(fiscal_year)
            created.append(label)
        cursor = year_end + timedelta(days=1)

    if created:
        await db.flush()
        _log(
            db, session, user, "run",
            f"Opened fiscal year(s) {', '.join(created)} so the imported period can post.",
            context={"years": created},
        )
    return created


async def _entities(db: AsyncSession, session: MigrationImport) -> list[MigrationImportEntity]:
    stmt = (
        select(MigrationImportEntity)
        .where(MigrationImportEntity.migration_import_id == session.id)
        .order_by(MigrationImportEntity.stage, MigrationImportEntity.label)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _refresh_totals(db: AsyncSession, session: MigrationImport) -> None:
    stmt = (
        select(MigrationStagingRecord.status, func.count())
        .where(MigrationStagingRecord.migration_import_id == session.id)
        .group_by(MigrationStagingRecord.status)
    )
    counts = dict((await db.execute(stmt)).all())
    session.imported_count = int(counts.get("Imported", 0)) + int(counts.get("Warning", 0))
    session.skipped_count = int(counts.get("Skipped", 0))
    session.error_count = int(counts.get("Error", 0))


# --------------------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------------------

#: Documents that must be cancelled (reversing GL/stock) before they are useless.
_CANCELLABLE: dict[str, str] = {
    "Journal Entry": "journal_entry",
    "Sales Invoice": "sales_invoice",
    "Purchase Invoice": "purchase_invoice",
    "Payment Entry": "payment_entry",
    "Stock Entry": "stock_entry",
    "Stock Reconciliation": "stock_reconciliation",
    "Delivery Note": "delivery_note",
    "Purchase Receipt": "purchase_receipt",
    "Sales Order": "sales_order",
    "Purchase Order": "purchase_order",
}


async def rollback_import(
    db: AsyncSession, session: MigrationImport, user: CurrentUser
) -> dict[str, Any]:
    """Cancel every document this import created, newest first.

    Cancellation (not deletion) is deliberate: it writes the reversing GL and
    stock entries, so the books stay auditable and a tester can see exactly what
    the import did and undid. Masters are left in place — they are harmless, and
    deleting them would break anything created against them since.
    """
    # "Rolled Back" is allowed through: rollback is idempotent (a cancelled
    # document releases its identity and its row stops matching), and refusing a
    # second attempt would strand anything an earlier, incomplete rollback
    # missed with no way to undo it from the UI.
    if session.status not in ("Imported", "Partially Imported", "Failed", "Rolled Back"):
        raise ValidationError("Only a completed import can be rolled back")

    from app.services import (  # imported here to avoid a cycle at module load
        delivery_note, journal_entry, payment_entry, purchase_invoice, purchase_order,
        purchase_receipt, sales_invoice, sales_order, stock_entry, stock_reconciliation,
    )

    services = {
        "Journal Entry": journal_entry.cancel_journal_entry,
        "Sales Invoice": sales_invoice.cancel_sales_invoice,
        "Purchase Invoice": purchase_invoice.cancel_purchase_invoice,
        "Payment Entry": payment_entry.cancel_payment_entry,
        "Stock Entry": stock_entry.cancel_stock_entry,
        "Stock Reconciliation": stock_reconciliation.cancel_stock_reconciliation,
        "Delivery Note": delivery_note.cancel_delivery_note,
        "Purchase Receipt": purchase_receipt.cancel_purchase_receipt,
        "Sales Order": sales_order.cancel_sales_order,
        "Purchase Order": purchase_order.cancel_purchase_order,
    }

    stmt = (
        select(MigrationStagingRecord)
        .where(
            MigrationStagingRecord.migration_import_id == session.id,
            MigrationStagingRecord.status.in_(("Imported", "Warning")),
            MigrationStagingRecord.target_doctype.in_(tuple(_CANCELLABLE)),
            MigrationStagingRecord.target_id.is_not(None),
        )
        # Newest first: payments before the invoices they allocate against.
        .order_by(MigrationStagingRecord.stage.desc(), MigrationStagingRecord.sequence.desc())
    )
    # One document, one cancel — however many staging rows point at it. Opening
    # balances are deliberately many rows against a single Journal Entry, so
    # iterating rows asked to cancel that entry once per ledger: the first
    # succeeded and the rest failed with "Document is not submitted", turning a
    # clean rollback into one reporting failures it never really had.
    rows = []
    claimed: set[uuid.UUID] = set()
    for row in (await db.execute(stmt)).scalars().all():
        if row.target_id in claimed:
            # Still mark it rolled back — the document it named is being
            # cancelled, just by the row that got there first.
            row.status = "Rolled Back"
            continue
        claimed.add(row.target_id)
        rows.append(row)

    # Staging status alone is not a trustworthy record of what this session
    # posted — a re-run can rewrite it, and a re-parse replaces the rows
    # outright. The identity ledger is the authoritative "this session created
    # that document", so anything it still holds gets cancelled too. Without
    # this, a document whose row no longer says "Imported" is orphaned: live in
    # the books, with rollback cheerfully reporting nothing to do.
    # Plain tuples, never live ORM handles: the cancel loop below commits per
    # document, and every commit expires attached objects — reading one
    # afterwards would lazy-load from sync context and raise MissingGreenlet.
    seen = set(claimed)
    orphans = [
        (row_id, doctype, target_id, target_name)
        for row_id, doctype, target_id, target_name in (
            await db.execute(
                select(
                    MigrationImportedDocument.id,
                    MigrationImportedDocument.target_doctype,
                    MigrationImportedDocument.target_id,
                    MigrationImportedDocument.target_name,
                )
                .where(
                    MigrationImportedDocument.company_id == session.company_id,
                    MigrationImportedDocument.migration_import_id == session.id,
                    MigrationImportedDocument.target_doctype.in_(tuple(_CANCELLABLE)),
                )
                # Reverse import order, which is the order these were created in
                # (ENTITY_ORDER puts invoices before the payments that settle
                # them). Unwinding forwards would try to cancel an invoice that
                # still has a live payment against it, and fail.
                .order_by(MigrationImportedDocument.creation.desc())
            )
        ).all()
        if target_id not in seen
    ]

    cancelled = 0
    failed: list[str] = []
    for row in rows:
        cancel = services.get(row.target_doctype or "")
        if cancel is None or row.target_id is None:
            continue
        # Read everything the failure path needs *before* attempting the cancel.
        # A failure issues `db.rollback()`, which expires every attached object —
        # reading these off `row` afterwards lazy-loads from sync context and
        # raises MissingGreenlet instead of the cancellation error, so the tester
        # gets a 500 rather than "this invoice has a payment against it".
        row_id, doctype = row.id, row.target_doctype
        target_id, target_name, guid = row.target_id, row.target_name, row.source_guid
        try:
            await cancel(db, target_id, user)
            row.status = "Rolled Back"
            # Release the identity: the document is cancelled, so importing this
            # record again is now the correct thing to allow. Without this the
            # import -> compare -> roll back -> fix -> re-import loop would skip
            # every voucher on the second pass.
            await db.execute(
                delete(MigrationImportedDocument).where(
                    MigrationImportedDocument.company_id == session.company_id,
                    MigrationImportedDocument.source_guid == guid,
                )
            )
            cancelled += 1
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - report, keep unwinding the rest
            await db.rollback()
            fresh = await db.get(MigrationStagingRecord, row_id)
            if fresh is not None:
                messages = list(fresh.messages or [])
                messages.append(
                    {"level": "error", "message": f"Rollback failed: {exc}", "field": None}
                )
                fresh.messages = messages
            failed.append(f"{doctype} {target_name or target_id}: {exc}")
            await db.commit()

    # Documents this session created whose staging row no longer marks them.
    for row_id, doctype, target_id, target_name in orphans:
        cancel = services.get(doctype or "")
        if cancel is None:
            continue
        try:
            await cancel(db, target_id, user)
            await db.execute(
                delete(MigrationImportedDocument).where(MigrationImportedDocument.id == row_id)
            )
            cancelled += 1
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - report, keep unwinding the rest
            await db.rollback()
            failed.append(f"{doctype} {target_name or target_id} ({exc})")

    # A failed cancel above issued a rollback, and that expires every attached
    # object — including this one. Touching it now would lazy-load from sync
    # context and raise MissingGreenlet *instead of* reporting the failure.
    if sa_inspect(session).expired:
        await db.refresh(session)

    session.status = "Rolled Back"
    session.imported_count = 0
    session.modified_by = user.id
    _log(
        db, session, user, "rollback",
        f"Rolled back {cancelled} document(s)"
        + (f"; {len(failed)} could not be cancelled." if failed else "."),
        level="warning" if failed else "info",
        context={"failed": failed[:50]},
    )
    await db.commit()
    return {"cancelled": cancelled, "failed": failed}


async def delete_import(
    db: AsyncSession, session: MigrationImport, user: CurrentUser
) -> None:
    """Remove a session and its staging rows. Refuses while documents are live."""
    if session.status in ("Imported", "Partially Imported"):
        raise ValidationError(
            "Roll this import back before deleting it, otherwise the documents it "
            "created would lose their audit trail."
        )
    await db.delete(session)
    await db.commit()


# --------------------------------------------------------------------------------------
# Reporting
# --------------------------------------------------------------------------------------


async def import_summary(db: AsyncSession, session: MigrationImport) -> dict[str, Any]:
    """Everything the detail screen needs in one round trip."""
    entities = await _entities(db, session)
    logs = list(
        (
            await db.execute(
                select(MigrationImportLog)
                .where(MigrationImportLog.migration_import_id == session.id)
                .order_by(MigrationImportLog.creation.desc())
                .limit(200)
            )
        ).scalars().all()
    )
    status_counts = dict(
        (
            await db.execute(
                select(MigrationStagingRecord.status, func.count())
                .where(MigrationStagingRecord.migration_import_id == session.id)
                .group_by(MigrationStagingRecord.status)
            )
        ).all()
    )
    mappings = await list_mappings(db, session.company_id)
    return {
        "entities": entities,
        "logs": logs,
        "status_counts": {str(k): int(v) for k, v in status_counts.items()},
        "unmapped_count": sum(1 for m in mappings if m.target_id is None),
        "low_confidence_count": sum(
            1 for m in mappings if m.target_id is not None and m.confidence < 85
        ),
    }


async def staging_records(
    db: AsyncSession,
    session: MigrationImport,
    *,
    entity_key: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[MigrationStagingRecord], int]:
    conditions = [MigrationStagingRecord.migration_import_id == session.id]
    if entity_key:
        conditions.append(MigrationStagingRecord.entity_key == entity_key)
    if status:
        conditions.append(MigrationStagingRecord.status == status)
    if search:
        pattern = f"%{search}%"
        conditions.append(
            MigrationStagingRecord.source_name.ilike(pattern)
            | MigrationStagingRecord.voucher_number.ilike(pattern)
        )
    total = int(
        await db.scalar(select(func.count()).select_from(MigrationStagingRecord).where(*conditions))
        or 0
    )
    stmt = (
        select(MigrationStagingRecord)
        .where(*conditions)
        .order_by(MigrationStagingRecord.stage, MigrationStagingRecord.sequence)
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all()), total


async def set_entity_selection(
    db: AsyncSession, session: MigrationImport, user: CurrentUser, selections: dict[str, bool]
) -> list[MigrationImportEntity]:
    """Turn entities on/off before a run (the wizard's checkbox column)."""
    entities = await _entities(db, session)
    for entity in entities:
        if entity.entity_key in selections:
            entity.selected = bool(selections[entity.entity_key])
            entity.modified_by = user.id
    await db.commit()
    return entities


def coverage() -> Sequence[EntitySpec]:
    return ENTITIES
