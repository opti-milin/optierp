"""Master importers — Tally's name lists become OptiERP master records.

Order matters and is enforced by the runner's stage numbers: groups before
ledgers, stock groups before stock items, godowns before anything that posts
stock. Within an entity, this module sorts parents ahead of children itself,
because Tally exports its tree in whatever order the user created it.

Every importer follows the same shape:

    for each staging row:
        resolve the mapping -> either an existing record (reuse) or a new one
        write the record
        bind the Tally name to the record id in the MappingBook

so later entities (and the voucher importers) can resolve names without a query.
Nothing here commits; the runner owns the transaction.
"""

from __future__ import annotations

import re
import uuid
from decimal import Decimal
from typing import Any, Iterable

from sqlalchemy import select

from app.models.accounts import Account, CostCenter
from app.models.accounts.common import ROOT_TYPE_REPORT
from app.models.buying import Supplier
from app.models.core import UOM, UOMConversion
from app.models.selling import Customer
from app.models.stock import Item, ItemGroup, ItemPrice, PriceList, Warehouse
from app.models.migration import MigrationStagingRecord
from app.services.migration.catalogue import (
    GST_REGISTRATION_TYPES,
    classify_group,
    normalise_unit,
    resolve_group_spec,
)
from app.services.migration.context import ImportContext
from app.services.migration.importers.contacts import ensure_address, ensure_contact
from app.services.migration.importers.setup import apply_party_attributes
from app.services.migration.sources.tally_xml import as_list, parse_amount, parse_bool, text_of

ZERO = Decimal("0")
_SLUG = re.compile(r"[^a-z0-9_]+")

# GSTIN: 2-digit state code, 10-char PAN, entity number, Z, checksum.
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def _slugify(label: str) -> str:
    return _SLUG.sub("_", (label or "").lower()).strip("_") or "node"


def _raw(record: MigrationStagingRecord) -> dict[str, Any]:
    data = (record.raw or {}).get("data")
    return data if isinstance(data, dict) else {}


def _sorted_by_depth(
    records: list[MigrationStagingRecord],
) -> list[MigrationStagingRecord]:
    """Parents before children.

    Tally exports a tree flat and unordered. We walk it iteratively: emit every
    record whose parent is already emitted (or absent), repeat. Anything left
    after a full pass has a missing or circular parent and is emitted last so it
    fails loudly on its own row rather than silently reordering the tree.
    """
    remaining = list(records)
    emitted_names: set[str] = set()
    ordered: list[MigrationStagingRecord] = []
    while remaining:
        progress = False
        deferred = []
        for record in remaining:
            parent = (record.source_parent or "").strip().casefold()
            if not parent or parent in emitted_names:
                ordered.append(record)
                emitted_names.add((record.source_name or "").strip().casefold())
                progress = True
            else:
                deferred.append(record)
        if not progress:
            ordered.extend(deferred)
            break
        remaining = deferred
    return ordered


# --------------------------------------------------------------------------------------
# Chart of accounts: Tally groups -> Account (group nodes)
# --------------------------------------------------------------------------------------


async def _account_by_name(context: ImportContext, name: str) -> Account | None:
    stmt = select(Account).where(
        Account.company_id == context.company_id, Account.account_name == name
    )
    return (await context.db.execute(stmt)).scalars().first()


