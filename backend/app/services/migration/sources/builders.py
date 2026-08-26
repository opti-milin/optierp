"""Constructing the pipeline's IR from ordinary spreadsheet values.

The importers read a Tally-XML-shaped dict (see :mod:`.ir` for why). A profile
author should never have to remember that a debit is a *negative* ``AMOUNT``
inside an ``ALLLEDGERENTRIES.LIST``, or that a quantity is the string
``"12 Nos"`` — so every one of those conventions is encoded exactly once, here.

The important ones, since getting any of them wrong misstates the books:

* **Sign.** ``AMOUNT`` is negative for a debit, positive for a credit.
  :func:`ledger_entry` takes explicit ``debit``/``credit`` and does the flip.
* **Quantity and rate carry their unit.** ``BILLEDQTY`` is ``"12 Nos"`` and
  ``RATE`` is ``"250.00/Nos"``; the voucher importer splits them back apart.
* **Tax is a ledger row, not a column.** A GST amount reaches the invoice only
  by being a ledger entry whose name contains a duty head (CGST/SGST/IGST/Cess),
  because that is how :func:`~...importers.vouchers._invoice_taxes` finds it.
* **Identity.** A spreadsheet has no GUIDs, so :func:`synth_guid` derives a
  stable one from the record's natural key. Same workbook twice, same GUIDs,
  and the existing duplicate detection works unchanged.
"""

from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any, Iterable, Sequence

from app.services.migration.sources.ir import ZERO, parse_amount

#: Tolerance when checking that a synthesised double entry balances. Sources
#: round tax to two places per line, so a ten-line invoice can legitimately be a
#: couple of paise out; anything larger is a real modelling error, not rounding.
BALANCE_TOLERANCE = Decimal("0.05")


# --------------------------------------------------------------------------------------
# Identity
# --------------------------------------------------------------------------------------


def synth_guid(*parts: Any) -> str:
    """A stable synthetic GUID for a record that has no native one.

    Deterministic on purpose. ``migration_imported_documents`` keys on this, so
    re-importing the same workbook must produce the same value or every voucher
    posts twice. The parts should be the record's *natural* key — voucher number
    and date, or invoice id — never the row number, which shifts the moment
    somebody sorts a sheet.

    Prefixed ``x-`` so a synthetic identity is visibly distinguishable from a
    real Tally GUID when someone is reading the staging table.
    """
    seed = "|".join("" if p is None else str(p).strip().casefold() for p in parts)
    return "x-" + hashlib.sha1(seed.encode("utf-8")).hexdigest()[:32]


# --------------------------------------------------------------------------------------
# Small value shapes the importers expect
# --------------------------------------------------------------------------------------


def qty_string(qty: Any, uom: str | None) -> str:
    """``(12, "Nos")`` -> ``"12 Nos"`` — the form ``_split_qty`` parses."""
    amount = parse_amount(qty)
    return f"{_plain(amount)} {uom}".strip() if uom else _plain(amount)


def rate_string(rate: Any, uom: str | None) -> str:
    """``(250, "Nos")`` -> ``"250/Nos"`` — the form ``_split_rate`` parses."""
    amount = parse_amount(rate)
    return f"{_plain(amount)}/{uom}" if uom else _plain(amount)


def _plain(value: Decimal) -> str:
    """Render a Decimal without exponent or trailing-zero noise."""
    text = format(value.normalize(), "f")
    return text if text not in ("-0", "") else "0"


def _amount(debit: Any, credit: Any) -> str:
    """Tally's signed amount: negative is a debit, positive is a credit."""
    dr, cr = abs(parse_amount(debit)), abs(parse_amount(credit))
    return _plain(-dr if dr else cr)


def _yes(flag: bool) -> str:
    return "Yes" if flag else "No"


# --------------------------------------------------------------------------------------
# Voucher sub-structures
# --------------------------------------------------------------------------------------


def bill_allocation(
    ref: str,
    amount: Any,
    *,
    bill_type: str = "New Ref",
    credit_period: str | None = None,
) -> dict[str, Any]:
    """One bill-wise reference on a party's ledger row.

    ``bill_type`` is Tally's vocabulary and the importer keeps it: ``New Ref`` is
    an invoice raising a bill, ``Agst Ref`` is a payment settling one. Getting it
    wrong does not lose money but does lose the payment-to-invoice link, which is
    the whole reason ageing reports work after a migration.
    """
    node: dict[str, Any] = {
        "NAME": ref,
        "BILLTYPE": bill_type,
        "AMOUNT": _plain(parse_amount(amount)),
    }
    if credit_period:
        node["BILLCREDITPERIOD"] = credit_period
    return node


def cost_centre_allocation(name: str, amount: Any) -> dict[str, Any]:
    """Tally nests cost centres two deep: category, then centre."""
    return {
        "COSTCENTREALLOCATIONS.LIST": {
            "NAME": name,
            "AMOUNT": _plain(parse_amount(amount)),
        }
    }


