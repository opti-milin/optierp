"""Asset service — fixed-asset register + depreciation (bespoke document).

Acquisition is via a manual Journal Entry or a fixed-asset Purchase Invoice line
(Dr a Fixed Asset account); this service does NOT post the acquisition. What it owns is
the depreciation lifecycle:

* **create** an Asset (Draft) and generate its depreciation **schedule** (Straight Line
  or Manual) so it can be previewed;
* **submit** to make it live;
* post each due schedule row as a Journal Entry (Dr Depreciation Expense /
  Cr Accumulated Depreciation) — driven manually (``depreciate_asset``) or by the daily
  job (``app.jobs.assets``). Posting reuses the existing GL service; the Assets module
  never re-implements posting.

Idempotency: a row's ``posted`` flag is set in the **same transaction** that writes its
Journal Entry + GL, so a re-run (or crash-retry) can never double-book a period. Book
value is derived from the posted rows, never stored.

Written Down Value is Phase 2; Phase 1 = Straight Line + Manual.
"""

import calendar
import uuid
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.database import set_company_context
from app.core.exceptions import NotFoundError, ValidationError
from app.core.logging import get_logger
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import JournalEntry, JournalEntryAccount
from app.models.assets import (
    Asset,
    AssetCategory,
    AssetDepreciationSchedule,
    AssetMovement,
    Location,
)
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.schemas.assets import (
    AssetCapitalizeIn,
    AssetCreate,
    AssetDisposeIn,
    AssetMoveIn,
    AssetValueAdjustIn,
    DepreciateResult,
)
from app.services import gl
from app.services.accounts_common import NAMING_SERIES, get_company, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.pagination import paginate

logger = get_logger(__name__)

_SERIES = "ASSET-.YYYY.-"
TWOPLACES = Decimal("0.01")
ZERO = Decimal("0")


# --- date math ---------------------------------------------------------------------


def add_months(d: date, months: int) -> date:
    """Add ``months`` to ``d``, clamping the day to the target month's last day."""
    total = d.month - 1 + months
    year = d.year + total // 12
    month = total % 12 + 1
    last_day = calendar.monthrange(year, month)[1]
    return date(year, month, min(d.day, last_day))


# --- schedule generation (pure) ----------------------------------------------------


def straight_line_schedule(
    *,
    gross: Decimal,
    salvage: Decimal,
    opening_accumulated: Decimal,
    number_of_depreciations: int,
    frequency_months: int,
    start_date: date,
    daily_prorata: bool = False,
) -> list[tuple[date, Decimal, Decimal]]:
    """Equal-instalment schedule. Returns ``(schedule_date, amount, accumulated)`` rows.

    ``amount`` is the depreciable base spread across the periods; the **last row absorbs the
    rounding remainder** so the total equals the depreciable base exactly and the final book
    value lands on ``salvage``. ``accumulated`` includes the asset's opening accumulated
    depreciation. Posting dates fall at the end of each period (``start_date`` + i × frequency).

    When ``daily_prorata`` is set, each period is weighted by its actual number of days
    (so a 28-day February gets less than a 31-day January) instead of an equal split.
    """
    if number_of_depreciations <= 0:
        raise ValidationError(
            "Asset Category needs a positive 'number of depreciations'",
            field="total_number_of_depreciations",
        )
    depreciable = gross - salvage - opening_accumulated
    if depreciable < ZERO:
        raise ValidationError(
            "Salvage + opening depreciation exceed the asset's gross value", field="gross_purchase_amount"
        )
    n = number_of_depreciations
    dates = [add_months(start_date, i * frequency_months) for i in range(1, n + 1)]
    total_days = (dates[-1] - start_date).days or 1
    per = (depreciable / n).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    rows: list[tuple[date, Decimal, Decimal]] = []
    running = ZERO
    prev = start_date
    for i, d in enumerate(dates, start=1):
        if i == n:
            amount = depreciable - running  # last row absorbs the remainder exactly
        elif daily_prorata:
            days = (d - prev).days
            amount = (depreciable * Decimal(days) / Decimal(total_days)).quantize(
                TWOPLACES, rounding=ROUND_HALF_UP
            )
        else:
            amount = per
        running += amount
        rows.append((d, amount, opening_accumulated + running))
        prev = d
    return rows


