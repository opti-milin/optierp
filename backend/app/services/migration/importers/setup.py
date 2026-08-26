"""Setup masters that surround the ledger — and the party fields that hang off them.

Everything here already existed as a table before this module did. Payment terms,
tax templates, currencies, bank accounts, budgets and fixed assets are all
ordinary OptiERP masters; what was missing was a path from a spreadsheet column
to them, so a workbook that carried them had nowhere to put them and the sheet
was reported as having no target. That report was wrong, and this closes it.

Two rules, the same ones the rest of the importer follows:

**Match before you make.** Every ``ensure_*`` helper looks for an existing record
of that name first. A migration into a company that already has "Net 30" must
reuse it, not create a second one that silently splits the customers between them.

**Say when you invent.** A master the source *implies* but does not ship — the
Tax Category behind Zoho's ``gst_treatment``, the Customer Group behind a party
type — is created, and the row carries a warning naming it. The accountant then
sees a short list of what appeared rather than discovering it months later.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select

from app.core.exceptions import ValidationError
from app.core.naming import get_next_name
from app.models.accounts import Account
from app.models.accounts.budget import Budget, BudgetAccount
from app.models.accounts.masters import (
    Bank,
    BankAccount,
    FiscalYear,
    PaymentTerm,
    PaymentTermsTemplate,
    PaymentTermsTemplateDetail,
    TaxCategory,
    TaxTemplate,
    TaxTemplateDetail,
)
from app.models.accounts.payments import BankTransaction
from app.models.assets import Asset, AssetCategory, Location
from app.models.buying import SupplierGroup
from app.models.core import Currency, CurrencyExchange
from app.models.migration import MigrationStagingRecord
from app.models.selling import CustomerGroup, Territory
from app.services.migration.context import ImportContext
from app.services.accounts_common import NAMING_SERIES
from app.services.asset import ASSET_SERIES
from app.services.migration.sources.tally_xml import as_list, parse_amount, parse_int, text_of
from app.services.tree import slugify

ZERO = Decimal("0")

#: Imported records are numbered by the *same* series the app uses when it makes
#: one itself. A migration that invented its own prefix would leave a company
#: with two numbering schemes for one doctype and two independent counters.
BANK_TXN_SERIES = NAMING_SERIES["Bank Transaction"]


def _raw(record: MigrationStagingRecord) -> dict[str, Any]:
    data = (record.raw or {}).get("data")
    return data if isinstance(data, dict) else {}


def _name(record: MigrationStagingRecord) -> str:
    return (record.source_name or "").strip()


# --------------------------------------------------------------------------------------
# Masters a source implies rather than ships
# --------------------------------------------------------------------------------------
#
# These four are reached from `import_parties` as well as from their own sheets,
# which is why they live here rather than beside one caller. Each is
# match-then-create, and each reports the create.


async def ensure_payment_terms(
    context: ImportContext, name: str | None, *, credit_days: int | None = None
) -> uuid.UUID | None:
    """A Payment Terms Template of this name, created with one full-value term.

    ``credit_days`` is a fallback used only when the name has to be invented and
    the source gave no terms sheet: "Net 30" then means 30 days. Guessing days
    from a name we did not import is better than a template that says nothing,
    but it is still a guess, so it is reported.
    """
    label = (name or "").strip()
    if not label:
        return None
    existing = (
        await context.db.execute(
            select(PaymentTermsTemplate).where(
                PaymentTermsTemplate.company_id == context.company_id,
                PaymentTermsTemplate.template_name == label,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id

    days = credit_days if credit_days is not None else _days_in(label)
    template = PaymentTermsTemplate(
        id=uuid.uuid4(),
        company_id=context.company_id,
        template_name=label[:140],
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(template)
    await context.db.flush()
    context.db.add(
        PaymentTermsTemplateDetail(
            id=uuid.uuid4(),
            template_id=template.id,
            description=label[:140],
            invoice_portion=Decimal("100"),
            due_date_based_on="Day(s) after invoice date",
            credit_days=days or 0,
            idx=0,
            owner=context.user.id,
            modified_by=context.user.id,
        )
    )
    context.warn(
        f"Payment terms '{label}' did not exist here, so a template was created "
        f"due {days or 0} day(s) after the invoice date.",
        "payment_terms",
    )
    return template.id


def _days_in(label: str) -> int | None:
    """``"Net 45"`` -> 45. Returns None when the name carries no number."""
    digits = "".join(ch for ch in label if ch.isdigit())
    return int(digits) if digits else None


async def ensure_tax_category(context: ImportContext, title: str | None) -> uuid.UUID | None:
    label = (title or "").strip()
    if not label:
        return None
    existing = (
        await context.db.execute(
            select(TaxCategory).where(
                TaxCategory.company_id == context.company_id, TaxCategory.title == label
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id
    category = TaxCategory(
        id=uuid.uuid4(),
        company_id=context.company_id,
        title=label[:140],
        # Inter-state is decided per invoice from the party's GSTIN against the
        # company's, so a category imported from another system's vocabulary must
        # not pre-empt that. False is the neutral value, not a claim.
        is_inter_state=False,
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(category)
    await context.db.flush()
    context.warn(f"Tax category '{label}' did not exist here and was created.", "tax_category")
    return category.id


async def _ensure_tree_node(
    context: ImportContext,
    model: Any,
    name_field: str,
    parent_field: str,
    label: str,
    warn_as: str,
) -> uuid.UUID | None:
    existing = (
        await context.db.execute(
            select(model).where(
                model.company_id == context.company_id,
                getattr(model, name_field) == label,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id
    # File it under the tree's existing root when there is one, so the new node
    # joins the tree rather than starting a second one beside it.
    root = (
        await context.db.execute(
            select(model).where(
                model.company_id == context.company_id,
                getattr(model, parent_field).is_(None),
            )
        )
    ).scalars().first()
    if root is not None and not root.is_group:
        root.is_group = True
    slug = slugify(label)
    node = model(
        id=uuid.uuid4(),
        company_id=context.company_id,
        is_group=False,
        path=f"{root.path}.{slug}" if root is not None else slug,
        owner=context.user.id,
        modified_by=context.user.id,
        **{name_field: label[:140], parent_field: root.id if root is not None else None},
    )
    context.db.add(node)
    await context.db.flush()
    context.warn(f"{warn_as} '{label}' did not exist here and was created.", warn_as.lower())
    return node.id


async def ensure_customer_group(context: ImportContext, name: str | None) -> uuid.UUID | None:
    label = (name or "").strip()
    if not label:
        return None
    return await _ensure_tree_node(
        context, CustomerGroup, "customer_group_name", "parent_customer_group_id",
        label, "Customer group",
    )


async def ensure_supplier_group(context: ImportContext, name: str | None) -> uuid.UUID | None:
    label = (name or "").strip()
    if not label:
        return None
    return await _ensure_tree_node(
        context, SupplierGroup, "supplier_group_name", "parent_supplier_group_id",
        label, "Supplier group",
    )


async def ensure_territory(context: ImportContext, name: str | None) -> uuid.UUID | None:
    label = (name or "").strip()
    if not label:
        return None
    return await _ensure_tree_node(
        context, Territory, "territory_name", "parent_territory_id", label, "Territory",
    )


# --------------------------------------------------------------------------------------
# Payment terms
# --------------------------------------------------------------------------------------


async def import_payment_terms(
    context: ImportContext, records: list[MigrationStagingRecord]
) -> None:
    """One template per name, with a term row per instalment the sheet listed."""
    for record in records:
        async with context.row(record):
            name = _name(record)
            if not name:
                context.skip(record, "The payment term has no name")
                continue
            data = _raw(record)
            existing = (
                await context.db.execute(
                    select(PaymentTermsTemplate).where(
                        PaymentTermsTemplate.company_id == context.company_id,
                        PaymentTermsTemplate.template_name == name,
                    )
                )
            ).scalars().first()
            if existing is not None:
                await context.book.bind(
                    context.db, "payment_terms", name, existing.id, name
                )
                context.reused(record, "Payment Terms Template", existing.id, name)
                continue

            template = PaymentTermsTemplate(
                id=uuid.uuid4(),
                company_id=context.company_id,
                template_name=name[:140],
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(template)
            await context.db.flush()

            rows = [row for row in as_list(data.get("PAYMENTTERM.LIST")) if isinstance(row, dict)]
            for idx, row in enumerate(rows or [{}]):
                description = text_of(row, "DESCRIPTION") or name
                days = parse_int(text_of(row, "CREDITDAYS")) or 0
                portion = parse_amount(text_of(row, "INVOICEPORTION")) or Decimal("100")
                # A reusable Payment Term of the same shape is shared across
                # templates, which is what makes "Net 30" one concept rather than
                # one row per customer who happens to use it.
                term = await _ensure_payment_term(context, description, days, portion)
                context.db.add(
                    PaymentTermsTemplateDetail(
                        id=uuid.uuid4(),
                        template_id=template.id,
                        payment_term_id=term,
                        description=description[:140],
                        invoice_portion=portion,
                        due_date_based_on="Day(s) after invoice date",
                        credit_days=days,
                        idx=idx,
                        owner=context.user.id,
                        modified_by=context.user.id,
                    )
                )
            total = sum(
                (parse_amount(text_of(row, "INVOICEPORTION")) for row in rows), ZERO
            )
            if rows and total and abs(total - Decimal("100")) > Decimal("0.01"):
                context.warn(
                    f"The instalments of '{name}' add up to {total}%, not 100%. "
                    "The template was imported as written.",
                    "invoice_portion",
                )
            await context.book.bind(context.db, "payment_terms", name, template.id, name)
            context.created(record, "Payment Terms Template", template.id, name)


async def _ensure_payment_term(
    context: ImportContext, name: str, days: int, portion: Decimal
) -> uuid.UUID | None:
    existing = (
        await context.db.execute(
            select(PaymentTerm).where(
                PaymentTerm.company_id == context.company_id, PaymentTerm.term_name == name
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id
    term = PaymentTerm(
        id=uuid.uuid4(),
        company_id=context.company_id,
        term_name=name[:140],
        invoice_portion=portion,
        due_date_based_on="Day(s) after invoice date",
        credit_days=days,
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(term)
    await context.db.flush()
    return term.id


# --------------------------------------------------------------------------------------
# Currencies
# --------------------------------------------------------------------------------------


async def import_currencies(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    """Match on ISO code — never create a currency — and keep the rate.

    A currency the app does not know is a data problem, not something to invent:
    inventing it would give every amount in that currency a code no report,
    exchange rate or print format can resolve.
    """
    for record in records:
        async with context.row(record):
            code = _name(record).upper()[:3]
            if not code:
                context.skip(record, "The currency row has no ISO code")
                continue
            data = _raw(record)
            currency = (
                await context.db.execute(select(Currency).where(Currency.code == code))
            ).scalars().first()
            if currency is None:
                context.skip(
                    record,
                    f"'{code}' is not in the OptiERP currency list. Add it under "
                    "Settings before re-running, or leave it — no imported "
                    "document uses it.",
                )
                continue
            await context.book.bind(context.db, "currency", code, currency.id, code)

            base = (context.company.default_currency or "INR").upper()
            rate = parse_amount(text_of(data, "DAILYRATE"))
            on_date = context.opening_date or context.session.to_date or date.today()
            if code == base or rate <= ZERO:
                # The base currency's rate against itself is 1 by definition;
                # storing it would be a row that can only ever be wrong.
                context.reused(record, "Currency", currency.id, code)
                continue

            duplicate = (
                await context.db.execute(
                    select(CurrencyExchange).where(
                        CurrencyExchange.from_currency == code,
                        CurrencyExchange.to_currency == base,
                        CurrencyExchange.date == on_date,
                    )
                )
            ).scalars().first()
            if duplicate is None:
                context.db.add(
                    CurrencyExchange(
                        id=uuid.uuid4(),
                        date=on_date,
                        from_currency=code,
                        to_currency=base,
                        exchange_rate=rate,
                        for_buying=True,
                        for_selling=True,
                        owner=context.user.id,
                        modified_by=context.user.id,
                    )
                )
                context.info(
                    f"Rate {rate} {code}/{base} recorded as at {on_date.isoformat()}."
                )
            context.reused(record, "Currency", currency.id, code)


# --------------------------------------------------------------------------------------
# Tax templates
# --------------------------------------------------------------------------------------

#: A GST rate splits into halves intra-state and stays whole inter-state. One
#: template per rate carries both, so the same template serves either invoice.
_HALF = Decimal("2")


async def import_tax_templates(
    context: ImportContext, records: list[MigrationStagingRecord]
) -> None:
    """A rate master becomes a Tax Template for documents raised after the move.

    Imported documents are untouched by this: they carry their own tax amounts
    across, line by line, exactly as the source wrote them. This is only so the
    first invoice raised *in* OptiERP has the rate it should.
    """
    for record in records:
        async with context.row(record):
            name = _name(record)
            if not name:
                context.skip(record, "The tax rate has no name")
                continue
            data = _raw(record)
            rate = parse_amount(text_of(data, "RATE"))
            if rate <= ZERO:
                context.skip(record, f"'{name}' has no rate to apply")
                continue

            kind = "purchase" if _is_purchase(text_of(data, "APPLIESTO")) else "sales"
            existing = (
                await context.db.execute(
                    select(TaxTemplate).where(
                        TaxTemplate.company_id == context.company_id,
                        TaxTemplate.title == name,
                        TaxTemplate.kind == kind,
                    )
                )
            ).scalars().first()
            if existing is not None:
                await context.book.bind(context.db, "tax_template", name, existing.id, name)
                context.reused(record, "Tax Template", existing.id, name)
                continue

            template = TaxTemplate(
                id=uuid.uuid4(),
                company_id=context.company_id,
                title=name[:140],
                kind=kind,
                is_default=False,
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(template)
            await context.db.flush()

            head = "output" if kind == "sales" else "input"
            explicit = text_of(data, "OUTPUTLEDGER" if kind == "sales" else "INPUTLEDGER")
            missing: list[str] = []
            for idx, (component, share) in enumerate(
                (("CGST", rate / _HALF), ("SGST", rate / _HALF), ("IGST", rate))
            ):
                account_name = explicit or f"{component} {head.title()}"
                account_id = context.book.resolve("ledger", account_name)
                if account_id is None:
                    account_id = await _account_named(context, account_name)
                if account_id is None:
                    missing.append(account_name)
                    continue
                context.db.add(
                    TaxTemplateDetail(
                        id=uuid.uuid4(),
                        template_id=template.id,
                        account_head_id=account_id,
                        description=f"{component} @ {share}%",
                        # A GST rate applies to the taxable value, which is the
                        # net total; "Actual" would mean a fixed amount and make
                        # the template silently produce zero tax.
                        charge_type="On Net Total",
                        rate=share,
                        idx=idx,
                        add_deduct_tax="Add",
                        category="Total",
                        owner=context.user.id,
                        modified_by=context.user.id,
                    )
                )
            if missing:
                context.warn(
                    "No account was found for "
                    + ", ".join(sorted(set(missing)))
                    + f", so '{name}' was imported without those rows. Add them on "
                    "the template before raising an invoice with it.",
                    "account_head",
                )
            await context.book.bind(context.db, "tax_template", name, template.id, name)
            context.created(record, "Tax Template", template.id, name)


def _is_purchase(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"purchase", "buying", "input", "buy"}


async def _account_named(context: ImportContext, name: str) -> uuid.UUID | None:
    row = (
        await context.db.execute(
            select(Account).where(
                Account.company_id == context.company_id,
                Account.account_name == name,
                Account.is_group.is_(False),
            )
        )
    ).scalars().first()
    return row.id if row is not None else None


# --------------------------------------------------------------------------------------
# Bank accounts
# --------------------------------------------------------------------------------------


async def import_bank_accounts(
    context: ImportContext, records: list[MigrationStagingRecord]
) -> None:
    for record in records:
        async with context.row(record):
            name = _name(record)
            if not name:
                context.skip(record, "The bank account has no name")
                continue
            data = _raw(record)
            existing = (
                await context.db.execute(
                    select(BankAccount).where(
                        BankAccount.company_id == context.company_id,
                        BankAccount.account_name == name,
                    )
                )
            ).scalars().first()
            if existing is not None:
                await context.book.bind(context.db, "bank_account", name, existing.id, name)
                context.reused(record, "Bank Account", existing.id, name)
                continue

            bank_id = await _ensure_bank(context, text_of(data, "BANKNAME"))
            # The ledger side. Falling back to the account's own name is right far
            # more often than not: a source that calls the bank account "HDFC Bank"
            # almost always calls its ledger that too.
            ledger_name = text_of(data, "LEDGERNAME") or name
            gl_account = context.book.resolve("ledger", ledger_name) or await _account_named(
                context, ledger_name
            )
            if gl_account is None:
                context.warn(
                    f"No ledger account called '{ledger_name}' was found, so this "
                    "bank account has no book side yet. Set it before reconciling.",
                    "gl_account",
                )

            account = BankAccount(
                id=uuid.uuid4(),
                company_id=context.company_id,
                account_name=name[:140],
                bank_id=bank_id,
                gl_account_id=gl_account,
                account_number=(text_of(data, "BANKACCOUNTNO") or None),
                iban=(text_of(data, "IFSCODE") or None),
                is_company_account=True,
                is_default=bool(text_of(data, "ISDEFAULT")),
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(account)
            await context.db.flush()
            await context.book.bind(context.db, "bank_account", name, account.id, name)
            context.created(record, "Bank Account", account.id, name)


async def _ensure_bank(context: ImportContext, bank_name: str | None) -> uuid.UUID | None:
    label = (bank_name or "").strip()
    if not label:
        return None
    existing = (
        await context.db.execute(
            select(Bank).where(
                Bank.company_id == context.company_id, Bank.bank_name == label
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id
    bank = Bank(
        id=uuid.uuid4(),
        company_id=context.company_id,
        bank_name=label[:140],
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(bank)
    await context.db.flush()
    return bank.id


# --------------------------------------------------------------------------------------
# Bank statement lines
# --------------------------------------------------------------------------------------


async def import_bank_transactions(
    context: ImportContext, records: list[MigrationStagingRecord]
) -> None:
    """Statement lines arrive unreconciled and post nothing.

    This is the whole point of the entity: a Bank Transaction is what the *bank*
    says happened. Posting it would double every payment the same import already
    created. Reconciliation is what connects the two, and it is a human decision.
    """
    for record in records:
        async with context.row(record):
            data = _raw(record)
            bank_name = text_of(data, "BANKNAME")
            account_id = context.book.resolve("bank_account", bank_name or "")
            if account_id is None:
                account_id = await _bank_account_named(context, bank_name)
            if account_id is None:
                context.skip(
                    record,
                    f"No bank account called '{bank_name}' was imported, so this "
                    "statement line has nothing to sit against.",
                )
                continue

            when = _as_date(text_of(data, "TRANSACTIONDATE"))
            if when is None:
                context.skip(record, "The statement line has no date")
                continue
            deposit = parse_amount(text_of(data, "DEPOSIT"))
            withdrawal = parse_amount(text_of(data, "WITHDRAWAL"))
            if deposit <= ZERO and withdrawal <= ZERO:
                context.skip(record, "The statement line has no amount")
                continue

            name = await get_next_name(
                context.db, BANK_TXN_SERIES, context.company_id, on_date=when
            )
            transaction = BankTransaction(
                id=uuid.uuid4(),
                company_id=context.company_id,
                name=name,
                bank_account_id=account_id,
                date=when,
                description=(text_of(data, "DESCRIPTION") or None),
                reference_number=(text_of(data, "REFERENCENUMBER") or None),
                deposit=deposit,
                withdrawal=withdrawal,
                status="Unreconciled",
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(transaction)
            await context.db.flush()
            if (text_of(data, "MATCHEDSTATUS") or "").strip().casefold() == "matched":
                context.info(
                    "The source marked this line reconciled. It arrives "
                    "unreconciled here, because the voucher it was matched to is "
                    "a different record in OptiERP."
                )
            context.created(record, "Bank Transaction", transaction.id, name)


async def _bank_account_named(context: ImportContext, name: str | None) -> uuid.UUID | None:
    if not name:
        return None
    row = (
        await context.db.execute(
            select(BankAccount).where(
                BankAccount.company_id == context.company_id,
                BankAccount.account_name == name.strip(),
            )
        )
    ).scalars().first()
    return row.id if row is not None else None


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip()[:10])
    except ValueError:
        return None


# --------------------------------------------------------------------------------------
# Budgets
# --------------------------------------------------------------------------------------


async def import_budgets(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    """A budget is per fiscal year and cost centre, with an amount per account."""
    for record in records:
        async with context.row(record):
            name = _name(record)
            data = _raw(record)
            lines = [
                row for row in as_list(data.get("BUDGETALLOCATION.LIST")) if isinstance(row, dict)
            ]
            if not lines:
                context.skip(record, f"Budget '{name}' names no accounts")
                continue

            starts = _as_date(text_of(data, "STARTINGFROM"))
            year, exact = await _fiscal_year_for(context, starts)
            if year is not None and not exact:
                context.warn(
                    "No fiscal year covers this budget's period, so it was filed "
                    "under the latest one. Move it if that is not where it belongs.",
                    "fiscal_year",
                )
            if year is None:
                context.skip(
                    record,
                    "No fiscal year covers this budget's period, so it has "
                    "nothing to be a budget for.",
                )
                continue

            centre_name = text_of(data, "COSTCENTRENAME")
            cost_centre = (
                context.book.resolve("cost_centre", centre_name) if centre_name else None
            ) or context.company.default_cost_center_id

            existing = (
                await context.db.execute(
                    select(Budget).where(
                        Budget.company_id == context.company_id,
                        Budget.fiscal_year_id == year,
                        Budget.cost_center_id == cost_centre,
                    )
                )
            ).scalars().first()
            if existing is not None:
                context.reused(record, "Budget", existing.id, name)
                context.warn(
                    "A budget already exists for this fiscal year and cost centre; "
                    f"'{name}' was not imported over it.",
                    "fiscal_year",
                )
                continue

            budget = Budget(
                id=uuid.uuid4(),
                company_id=context.company_id,
                fiscal_year_id=year,
                cost_center_id=cost_centre,
                action_if_annual_budget_exceeded="Warn",
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(budget)
            await context.db.flush()

            written = 0
            for line in lines:
                ledger = text_of(line, "LEDGERNAME")
                account_id = context.book.resolve("ledger", ledger or "") or await _account_named(
                    context, (ledger or "").strip()
                )
                if account_id is None:
                    context.warn(
                        f"Budget line skipped: no account called '{ledger}' is in "
                        "this import.",
                        "account",
                    )
                    continue
                context.db.add(
                    BudgetAccount(
                        id=uuid.uuid4(),
                        budget_id=budget.id,
                        account_id=account_id,
                        budget_amount=parse_amount(text_of(line, "AMOUNT")),
                        owner=context.user.id,
                        modified_by=context.user.id,
                    )
                )
                written += 1
            if not written:
                raise ValidationError(
                    f"None of the accounts named in budget '{name}' exist here"
                )
            context.created(record, "Budget", budget.id, name)


async def _fiscal_year_for(
    context: ImportContext, when: date | None
) -> tuple[uuid.UUID | None, bool]:
    """``(fiscal year, was it an actual match)`` for ``when``.

    The run opens every year the imported period touches before this point, so a
    budget inside that period always matches exactly. The second element exists
    so a budget *outside* it is reported rather than quietly filed in the newest
    year, where its variance report would be nonsense.
    """
    rows = (
        await context.db.execute(
            select(FiscalYear).where(FiscalYear.company_id == context.company_id)
        )
    ).scalars().all()
    if not rows:
        return None, False
    if when is not None:
        for row in rows:
            if row.year_start_date <= when <= row.year_end_date:
                return row.id, True
    return max(rows, key=lambda r: r.year_start_date).id, False


# --------------------------------------------------------------------------------------
# Fixed assets
# --------------------------------------------------------------------------------------

#: How a source spells a depreciation method -> ours.
_DEP_METHODS = {
    "slm": "Straight Line",
    "straight line": "Straight Line",
    "straight-line": "Straight Line",
    "sl": "Straight Line",
    "wdv": "Written Down Value",
    "written down value": "Written Down Value",
    "reducing balance": "Written Down Value",
    "diminishing": "Written Down Value",
}


async def import_assets(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    """Onboard an asset mid-life: cost, plus the depreciation already taken.

    Nothing is posted to the ledger. The asset's cost and accumulated
    depreciation are already sitting in the opening balances that came across
    with the trial balance; creating GL entries here would count them twice. The
    Asset record exists so depreciation *from now on* is calculated on the right
    base, which is why it carries opening accumulated depreciation and arrives as
    a Draft for the accountant to check and submit.
    """
    for record in records:
        async with context.row(record):
            label = _name(record)
            if not label:
                context.skip(record, "The asset has no name")
                continue
            data = _raw(record)
            cost = parse_amount(text_of(data, "ORIGINALCOST"))
            if cost <= ZERO:
                context.skip(record, f"Asset '{label}' has no cost")
                continue

            category_id = await _ensure_asset_category(
                context,
                text_of(data, "ASSETGROUP") or "Migrated Assets",
                method=_DEP_METHODS.get(
                    (text_of(data, "DEPMETHOD") or "").strip().casefold(), "Straight Line"
                ),
                rate=parse_amount(text_of(data, "DEPRATE")),
                life=parse_int(text_of(data, "USEFULLIFE")),
            )
            purchased = _as_date(text_of(data, "PURCHASEDATE"))
            # Depreciation restarts from the migration cut-off, not from the
            # purchase date: everything before it is already in the accumulated
            # figure, and replaying it would depreciate the asset twice.
            in_use = context.opening_date or purchased or date.today()
            accumulated = parse_amount(text_of(data, "ACCUMULATEDDEP"))
            if accumulated > cost:
                context.warn(
                    f"Accumulated depreciation ({accumulated}) exceeds cost "
                    f"({cost}); it was capped at cost.",
                    "opening_accumulated_depreciation",
                )
                accumulated = cost

            name = await get_next_name(
                context.db, ASSET_SERIES, context.company_id, on_date=in_use
            )
            asset = Asset(
                id=uuid.uuid4(),
                company_id=context.company_id,
                name=name,
                asset_name=label[:140],
                asset_category_id=category_id,
                location_id=await _ensure_location(context, text_of(data, "LOCATIONNAME")),
                custodian=(text_of(data, "CUSTODIAN") or None),
                gross_purchase_amount=cost,
                opening_accumulated_depreciation=accumulated,
                purchase_date=purchased,
                available_for_use_date=in_use,
                status="Draft",
                remarks=(
                    f"Migrated from {context.session.source_app or 'a previous system'}"
                    + (f" (asset {text_of(data, 'ASSETCODE')})" if text_of(data, "ASSETCODE") else "")
                ),
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(asset)
            await context.db.flush()
            context.info(
                "Imported as a Draft with its depreciation to date already "
                "recognised. Submit it once the category's accounts are set."
            )
            context.created(record, "Asset", asset.id, label)


async def _ensure_asset_category(
    context: ImportContext,
    name: str,
    *,
    method: str,
    rate: Decimal,
    life: int | None,
) -> uuid.UUID:
    label = name.strip()[:140] or "Migrated Assets"
    existing = (
        await context.db.execute(
            select(AssetCategory).where(
                AssetCategory.company_id == context.company_id,
                AssetCategory.category_name == label,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id
    category = AssetCategory(
        id=uuid.uuid4(),
        company_id=context.company_id,
        category_name=label,
        depreciation_method=method,
        # Annual entries: a source that states a rate states an annual one, and a
        # useful life is stated in years. Monthly would be a different claim.
        total_number_of_depreciations=life or 0,
        frequency_of_depreciation_months=12,
        rate_of_depreciation=rate if rate > ZERO else None,
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(category)
    await context.db.flush()
    context.warn(
        f"Asset category '{label}' did not exist here and was created. Set its "
        "fixed-asset, depreciation-expense and accumulated-depreciation accounts "
        "before submitting assets under it.",
        "asset_category",
    )
    return category.id


async def _ensure_location(context: ImportContext, name: str | None) -> uuid.UUID | None:
    label = (name or "").strip()
    if not label:
        return None
    existing = (
        await context.db.execute(
            select(Location).where(
                Location.company_id == context.company_id,
                Location.location_name == label,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing.id
    location = Location(
        id=uuid.uuid4(),
        company_id=context.company_id,
        location_name=label[:140],
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(location)
    await context.db.flush()
    return location.id


# --------------------------------------------------------------------------------------
# Party attributes — used by masters.import_parties, not by a sheet of their own
# --------------------------------------------------------------------------------------


async def apply_party_attributes(
    context: ImportContext,
    data: dict[str, Any],
    *,
    party_type: str,
    kwargs: dict[str, Any],
) -> None:
    """Fill the Customer/Supplier fields a source states but the ledger has no room for.

    Mutates ``kwargs`` in place because the caller is still assembling the model.
    Every lookup is match-then-create, so a second import of the same file reuses
    what the first one made.
    """
    entity_kind = (text_of(data, "PARTYENTITYKIND") or "").strip().casefold()
    if entity_kind in {"individual", "person", "consumer", "proprietor"}:
        kwargs["customer_type" if party_type == "Customer" else "supplier_type"] = "Individual"

    terms = text_of(data, "PAYMENTTERMSNAME")
    if terms:
        kwargs["payment_terms_template_id"] = await ensure_payment_terms(
            context, terms, credit_days=parse_int(text_of(data, "BILLCREDITPERIOD"))
        )
    elif text_of(data, "BILLCREDITPERIOD"):
        # No named terms, but a credit period: that *is* a payment term, and
        # dropping it would lose the only due-date rule the source stated.
        days = parse_int(text_of(data, "BILLCREDITPERIOD")) or 0
        if days > 0:
            kwargs["payment_terms_template_id"] = await ensure_payment_terms(
                context, f"Net {days}", credit_days=days
            )

    tax_category = text_of(data, "TAXCATEGORYNAME")
    if tax_category:
        kwargs["tax_category_id"] = await ensure_tax_category(
            context, GST_TREATMENTS.get(tax_category.strip().casefold(), tax_category)
        )

    group = text_of(data, "PARTYGROUPNAME")
    if group:
        kwargs[
            "customer_group_id" if party_type == "Customer" else "supplier_group_id"
        ] = await (
            ensure_customer_group(context, group)
            if party_type == "Customer"
            else ensure_supplier_group(context, group)
        )

    if party_type == "Customer":
        territory = text_of(data, "TERRITORYNAME")
        if territory:
            kwargs["territory_id"] = await ensure_territory(context, territory)

    notes = text_of(data, "DESCRIPTION")
    if notes:
        kwargs["notes"] = notes
    legal = text_of(data, "LEDGERLEGALNAME")
    if legal and legal.strip() != str(kwargs.get("customer_name") or kwargs.get("supplier_name")):
        kwargs["notes"] = "\n".join(
            part for part in (kwargs.get("notes"), f"Registered name: {legal.strip()}") if part
        )
    if (text_of(data, "ISDELETED") or "").strip().casefold() in {"yes", "true", "1"}:
        kwargs["disabled"] = True


#: Another system's registration vocabulary -> a tax category name that means
#: something here. Anything unrecognised keeps the source's own word, which is
#: more useful than dropping it.
GST_TREATMENTS: dict[str, str] = {
    "business_gst": "Registered",
    "business_registered": "Registered",
    "regular": "Registered",
    "business_none": "Unregistered",
    "consumer": "Unregistered",
    "unregistered": "Unregistered",
    "overseas": "Overseas",
    "export": "Overseas",
    "sez": "SEZ",
    "sez_developer": "SEZ",
    "deemed_export": "Deemed Export",
    "composition": "Composition",
    "uin_holders": "UIN Holder",
    "tax_deductor": "Tax Deductor",
}
