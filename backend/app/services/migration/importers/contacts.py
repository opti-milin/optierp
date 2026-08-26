"""Addresses and contact people — from wherever the source happens to keep them.

Two sources, one destination. Tally writes a party's address and contact person
*inside* the ledger record; a spreadsheet export almost always puts them on their
own sheets, joined back to the party by id. Both arrive here, and both produce
the same Address and Contact rows, which is why the write itself lives in one
place: :func:`ensure_address` and :func:`ensure_contact`. Duplicate handling is
the reason that matters — ``address_title`` is unique per company and a Customer
and a Supplier can legitimately share a name, so the rule for what happens on a
collision has to be one rule, not two that drift.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.models.migration import MigrationStagingRecord
from app.models.selling import Address, Contact
from app.services.migration.context import ImportContext
from app.services.migration.sources.tally_xml import as_list, text_of

#: What a source may call an address type -> ours. Anything else becomes Billing,
#: because a party's address with no stated purpose is where you invoice them.
ADDRESS_TYPES = {
    "billing": "Billing",
    "bill to": "Billing",
    "invoice": "Billing",
    "shipping": "Shipping",
    "ship to": "Shipping",
    "delivery": "Shipping",
    "dispatch": "Shipping",
    "office": "Office",
    "registered": "Office",
    "permanent": "Office",
}


def _raw(record: MigrationStagingRecord) -> dict[str, Any]:
    data = (record.raw or {}).get("data")
    return data if isinstance(data, dict) else {}


def _lines(node: Any) -> list[str]:
    if isinstance(node, dict):
        return [str(v).strip() for v in as_list(node.get("ADDRESS")) if str(v).strip()]
    if isinstance(node, list):
        return [str(v).strip() for v in node if str(v).strip()]
    return []


def _flag(value: str | None) -> bool:
    return (value or "").strip().casefold() in {"yes", "true", "1"}


# --------------------------------------------------------------------------------------
# The single place an Address or a Contact is written
# --------------------------------------------------------------------------------------


async def ensure_address(
    context: ImportContext,
    *,
    party_type: str,
    party_id: uuid.UUID,
    party_name: str,
    lines: list[str],
    title: str | None = None,
    address_type: str = "Billing",
    city: str | None = None,
    state: str | None = None,
    pincode: str | None = None,
    country: str | None = None,
    disabled: bool = False,
) -> Address | None:
    """One Address for this party, reusing an identical one if it is already here.

    ``address_title`` carries a uniqueness constraint per company, and three
    things collide on it in practice: the same party imported twice, a Customer
    and a Supplier with the same trading name, and a party with both a billing
    and a shipping address. The title is qualified rather than the row dropped,
    because losing a shipping address silently is how deliveries go to the wrong
    place.
    """
    if not lines and not (city or state or pincode):
        return None

    link = {"customer_id" if party_type == "Customer" else "supplier_id": party_id}
    line1 = (lines[0] if lines else party_name)[:240]
    existing = (
        await context.db.execute(
            select(Address).where(
                Address.company_id == context.company_id,
                Address.address_line1 == line1,
                Address.address_type == address_type,
                getattr(Address, "customer_id" if party_type == "Customer" else "supplier_id")
                == party_id,
            )
        )
    ).scalars().first()
    if existing is not None:
        return existing

    wanted = (title or party_name)[:140]
    address = Address(
        id=uuid.uuid4(),
        company_id=context.company_id,
        address_title=await _free_title(context, wanted, party_type, address_type),
        address_type=address_type,
        address_line1=line1,
        address_line2=(" ".join(lines[1:])[:240] or None) if len(lines) > 1 else None,
        city=(city or (lines[-1] if len(lines) > 2 else None) or None),
        state=state[:100] if state else None,
        country=country[:100] if country else context.company.country_code,
        pincode=pincode[:20] if pincode else None,
        disabled=disabled,
        owner=context.user.id,
        modified_by=context.user.id,
        **link,
    )
    if address.city:
        address.city = address.city[:100]
    context.db.add(address)
    await context.db.flush()
    return address


async def _free_title(
    context: ImportContext, wanted: str, party_type: str, address_type: str
) -> str:
    """``wanted``, then ``wanted (Customer)``, then ``wanted (Customer Billing) 2``…"""
    for candidate in (
        wanted,
        f"{wanted[:120]} ({party_type})",
        f"{wanted[:110]} ({party_type} {address_type})",
    ):
        if not await _title_taken(context, candidate):
            return candidate[:140]
    stem = f"{wanted[:110]} ({party_type} {address_type})"
    for suffix in range(2, 100):
        candidate = f"{stem} {suffix}"[:140]
        if not await _title_taken(context, candidate):
            return candidate
    return f"{wanted[:120]} {uuid.uuid4().hex[:8]}"[:140]


async def _title_taken(context: ImportContext, title: str) -> bool:
    row = (
        await context.db.execute(
            select(Address.id).where(
                Address.company_id == context.company_id,
                Address.address_title == title[:140],
            )
        )
    ).first()
    return row is not None


async def ensure_contact(
    context: ImportContext,
    *,
    party_type: str,
    party_id: uuid.UUID,
    first_name: str,
    last_name: str | None = None,
    email: str | None = None,
    phone: str | None = None,
    mobile: str | None = None,
    designation: str | None = None,
    disabled: bool = False,
) -> Contact | None:
    """One Contact per (first, last, email), which is the table's own uniqueness."""
    if not first_name:
        return None
    link = {"customer_id" if party_type == "Customer" else "supplier_id": party_id}
    first = first_name[:140]
    last = (last_name or None) and last_name[:140]
    mail = (email or None) and email[:140]
    existing = (
        await context.db.execute(
            select(Contact).where(
                Contact.company_id == context.company_id,
                Contact.first_name == first,
                Contact.last_name.is_(None) if last is None else Contact.last_name == last,
                Contact.email_id.is_(None) if mail is None else Contact.email_id == mail,
            )
        )
    ).scalars().first()
    if existing is not None:
        # The same person can be reached about both a sale and a purchase; fill
        # in whichever link is still empty rather than creating a second row that
        # would violate the unique constraint anyway.
        for field, value in link.items():
            if getattr(existing, field) is None:
                setattr(existing, field, value)
        return existing

    contact = Contact(
        id=uuid.uuid4(),
        company_id=context.company_id,
        first_name=first,
        last_name=last,
        email_id=mail,
        mobile_no=(mobile or None) and mobile[:40],
        phone=(phone or None) and phone[:40],
        designation=(designation or None) and designation[:140],
        disabled=disabled,
        owner=context.user.id,
        modified_by=context.user.id,
        **link,
    )
    context.db.add(contact)
    await context.db.flush()
    return contact