def written_down_value_schedule(
    *,
    gross: Decimal,
    salvage: Decimal,
    opening_accumulated: Decimal,
    number_of_depreciations: int,
    frequency_months: int,
    start_date: date,
    rate_pct: Decimal | None = None,
) -> list[tuple[date, Decimal, Decimal]]:
    """Declining-balance schedule. Each period writes down a fixed **rate** of the
    *opening book value*, so early years depreciate more than later ones.

    The rate is either given explicitly (``rate_pct`` — e.g. a statutory IT-Act block rate)
    or derived from the salvage ratio over the life (ERPNext: ``1 − (salvage/gross)^(1/n)``).
    The last row lands book value exactly on salvage. Returns ``(date, amount, accumulated)``.

    A *derived* rate needs salvage > 0 (a declining balance can't reach zero); an *explicit*
    rate works with any salvage (incl. 0 — the last row then zeroes the book).
    """
    if number_of_depreciations <= 0:
        raise ValidationError(
            "Asset Category needs a positive 'number of depreciations'",
            field="total_number_of_depreciations",
        )
    if salvage >= gross:
        raise ValidationError(
            "Salvage value must be below the asset's gross value", field="gross_purchase_amount"
        )
    n = number_of_depreciations
    if rate_pct is not None:
        if rate_pct <= ZERO:
            raise ValidationError("Rate of depreciation must be above 0%", field="rate_of_depreciation")
        rate = rate_pct / Decimal(100)
    else:
        if salvage <= ZERO:
            raise ValidationError(
                "Written Down Value needs a salvage value above 0% (or set an explicit Rate of "
                "Depreciation) — a declining balance never reaches zero",
                field="salvage_value_percent",
            )
        rate = Decimal(1) - (salvage / gross) ** (Decimal(1) / Decimal(n))
    opening_book = gross - opening_accumulated
    if opening_book <= salvage:
        raise ValidationError(
            "Asset is already at or below its salvage value", field="opening_accumulated_depreciation"
        )
    rows: list[tuple[date, Decimal, Decimal]] = []
    book = opening_book
    accumulated = opening_accumulated
    for i in range(1, n + 1):
        if i < n:
            amount = (book * rate).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
            if book - amount < salvage:  # never depreciate below salvage
                amount = book - salvage
            if amount < ZERO:
                amount = ZERO
        else:
            amount = book - salvage  # final row lands exactly on salvage
        book -= amount
        accumulated += amount
        rows.append((add_months(start_date, i * frequency_months), amount, accumulated))
    return rows


def _build_schedule_rows(
    asset: Asset, category: AssetCategory, payload: AssetCreate
) -> list[AssetDepreciationSchedule]:
    if category.is_non_depreciable:
        return []  # land / freehold: held at cost, no depreciation schedule
    salvage = (
        asset.gross_purchase_amount * category.salvage_value_percent / Decimal("100")
    ).quantize(TWOPLACES, rounding=ROUND_HALF_UP)

    if category.depreciation_method == "Manual":
        rows = payload.manual_schedule or []
        if not rows:
            raise ValidationError(
                "This category uses Manual depreciation — provide schedule rows", field="manual_schedule"
            )
        out: list[AssetDepreciationSchedule] = []
        running = asset.opening_accumulated_depreciation
        for idx, r in enumerate(sorted(rows, key=lambda x: x.schedule_date), start=1):
            running += r.depreciation_amount
            out.append(
                AssetDepreciationSchedule(
                    idx=idx, schedule_date=r.schedule_date,
                    depreciation_amount=r.depreciation_amount, accumulated_depreciation=running,
                )
            )
        return out

    common = dict(
        gross=asset.gross_purchase_amount,
        salvage=salvage,
        opening_accumulated=asset.opening_accumulated_depreciation,
        number_of_depreciations=category.total_number_of_depreciations,
        frequency_months=category.frequency_of_depreciation_months,
        start_date=asset.available_for_use_date,
    )
    if category.depreciation_method == "Straight Line":
        triples = straight_line_schedule(**common, daily_prorata=category.daily_prorata)
    elif category.depreciation_method == "Written Down Value":
        triples = written_down_value_schedule(**common, rate_pct=category.rate_of_depreciation)
    else:
        raise ValidationError(
            f"Unknown depreciation method '{category.depreciation_method}'",
            field="depreciation_method",
        )
    return [
        AssetDepreciationSchedule(
            idx=idx, schedule_date=d, depreciation_amount=amt, accumulated_depreciation=acc,
        )
        for idx, (d, amt, acc) in enumerate(triples, start=1)
    ]


# --- CRUD --------------------------------------------------------------------------


