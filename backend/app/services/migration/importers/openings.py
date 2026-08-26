"""Opening balances — the state of the books at the migration cut-off.

Tally does not ship opening balances as vouchers. It hangs them off the master
itself: a ledger carries ``OPENINGBALANCE``, a stock item carries that plus
``OPENINGVALUE``. The parser lifts them out into their own records (see
``_opening_record``) and this module turns them into documents.

This is the part of a migration that decides whether the result is usable.
Vouchers alone reproduce the *movements* for a period; without the openings the
books start from zero, so every ledger balance, every debtor and creditor
outstanding, and every stock quantity is wrong from the first day.

Two documents, not hundreds:

* every ledger opening becomes one balanced **Journal Entry**, because an
  accountant opening the books expects a single opening voucher; and
* every stock opening becomes one **Stock Reconciliation** with purpose
  ``Opening Stock``.

Each master is still resolved individually first, so one unmappable ledger fails
on its own staging row and the rest of the opening still posts.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any

from app.models.accounts import Account
from app.models.accounts.common import ROOT_TYPE_REPORT
from app.models.migration import MigrationStagingRecord
from app.services.migration.catalogue import normalise_unit
from app.services.migration.context import ImportContext
from app.services.migration.importers.masters import _account_by_name, _resolve_root
from app.services.migration.sources.tally_xml import parse_amount

ZERO = Decimal("0")
_QTY = re.compile(r"^\s*(-?[\d,]*\.?\d+)\s*(.*)$")


def _split_opening_qty(value: str | None) -> tuple[Decimal, str | None]:
    """Tally writes an opening stock balance as "10 Nos" — split it."""
    text = (value or "").strip()
    if not text:
        return ZERO, None
    match = _QTY.match(text)
    if match is None:
        return ZERO, None
    unit = (match.group(2) or "").strip()
    return parse_amount(match.group(1)), (normalise_unit(unit) if unit else None)


async def _temporary_opening_account(context: ImportContext) -> Account:
    """The contra side of an opening entry.

    Every opening balance needs somewhere for its other leg to go. Both shipped
    Charts of Accounts include "Temporary Opening" for exactly this; if a company
    has neither, create it rather than refuse the whole migration.
    """
    existing = await _account_by_name(context, "Temporary Opening")
    if existing is not None and not existing.is_group:
        return existing

    root = await _resolve_root(context, "Asset")
    account = Account(
        id=uuid.uuid4(),
        company_id=context.company_id,
        account_name="Temporary Opening",
        parent_account_id=root.id,
        root_type=root.root_type,
        report_type=ROOT_TYPE_REPORT[root.root_type],
        account_type="Temporary",
        is_group=False,
        account_currency=context.company.default_currency,
        path=f"{root.path}.temporary_opening",
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(account)
    await context.db.flush()
    return account


def _fail(context: ImportContext, record: MigrationStagingRecord, message: str) -> None:
    """Mark one master's opening as unusable without losing the rest."""
    context.message(record, "error", message)
    record.status = "Error"
    context.counter(record.entity_key).failed += 1


async def _fallback_warehouse(context: ImportContext) -> uuid.UUID | None:
    """Where opening stock goes when Tally names no godown.

    Tally companies that never enabled godowns still hold stock; it just has no
    location. Prefer whatever the import was configured with, else the only
    warehouse the company has — guessing between several would be worse than
    reporting it.
    """
    from sqlalchemy import select

    from app.models.stock import Warehouse

    configured = context.option("default_warehouse_id")
    if configured:
        return uuid.UUID(str(configured))

    rows = (
        await context.db.execute(
            select(Warehouse.id).where(
                Warehouse.company_id == context.company_id,
                Warehouse.is_group.is_(False),
            ).limit(2)
        )
    ).scalars().all()
    return rows[0] if len(rows) == 1 else None


