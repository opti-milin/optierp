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

from app.core.exceptions import ValidationError
from app.models.accounts import Account, CostCenter
from app.models.accounts.common import ROOT_TYPE_REPORT
from app.models.buying import Supplier
from app.models.core import UOM, UOMConversion
from app.models.selling import Address, Contact, Customer
from app.models.stock import Item, ItemGroup, ItemPrice, PriceList, Warehouse
from app.models.tally import TallyStagingRecord
from app.services.tally.catalogue import (
    GST_REGISTRATION_TYPES,
    classify_group,
    normalise_unit,
    resolve_group_spec,
)
from app.services.tally.context import ImportContext
from app.services.tally.parser import as_list, parse_amount, parse_bool, text_of

ZERO = Decimal("0")
_SLUG = re.compile(r"[^a-z0-9_]+")

# GSTIN: 2-digit state code, 10-char PAN, entity number, Z, checksum.
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z]{1}[1-9A-Z]{1}Z[0-9A-Z]{1}$")


def _slugify(label: str) -> str:
    return _SLUG.sub("_", (label or "").lower()).strip("_") or "node"


def _raw(record: TallyStagingRecord) -> dict[str, Any]:
    data = (record.raw or {}).get("data")
    return data if isinstance(data, dict) else {}


def _sorted_by_depth(
    records: list[TallyStagingRecord],
) -> list[TallyStagingRecord]:
    """Parents before children.

    Tally exports a tree flat and unordered. We walk it iteratively: emit every
    record whose parent is already emitted (or absent), repeat. Anything left
    after a full pass has a missing or circular parent and is emitted last so it
    fails loudly on its own row rather than silently reordering the tree.
    """
    remaining = list(records)
    emitted_names: set[str] = set()
    ordered: list[TallyStagingRecord] = []
    while remaining:
        progress = False
        deferred = []
        for record in remaining:
            parent = (record.tally_parent or "").strip().casefold()
            if not parent or parent in emitted_names:
                ordered.append(record)
                emitted_names.add((record.tally_name or "").strip().casefold())
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
    if root is None:
        raise ValidationError(
            f"This company has no '{root_type}' root account. Seed a Chart of "
            "Accounts before importing from Tally."
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


async def import_groups(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    """Tally ledger groups -> Chart of Accounts group nodes."""
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.tally_name or "").strip()
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
            parent_name = (record.tally_parent or "").strip()
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


def _ledger_address(data: dict[str, Any]) -> tuple[list[str], str | None, str | None]:
    """(address lines, state, pincode) from a Tally ledger's mailing details."""
    lines: list[str] = []
    address_node = data.get("ADDRESS.LIST")
    if isinstance(address_node, dict):
        lines = [str(v) for v in as_list(address_node.get("ADDRESS")) if v]
    elif isinstance(address_node, list):
        lines = [str(v) for v in address_node if isinstance(v, str)]
    state = text_of(data, "LEDSTATENAME", "STATENAME", "PRIORSTATENAME")
    pincode = text_of(data, "PINCODE", "LEDGERPINCODE")
    return [line for line in lines if line.strip()], state, pincode


async def import_ledgers(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    """Every Tally ledger becomes a COA leaf account.

    Party ledgers (under Sundry Debtors/Creditors) get a Customer or Supplier in
    :func:`import_parties`, which runs at the next stage using the same rows.
    """
    for record in records:
        async with context.row(record):
            name = (record.tally_name or "").strip()
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

            parent_name = (record.tally_parent or text_of(data, "PARENT") or "").strip()
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
    """Carry Tally's mailing details across as an Address + Contact."""
    lines, state, pincode = _ledger_address(data)
    email = text_of(data, "EMAIL", "EMAILCC")
    phone = text_of(data, "LEDGERPHONE", "PHONENUMBER", "LEDGERMOBILE", "MOBILENUMBER")
    contact_person = text_of(data, "LEDGERCONTACT", "CONTACTPERSON")
    link = {"customer_id" if party_type == "Customer" else "supplier_id": party_id}

    if lines or state or pincode:
        # address_title is unique per company; a Customer and a Supplier can share
        # a Tally name, so qualify the title rather than lose the second one.
        title = party_name[:140]
        clash = (
            await context.db.execute(
                select(Address).where(
                    Address.company_id == context.company_id, Address.address_title == title
                )
            )
        ).scalars().first()
        if clash is not None:
            title = f"{party_name[:120]} ({party_type})"
        context.db.add(
            Address(
                id=uuid.uuid4(),
                company_id=context.company_id,
                address_title=title,
                address_type="Billing",
                address_line1=(lines[0] if lines else party_name)[:240],
                address_line2=(" ".join(lines[1:])[:240] or None) if len(lines) > 1 else None,
                city=(lines[-1][:100] if len(lines) > 2 else None),
                state=state[:100] if state else None,
                country=context.company.country_code,
                pincode=pincode[:20] if pincode else None,
                owner=context.user.id,
                modified_by=context.user.id,
                **link,
            )
        )
    if contact_person or email or phone:
        first, _, last = (contact_person or party_name).partition(" ")
        context.db.add(
            Contact(
                id=uuid.uuid4(),
                company_id=context.company_id,
                first_name=(first or party_name)[:140],
                last_name=last[:140] or None,
                email_id=email[:140] if email else None,
                mobile_no=phone[:40] if phone else None,
                owner=context.user.id,
                modified_by=context.user.id,
                **link,
            )
        )


async def import_parties(
    context: ImportContext, records: list[TallyStagingRecord], *, party_type: str
) -> None:
    """Ledgers under Sundry Debtors / Sundry Creditors -> Customer / Supplier."""
    entity_key = "customer" if party_type == "Customer" else "supplier"
    model = Customer if party_type == "Customer" else Supplier
    name_field = "customer_name" if party_type == "Customer" else "supplier_name"

    for record in records:
        async with context.row(record):
            name = (record.tally_name or "").strip()
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


async def import_units(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    """Tally simple units -> UOM; compound units -> UOM + UOM Conversion."""
    for record in records:
        async with context.row(record):
            symbol = (record.tally_name or "").strip()
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


async def import_stock_groups(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.tally_name or "").strip()
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

            parent_name = (record.tally_parent or "").strip()
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


async def import_godowns(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.tally_name or "").strip()
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

            parent_name = (record.tally_parent or "").strip()
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


async def import_stock_items(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    """Tally stock items -> Item, with HSN/GST and the default unit carried over."""
    for record in records:
        async with context.row(record):
            name = (record.tally_name or "").strip()
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

            group_name = (record.tally_parent or text_of(data, "PARENT") or "").strip()
            group_id = context.book.resolve("stock_group", group_name) if group_name else None
            unit_symbol = text_of(data, "BASEUNITS", "ADDITIONALUNITS")
            stock_uom = normalise_unit(unit_symbol) or "Nos"
            await _ensure_uom(context, stock_uom)

            hsn = text_of(data, "HSNCODE", "GSTHSNCODE", "HSN")
            gst_rate = _item_gst_rate(data)
            category = text_of(data, "CATEGORY")

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


async def import_cost_centres(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    for record in _sorted_by_depth(records):
        async with context.row(record):
            name = (record.tally_name or "").strip()
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

            parent_name = (record.tally_parent or "").strip()
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


async def import_price_lists(context: ImportContext, records: list[TallyStagingRecord]) -> None:
    for record in records:
        async with context.row(record):
            name = (record.tally_name or "").strip()
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