async def _get_category(db: AsyncSession, category_id: uuid.UUID, company_id: uuid.UUID) -> AssetCategory:
    cat = await db.get(AssetCategory, category_id)
    if cat is None or cat.company_id != company_id:
        raise NotFoundError("Asset Category not found")
    if cat.disabled:
        raise ValidationError("Asset Category is disabled", field="asset_category_id")
    return cat


async def get_asset(db: AsyncSession, asset_id: uuid.UUID, company_id: uuid.UUID | None) -> Asset:
    asset = await db.scalar(
        select(Asset)
        .options(selectinload(Asset.schedule), selectinload(Asset.movements))
        .where(Asset.id == asset_id, Asset.company_id == company_id)
    )
    if asset is None:
        raise NotFoundError("Asset not found")
    return asset


async def list_assets(
    db: AsyncSession,
    company_id: uuid.UUID | None,
    page: int = 1,
    page_size: int = 20,
    status: str | None = None,
    asset_category_id: uuid.UUID | None = None,
) -> tuple[list[Asset], int]:
    stmt = (
        select(Asset)
        .options(selectinload(Asset.schedule))
        .where(Asset.company_id == company_id)
        .order_by(Asset.available_for_use_date.desc(), Asset.creation.desc())
    )
    if status:
        stmt = stmt.where(Asset.status == status)
    if asset_category_id is not None:
        stmt = stmt.where(Asset.asset_category_id == asset_category_id)
    return await paginate(db, stmt, page, page_size)


async def create_asset(db: AsyncSession, payload: AssetCreate, user: CurrentUser) -> Asset:
    company = await get_company(db, user.company_id)
    category = await _get_category(db, payload.asset_category_id, company.id)
    if payload.location_id is not None:
        loc = await db.get(Location, payload.location_id)
        if loc is None or loc.company_id != company.id:
            raise NotFoundError("Location not found")

    name = await get_next_name(db, _SERIES, company.id, on_date=payload.available_for_use_date)
    asset = Asset(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        asset_name=payload.asset_name,
        asset_category_id=category.id,
        location_id=payload.location_id,
        custodian=payload.custodian,
        gross_purchase_amount=payload.gross_purchase_amount,
        opening_accumulated_depreciation=payload.opening_accumulated_depreciation,
        purchase_date=payload.purchase_date,
        available_for_use_date=payload.available_for_use_date,
        status="Draft",
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(asset)
    await db.flush()

    # generate the depreciation schedule now so it can be previewed in the draft
    for row in _build_schedule_rows(asset, category, payload):
        row.asset_id = asset.id
        db.add(row)
    await db.flush()
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    return await get_asset(db, asset.id, company.id)


async def submit_asset(db: AsyncSession, asset_id: uuid.UUID, user: CurrentUser) -> Asset:
    asset = await get_asset(db, asset_id, user.company_id)
    require_draft(asset.docstatus)
    # depreciation accounts must be set before the asset can depreciate (skipped for
    # non-depreciable categories like land, which never post depreciation)
    category = await _get_category(db, asset.asset_category_id, asset.company_id)
    if not category.is_non_depreciable:
        _require_accounts(category)
    asset.docstatus = DOCSTATUS_SUBMITTED
    asset.status = "Submitted"
    asset.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="SUBMIT",
        user_id=user.id, company_id=asset.company_id,
    )
    await db.commit()
    return await get_asset(db, asset.id, user.company_id)


async def cancel_asset(db: AsyncSession, asset_id: uuid.UUID, user: CurrentUser) -> Asset:
    """Cancel an asset. Blocked once any depreciation has posted — reversing booked
    depreciation belongs to Disposal (Phase 2), not a plain cancel."""
    asset = await get_asset(db, asset_id, user.company_id)
    require_submitted(asset.docstatus)
    if any(r.posted for r in asset.schedule):
        raise ValidationError(
            "Cannot cancel: depreciation has already been posted. Use Disposal (Phase 2).",
            code="ERR_DOCSTATUS",
        )
    asset.docstatus = DOCSTATUS_CANCELLED
    asset.status = "Cancelled"
    asset.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="CANCEL",
        user_id=user.id, company_id=asset.company_id,
    )
    await db.commit()
    return await get_asset(db, asset.id, user.company_id)


# --- depreciation posting (the core) -----------------------------------------------


def _require_accounts(category: AssetCategory) -> None:
    if not category.depreciation_expense_account_id or not category.accumulated_depreciation_account_id:
        raise ValidationError(
            f"Asset Category '{category.category_name}' is missing its Depreciation Expense / "
            "Accumulated Depreciation account",
            field="asset_category_id",
        )