def ledger_entry(
    ledger: str,
    *,
    debit: Any = ZERO,
    credit: Any = ZERO,
    is_party: bool = False,
    cost_centre: str | None = None,
    bills: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """One accounting posting. Pass ``debit`` **or** ``credit``, never both."""
    node: dict[str, Any] = {
        "LEDGERNAME": ledger,
        "AMOUNT": _amount(debit, credit),
        "ISDEEMEDPOSITIVE": _yes(bool(parse_amount(debit))),
        "ISPARTYLEDGER": _yes(is_party),
    }
    if cost_centre:
        node["CATEGORYALLOCATIONS.LIST"] = cost_centre_allocation(
            cost_centre, abs(parse_amount(debit) or parse_amount(credit))
        )
    if bills:
        node["BILLALLOCATIONS.LIST"] = list(bills)
    return node


def inventory_entry(
    item: str,
    *,
    qty: Any,
    uom: str | None = None,
    rate: Any = ZERO,
    amount: Any = ZERO,
    godown: str | None = None,
    batch: str | None = None,
    ledger: str | None = None,
    expiry: date | None = None,
) -> dict[str, Any]:
    """One stock movement, with its godown/batch split and its income ledger.

    ``ledger`` is what makes an invoice line post to the right income or expense
    account; without it the importer falls back to the document's default and the
    P&L lands in one lump instead of split by revenue stream.
    """
    node: dict[str, Any] = {
        "STOCKITEMNAME": item,
        "BILLEDQTY": qty_string(qty, uom),
        "ACTUALQTY": qty_string(qty, uom),
        "RATE": rate_string(rate, uom),
        "AMOUNT": _plain(parse_amount(amount)),
    }
    if godown or batch:
        batch_node: dict[str, Any] = {
            "BATCHNAME": batch or "Primary Batch",
            "BILLEDQTY": qty_string(qty, uom),
            "ACTUALQTY": qty_string(qty, uom),
            "AMOUNT": _plain(parse_amount(amount)),
        }
        if godown:
            batch_node["GODOWNNAME"] = godown
        if expiry:
            batch_node["EXPIRYPERIOD"] = expiry.isoformat()
        node["BATCHALLOCATIONS.LIST"] = [batch_node]
    if ledger:
        node["ACCOUNTINGALLOCATIONS.LIST"] = [
            {"LEDGERNAME": ledger, "AMOUNT": _plain(parse_amount(amount))}
        ]
    return node


def bank_allocation(
    *,
    instrument_no: str | None = None,
    instrument_date: date | None = None,
    transaction_type: str | None = None,
    bank_date: date | None = None,
    amount: Any = ZERO,
) -> dict[str, Any]:
    node: dict[str, Any] = {"AMOUNT": _plain(parse_amount(amount))}
    if instrument_no:
        node["INSTRUMENTNUMBER"] = instrument_no
    if instrument_date:
        node["INSTRUMENTDATE"] = instrument_date.isoformat()
    if transaction_type:
        node["TRANSACTIONTYPE"] = transaction_type
    if bank_date:
        node["BANKERSDATE"] = bank_date.isoformat()
    return node


# --------------------------------------------------------------------------------------
# Records
# --------------------------------------------------------------------------------------


def master(
    entity_key: str,
    sequence: int,
    *,
    name: str,
    parent: str | None = None,
    data: dict[str, Any] | None = None,
    guid: str | None = None,
    reserved_name: str | None = None,
) -> dict[str, Any]:
    """A master record in the shape ``_master_record`` produces for XML."""
    payload = dict(data or {})
    payload.setdefault("NAME", name)
    if parent:
        payload.setdefault("PARENT", parent)
    return {
        "_entity_key": entity_key,
        "_sequence": sequence,
        "guid": guid,
        "name": (name or "").strip(),
        "parent": (parent or "").strip() or None,
        "reserved_name": reserved_name,
        "data": payload,
    }


def opening_ledger(
    sequence: int,
    *,
    name: str,
    balance: Any,
    is_debit: bool,
    parent: str | None = None,
    guid: str | None = None,
) -> dict[str, Any]:
    """A ledger's opening balance as its own record.

    ``is_debit`` rather than a signed number, because every source spells the
    side differently — ``Dr``/``Cr``, ``debit``/``credit``, a negative figure —
    and the profile has already decided which. Stored signed, Tally-style.
    """
    magnitude = abs(parse_amount(balance))
    signed = -magnitude if is_debit else magnitude
    return {
        "_entity_key": "opening_ledger",
        "_sequence": sequence,
        "guid": guid,
        "name": (name or "").strip(),
        "parent": parent,
        "opening_balance": _plain(signed),
        "opening_value": None,
        "opening_rate": None,
        "data": {"NAME": name, "OPENINGBALANCE": _plain(signed)},
    }


def opening_stock(
    sequence: int,
    *,
    name: str,
    qty: Any,
    value: Any,
    uom: str | None = None,
    godown: str | None = None,
    guid: str | None = None,
) -> dict[str, Any]:
    """A stock item's opening quantity and value, per godown where known."""
    quantity = qty_string(qty, uom)
    data: dict[str, Any] = {
        "NAME": name,
        "OPENINGBALANCE": quantity,
        "OPENINGVALUE": _plain(parse_amount(value)),
    }
    if godown:
        data["BATCHALLOCATIONS.LIST"] = [
            {
                "GODOWNNAME": godown,
                "BATCHNAME": "Primary Batch",
                "OPENINGBALANCE": quantity,
                "OPENINGVALUE": _plain(parse_amount(value)),
            }
        ]
    return {
        "_entity_key": "opening_stock",
        "_sequence": sequence,
        "guid": guid,
        "name": (name or "").strip(),
        "parent": None,
        "opening_balance": quantity,
        "opening_value": _plain(parse_amount(value)),
        "opening_rate": None,
        "data": data,
    }


def voucher(
    entity_key: str,
    sequence: int,
    *,
    stage: int,
    voucher_type: str,
    voucher_number: str | None,
    posting_date: date | None,
    party: str | None = None,
    narration: str | None = None,
    guid: str | None = None,
    is_cancelled: bool = False,
    is_optional: bool = False,
    ledger_entries: Sequence[dict[str, Any]] = (),
    inventory_entries: Sequence[dict[str, Any]] = (),
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A voucher record in the shape ``_voucher_record`` produces for XML."""
    payload: dict[str, Any] = dict(data or {})
    payload.setdefault("VOUCHERTYPENAME", voucher_type)
    if voucher_number:
        payload.setdefault("VOUCHERNUMBER", voucher_number)
    if party:
        payload.setdefault("PARTYLEDGERNAME", party)
    if narration:
        payload.setdefault("NARRATION", narration)
    if ledger_entries:
        payload["ALLLEDGERENTRIES.LIST"] = list(ledger_entries)
    if inventory_entries:
        payload["ALLINVENTORYENTRIES.LIST"] = list(inventory_entries)
    return {
        "_entity_key": entity_key,
        "_sequence": sequence,
        "_stage": stage,
        "guid": guid,
        "voucher_type": voucher_type,
        "voucher_number": voucher_number,
        "date": posting_date,
        "party": party,
        "narration": narration,
        "is_cancelled": is_cancelled,
        "is_optional": is_optional,
        "alter_id": None,
        "master_id": None,
        "vch_key": None,
        "remote_id": None,
        "data": payload,
    }


# --------------------------------------------------------------------------------------
# Synthesised double entry
# --------------------------------------------------------------------------------------


class Posting:
    """Accumulates one voucher's ledger rows and refuses to hand back an unbalanced set.

    Sources like Zoho Books ship documents, not journals: an invoice carries a
    customer, some lines and a tax total, with the double entry left implicit.
    Rebuilding it is unavoidable — but a rebuild that is quietly a rupee out
    would post a wrong trial balance and be found months later, so the balance is
    checked here and a failure is reported as a staging error rather than nudged.
    """

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._debit = ZERO
        self._credit = ZERO

    def debit(self, ledger: str | None, amount: Any, **kwargs: Any) -> "Posting":
        return self._add(ledger, amount, side="debit", **kwargs)

    def credit(self, ledger: str | None, amount: Any, **kwargs: Any) -> "Posting":
        return self._add(ledger, amount, side="credit", **kwargs)

    def _add(self, ledger: str | None, amount: Any, *, side: str, **kwargs: Any) -> "Posting":
        value = abs(parse_amount(amount))
        # A zero row is noise on every screen it reaches — an invoice with no IGST
        # should not carry an IGST line reading 0.00.
        if not ledger or value == ZERO:
            return self
        self.rows.append(ledger_entry(ledger, **{side: value}, **kwargs))
        if side == "debit":
            self._debit += value
        else:
            self._credit += value
        return self

    @property
    def difference(self) -> Decimal:
        return self._debit - self._credit

    @property
    def balanced(self) -> bool:
        return abs(self.difference) <= BALANCE_TOLERANCE

    def round_off(self, ledger: str) -> "Posting":
        """Absorb a sub-tolerance rounding difference into a round-off ledger.

        Only ever called for a difference already inside ``BALANCE_TOLERANCE`` —
        this exists so the two paise a source's per-line tax rounding leaves
        behind land somewhere explicit and visible, instead of being silently
        added to a revenue line where nobody would ever find them.
        """
        gap = self.difference
        if gap == ZERO or abs(gap) > BALANCE_TOLERANCE:
            return self
        return self.credit(ledger, gap) if gap > ZERO else self.debit(ledger, -gap)


def tax_rows(
    posting: Posting,
    *,
    side: str,
    taxes: Iterable[tuple[str | None, Any]],
) -> Posting:
    """Add the GST rows of a document, skipping the heads it did not use.

    ``taxes`` is ``[(ledger name, amount), ...]`` — typically CGST/SGST for an
    intra-state document and IGST for inter-state. Both sets are passed and the
    unused one drops out on its zero amount, so a profile never has to decide
    which of the two a row was.
    """
    for ledger, amount in taxes:
        posting._add(ledger, amount, side=side)
    return posting
