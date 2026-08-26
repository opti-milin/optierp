"""Journal Entry service — Module 02.

Source: erpnext/accounts/doctype/journal_entry. Draft -> submit posts GL;
cancel writes reversing GL entries.
"""

import uuid
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import NotFoundError, ValidationError
from app.core.naming import get_next_name
from app.core.security import CurrentUser
from app.models.accounts import Account, JournalEntry, JournalEntryAccount
from app.models.base import DOCSTATUS_CANCELLED, DOCSTATUS_SUBMITTED
from app.schemas.accounts import JournalEntryCreate
from app.services import gl
from app.services.accounts_common import NAMING_SERIES, require_draft, require_submitted
from app.services.audit import log_audit
from app.services.pagination import paginate

ZERO = Decimal("0")


async def create_journal_entry(
    db: AsyncSession, payload: JournalEntryCreate, user: CurrentUser
) -> JournalEntry:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    if len(payload.accounts) < 2:
        raise ValidationError("A Journal Entry needs at least two account rows", field="accounts")

    total_debit = sum((row.debit for row in payload.accounts), ZERO)
    total_credit = sum((row.credit for row in payload.accounts), ZERO)
    if total_debit != total_credit:
        raise ValidationError(
            f"Total debit ({total_debit}) must equal total credit ({total_credit})",
            field="accounts",
        )
    if total_debit == ZERO:
        raise ValidationError("Journal Entry cannot be for zero amount", field="accounts")

    # party is mandatory on receivable/payable rows (mirrors ERPNext validation)
    account_ids = [row.account_id for row in payload.accounts]
    accounts = {
        a.id: a
        for a in (await db.execute(select(Account).where(Account.id.in_(account_ids)))).scalars()
    }
    for row in payload.accounts:
        account = accounts.get(row.account_id)
        if account is None:
            raise NotFoundError(f"Account {row.account_id} not found")
        if account.account_type in ("Receivable", "Payable") and not (
            row.party_type and row.party_id
        ):
            raise ValidationError(
                f"Party is required for {account.account_type} account '{account.account_name}'",
                field="accounts",
            )

    name = await get_next_name(db, NAMING_SERIES["Journal Entry"], user.company_id)
    entry = JournalEntry(
        id=uuid.uuid4(),
        company_id=user.company_id,
        name=name,
        posting_date=payload.posting_date,
        voucher_type=payload.voucher_type,
        remarks=payload.remarks,
        total_debit=total_debit,
        total_credit=total_credit,
        multi_currency=payload.multi_currency,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(entry)
    await db.flush()
    for idx, row in enumerate(payload.accounts, start=1):
        db.add(
            JournalEntryAccount(
                journal_entry_id=entry.id,
                idx=idx,
                account_id=row.account_id,
                party_type=row.party_type,
                party_id=row.party_id,
                cost_center_id=row.cost_center_id,
                account_currency=row.account_currency,
                exchange_rate=row.exchange_rate,
                debit=row.debit,
                credit=row.credit,
                debit_in_account_currency=row.debit_in_account_currency or row.debit,
                credit_in_account_currency=row.credit_in_account_currency or row.credit,
                reference_type=row.reference_type,
                reference_id=row.reference_id,
                user_remark=row.user_remark,
            )
        )
    await db.flush()
    await log_audit(
        db, doctype="Journal Entry", document_id=entry.id, action="INSERT",
        user_id=user.id, company_id=user.company_id,
    )
    await db.commit()
    return await get_journal_entry(db, entry.id, user.company_id)


async def get_journal_entry(
    db: AsyncSession, entry_id: uuid.UUID, company_id: uuid.UUID | None
) -> JournalEntry:
    entry = await db.scalar(
        select(JournalEntry)
        .options(selectinload(JournalEntry.accounts))
        .where(JournalEntry.id == entry_id, JournalEntry.company_id == company_id)
    )
    if entry is None:
        raise NotFoundError("Journal Entry not found")
    return entry


async def list_journal_entries(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    docstatus: int | None = None,
) -> tuple[list[JournalEntry], int]:
    stmt = (
        select(JournalEntry)
        .where(JournalEntry.company_id == company_id)
        .order_by(JournalEntry.posting_date.desc(), JournalEntry.creation.desc())
    )
    if docstatus is not None:
        stmt = stmt.where(JournalEntry.docstatus == docstatus)
    else:
        # Cancelled entries stay in the books for audit but are noise here;
        # ask for them with ?docstatus=2.
        stmt = stmt.where(JournalEntry.docstatus != DOCSTATUS_CANCELLED)
    return await paginate(db, stmt, page, page_size)


async def submit_journal_entry(
    db: AsyncSession, entry_id: uuid.UUID, user: CurrentUser
) -> JournalEntry:
    entry = await get_journal_entry(db, entry_id, user.company_id)
    require_draft(entry.docstatus)

    rows = [
        gl.GLRow(
            account_id=row.account_id,
            debit=row.debit,
            credit=row.credit,
            party_type=row.party_type,
            party_id=row.party_id,
            cost_center_id=row.cost_center_id,
            account_currency=row.account_currency,
            debit_in_account_currency=row.debit_in_account_currency,
            credit_in_account_currency=row.credit_in_account_currency,
            against_voucher_type=row.reference_type,
            against_voucher_id=row.reference_id,
            remarks=row.user_remark,
        )
        for row in entry.accounts
    ]
    await gl.make_gl_entries(
        db,
        company_id=entry.company_id,
        voucher_type="Journal Entry",
        voucher_id=entry.id,
        voucher_no=entry.name,
        posting_date=entry.posting_date,
        rows=rows,
        user_id=user.id,
        remarks=entry.remarks,
    )
    entry.docstatus = DOCSTATUS_SUBMITTED
    entry.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Journal Entry", document_id=entry.id, action="SUBMIT",
        user_id=user.id, company_id=entry.company_id,
    )
    await db.commit()
    return await get_journal_entry(db, entry.id, user.company_id)


async def cancel_journal_entry(
    db: AsyncSession, entry_id: uuid.UUID, user: CurrentUser
) -> JournalEntry:
    entry = await get_journal_entry(db, entry_id, user.company_id)
    require_submitted(entry.docstatus)
    await gl.make_reverse_gl_entries(
        db, voucher_type="Journal Entry", voucher_id=entry.id, user_id=user.id
    )
    entry.docstatus = DOCSTATUS_CANCELLED
    entry.modified_by = user.id
    await db.flush()
    # A cancelled entry can no longer back a bank reconciliation — free any line
    # matched to it (lazy import avoids a module-level cycle).
    from app.services import bank_reconciliation_tool

    await bank_reconciliation_tool.release_matched_transactions(
        db, "Journal Entry", entry.id, user_id=user.id
    )
    await log_audit(
        db, doctype="Journal Entry", document_id=entry.id, action="CANCEL",
        user_id=user.id, company_id=entry.company_id,
    )
    await db.commit()
    return await get_journal_entry(db, entry.id, user.company_id)


async def set_clearance_date(
    db: AsyncSession, entry_id: uuid.UUID, clearance_date, user: CurrentUser
) -> JournalEntry:
    """Bank reconciliation: mark when the bank cleared this entry."""
    entry = await get_journal_entry(db, entry_id, user.company_id)
    require_submitted(entry.docstatus)
    entry.clearance_date = clearance_date
    entry.modified_by = user.id
    await db.commit()
    return await get_journal_entry(db, entry.id, user.company_id)