def _update_status(asset: Asset) -> None:
    rows = asset.schedule
    if rows and all(r.posted for r in rows):
        asset.status = "Fully Depreciated"
    elif any(r.posted for r in rows):
        asset.status = "Partially Depreciated"
    else:
        asset.status = "Submitted"


async def _post_journal(
    db: AsyncSession, *, company_id: uuid.UUID, posting_date: date, voucher_type: str,
    lines: list[tuple[uuid.UUID, Decimal, Decimal]], remark: str, actor: CurrentUser,
) -> JournalEntry:
    """Write one balanced Journal Entry + its GL rows in the caller's transaction (flush
    only — the caller commits). ``lines`` is ``(account_id, debit, credit)`` per row. The
    GL voucher_type is always "Journal Entry" (so reversal works), while the JE carries the
    business label (``voucher_type``), exactly like the Journal Entry service."""
    total_debit = sum((d for _a, d, _c in lines), ZERO)
    total_credit = sum((c for _a, _d, c in lines), ZERO)
    name = await get_next_name(db, NAMING_SERIES["Journal Entry"], company_id, on_date=posting_date)
    je = JournalEntry(
        id=uuid.uuid4(), company_id=company_id, name=name, posting_date=posting_date,
        voucher_type=voucher_type, remarks=remark, total_debit=total_debit,
        total_credit=total_credit, docstatus=DOCSTATUS_SUBMITTED, owner=actor.id, modified_by=actor.id,
    )
    db.add(je)
    await db.flush()
    gl_rows: list[gl.GLRow] = []
    for idx, (account_id, debit, credit) in enumerate(lines, start=1):
        db.add(JournalEntryAccount(
            journal_entry_id=je.id, idx=idx, account_id=account_id,
            debit=debit, credit=credit,
            debit_in_account_currency=debit, credit_in_account_currency=credit,
        ))
        gl_rows.append(gl.GLRow(account_id=account_id, debit=debit, credit=credit, remarks=remark))
    await db.flush()
    await gl.make_gl_entries(
        db, company_id=company_id, voucher_type="Journal Entry", voucher_id=je.id,
        voucher_no=je.name, posting_date=posting_date, rows=gl_rows, user_id=actor.id, remarks=remark,
    )
    return je


async def depreciate_due(
    db: AsyncSession, asset: Asset, *, on_date: date, actor: CurrentUser
) -> DepreciateResult:
    """Post every due, unposted schedule row for ``asset`` (``schedule_date <= on_date``).

    Each row is posted + flagged in **one commit**, so a re-run skips already-posted rows
    (idempotent). A failing row (e.g. its date has no fiscal year) stops further posting
    for this asset without losing earlier work.
    """
    if asset.docstatus != DOCSTATUS_SUBMITTED:
        return DepreciateResult(asset_id=asset.id, posted_count=0, status=asset.status,
                                detail="Asset is not submitted")
    if asset.status in ("Sold", "Scrapped"):
        return DepreciateResult(asset_id=asset.id, posted_count=0, status=asset.status,
                                detail="Asset has been disposed")

    due = [r for r in asset.schedule if not r.posted and r.schedule_date <= on_date]
    if not due:  # nothing to post (incl. non-depreciable assets with no schedule)
        return DepreciateResult(asset_id=asset.id, posted_count=0, status=asset.status,
                                detail="Nothing due")
    category = await _get_category(db, asset.asset_category_id, asset.company_id)
    _require_accounts(category)
    await set_company_context(db, asset.company_id)

    je_ids: list[uuid.UUID] = []
    for row in due:
        try:
            remark = (
                f"Depreciation for {asset.asset_name} ({asset.name}) — {row.schedule_date.isoformat()}"
            )
            je = await _post_journal(
                db, company_id=asset.company_id, posting_date=row.schedule_date,
                voucher_type="Depreciation Entry",
                lines=[
                    (category.depreciation_expense_account_id, row.depreciation_amount, ZERO),
                    (category.accumulated_depreciation_account_id, ZERO, row.depreciation_amount),
                ],
                remark=remark, actor=actor,
            )
            row.posted = True
            row.posted_date = on_date
            row.journal_entry_id = je.id
            _update_status(asset)
            asset.modified_by = actor.id
            await log_audit(
                db, doctype="Asset", document_id=asset.id, action="UPDATE",
                user_id=actor.id, company_id=asset.company_id,
            )
            await db.commit()
            je_ids.append(je.id)
            logger.info("asset_depreciation_posted", asset=str(asset.id), journal_entry=str(je.id))
        except Exception:  # noqa: BLE001 — one bad row must not lose earlier postings
            await db.rollback()
            logger.exception("asset_depreciation_failed", asset=str(asset.id))
            break
    detail = None if je_ids else "Nothing due"
    return DepreciateResult(
        asset_id=asset.id, posted_count=len(je_ids), journal_entry_ids=je_ids,
        status=asset.status, detail=detail,
    )


