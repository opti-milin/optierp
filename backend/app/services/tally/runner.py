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

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import FiscalYear
from app.models.core import Company
from app.models.tally import (
    TallyImport,
    TallyImportEntity,
    TallyImportLog,
    TallyStagingRecord,
)
from app.services.tally import parser
from app.services.tally.catalogue import (
    ENTITIES,
    ENTITY_BY_KEY,
    STAGE_VOUCHER,
    SUPPORT_NONE,
    SUPPORT_REFERENCE,
    EntitySpec,
)
from app.services.tally.context import ImportContext, apply_counters
from app.services.tally.importers import masters as master_importers
from app.services.tally.importers import vouchers as voucher_importers
from app.services.tally.mapping import (
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
    "group",
    "ledger",
    "customer",
    "supplier",
    "cost_category",
    "cost_centre",
    "stock_group",
    "stock_category",
    "godown",
    "stock_item",
    "price_list",
    "voucher_type",
    "budget",
    # openings
    "opening_ledger",
    "opening_stock",
    # vouchers — orders, then goods movements, then invoices, then journals,
    # then payments (which allocate against the invoices above).
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
)

NAMING_SERIES = "TALLY-IMP-.YYYY.-"


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
) -> TallyImport:
    session = await db.get(TallyImport, import_id)
    if session is None or session.company_id != company_id:
        raise NotFoundError("Tally import not found")
    return session