def _opening_stock_rows(
    context: ImportContext, raw: dict[str, Any], fallback: uuid.UUID | None
) -> list[tuple[uuid.UUID | None, Decimal, Decimal, str | None]]:
    """(warehouse_id, qty, value, uom) per godown for one stock item.

    Tally splits an item's opening stock across godowns in
    ``BATCHALLOCATIONS.LIST``, each with its own quantity and value. Collapsing
    them to a single figure would put all the stock in one place and misstate
    every per-warehouse report, so each godown becomes its own row.
    """
    from app.services.migration.sources.tally_xml import as_list, text_of

    data = raw.get("data") if isinstance(raw.get("data"), dict) else {}
    rows: list[tuple[uuid.UUID | None, Decimal, Decimal, str | None]] = []

    for node in as_list(data.get("BATCHALLOCATIONS.LIST")):
        if not isinstance(node, dict):
            continue
        qty, uom = _split_opening_qty(text_of(node, "OPENINGBALANCE"))
        if qty == ZERO:
            continue
        godown = text_of(node, "GODOWNNAME")
        warehouse_id = context.book.warehouse(godown) if godown else fallback
        rows.append(
            (
                warehouse_id or fallback,
                abs(qty),
                abs(parse_amount(text_of(node, "OPENINGVALUE"))),
                uom,
            )
        )
    if rows:
        return rows

    # No godown breakdown — a single unplaced balance on the item itself.
    qty, uom = _split_opening_qty(raw.get("opening_balance"))
    if qty == ZERO:
        return []
    return [(fallback, abs(qty), abs(parse_amount(raw.get("opening_value"))), uom)]


async def import_opening_ledgers(
    context: ImportContext, records: list[MigrationStagingRecord]
) -> None:
    """Ledger opening balances -> one balanced opening Journal Entry."""
    from app.schemas.accounts.journal import JournalEntryAccountIn, JournalEntryCreate
    from app.services import journal_entry as journal_entry_service

    posting_date = context.opening_date
    if posting_date is None:
        for record in records:
            context.skip(
                record,
                "No migration cut-off date on this import, so there is no date to "
                "book the opening balance on. Set one and run again.",
            )
        return

    lines: list[Any] = []
    contributing: list[MigrationStagingRecord] = []
    total_debit = ZERO
    total_credit = ZERO

    for record in records:
        name = (record.source_name or "").strip()
        amount = parse_amount((record.raw or {}).get("opening_balance"))
        if not name or amount == ZERO:
            context.skip(record, "No opening balance on this ledger.")
            continue

        account_id = context.book.account(name)
        if account_id is None:
            _fail(
                context, record,
                f"Ledger '{name}' is not mapped to an account, so its opening "
                "balance was left out of the opening entry.",
            )
            continue

        # Same sign convention as every other Tally amount: negative = debit.
        debit = abs(amount) if amount < ZERO else ZERO
        credit = amount if amount > ZERO else ZERO
        total_debit += debit
        total_credit += credit

        # A party row must carry its party or the balance never reaches the
        # ageing reports — the outstanding would sit in the control account with
        # nothing to attribute it to.
        party_type = context.book.party_type_of(name)
        party_id = (
            context.book.customer(name)
            if party_type == "Customer"
            else context.book.supplier(name)
            if party_type == "Supplier"
            else None
        )
        lines.append(
            JournalEntryAccountIn(
                account_id=account_id,
                debit=debit,
                credit=credit,
                party_type=party_type if party_id else None,
                party_id=party_id,
                user_remark=f"Opening balance from Tally: {name}"[:180],
            )
        )
        contributing.append(record)

    if not lines:
        return

    # Tally's own trial balance is rarely exactly square once a subset of
    # ledgers is mapped, so the difference goes to Temporary Opening rather than
    # failing the entry. That is what the account is for, and it leaves the
    # discrepancy visible in one place instead of silently absorbed.
    difference = total_debit - total_credit
    if difference != ZERO:
        opening_account = await _temporary_opening_account(context)
        lines.append(
            JournalEntryAccountIn(
                account_id=opening_account.id,
                debit=ZERO if difference > ZERO else -difference,
                credit=difference if difference > ZERO else ZERO,
                user_remark="Opening balance contra (Tally migration)",
            )
        )
        context.log(
            "run",
            f"Opening balances were out by {abs(difference)}; the difference was "
            "posted to Temporary Opening.",
            level="warning" if abs(difference) > ZERO else "info",
            entity_key="opening_ledger",
        )

    if len(lines) < 2:
        for record in contributing:
            context.skip(
                record,
                "A single opening balance cannot form a balanced entry on its own.",
            )
        return

    try:
        entry = await journal_entry_service.create_journal_entry(
            context.db,
            JournalEntryCreate(
                posting_date=posting_date,
                remarks="Opening balances imported from Tally",
                accounts=lines,
            ),
            context.user,
        )
        if context.option("submit_vouchers", True):
            entry = await journal_entry_service.submit_journal_entry(
                context.db, entry.id, context.user
            )
    except Exception as exc:  # noqa: BLE001 - one document, many rows to tell
        # These rows are not wrapped in context.row() — they contribute to a
        # single document — so nothing above would catch this, and letting it
        # escape aborts the whole run at the openings phase.
        await context.db.rollback()
        for record in contributing:
            _fail(context, record, f"Opening entry could not be posted: {exc}")
        return
    for record in contributing:
        context.created(record, "Journal Entry", entry.id, entry.name)