async def depreciate_asset(
    db: AsyncSession, asset_id: uuid.UUID, user: CurrentUser, *, on_date: date | None = None
) -> DepreciateResult:
    """Manual trigger: post one asset's due depreciation now (same logic as the job)."""
    asset = await get_asset(db, asset_id, user.company_id)
    return await depreciate_due(db, asset, on_date=on_date or date.today(), actor=user)


async def cancel_depreciation(
    db: AsyncSession, asset_id: uuid.UUID, user: CurrentUser
) -> Asset:
    """Reverse **all** posted depreciation for an asset: write reversing GL entries for each
    depreciation Journal Entry, clear the rows' posted flags, and reopen the asset (status →
    Submitted). The ledger stays append-only (reversing entries, never deletes). Use this to
    correct a wrong schedule, then re-depreciate."""
    asset = await get_asset(db, asset_id, user.company_id)
    require_submitted(asset.docstatus)
    if asset.status in ("Sold", "Scrapped"):
        raise ValidationError(f"Cannot cancel depreciation of a {asset.status.lower()} asset",
                              field="status")
    posted = [r for r in asset.schedule if r.posted and r.journal_entry_id is not None]
    if not posted:
        raise ValidationError("No posted depreciation to cancel", field="status")

    await set_company_context(db, asset.company_id)
    for row in posted:
        await gl.make_reverse_gl_entries(
            db, voucher_type="Journal Entry", voucher_id=row.journal_entry_id, user_id=user.id
        )
        je = await db.get(JournalEntry, row.journal_entry_id)
        if je is not None:
            je.docstatus = DOCSTATUS_CANCELLED
        row.posted = False
        row.posted_date = None
        row.journal_entry_id = None
    _update_status(asset)
    asset.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="UPDATE",
        user_id=user.id, company_id=asset.company_id,
    )
    await db.commit()
    logger.info("asset_depreciation_cancelled", asset=str(asset.id), reversed=len(posted))
    return await get_asset(db, asset.id, user.company_id)


# --- disposal (sell / scrap) -------------------------------------------------------


async def dispose_asset(
    db: AsyncSession, asset_id: uuid.UUID, payload: AssetDisposeIn, user: CurrentUser
) -> Asset:
    """Sell or scrap an asset: remove its cost + accumulated depreciation and book the
    gain/loss vs book value as a Journal Entry. Depreciation then halts (the job/manual
    trigger skip Sold/Scrapped assets).

    Sale:   Dr Accumulated Dep, Dr Bank (proceeds), Cr Fixed Asset, ± Gain/Loss (plug).
    Scrap:  Dr Accumulated Dep, Dr Gain/Loss (the loss = book value), Cr Fixed Asset.
    """
    asset = await get_asset(db, asset_id, user.company_id)
    require_submitted(asset.docstatus)
    if asset.status in ("Sold", "Scrapped"):
        raise ValidationError(f"Asset is already {asset.status.lower()}", field="status")
    if payload.disposal_type not in ("Sell", "Scrap"):
        raise ValidationError("disposal_type must be 'Sell' or 'Scrap'", field="disposal_type")

    category = await _get_category(db, asset.asset_category_id, asset.company_id)
    if not category.fixed_asset_account_id or not category.accumulated_depreciation_account_id:
        raise ValidationError(
            f"Asset Category '{category.category_name}' needs a Fixed Asset + Accumulated "
            "Depreciation account to dispose",
            field="asset_category_id",
        )

    if payload.disposal_type == "Sell":
        if payload.sale_amount <= ZERO:
            raise ValidationError("A sale needs a sale amount above 0", field="sale_amount")
        if payload.proceeds_account_id is None:
            raise ValidationError(
                "A sale needs a proceeds account (the bank/cash account that received the money)",
                field="proceeds_account_id",
            )
        proceeds = payload.sale_amount
    else:  # Scrap
        proceeds = ZERO

    accumulated = asset.accumulated_depreciation  # opening + posted rows
    book_value = asset.gross_purchase_amount - accumulated
    gain_loss = proceeds - book_value  # positive = gain, negative = loss

    lines: list[tuple[uuid.UUID, Decimal, Decimal]] = []
    if accumulated > ZERO:
        lines.append((category.accumulated_depreciation_account_id, accumulated, ZERO))  # Dr
    if proceeds > ZERO:
        lines.append((payload.proceeds_account_id, proceeds, ZERO))  # Dr bank/cash
    lines.append((category.fixed_asset_account_id, ZERO, asset.gross_purchase_amount))  # Cr asset
    if gain_loss > ZERO:
        lines.append((payload.gain_loss_account_id, ZERO, gain_loss))  # Cr gain (income)
    elif gain_loss < ZERO:
        lines.append((payload.gain_loss_account_id, -gain_loss, ZERO))  # Dr loss

    await set_company_context(db, asset.company_id)
    remark = (
        f"Disposal ({payload.disposal_type}) of {asset.asset_name} ({asset.name}) — "
        f"book value {book_value}, proceeds {proceeds}"
    )
    je = await _post_journal(
        db, company_id=asset.company_id, posting_date=payload.disposal_date,
        voucher_type="Asset Disposal", lines=lines, remark=remark, actor=user,
    )
    asset.status = "Sold" if payload.disposal_type == "Sell" else "Scrapped"
    asset.disposal_date = payload.disposal_date
    asset.disposal_type = payload.disposal_type
    asset.disposal_amount = proceeds
    asset.gain_loss_amount = gain_loss
    asset.disposal_journal_entry_id = je.id
    asset.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="UPDATE",
        user_id=user.id, company_id=asset.company_id,
    )
    await db.commit()
    return await get_asset(db, asset.id, user.company_id)