async def list_imports(
    db: AsyncSession, company_id: uuid.UUID | None, *, limit: int = 50
) -> list[TallyImport]:
    stmt = (
        select(TallyImport)
        .where(TallyImport.company_id == company_id)
        .order_by(TallyImport.creation.desc())
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
) -> TallyImport:
    """Store an upload and parse it immediately — a session always has content."""
    company = await _require_company(db, user.company_id)
    if not raw:
        raise ValidationError("The uploaded file is empty")

    digest = hashlib.sha256(raw).hexdigest()
    duplicate = (
        await db.execute(
            select(TallyImport).where(
                TallyImport.company_id == company.id,
                TallyImport.file_hash == digest,
                TallyImport.status.in_(("Imported", "Partially Imported")),
            )
        )
    ).scalars().first()

    name = await get_next_name(db, NAMING_SERIES, company.id)
    session = TallyImport(
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

    await parse_import(db, session, user, raw=raw)
    await db.commit()
    return session


async def parse_import(
    db: AsyncSession,
    session: TallyImport,
    user: CurrentUser,
    *,
    raw: bytes | None = None,
) -> TallyImport:
    """Read the file into staging rows. Safe to re-run — it replaces the staging set."""
    if raw is None:
        if not session.payload:
            raise ValidationError("This import has no stored file to re-parse")
        raw = session.payload.encode("utf-8")
    if session.status in ("Importing",):
        raise ValidationError("This import is currently running")
    if session.status in ("Imported", "Partially Imported"):
        raise ValidationError(
            "This import has already been run. Roll it back before re-parsing."
        )

    parsed, source_type, text = parser.parse(raw, file_name=session.file_name)

    await db.execute(
        delete(TallyStagingRecord).where(TallyStagingRecord.tally_import_id == session.id)
    )
    await db.execute(
        delete(TallyImportEntity).where(TallyImportEntity.tally_import_id == session.id)
    )

    session.source_type = source_type
    session.payload = text
    session.tally_company_name = parsed.company_name
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
            TallyImportEntity(
                id=uuid.uuid4(),
                tally_import_id=session.id,
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
    for tag, count in parsed.unknown_tags.items():
        _log(
            db, session, user, "parse",
            f"{count} '{tag}' record(s) in the file are not part of the supported "
            "Tally entity set and were not staged.",
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
    session: TallyImport, user: CurrentUser, spec: EntitySpec, record: dict[str, Any]
) -> TallyStagingRecord:
    is_voucher = spec.stage >= STAGE_VOUCHER
    return TallyStagingRecord(
        id=uuid.uuid4(),
        tally_import_id=session.id,
        entity_key=spec.key,
        stage=spec.stage,
        sequence=int(record.get("_sequence") or 0),
        tally_guid=(record.get("guid") or None),
        tally_name=(record.get("name") or record.get("party") or None),
        tally_parent=record.get("parent"),
        tally_voucher_type=record.get("voucher_type"),
        voucher_number=record.get("voucher_number"),
        posting_date=record.get("date") if is_voucher else None,
        raw=_jsonable(record),
        status="Pending",
        owner=user.id,
        modified_by=user.id,
    )


def _add_party_entities(
    db: AsyncSession, session: TallyImport, user: CurrentUser, parsed: parser.ParsedFile
) -> None:
    """Stage Customer/Supplier rows for the ledgers that are parties.

    A party ledger is imported twice, on purpose: once as a Chart of Accounts
    leaf (Tally posts to the ledger) and once as a Customer or Supplier (we post
    to a party). They get separate staging rows so each has its own status,
    target and error message — "the ledger imported but the customer failed" is
    a real outcome a tester needs to see.
    """
    from app.services.tally.catalogue import resolve_group_spec

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
            TallyImportEntity(
                id=uuid.uuid4(),
                tally_import_id=session.id,
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
    session: TallyImport,
    user: CurrentUser,
    phase: str,
    message: str,
    *,
    level: str = "info",
    entity_key: str | None = None,
    context: dict[str, Any] | None = None,
) -> None:
    db.add(
        TallyImportLog(
            id=uuid.uuid4(),
            tally_import_id=session.id,
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
    db: AsyncSession, session: TallyImport, user: CurrentUser
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
            ((r.tally_name or "", r.tally_guid, r.tally_parent) for r in rows),
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
    db: AsyncSession, session: TallyImport, entity_key: str
) -> list[TallyStagingRecord]:
    stmt = (
        select(TallyStagingRecord)
        .where(
            TallyStagingRecord.tally_import_id == session.id,
            TallyStagingRecord.entity_key == entity_key,
        )
        .order_by(TallyStagingRecord.sequence)
    )
    return list((await db.execute(stmt)).scalars().all())


# --------------------------------------------------------------------------------------
# Validate (dry run) + run
# --------------------------------------------------------------------------------------


async def validate_import(
    db: AsyncSession, session: TallyImport, user: CurrentUser
) -> dict[str, Any]:
    """Check what would happen without writing a single document.

    A dry run answers the two questions a tester actually has: how many
    documents will I get, and which names are still unresolved?
    """
    company = await _require_company(db, session.company_id)
    book = await MappingBook.load(db, company.id)
    entities = await _entities(db, session)
    selected = {e.entity_key for e in entities if e.selected}

    unresolved: dict[str, list[str]] = {}
    planned: dict[str, int] = {}
    blockers: list[str] = []

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
        planned[entity_key] = len(rows)

        if spec.stage < STAGE_VOUCHER:
            continue
        # Vouchers can only post if the ledgers/items they name are mapped.
        for row in rows:
            for name, kind in _referenced_names(row):
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
    }
    _log(db, session, user, "validate", f"Dry run: {total_planned} document(s) planned.",
         level="warning" if blockers else "info", context=summary)
    await db.commit()
    return summary


def _referenced_names(row: TallyStagingRecord) -> list[tuple[str, str]]:
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
            if isinstance(node, dict):
                item = parser.text_of(node, "STOCKITEMNAME")
                if item:
                    names.append((item.strip(), "stock_item"))
    return names


async def run_import(
    db: AsyncSession, session: TallyImport, user: CurrentUser
) -> TallyImport:
    """Create the documents. Every row is its own transaction (see ImportContext)."""
    if session.status in ("Importing",):
        raise ValidationError("This import is already running")
    if session.status in ("Imported", "Partially Imported"):
        raise ValidationError(
            "This import has already been run. Roll it back first if you want to redo it."
        )
    company = await _require_company(db, session.company_id)

    session.status = "Importing"
    session.started_at = datetime.now(timezone.utc)
    session.error_message = None
    session.modified_by = user.id
    await db.commit()

    book = await MappingBook.load(db, company.id)
    context = ImportContext(db=db, company=company, user=user, session=session, book=book)
    context.group_parents = {
        (g.tally_name or "").strip().casefold(): g.tally_parent
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
            rows = [
                r
                for r in await _staging_for(db, session, entity_key)
                if r.status != "Imported"
            ]
            if not rows:
                continue
            await _dispatch(context, entity_key, rows)
            await db.commit()
    except Exception as exc:  # noqa: BLE001 - a phase-level failure must be reported, not lost
        await db.rollback()
        session_row = await db.get(TallyImport, session.id)
        if session_row is not None:
            session_row.status = "Failed"
            session_row.error_message = f"{exc.__class__.__name__}: {exc}"
            session_row.finished_at = datetime.now(timezone.utc)
            await db.commit()
        raise

    entities = await _entities(db, session)
    apply_counters(context, entities)
    await _refresh_totals(db, session)
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


async def _dispatch(
    context: ImportContext, entity_key: str, rows: list[TallyStagingRecord]
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
        case "price_list":
            await master_importers.import_price_lists(context, rows)
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
    db: AsyncSession, session: TallyImport, user: CurrentUser
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


async def _entities(db: AsyncSession, session: TallyImport) -> list[TallyImportEntity]:
    stmt = (
        select(TallyImportEntity)
        .where(TallyImportEntity.tally_import_id == session.id)
        .order_by(TallyImportEntity.stage, TallyImportEntity.label)
    )
    return list((await db.execute(stmt)).scalars().all())


async def _refresh_totals(db: AsyncSession, session: TallyImport) -> None:
    stmt = (
        select(TallyStagingRecord.status, func.count())
        .where(TallyStagingRecord.tally_import_id == session.id)
        .group_by(TallyStagingRecord.status)
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
    db: AsyncSession, session: TallyImport, user: CurrentUser
) -> dict[str, Any]:
    """Cancel every document this import created, newest first.

    Cancellation (not deletion) is deliberate: it writes the reversing GL and
    stock entries, so the books stay auditable and a tester can see exactly what
    the import did and undid. Masters are left in place — they are harmless, and
    deleting them would break anything created against them since.
    """
    if session.status not in ("Imported", "Partially Imported", "Failed"):
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
        select(TallyStagingRecord)
        .where(
            TallyStagingRecord.tally_import_id == session.id,
            TallyStagingRecord.status.in_(("Imported", "Warning")),
            TallyStagingRecord.target_doctype.in_(tuple(_CANCELLABLE)),
            TallyStagingRecord.target_id.is_not(None),
        )
        # Newest first: payments before the invoices they allocate against.
        .order_by(TallyStagingRecord.stage.desc(), TallyStagingRecord.sequence.desc())
    )
    rows = list((await db.execute(stmt)).scalars().all())

    cancelled = 0
    failed: list[str] = []
    for row in rows:
        cancel = services.get(row.target_doctype or "")
        if cancel is None or row.target_id is None:
            continue
        try:
            await cancel(db, row.target_id, user)
            row.status = "Rolled Back"
            cancelled += 1
            await db.commit()
        except Exception as exc:  # noqa: BLE001 - report, keep unwinding the rest
            await db.rollback()
            fresh = await db.get(TallyStagingRecord, row.id)
            if fresh is not None:
                messages = list(fresh.messages or [])
                messages.append(
                    {"level": "error", "message": f"Rollback failed: {exc}", "field": None}
                )
                fresh.messages = messages
            failed.append(f"{row.target_doctype} {row.target_name or row.target_id}")
            await db.commit()

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
    db: AsyncSession, session: TallyImport, user: CurrentUser
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


async def import_summary(db: AsyncSession, session: TallyImport) -> dict[str, Any]:
    """Everything the detail screen needs in one round trip."""
    entities = await _entities(db, session)
    logs = list(
        (
            await db.execute(
                select(TallyImportLog)
                .where(TallyImportLog.tally_import_id == session.id)
                .order_by(TallyImportLog.creation.desc())
                .limit(200)
            )
        ).scalars().all()
    )
    status_counts = dict(
        (
            await db.execute(
                select(TallyStagingRecord.status, func.count())
                .where(TallyStagingRecord.tally_import_id == session.id)
                .group_by(TallyStagingRecord.status)
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
    session: TallyImport,
    *,
    entity_key: str | None = None,
    status: str | None = None,
    search: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[TallyStagingRecord], int]:
    conditions = [TallyStagingRecord.tally_import_id == session.id]
    if entity_key:
        conditions.append(TallyStagingRecord.entity_key == entity_key)
    if status:
        conditions.append(TallyStagingRecord.status == status)
    if search:
        pattern = f"%{search}%"
        conditions.append(
            TallyStagingRecord.tally_name.ilike(pattern)
            | TallyStagingRecord.voucher_number.ilike(pattern)
        )
    total = int(
        await db.scalar(select(func.count()).select_from(TallyStagingRecord).where(*conditions))
        or 0
    )
    stmt = (
        select(TallyStagingRecord)
        .where(*conditions)
        .order_by(TallyStagingRecord.stage, TallyStagingRecord.sequence)
        .limit(limit)
        .offset(offset)
    )
    return list((await db.execute(stmt)).scalars().all()), total


async def set_entity_selection(
    db: AsyncSession, session: TallyImport, user: CurrentUser, selections: dict[str, bool]
) -> list[TallyImportEntity]:
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