async def _resolve_root(context: ImportContext, root_type: str) -> Account:
    """The company's top-level node for a root type (created by the COA seed)."""
    stmt = (
        select(Account)
        .where(
            Account.company_id == context.company_id,
            Account.root_type == root_type,
            Account.parent_account_id.is_(None),
        )
        .limit(1)
    )
    root = (await context.db.execute(stmt)).scalars().first()
    if root is not None:
        return root

    # No root of this type. That is not a broken company — the Indian Chart of
    # Accounts (`in_standard.json`) ships only Asset/Liability/Income/Expense,
    # presenting capital under "Source of Funds" the Schedule III way. But Tally
    # classifies Capital Account and Reserves & Surplus as Equity, so importing
    # any real Indian company's masters would dead-end here.
    #
    # Create the root instead of refusing. The alternative — filing equity under
    # Liability — would misstate the Balance Sheet, and silently.
    root = Account(
        id=uuid.uuid4(),
        company_id=context.company_id,
        account_name=root_type,
        parent_account_id=None,
        root_type=root_type,
        report_type=ROOT_TYPE_REPORT[root_type],
        is_group=True,
        account_currency=context.company.default_currency,
        path=_slugify(root_type),
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(root)
    await context.db.flush()
    context.warn(
        f"This company's Chart of Accounts had no '{root_type}' root, so one was "
        f"created to hold Tally's {root_type.lower()} groups.",
        "root_type",
    )
    return root


async def _classify_from_ancestry(
    context: ImportContext, name: str | None
) -> tuple[str, str | None, str | None]:
    """Classify a Tally group name via its nearest reserved ancestor.

    Users nest their own groups under Tally's reserved ones ("Debtors - North"
    under "Sundry Debtors"), so the classification of any group is whatever its
    nearest reserved ancestor says. Returns (root_type, account_type, party_type).
    """
    spec = resolve_group_spec(name, context.group_parents)
    if spec is not None:
        return spec.root_type, spec.account_type, spec.party_type
    # Unknown ancestry: an existing account of that name settles it, else assume
    # an expense (the safest P&L bucket — visible, and never silently a liability).
    existing = await _account_by_name(context, (name or "").strip())
    if existing is not None:
        return existing.root_type, existing.account_type, None
    return "Expense", None, None


async def _ensure_group_account(
    context: ImportContext, name: str | None
) -> Account | None:
    """Find — or create — the group account a Tally group name refers to.

    Tally's 28 reserved groups are implicit: a Day Book export, and plenty of
    "All Masters" exports, name "Sundry Debtors" as a parent without ever
    shipping a GROUP record for it. Filing those ledgers at the tree root would
    make the imported Chart of Accounts unrecognisable next to Tally's, so we
    materialise the reserved group under the right root the first time it is
    needed. Non-reserved names are left alone — inventing them would be guessing.
    """
    cleaned = (name or "").strip()
    if not cleaned:
        return None

    resolved = context.book.resolve("group", cleaned)
    if resolved is not None:
        account = await context.db.get(Account, resolved)
        if account is not None and account.is_group:
            return account

    existing = await _account_by_name(context, cleaned)
    if existing is not None and existing.is_group:
        return existing

    spec = classify_group(cleaned)
    if spec is None:
        return None  # a name we cannot classify — caller falls back to the root

    root = await _resolve_root(context, spec.root_type)
    account = Account(
        id=uuid.uuid4(),
        company_id=context.company_id,
        account_name=cleaned,
        parent_account_id=root.id,
        root_type=root.root_type,
        report_type=ROOT_TYPE_REPORT[root.root_type],
        account_type=spec.account_type,
        is_group=True,
        account_currency=context.company.default_currency,
        path=f"{root.path}.{_slugify(cleaned)}",
        owner=context.user.id,
        modified_by=context.user.id,
    )
    context.db.add(account)
    await context.db.flush()
    await context.book.bind(context.db, "group", cleaned, account.id, cleaned)
    return account


async def import_groups(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    """Tally ledger groups -> Chart of Accounts group nodes."""
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Group has no name in the Tally export")
                continue

            mapping = context.book.get("group", name)
            if mapping is not None and mapping.target_id is not None:
                await context.book.bind(context.db, "group", name, mapping.target_id, mapping.target_name)
                context.reused(record, "Account", mapping.target_id, mapping.target_name)
                continue

            # A reserved Tally group we already model in the COA -> reuse it
            # instead of creating a duplicate branch.
            existing = await _account_by_name(context, name)
            if existing is not None and existing.is_group:
                await context.book.bind(context.db, "group", name, existing.id, existing.account_name)
                context.reused(record, "Account", existing.id, existing.account_name)
                continue

            root_type, account_type, _party = await _classify_from_ancestry(context, name)
            parent_name = (record.source_parent or "").strip()
            parent = await _ensure_group_account(context, parent_name)
            if parent is None or not parent.is_group:
                parent = await _resolve_root(context, root_type)
                if parent_name:
                    context.warn(
                        f"Parent group '{parent_name}' was not found; filed under "
                        f"'{parent.account_name}' instead.",
                        "parent",
                    )

            account = Account(
                id=uuid.uuid4(),
                company_id=context.company_id,
                account_name=name,
                parent_account_id=parent.id,
                root_type=parent.root_type,
                report_type=ROOT_TYPE_REPORT[parent.root_type],
                account_type=account_type,
                is_group=True,
                account_currency=context.company.default_currency,
                path=f"{parent.path}.{_slugify(name)}",
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(account)
            await context.db.flush()
            await context.book.bind(context.db, "group", name, account.id, name)
            context.created(record, "Account", account.id, name)


# --------------------------------------------------------------------------------------
# Ledgers -> Account leaves (+ Customer / Supplier)
# --------------------------------------------------------------------------------------


def _ledger_gstin(data: dict[str, Any]) -> str | None:
    """Pull a GSTIN off a Tally ledger, wherever that version stored it."""
    direct = text_of(data, "PARTYGSTIN", "GSTIN", "VATTINNUMBER")
    if direct and _GSTIN.match(direct.strip().upper()):
        return direct.strip().upper()
    for detail in as_list(data.get("LEDGSTREGDETAILS.LIST")):
        value = text_of(detail, "GSTIN") if isinstance(detail, dict) else None
        if value and _GSTIN.match(value.strip().upper()):
            return value.strip().upper()
    return None


def _address_lines(node: Any) -> list[str]:
    """Tally writes an address as a repeated <ADDRESS> inside an ADDRESS.LIST."""
    if isinstance(node, dict):
        return [str(v) for v in as_list(node.get("ADDRESS")) if v]
    if isinstance(node, list):
        return [str(v) for v in node if isinstance(v, str)]
    return []


def _latest_mailing_details(data: dict[str, Any]) -> dict[str, Any] | None:
    """The applicable LEDMAILINGDETAILS block, newest APPLICABLEFROM first.

    TallyPrime keeps a *history* of a party's mailing details, one block per
    change, each stamped APPLICABLEFROM. Taking the first would import an
    address the customer moved out of years ago.
    """
    blocks = [b for b in as_list(data.get("LEDMAILINGDETAILS.LIST")) if isinstance(b, dict)]
    if not blocks:
        return None
    return max(blocks, key=lambda b: text_of(b, "APPLICABLEFROM") or "")


def _ledger_address(data: dict[str, Any]) -> tuple[list[str], str | None, str | None]:
    """(address lines, state, pincode) from a Tally ledger's mailing details.

    Two shapes. Tally.ERP 9 puts ADDRESS.LIST, PINCODE and LEDSTATENAME straight
    on the ledger. TallyPrime nests all three inside LEDMAILINGDETAILS.LIST —
    reading only the flat form meant every party imported from a real TallyPrime
    export arrived with no address and no pincode at all.
    """
    lines = _address_lines(data.get("ADDRESS.LIST"))
    state = text_of(data, "LEDSTATENAME", "STATENAME", "PRIORSTATENAME")
    pincode = text_of(data, "PINCODE", "LEDGERPINCODE")

    mailing = _latest_mailing_details(data)
    if mailing is not None:
        lines = lines or _address_lines(mailing.get("ADDRESS.LIST"))
        state = state or text_of(mailing, "STATE", "LEDSTATENAME")
        pincode = pincode or text_of(mailing, "PINCODE")

    return [line for line in lines if line.strip()], state, pincode


async def import_ledgers(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    """Every Tally ledger becomes a COA leaf account.

    Party ledgers (under Sundry Debtors/Creditors) get a Customer or Supplier in
    :func:`import_parties`, which runs at the next stage using the same rows.
    """
    for record in records:
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Ledger has no name in the Tally export")
                continue

            data = _raw(record)
            mapping = context.book.get("ledger", name)
            if mapping is not None and mapping.target_id is not None:
                await context.book.bind(context.db, "ledger", name, mapping.target_id, mapping.target_name)
                context.reused(record, "Account", mapping.target_id, mapping.target_name)
                continue

            existing = await _account_by_name(context, name)
            if existing is not None:
                await context.book.bind(context.db, "ledger", name, existing.id, existing.account_name)
                context.reused(record, "Account", existing.id, existing.account_name)
                continue

            parent_name = (record.source_parent or text_of(data, "PARENT") or "").strip()
            root_type, account_type, _party = await _classify_from_ancestry(
                context, parent_name or name
            )
            parent = await _ensure_group_account(context, parent_name)
            if parent is None or not parent.is_group:
                parent = await _resolve_root(context, root_type)
                if parent_name:
                    context.warn(
                        f"Group '{parent_name}' was not found; the ledger was filed "
                        f"under '{parent.account_name}'.",
                        "parent",
                    )

            account = Account(
                id=uuid.uuid4(),
                company_id=context.company_id,
                account_name=name,
                parent_account_id=parent.id,
                root_type=parent.root_type,
                report_type=ROOT_TYPE_REPORT[parent.root_type],
                account_type=account_type,
                is_group=False,
                account_currency=(
                    text_of(data, "CURRENCYNAME") or context.company.default_currency
                )[:3].upper(),
                path=f"{parent.path}.{_slugify(name)}",
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(account)
            await context.db.flush()
            await context.book.bind(context.db, "ledger", name, account.id, name)
            context.created(record, "Account", account.id, name)


async def _create_address_and_contact(
    context: ImportContext,
    data: dict[str, Any],
    *,
    party_name: str,
    party_type: str,
    party_id: uuid.UUID,
) -> None:
    """Carry Tally's mailing details across as an Address + Contact.

    A Tally ledger keeps both inline. A spreadsheet source usually ships them on
    their own sheets instead, which `importers.contacts` handles — and both end
    up calling the same two writers, so there is exactly one rule for what
    happens when an address title or a contact's name already exists.
    """
    lines, state, pincode = _ledger_address(data)
    email = text_of(data, "EMAIL", "EMAILCC")
    phone = text_of(data, "LEDGERPHONE", "PHONENUMBER", "LEDGERMOBILE", "MOBILENUMBER")
    contact_person = text_of(data, "LEDGERCONTACT", "CONTACTPERSON")

    await ensure_address(
        context,
        party_type=party_type,
        party_id=party_id,
        party_name=party_name,
        lines=lines,
        state=state,
        pincode=pincode,
    )
    if contact_person or email or phone:
        first, _, last = (contact_person or party_name).partition(" ")
        await ensure_contact(
            context,
            party_type=party_type,
            party_id=party_id,
            first_name=(first or party_name),
            last_name=last or None,
            email=email,
            mobile=phone,
        )


async def import_parties(
    context: ImportContext, records: list[MigrationStagingRecord], *, party_type: str
) -> None:
    """Ledgers under Sundry Debtors / Sundry Creditors -> Customer / Supplier."""
    entity_key = "customer" if party_type == "Customer" else "supplier"
    model = Customer if party_type == "Customer" else Supplier
    name_field = "customer_name" if party_type == "Customer" else "supplier_name"

    for record in records:
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Party ledger has no name")
                continue

            data = _raw(record)
            mapping = context.book.get(entity_key, name)
            if mapping is not None and mapping.target_id is not None:
                await context.book.bind(context.db, entity_key, name, mapping.target_id, mapping.target_name)
                context.reused(record, party_type, mapping.target_id, mapping.target_name)
                continue

            stmt = select(model).where(
                model.company_id == context.company_id,
                getattr(model, name_field) == name,
            )
            existing = (await context.db.execute(stmt)).scalars().first()
            if existing is not None:
                await context.book.bind(context.db, entity_key, name, existing.id, name)
                context.reused(record, party_type, existing.id, name)
                continue

            gstin = _ledger_gstin(data)
            registration = text_of(data, "GSTREGISTRATIONTYPE")
            if registration and registration not in GST_REGISTRATION_TYPES:
                context.warn(
                    f"Tally GST registration type '{registration}' has no direct "
                    "equivalent; treated as Regular.",
                    "gst_registration_type",
                )
            credit_limit = parse_amount(text_of(data, "CREDITLIMIT"))
            # Tally's own account for this party is the ledger we just created.
            party_account_id = context.book.resolve("ledger", name)

            kwargs: dict[str, Any] = {
                "id": uuid.uuid4(),
                "company_id": context.company_id,
                name_field: name[:140],
                "tax_id": gstin,
                "email_id": (text_of(data, "EMAIL") or None),
                "default_currency": (
                    text_of(data, "CURRENCYNAME") or context.company.default_currency
                )[:3].upper(),
                "owner": context.user.id,
                "modified_by": context.user.id,
            }
            if party_type == "Customer":
                kwargs["receivable_account_id"] = party_account_id
                if credit_limit > ZERO:
                    kwargs["credit_limit"] = credit_limit
            else:
                kwargs["payable_account_id"] = party_account_id

            # Payment terms, tax category, group, territory, notes, entity type:
            # real columns on Customer and Supplier that no source could reach
            # until the profile vocabulary could name them.
            await apply_party_attributes(
                context, data, party_type=party_type, kwargs=kwargs
            )

            party = model(**kwargs)
            context.db.add(party)
            await context.db.flush()
            await _create_address_and_contact(
                context, data, party_name=name, party_type=party_type, party_id=party.id
            )
            await context.book.bind(context.db, entity_key, name, party.id, name)
            context.created(record, party_type, party.id, name)


# --------------------------------------------------------------------------------------
# Units of measure
# --------------------------------------------------------------------------------------


async def import_units(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    """Tally simple units -> UOM; compound units -> UOM + UOM Conversion."""
    for record in records:
        async with context.row(record):
            symbol = (record.source_name or "").strip()
            if not symbol:
                context.skip(record, "Unit has no symbol")
                continue

            data = _raw(record)
            uom_name = normalise_unit(symbol) or symbol

            existing = (
                await context.db.execute(select(UOM).where(UOM.uom_name == uom_name))
            ).scalars().first()
            if existing is None:
                existing = UOM(
                    id=uuid.uuid4(),
                    uom_name=uom_name[:140],
                    must_be_whole_number=parse_bool(text_of(data, "ISSIMPLEUNIT")) is False
                    and parse_amount(text_of(data, "DECIMALPLACES")) == ZERO,
                    owner=context.user.id,
                    modified_by=context.user.id,
                )
                context.db.add(existing)
                await context.db.flush()
                await context.book.bind(context.db, "unit", symbol, existing.id, uom_name)
                context.created(record, "UOM", existing.id, uom_name)
            else:
                await context.book.bind(context.db, "unit", symbol, existing.id, uom_name)
                context.reused(record, "UOM", existing.id, uom_name)

            # Compound unit: "Box of 12 Nos" -> conversion Box -> Nos = 12.
            base = normalise_unit(text_of(data, "BASEUNITS"))
            additional = normalise_unit(text_of(data, "ADDITIONALUNITS"))
            factor = parse_amount(text_of(data, "CONVERSION"))
            if base and additional and factor > ZERO and base != additional:
                await _ensure_uom(context, base)
                await _ensure_uom(context, additional)
                duplicate = (
                    await context.db.execute(
                        select(UOMConversion).where(
                            UOMConversion.from_uom == uom_name, UOMConversion.to_uom == base
                        )
                    )
                ).scalars().first()
                if duplicate is None:
                    context.db.add(
                        UOMConversion(
                            id=uuid.uuid4(),
                            category="Tally",
                            from_uom=uom_name,
                            to_uom=base,
                            value=factor,
                            owner=context.user.id,
                            modified_by=context.user.id,
                        )
                    )
                    context.info(f"Compound unit carried across: 1 {uom_name} = {factor} {base}")


async def _ensure_uom(context: ImportContext, uom_name: str) -> UOM:
    existing = (
        await context.db.execute(select(UOM).where(UOM.uom_name == uom_name))
    ).scalars().first()
    if existing is not None:
        return existing
    uom = UOM(
        id=uuid.uuid4(), uom_name=uom_name[:140],
        owner=context.user.id, modified_by=context.user.id,
    )
    context.db.add(uom)
    await context.db.flush()
    return uom


# --------------------------------------------------------------------------------------
# Stock groups / items / godowns
# --------------------------------------------------------------------------------------


async def import_stock_groups(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Stock group has no name")
                continue

            mapping = context.book.get("stock_group", name)
            if mapping is not None and mapping.target_id is not None:
                await context.book.bind(context.db, "stock_group", name, mapping.target_id, mapping.target_name)
                context.reused(record, "Item Group", mapping.target_id, mapping.target_name)
                continue

            stmt = select(ItemGroup).where(
                ItemGroup.company_id == context.company_id, ItemGroup.item_group_name == name
            )
            existing = (await context.db.execute(stmt)).scalars().first()
            if existing is not None:
                await context.book.bind(context.db, "stock_group", name, existing.id, name)
                context.reused(record, "Item Group", existing.id, name)
                continue

            parent_name = (record.source_parent or "").strip()
            parent_id = context.book.resolve("stock_group", parent_name) if parent_name else None
            if parent_id is not None:
                parent = await context.db.get(ItemGroup, parent_id)
                if parent is not None and not parent.is_group:
                    parent.is_group = True

            group = ItemGroup(
                id=uuid.uuid4(),
                company_id=context.company_id,
                item_group_name=name[:140],
                parent_item_group_id=parent_id,
                is_group=False,
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(group)
            await context.db.flush()
            await context.book.bind(context.db, "stock_group", name, group.id, name)
            context.created(record, "Item Group", group.id, name)


async def import_godowns(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Godown has no name")
                continue

            mapping = context.book.get("godown", name)
            if mapping is not None and mapping.target_id is not None:
                await context.book.bind(context.db, "godown", name, mapping.target_id, mapping.target_name)
                context.reused(record, "Warehouse", mapping.target_id, mapping.target_name)
                continue

            stmt = select(Warehouse).where(
                Warehouse.company_id == context.company_id, Warehouse.warehouse_name == name
            )
            existing = (await context.db.execute(stmt)).scalars().first()
            if existing is not None:
                await context.book.bind(context.db, "godown", name, existing.id, name)
                context.reused(record, "Warehouse", existing.id, name)
                continue

            parent_name = (record.source_parent or "").strip()
            parent_id = context.book.resolve("godown", parent_name) if parent_name else None
            if parent_id is not None:
                parent = await context.db.get(Warehouse, parent_id)
                if parent is not None and not parent.is_group:
                    parent.is_group = True

            warehouse = Warehouse(
                id=uuid.uuid4(),
                company_id=context.company_id,
                warehouse_name=name[:140],
                parent_warehouse_id=parent_id,
                is_group=False,
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(warehouse)
            await context.db.flush()
            await context.book.bind(context.db, "godown", name, warehouse.id, name)
            context.created(record, "Warehouse", warehouse.id, name)


async def _item_accounts(
    context: ImportContext, data: dict[str, Any]
) -> tuple[uuid.UUID | None, uuid.UUID | None]:
    """The item's own revenue and cost accounts, when the source names them."""
    out: list[uuid.UUID | None] = []
    for key in ("INCOMELEDGER", "EXPENSELEDGER"):
        name = text_of(data, key)
        if not name:
            out.append(None)
            continue
        resolved = context.book.resolve("ledger", name) or await _account_by_name(context, name)
        if isinstance(resolved, Account):
            resolved = resolved.id
        if resolved is None:
            context.warn(
                f"No account called '{name}' is in this import, so the item was "
                "left on the company default.",
                "income_account" if key == "INCOMELEDGER" else "expense_account",
            )
        out.append(resolved)
    return out[0], out[1]


def _item_gst_rate(data: dict[str, Any]) -> Decimal | None:
    """The total GST rate on a Tally stock item's GST details, if it has any."""
    for details in as_list(data.get("GSTDETAILS.LIST")):
        if not isinstance(details, dict):
            continue
        for rate_detail in as_list(details.get("STATEWISEDETAILS.LIST")):
            if not isinstance(rate_detail, dict):
                continue
            for rate in as_list(rate_detail.get("RATEDETAILS.LIST")):
                if isinstance(rate, dict) and text_of(rate, "GSTRATEDUTYHEAD") in (
                    "Integrated Tax", "IGST"
                ):
                    return parse_amount(text_of(rate, "GSTRATE"))
    return None


async def import_stock_items(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    """Tally stock items -> Item, with HSN/GST and the default unit carried over."""
    for record in records:
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Stock item has no name")
                continue

            data = _raw(record)
            mapping = context.book.get("stock_item", name)
            if mapping is not None and mapping.target_id is not None:
                await context.book.bind(context.db, "stock_item", name, mapping.target_id, mapping.target_name)
                context.reused(record, "Item", mapping.target_id, mapping.target_name)
                continue

            stmt = select(Item).where(
                Item.company_id == context.company_id,
                (Item.item_code == name) | (Item.item_name == name),
            )
            existing = (await context.db.execute(stmt)).scalars().first()
            if existing is not None:
                await context.book.bind(context.db, "stock_item", name, existing.id, existing.item_code)
                context.reused(record, "Item", existing.id, existing.item_code)
                continue

            group_name = (record.source_parent or text_of(data, "PARENT") or "").strip()
            group_id = context.book.resolve("stock_group", group_name) if group_name else None
            unit_symbol = text_of(data, "BASEUNITS", "ADDITIONALUNITS")
            stock_uom = normalise_unit(unit_symbol) or "Nos"
            await _ensure_uom(context, stock_uom)

            hsn = text_of(data, "HSNCODE", "GSTHSNCODE", "HSN")
            gst_rate = _item_gst_rate(data)
            category = text_of(data, "CATEGORY")
            # A source that files each item against its own revenue and cost
            # accounts is stating something real about how it books that item;
            # dropping it sends every future invoice to the company default.
            income_account, expense_account = await _item_accounts(context, data)

            item = Item(
                id=uuid.uuid4(),
                company_id=context.company_id,
                item_code=name[:140],
                item_name=name[:140],
                description=text_of(data, "DESCRIPTION", "NARRATION"),
                item_group_id=group_id,
                stock_uom=stock_uom[:140],
                is_stock_item=True,
                standard_rate=parse_amount(text_of(data, "STANDARDPRICE")) or ZERO,
                hsn_sac_code=(hsn.strip()[:8] if hsn else None),
                brand=(category[:140] if category else None),
                income_account_id=income_account,
                expense_account_id=expense_account,
                has_batch_no=parse_bool(text_of(data, "ISBATCHWISEON")),
                barcode=text_of(data, "PARTNO", "ALIASNAME"),
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(item)
            await context.db.flush()

            if gst_rate is not None:
                context.info(f"Tally GST rate {gst_rate}% recorded; HSN drives tax on invoices.")
            if group_name and group_id is None:
                context.warn(
                    f"Stock group '{group_name}' was not found; the item is ungrouped.",
                    "item_group",
                )
            await context.book.bind(context.db, "stock_item", name, item.id, item.item_code)
            context.created(record, "Item", item.id, item.item_code)


# --------------------------------------------------------------------------------------
# Cost centres
# --------------------------------------------------------------------------------------


async def import_cost_centres(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Cost centre has no name")
                continue

            mapping = context.book.get("cost_centre", name)
            if mapping is not None and mapping.target_id is not None:
                await context.book.bind(context.db, "cost_centre", name, mapping.target_id, mapping.target_name)
                context.reused(record, "Cost Center", mapping.target_id, mapping.target_name)
                continue

            stmt = select(CostCenter).where(
                CostCenter.company_id == context.company_id,
                CostCenter.cost_center_name == name,
            )
            existing = (await context.db.execute(stmt)).scalars().first()
            if existing is not None:
                await context.book.bind(context.db, "cost_centre", name, existing.id, name)
                context.reused(record, "Cost Center", existing.id, name)
                continue

            parent_name = (record.source_parent or "").strip()
            parent_id = context.book.resolve("cost_centre", parent_name) if parent_name else None
            if parent_id is None:
                parent_id = context.company.default_cost_center_id
            if parent_id is not None:
                parent = await context.db.get(CostCenter, parent_id)
                if parent is not None and not parent.is_group:
                    parent.is_group = True

            cost_center = CostCenter(
                id=uuid.uuid4(),
                company_id=context.company_id,
                cost_center_name=name[:140],
                parent_cost_center_id=parent_id,
                is_group=False,
                owner=context.user.id,
                modified_by=context.user.id,
            )
            context.db.add(cost_center)
            await context.db.flush()
            await context.book.bind(context.db, "cost_centre", name, cost_center.id, name)
            context.created(record, "Cost Center", cost_center.id, name)


# --------------------------------------------------------------------------------------
# Price lists (Tally "Price Levels")
# --------------------------------------------------------------------------------------


async def import_price_lists(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    for record in records:
        async with context.row(record):
            name = (record.source_name or "").strip()
            if not name:
                context.skip(record, "Price level has no name")
                continue

            stmt = select(PriceList).where(
                PriceList.company_id == context.company_id, PriceList.price_list_name == name
            )
            price_list = (await context.db.execute(stmt)).scalars().first()
            if price_list is None:
                price_list = PriceList(
                    id=uuid.uuid4(),
                    company_id=context.company_id,
                    price_list_name=name[:140],
                    currency=context.company.default_currency,
                    selling=True,
                    owner=context.user.id,
                    modified_by=context.user.id,
                )
                context.db.add(price_list)
                await context.db.flush()
                context.created(record, "Price List", price_list.id, name)
            else:
                context.reused(record, "Price List", price_list.id, name)
            await context.book.bind(context.db, "price_list", name, price_list.id, name)

            for row in _price_rows(_raw(record)):
                item_id = context.book.item(row["item"])
                if item_id is None:
                    context.warn(
                        f"Price row skipped: stock item '{row['item']}' is not in this import.",
                        "item",
                    )
                    continue
                duplicate = (
                    await context.db.execute(
                        select(ItemPrice).where(
                            ItemPrice.item_id == item_id,
                            ItemPrice.price_list_id == price_list.id,
                            ItemPrice.valid_from.is_(None),
                        )
                    )
                ).scalars().first()
                if duplicate is not None:
                    continue
                context.db.add(
                    ItemPrice(
                        id=uuid.uuid4(),
                        company_id=context.company_id,
                        item_id=item_id,
                        price_list_id=price_list.id,
                        price_list_rate=row["rate"],
                        currency=context.company.default_currency,
                        owner=context.user.id,
                        modified_by=context.user.id,
                    )
                )
                if row["slabs"] > 1:
                    context.warn(
                        f"'{row['item']}' had {row['slabs']} quantity slabs in Tally; "
                        "the lowest-quantity rate was used.",
                        "rate",
                    )


def _price_rows(data: dict[str, Any]) -> Iterable[dict[str, Any]]:
    """Flatten Tally's nested price-level structure to one row per item."""
    for level in as_list(data.get("PRICELEVELLIST.LIST")):
        if not isinstance(level, dict):
            continue
        for entry in as_list(level.get("PRICELEVELENTRY.LIST")) or as_list(level.get("RATE.LIST")):
            if not isinstance(entry, dict):
                continue
            item = text_of(entry, "STOCKITEMNAME", "NAME")
            rates = as_list(entry.get("PRICELEVELRATE.LIST")) or [entry]
            best = None
            for rate_node in rates:
                if not isinstance(rate_node, dict):
                    continue
                value = parse_amount(text_of(rate_node, "RATE", "PRICELEVELRATE"))
                if value > ZERO and (best is None or value < best):
                    best = value
            if item and best is not None:
                yield {"item": item, "rate": best, "slabs": len(rates)}