# --- movement (location / custodian transfer, no GL) -------------------------------


async def move_asset(
    db: AsyncSession, asset_id: uuid.UUID, payload: AssetMoveIn, user: CurrentUser
) -> Asset:
    """Record a location/custodian transfer and update the asset (no GL)."""
    asset = await get_asset(db, asset_id, user.company_id)
    require_submitted(asset.docstatus)
    if asset.status in ("Sold", "Scrapped", "Cancelled"):
        raise ValidationError(f"Cannot move a {asset.status.lower()} asset", field="status")
    if payload.to_location_id is not None:
        loc = await db.get(Location, payload.to_location_id)
        if loc is None or loc.company_id != asset.company_id:
            raise NotFoundError("Location not found")

    # append via the relationship (not a bare db.add) so the asset's already-loaded
    # movements collection reflects the new row in the response after commit
    asset.movements.append(AssetMovement(
        company_id=asset.company_id,
        movement_date=payload.movement_date,
        from_location_id=asset.location_id,
        to_location_id=payload.to_location_id,
        from_custodian=asset.custodian,
        to_custodian=payload.to_custodian,
    ))
    asset.location_id = payload.to_location_id
    asset.custodian = payload.to_custodian
    asset.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="UPDATE",
        user_id=user.id, company_id=asset.company_id,
    )
    await db.commit()
    return await get_asset(db, asset.id, user.company_id)


# --- value adjustment (revalue + reschedule) ---------------------------------------


def _reschedule_unposted(asset: Asset, category: AssetCategory, new_book_value: Decimal) -> None:
    """Recompute the *unposted* schedule rows so they depreciate ``new_book_value`` down to
    salvage over the remaining periods, keeping their dates. Manual schedules are left as-is."""
    unposted = [r for r in asset.schedule if not r.posted]
    if not unposted or category.depreciation_method == "Manual":
        return
    salvage = (
        asset.gross_purchase_amount * category.salvage_value_percent / Decimal("100")
    ).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
    n = len(unposted)
    depreciable = max(new_book_value - salvage, ZERO)

    amounts: list[Decimal] = []
    if category.depreciation_method == "Written Down Value" and depreciable > ZERO:
        if category.rate_of_depreciation is not None and category.rate_of_depreciation > ZERO:
            rate = category.rate_of_depreciation / Decimal(100)
        else:
            rate = Decimal(1) - (salvage / new_book_value) ** (Decimal(1) / Decimal(n))
        book = new_book_value
        for i in range(1, n + 1):
            if i < n:
                amt = (book * rate).quantize(TWOPLACES, rounding=ROUND_HALF_UP)
                if book - amt < salvage:
                    amt = book - salvage
                if amt < ZERO:
                    amt = ZERO
            else:
                amt = book - salvage
            book -= amt
            amounts.append(amt)
    else:  # Straight Line (or nothing left to depreciate)
        per = (depreciable / n).quantize(TWOPLACES, rounding=ROUND_HALF_UP) if depreciable > ZERO else ZERO
        running = ZERO
        for i in range(1, n + 1):
            amt = per if i < n else (depreciable - running)
            running += amt
            amounts.append(amt)

    accumulated_base = asset.accumulated_depreciation  # real accumulated (incl. this adjustment)
    running = ZERO
    for row, amt in zip(unposted, amounts):
        running += amt
        row.depreciation_amount = amt
        row.accumulated_depreciation = accumulated_base + running