# --------------------------------------------------------------------------------------
# Sheets of their own
# --------------------------------------------------------------------------------------


def _resolve_party(context: ImportContext, name: str | None) -> tuple[str, uuid.UUID] | None:
    """``("Customer", id)`` for a party name, whichever side it came in on."""
    if not name:
        return None
    customer = context.book.customer(name)
    if customer is not None:
        return "Customer", customer
    supplier = context.book.supplier(name)
    if supplier is not None:
        return "Supplier", supplier
    return None


async def import_addresses(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    for record in records:
        async with context.row(record):
            data = _raw(record)
            party_name = text_of(data, "PARTYNAME")
            party = _resolve_party(context, party_name)
            if party is None:
                context.skip(
                    record,
                    f"'{party_name}' is not a Customer or Supplier in this import, "
                    "so the address has nothing to belong to.",
                )
                continue
            party_type, party_id = party

            lines = _lines(data.get("ADDRESS.LIST"))
            attention = text_of(data, "ATTENTION")
            address = await ensure_address(
                context,
                party_type=party_type,
                party_id=party_id,
                party_name=party_name or "",
                title=attention or party_name,
                lines=lines,
                address_type=ADDRESS_TYPES.get(
                    (text_of(data, "ADDRESSTYPE") or "").strip().casefold(), "Billing"
                ),
                city=text_of(data, "CITYNAME"),
                state=text_of(data, "LEDSTATENAME"),
                pincode=text_of(data, "PINCODE"),
                country=text_of(data, "COUNTRYNAME"),
                disabled=_flag(text_of(data, "ISDELETED")),
            )
            if address is None:
                context.skip(record, "The address row has no address to import")
                continue

            # A phone on an address sheet belongs to the party, and Address has
            # nowhere to keep it. Rather than drop it, it becomes the party's
            # contact number — findable, which a discarded value never is.
            phone = text_of(data, "LEDGERPHONE")
            if phone and party_name:
                await ensure_contact(
                    context,
                    party_type=party_type,
                    party_id=party_id,
                    first_name=(attention or party_name),
                    phone=phone,
                )
            context.created(record, "Address", address.id, address.address_title)


async def import_contacts(context: ImportContext, records: list[MigrationStagingRecord]) -> None:
    for record in records:
        async with context.row(record):
            data = _raw(record)
            party_name = text_of(data, "PARTYNAME")
            party = _resolve_party(context, party_name)
            if party is None:
                context.skip(
                    record,
                    f"'{party_name}' is not a Customer or Supplier in this import, "
                    "so the contact has nothing to belong to.",
                )
                continue
            party_type, party_id = party

            first = text_of(data, "FIRSTNAME")
            if not first:
                context.skip(record, "The contact has no name")
                continue
            salutation = text_of(data, "SALUTATION")
            contact = await ensure_contact(
                context,
                party_type=party_type,
                party_id=party_id,
                first_name=first,
                last_name=text_of(data, "LASTNAME"),
                email=text_of(data, "EMAIL"),
                phone=text_of(data, "LEDGERPHONE"),
                mobile=text_of(data, "LEDGERMOBILE"),
                # Salutation has no column of its own here, and a designation is
                # the nearest true thing it can be: both answer "how is this
                # person addressed". Only used when there is no real designation.
                designation=text_of(data, "DESIGNATION") or salutation,
                disabled=_flag(text_of(data, "ISDELETED")),
            )
            if contact is None:  # pragma: no cover - guarded by the name check above
                context.skip(record, "The contact has no name")
                continue
            label = " ".join(
                part for part in (contact.first_name, contact.last_name) if part
            )
            context.created(record, "Contact", contact.id, label)