async def import_opening_stock(
    context: ImportContext, records: list[MigrationStagingRecord]
) -> None:
    """Stock item opening balances -> one opening Stock Reconciliation."""
    from app.schemas.stock import StockReconciliationCreate, StockReconciliationItemIn
    from app.services import stock_reconciliation as recon_service

    posting_date = context.opening_date
    if posting_date is None:
        for record in records:
            context.skip(
                record,
                "No migration cut-off date on this import, so there is no date to "
                "book the opening stock on. Set one and run again.",
            )
        return

    fallback_warehouse = await _fallback_warehouse(context)
    items: list[Any] = []
    contributing: list[MigrationStagingRecord] = []

    for record in records:
        name = (record.source_name or "").strip()
        raw = record.raw or {}
        item_id = context.book.item(name) if name else None
        if item_id is None:
            _fail(
                context, record,
                f"Stock item '{name}' is not mapped, so its opening stock was "
                "left out of the opening reconciliation.",
            )
            continue

        rows = _opening_stock_rows(context, raw, fallback_warehouse)
        if not rows:
            context.skip(record, "No opening stock on this item.")
            continue

        unplaced = [r for r in rows if r[0] is None]
        if unplaced:
            _fail(
                context, record,
                f"'{name}' has opening stock but no warehouse to put it in. Import "
                "the godowns from Tally, or set a default warehouse on this import.",
            )
            continue

        for warehouse_id, quantity, value, uom in rows:
            items.append(
                StockReconciliationItemIn(
                    item_id=item_id,
                    warehouse_id=warehouse_id,
                    qty=quantity,
                    # Rate from the value Tally already computed rather than
                    # OPENINGRATE's "500.00/Nos" form — one less thing to
                    # misparse, and qty x rate keeps agreeing with Tally.
                    valuation_rate=(value / quantity) if value and quantity else None,
                    uom=uom,
                )
            )
        contributing.append(record)

    if not items:
        return

    try:
        recon = await recon_service.create_stock_reconciliation(
            context.db,
            StockReconciliationCreate(
                purpose="Opening Stock",
                posting_date=posting_date,
                remarks="Opening stock imported from Tally",
                items=items,
            ),
            context.user,
        )
        if context.option("submit_vouchers", True):
            recon = await recon_service.submit_stock_reconciliation(
                context.db, recon.id, context.user
            )
    except Exception as exc:  # noqa: BLE001 - one document, many rows to tell
        # These rows are not wrapped in context.row() — they contribute to a
        # single document — so nothing above would catch this. Letting it escape
        # aborts the whole run at the openings phase.
        await context.db.rollback()
        for record in contributing:
            _fail(context, record, f"Opening stock could not be posted: {exc}")
        return
    for record in contributing:
        context.created(record, "Stock Reconciliation", recon.id, recon.name)