async def adjust_asset_value(
    db: AsyncSession, asset_id: uuid.UUID, payload: AssetValueAdjustIn, user: CurrentUser
) -> Asset:
    """Revalue an asset to a new book value, then reschedule remaining depreciation.

    * **Write-down (impairment):** value falls → Dr difference account (impairment loss) /
      Cr Accumulated Depreciation. Tracked on ``accumulated_depreciation_adjustment``.
    * **Write-up (appreciation / revaluation):** value rises → Dr the Fixed Asset account
      (the carrying value on the balance sheet goes up) / Cr the difference account (a
      **Revaluation Surplus** in equity — appreciation is *not* income). This is how an
      asset that gains value (e.g. land) is handled: you revalue it, the gain sits in
      equity. Increases the asset's gross/carrying value.
    """
    asset = await get_asset(db, asset_id, user.company_id)
    require_submitted(asset.docstatus)
    if asset.status in ("Sold", "Scrapped"):
        raise ValidationError(f"Cannot revalue a {asset.status.lower()} asset", field="status")
    category = await _get_category(db, asset.asset_category_id, asset.company_id)

    current_book = asset.book_value
    new_value = payload.new_asset_value
    difference = current_book - new_value  # positive = write-down, negative = write-up
    if difference == ZERO:
        raise ValidationError("New value equals the current book value — nothing to adjust",
                              field="new_asset_value")

    write_up = difference < ZERO
    if write_up and not category.fixed_asset_account_id:
        raise ValidationError(
            f"Asset Category '{category.category_name}' needs a Fixed Asset account to revalue up",
            field="asset_category_id",
        )
    if not write_up and not category.accumulated_depreciation_account_id:
        raise ValidationError(
            f"Asset Category '{category.category_name}' needs an Accumulated Depreciation account",
            field="asset_category_id",
        )

    if write_up:  # appreciation: raise carrying value, credit revaluation surplus
        amt = -difference
        lines = [
            (category.fixed_asset_account_id, amt, ZERO),  # Dr fixed asset (carrying value ↑)
            (payload.difference_account_id, ZERO, amt),  # Cr revaluation surplus (equity)
        ]
    else:  # impairment: reduce carrying value via accumulated depreciation
        lines = [
            (payload.difference_account_id, difference, ZERO),  # Dr impairment loss
            (category.accumulated_depreciation_account_id, ZERO, difference),  # Cr accum dep
        ]

    await set_company_context(db, asset.company_id)
    remark = (
        f"Value adjustment of {asset.asset_name} ({asset.name}): {current_book} → {new_value}"
    )
    je = await _post_journal(
        db, company_id=asset.company_id, posting_date=payload.adjustment_date,
        voucher_type="Asset Value Adjustment", lines=lines, remark=remark, actor=user,
    )
    if write_up:
        asset.gross_purchase_amount += -difference  # carrying value rises
    else:
        asset.accumulated_depreciation_adjustment += difference  # accumulated dep rises
    _reschedule_unposted(asset, category, new_value)
    asset.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="UPDATE",
        user_id=user.id, company_id=asset.company_id,
    )
    await db.commit()
    logger.info("asset_value_adjusted", asset=str(asset.id), journal_entry=str(je.id))
    return await get_asset(db, asset.id, user.company_id)


# --- capitalization (build an asset from costed components) -------------------------


async def capitalize_asset(
    db: AsyncSession, payload: AssetCapitalizeIn, user: CurrentUser
) -> Asset:
    """Build a new (submitted) asset by capitalising costed components into the category's
    Fixed Asset account: Dr Fixed Asset (total) / Cr each component's source account. The
    sources can be a CWIP account (clears capital-work-in-progress), Stock In Hand (parts
    consumed), a labour/expense account, Bank, etc. The asset is created live with its
    depreciation schedule (none for non-depreciable categories)."""
    company = await get_company(db, user.company_id)
    category = await _get_category(db, payload.asset_category_id, company.id)
    if not category.fixed_asset_account_id:
        raise ValidationError(
            f"Asset Category '{category.category_name}' needs a Fixed Asset account to capitalise into",
            field="asset_category_id",
        )
    if payload.location_id is not None:
        loc = await db.get(Location, payload.location_id)
        if loc is None or loc.company_id != company.id:
            raise NotFoundError("Location not found")

    total = sum((c.amount for c in payload.components), ZERO)
    if total <= ZERO:
        raise ValidationError("Capitalisation needs at least one positive component", field="components")
    in_use = payload.available_for_use_date or payload.posting_date

    name = await get_next_name(db, _SERIES, company.id, on_date=payload.posting_date)
    asset = Asset(
        id=uuid.uuid4(),
        company_id=company.id,
        name=name,
        asset_name=payload.asset_name,
        asset_category_id=category.id,
        location_id=payload.location_id,
        custodian=payload.custodian,
        gross_purchase_amount=total,
        purchase_date=payload.posting_date,
        available_for_use_date=in_use,
        status="Submitted",
        docstatus=DOCSTATUS_SUBMITTED,
        remarks=payload.remarks,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(asset)
    await db.flush()

    sched_payload = AssetCreate(
        asset_name=asset.asset_name, asset_category_id=category.id,
        gross_purchase_amount=total, available_for_use_date=in_use,
    )
    for row in _build_schedule_rows(asset, category, sched_payload):
        row.asset_id = asset.id
        db.add(row)
    await db.flush()

    await set_company_context(db, company.id)
    remark = f"Capitalization of {asset.asset_name} ({asset.name})"
    lines: list[tuple[uuid.UUID, Decimal, Decimal]] = [
        (category.fixed_asset_account_id, total, ZERO)  # Dr the new fixed asset
    ]
    lines += [(c.account_id, ZERO, c.amount) for c in payload.components]  # Cr each source
    await _post_journal(
        db, company_id=company.id, posting_date=payload.posting_date,
        voucher_type="Asset Capitalization", lines=lines, remark=remark, actor=user,
    )
    await log_audit(
        db, doctype="Asset", document_id=asset.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    logger.info("asset_capitalized", asset=str(asset.id))
    return await get_asset(db, asset.id, company.id)


# --- auto-create from a fixed-asset Purchase Invoice line --------------------------


async def create_assets_from_purchase_invoice(db: AsyncSession, invoice, user: CurrentUser) -> int:
    """Create one **draft** Asset per fixed-asset line on a submitted Purchase Invoice.

    Decoupled from the invoice's own GL (the line already debits the item's account — point
    that at the Fixed Asset COA account). The user reviews + submits the draft Asset to start
    depreciation. Returns how many assets were created. Best-effort: the caller wraps this so
    a failure here never rolls back the invoice.
    """
    from app.models.stock import Item

    created = 0
    for line in invoice.items:
        if line.item_id is None:
            continue
        item = await db.get(Item, line.item_id)
        if item is None or not item.is_fixed_asset or item.asset_category_id is None:
            continue
        category = await db.get(AssetCategory, item.asset_category_id)
        if category is None or category.company_id != invoice.company_id:
            continue
        gross = Decimal(line.base_net_amount)
        if gross <= ZERO:
            continue
        name = await get_next_name(db, _SERIES, invoice.company_id, on_date=invoice.posting_date)
        asset = Asset(
            id=uuid.uuid4(),
            company_id=invoice.company_id,
            name=name,
            asset_name=item.item_name,
            asset_category_id=category.id,
            gross_purchase_amount=gross,
            purchase_date=invoice.posting_date,
            available_for_use_date=invoice.posting_date,
            status="Draft",
            remarks=f"Auto-created from Purchase Invoice {invoice.name}",
            owner=user.id,
            modified_by=user.id,
        )
        db.add(asset)
        await db.flush()
        payload = AssetCreate(
            asset_name=item.item_name, asset_category_id=category.id,
            gross_purchase_amount=gross, available_for_use_date=invoice.posting_date,
        )
        for row in _build_schedule_rows(asset, category, payload):
            row.asset_id = asset.id
            db.add(row)
        await db.flush()
        await log_audit(
            db, doctype="Asset", document_id=asset.id, action="INSERT",
            user_id=user.id, company_id=invoice.company_id,
        )
        created += 1
    if created:
        await db.commit()
    return created
