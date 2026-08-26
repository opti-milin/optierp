"""Folding a workbook into the pipeline's IR, one shape at a time.

Given a :class:`~.workbook.Workbook` and a :class:`~.profiles.SourceProfile`, this
produces the same :class:`~.ir.ParsedFile` a Tally XML export produces — after
which the entire rest of Module 12 cannot tell the two apart.

Two rules run through everything here:

**Nothing is invented.** Where a source states an amount, it is carried across to
the paisa. Where a source states a *document* but leaves its double entry
implicit — Zoho Books ships invoices, not journals — the entry is rebuilt from
the document's own totals and then checked to balance. A rebuild that does not
balance is staged as an error carrying the difference, never nudged into shape.

**Nothing is dropped silently.** A sheet the profile marks ``reference``, a
voucher type with no target, a row missing a required field: each is counted and
explained, so the import report is the honest answer to "what came across".
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal
from typing import Any, Callable

from app.services.migration.catalogue import (
    ENTITY_BY_KEY,
    classify_voucher,
    normalise_unit,
)
from app.services.migration.sources import builders as b
from app.services.migration.sources.ir import (
    ZERO,
    ParsedFile,
    parse_amount,
    parse_bool,
    parse_date,
)
from app.services.migration.sources.profiles import (
    ChildSpec,
    SheetSpec,
    SourceProfile,
    missing_required,
    resolve,
    value,
)
from app.services.migration.sources.workbook import Sheet, Workbook, normalise_header

#: Words a source may use for the debit side. Anything else is a credit — the
#: asymmetry is deliberate: mislabelling a credit as a debit doubles the error,
#: so only an explicit debit marker counts as one.
_DEBIT_WORDS = {"dr", "d", "debit", "debits", "-", "by"}
_CREDIT_WORDS = {"cr", "c", "credit", "credits", "+", "to"}


class _Counter:
    """Sequence numbers, shared across every sheet so staging rows sort sensibly."""

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        self._value += 1
        return self._value


# --------------------------------------------------------------------------------------
# Typed reads
# --------------------------------------------------------------------------------------


def _text(row: dict[str, Any], spec: SheetSpec | ChildSpec, name: str) -> str | None:
    raw = value(row, spec, name)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _num(row: dict[str, Any], spec: SheetSpec | ChildSpec, name: str) -> Decimal:
    return parse_amount(value(row, spec, name))


def _day(row: dict[str, Any], spec: SheetSpec | ChildSpec, name: str) -> date | None:
    return parse_date(value(row, spec, name))


def _flag(row: dict[str, Any], spec: SheetSpec | ChildSpec, name: str) -> bool:
    return parse_bool(value(row, spec, name))


def _is_debit(marker: str | None, *, amount: Decimal = ZERO, default: bool = False) -> bool:
    """Which side a line sits on, from whatever the source wrote.

    Sources disagree completely here: ``Dr``/``Cr``, ``debit``/``credit``, or a
    bare signed number. All three are accepted, in that order of authority — an
    explicit marker always beats the sign, because a source that writes both and
    disagrees with itself is telling us the sign is presentational.
    """
    if marker:
        folded = str(marker).strip().casefold()
        if folded in _DEBIT_WORDS:
            return True
        if folded in _CREDIT_WORDS:
            return False
    if amount < ZERO:
        return True
    return default


def _active(row: dict[str, Any], spec: SheetSpec) -> bool:
    """False only when the source explicitly says the record is inactive."""
    raw = value(row, spec, "is_active")
    if raw is None:
        return True
    folded = str(raw).strip().casefold()
    return folded not in ("no", "false", "0", "inactive", "disabled", "archived")


# --------------------------------------------------------------------------------------
# The normaliser
# --------------------------------------------------------------------------------------


class Normaliser:
    """One run of one workbook through one profile."""

    def __init__(self, book: Workbook, profile: SourceProfile) -> None:
        self.book = book
        self.profile = profile
        self.result = ParsedFile()
        self.result.source_app = profile.app
        self.result.source_profile = profile.key
        self.result.sheet_map = profile.to_dict()
        self.seq = _Counter()

        #: Ledger names the workbook itself defines. Anything a synthesised
        #: posting needs that is *not* in here has to be created, and said so.
        self._known_ledgers: set[str] = set()
        self._invented: dict[str, str] = {}
        #: ``{namespace: {folded source id: the name we filed it under}}``. Zoho
        #: keys Contact_Addresses and Contact_Persons on `contact_id` and
        #: Bank_Transactions on `bank_account_id` — never on the name. Without
        #: this every address would hang off a party called "CUS001" and every
        #: statement line off a bank called "BA001", neither of which any
        #: importer could resolve.
        self._aliases: dict[str, dict[str, str]] = {}
        #: Opening balances, keyed so the same account cannot be booked twice.
        #: Both a master sheet's own opening column and a dedicated
        #: Opening_Balances sheet are read, because neither is reliably complete:
        #: Zoho's Opening_Balances covers its chart of accounts but not its
        #: contacts, so trusting only the dedicated sheet silently dropped every
        #: customer's and supplier's opening balance. The dedicated sheet wins
        #: per name; the master column fills the gaps.
        self._opening_ledgers: dict[str, tuple[int, dict[str, Any]]] = {}
        self._opening_stock: dict[tuple[str, str], tuple[int, dict[str, Any]]] = {}
        self._dates: list[date] = []

    def _stage_opening_ledger(self, priority: int, record: dict[str, Any]) -> None:
        key = normalise_header(record.get("name") or "")
        if not key:
            return
        current = self._opening_ledgers.get(key)
        if current is None or priority >= current[0]:
            self._opening_ledgers[key] = (priority, record)

    def _stage_opening_stock(self, priority: int, record: dict[str, Any], godown: str | None) -> None:
        key = (normalise_header(record.get("name") or ""), normalise_header(godown or ""))
        if not key[0]:
            return
        current = self._opening_stock.get(key)
        if current is None or priority >= current[0]:
            self._opening_stock[key] = (priority, record)

    # -- entry point ------------------------------------------------------------------

    def run(self) -> ParsedFile:
        masters = (
            "ledger_master", "group_master", "item_master", "simple_master",
            # Setup masters go in the same first pass: a statement line resolves
            # its bank by the id the bank-account sheet defines, and a party
            # names payment terms the terms sheet defines. Reading them in
            # profile order instead would make correctness depend on how the
            # sheets happen to be listed.
            "payment_terms", "currency_master", "price_list_master", "tax_master",
            "bank_account", "budget_master", "asset_master",
        )
        # Addresses and contacts join to a party by the source's own id, which
        # only `_shape_ledger_master` knows how to translate into a name — so
        # they cannot be read in the same pass as the sheet that teaches it.
        linked = ("party_address", "party_contact")
        openings = ("opening_ledger", "opening_stock")
        # Order matters only for `_known_ledgers`: a voucher shaper asks whether a
        # ledger exists, so every master sheet has to have been read by then.
        for phase in (masters, linked, openings, None):
            for spec in self.profile.sheets:
                if phase is not None and spec.kind not in phase:
                    continue
                if phase is None and spec.kind in masters + linked + openings:
                    continue
                self._sheet(spec)

        self._emit_invented_ledgers()
        self._finish()
        return self.result

    def _sheet(self, spec: SheetSpec) -> None:
        sheet = self.book.by_name(spec.sheet)
        if sheet is None:
            return
        if spec.kind == "reference":
            if sheet.rows:
                self.result.note_unknown(sheet.name, len(sheet.rows))
                self.result.skipped_sheets[sheet.name] = (
                    len(sheet.rows),
                    spec.reason or "Not imported.",
                )
            return

        trimmed = resolve(spec, sheet)
        gaps = missing_required(trimmed, sheet)
        if gaps:
            self.result.warnings.append(
                f"Sheet '{sheet.name}' was not imported: it has no column for "
                f"{', '.join(gaps)}. Map those columns and re-parse."
            )
            self.result.note_unknown(sheet.name, len(sheet.rows))
            self.result.skipped_sheets[sheet.name] = (
                len(sheet.rows), f"missing required column(s): {', '.join(gaps)}"
            )
            return

        handler: Callable[[SheetSpec, Sheet], None] | None = getattr(
            self, f"_shape_{spec.kind}", None
        )
        if handler is None:  # pragma: no cover - KINDS and methods are kept in step
            self.result.warnings.append(
                f"Sheet '{sheet.name}' claims an unsupported shape '{spec.kind}'."
            )
            return
        handler(trimmed, sheet)

    # -- masters ----------------------------------------------------------------------

    def _shape_group_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            self.result.add(
                spec.entity or "group",
                b.master(
                    spec.entity or "group",
                    self.seq.next(),
                    name=name,
                    parent=_clean_parent(_text(row, spec, "parent")),
                    guid=b.synth_guid(self.profile.key, "group", name),
                ),
            )

    def _shape_ledger_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "ledger"
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            parent = self._ledger_group(row, spec)
            data: dict[str, Any] = {"NAME": name}
            if parent:
                data["PARENT"] = parent
            _put(data, "PARTYGSTIN", _text(row, spec, "gstin"))
            _put(data, "INCOMETAXNUMBER", _text(row, spec, "pan"))
            _put(data, "LEDSTATENAME", _text(row, spec, "state"))
            _put(data, "PINCODE", _text(row, spec, "pincode"))
            _put(data, "EMAIL", _text(row, spec, "email"))
            _put(data, "LEDGERPHONE", _text(row, spec, "phone"))
            _put(data, "CURRENCYNAME", _text(row, spec, "currency"))
            # Party-only columns. These do nothing to the Account; they are read
            # off the same row by `import_parties` when it builds the Customer or
            # Supplier. `credit_days` in particular was mapped in the Tally
            # profile from the start and never emitted here, so every workbook
            # import silently lost its credit terms.
            _put(data, "BILLCREDITPERIOD", _text(row, spec, "credit_days"))
            _put(data, "CREDITLIMIT", _text(row, spec, "credit_limit"))
            _put(data, "LEDGERLEGALNAME", _text(row, spec, "company_name"))
            _put(data, "PARTYENTITYKIND", _text(row, spec, "entity_kind"))
            _put(data, "PAYMENTTERMSNAME", _text(row, spec, "payment_terms"))
            _put(data, "TAXCATEGORYNAME", _text(row, spec, "tax_category"))
            _put(data, "PARTYGROUPNAME", _text(row, spec, "party_group"))
            _put(data, "TERRITORYNAME", _text(row, spec, "territory"))
            _put(data, "BANKACCOUNTNO", _text(row, spec, "bank_account_no"))
            _put(data, "IFSCODE", _text(row, spec, "ifsc"))
            _put(data, "DESCRIPTION", _text(row, spec, "notes"))
            self._remember_id("party", _text(row, spec, "key"), name)
            address = [
                line for line in (_text(row, spec, "address"), _text(row, spec, "city"))
                if line
            ]
            if address:
                data["ADDRESS.LIST"] = {"ADDRESS": address}
            if not _active(row, spec):
                data["ISDELETED"] = "Yes"

            opening = _num(row, spec, "opening")
            is_debit = _is_debit(_text(row, spec, "dr_cr"), amount=opening, default=True)
            if opening:
                data["OPENINGBALANCE"] = b._plain(-abs(opening) if is_debit else abs(opening))

            self._known_ledgers.add(normalise_header(name))
            self.result.add(
                entity,
                b.master(
                    entity,
                    self.seq.next(),
                    name=name,
                    parent=parent,
                    data=data,
                    guid=b.synth_guid(self.profile.key, "ledger", name),
                ),
            )
            if opening:
                self._stage_opening_ledger(
                    1,
                    b.opening_ledger(
                        self.seq.next(),
                        name=name,
                        balance=opening,
                        is_debit=is_debit,
                        parent=parent,
                        guid=b.synth_guid(self.profile.key, "opening_ledger", name),
                    ),
                )

    def _ledger_group(self, row: dict[str, Any], spec: SheetSpec) -> str | None:
        """Which reserved group this account belongs under.

        Three sources of truth, most specific first: an explicit party type
        (Zoho's ``contact_type``), the parent account's name (which is how Zoho
        files its GST accounts), then the account type. Falling back to the raw
        parent keeps a source that already speaks Tally's vocabulary intact.
        """
        party = _text(row, spec, "party_type")
        if party:
            mapped = self.profile.party_types.get(party.strip().casefold())
            if mapped:
                return mapped

        parent = _clean_parent(_text(row, spec, "parent"))
        if parent:
            mapped = self.profile.account_parents.get(normalise_header(parent))
            if mapped:
                return mapped

        account_type = _text(row, spec, "account_type")
        if account_type:
            mapped = self.profile.account_types.get(account_type.strip().casefold())
            if mapped:
                return mapped
        return parent

    def _shape_item_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "stock_item"
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            parent = _clean_parent(_text(row, spec, "parent"))
            uom = normalise_unit(_text(row, spec, "uom") or "") or None
            data: dict[str, Any] = {"NAME": name}
            if parent:
                data["PARENT"] = parent
            _put(data, "CATEGORY", _text(row, spec, "category"))
            _put(data, "BASEUNITS", uom)
            _put(data, "HSNCODE", _text(row, spec, "hsn"))
            _put(data, "DESCRIPTION", _text(row, spec, "description"))
            _put(data, "PARTNO", _text(row, spec, "code"))
            _put(data, "INCOMELEDGER", _text(row, spec, "income_account"))
            _put(data, "EXPENSELEDGER", _text(row, spec, "expense_account"))
            rate = _num(row, spec, "standard_rate")
            if rate:
                data["STANDARDPRICE"] = b._plain(rate)
            gst_rate = _num(row, spec, "gst_rate")
            if gst_rate:
                # The importer reads the item's GST rate off Tally's nested
                # rate-details block, so it has to be written in that shape or the
                # rate is lost and every invoice falls back to the item default.
                data["GSTDETAILS.LIST"] = {
                    "STATEWISEDETAILS.LIST": {
                        "RATEDETAILS.LIST": [
                            {"GSTRATEDUTYHEAD": "Integrated Tax", "GSTRATE": b._plain(gst_rate)}
                        ]
                    }
                }

            self.result.add(
                entity,
                b.master(
                    entity,
                    self.seq.next(),
                    name=name,
                    parent=parent,
                    data=data,
                    guid=b.synth_guid(self.profile.key, "item", name),
                ),
            )

            qty = _num(row, spec, "opening_qty")
            if qty:
                self._stage_opening_stock(
                    1,
                    b.opening_stock(
                        self.seq.next(),
                        name=name,
                        qty=qty,
                        value=_num(row, spec, "opening_value"),
                        uom=uom,
                        guid=b.synth_guid(self.profile.key, "opening_stock", name),
                    ),
                    None,
                )

    def _shape_simple_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "godown"
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            if entity == "unit":
                name = normalise_unit(name) or name
            data: dict[str, Any] = {"NAME": name}
            _put(data, "DESCRIPTION", _text(row, spec, "description"))
            _put(data, "DECIMALPLACES", _text(row, spec, "decimals"))
            parent = _clean_parent(_text(row, spec, "parent"))
            if parent:
                data["PARENT"] = parent
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=name, parent=parent, data=data,
                    guid=b.synth_guid(self.profile.key, entity, name),
                ),
            )

    # -- openings ---------------------------------------------------------------------

    def _shape_opening_ledger(self, spec: SheetSpec, sheet: Sheet) -> None:
        for row in sheet.rows:
            name = _text(row, spec, "name")
            balance = _num(row, spec, "opening")
            if not name or not balance:
                continue
            self._note_date(_day(row, spec, "date"))
            self._stage_opening_ledger(
                2,
                b.opening_ledger(
                    self.seq.next(),
                    name=name,
                    balance=balance,
                    is_debit=_is_debit(_text(row, spec, "dr_cr"), amount=balance, default=True),
                    guid=b.synth_guid(self.profile.key, "opening_ledger", name),
                ),
            )

    def _shape_opening_stock(self, spec: SheetSpec, sheet: Sheet) -> None:
        for row in sheet.rows:
            name = _text(row, spec, "name")
            qty = _num(row, spec, "qty")
            if not name or not qty:
                continue
            self._stage_opening_stock(
                2,
                b.opening_stock(
                    self.seq.next(),
                    name=name,
                    qty=qty,
                    value=_num(row, spec, "value"),
                    uom=normalise_unit(_text(row, spec, "uom") or "") or None,
                    godown=_text(row, spec, "godown"),
                    guid=b.synth_guid(
                        self.profile.key, "opening_stock", name, _text(row, spec, "godown")
                    ),
                ),
                _text(row, spec, "godown"),
            )

    # -- vouchers: the source already holds the double entry --------------------------

    def _shape_voucher_relational(self, spec: SheetSpec, sheet: Sheet) -> None:
        children = self._children(spec)
        for row in sheet.rows:
            key = _text(row, spec, "key") or _text(row, spec, "number")
            voucher_type = _text(row, spec, "voucher_type")
            entity, resolved_type = self._voucher_entity(voucher_type)
            if entity is None:
                continue

            ledger_rows = self._ledger_lines(children.get("ledger_lines"), key)
            item_rows = self._item_lines(children.get("item_lines"), key)
            bills = self._bill_lines(children.get("bill_allocations"), key)
            banks = self._bank_lines(children.get("bank_allocations"), key)

            party = _text(row, spec, "party")
            if bills and ledger_rows:
                _attach_bills(ledger_rows, bills, party)
            # An item line that names no income/expense ledger posts to the
            # document default, which quietly collapses the P&L into one account.
            # When the voucher has exactly one non-party, non-tax ledger, that is
            # unambiguously the line's account, so carry it down.
            _carry_revenue_ledger(ledger_rows, item_rows)

            data: dict[str, Any] = {}
            _put(data, "REFERENCE", _text(row, spec, "reference"))
            _put(data, "PLACEOFSUPPLY", _text(row, spec, "place_of_supply"))
            _put(data, "BASICBASEPARTYNAME", party)
            if banks:
                data["BANKALLOCATIONS.LIST"] = banks

            posting_date = _day(row, spec, "date")
            self._note_date(posting_date)
            number = _text(row, spec, "number")
            self.result.add(
                entity.key,
                b.voucher(
                    entity.key,
                    self.seq.next(),
                    stage=entity.stage,
                    voucher_type=resolved_type,
                    voucher_number=number,
                    posting_date=posting_date,
                    party=party,
                    narration=_text(row, spec, "narration"),
                    guid=b.synth_guid(self.profile.key, "voucher", key or number, posting_date),
                    is_cancelled=_flag(row, spec, "is_cancelled"),
                    is_optional=_flag(row, spec, "is_optional"),
                    ledger_entries=ledger_rows,
                    inventory_entries=item_rows,
                    data=data,
                ),
            )

    def _shape_voucher_flat(self, spec: SheetSpec, sheet: Sheet) -> None:
        """One row per line; rows sharing a document number are one voucher.

        Folding on the number rather than emitting a voucher per row is the whole
        point: a three-item invoice written across three rows is one invoice with
        three lines, and importing it as three invoices would treble the document
        count, the invoice numbers and the customer's balance.
        """
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        order: list[str] = []
        for row in sheet.rows:
            key = (
                _text(row, spec, "key")
                or _text(row, spec, "number")
                or f"__row{len(order)}"
            )
            if key not in grouped:
                order.append(key)
            grouped[key].append(row)

        for key in order:
            rows = grouped[key]
            head = rows[0]
            voucher_type = _text(head, spec, "voucher_type")
            entity, resolved_type = self._voucher_entity(voucher_type)
            if entity is None:
                continue

            party = _text(head, spec, "party")
            posting_date = _day(head, spec, "date")
            self._note_date(posting_date)
            number = _text(head, spec, "number")

            item_rows: list[dict[str, Any]] = []
            total = ZERO
            taxable = ZERO
            cgst = sgst = igst = ZERO
            for row in rows:
                total += _num(row, spec, "total")
                taxable += _num(row, spec, "sub_total")
                cgst += _num(row, spec, "cgst")
                sgst += _num(row, spec, "sgst")
                igst += _num(row, spec, "igst")
                item = _text(row, spec, "item")
                qty = _num(row, spec, "qty")
                if item and qty:
                    uom = normalise_unit(_text(row, spec, "uom") or "") or None
                    item_rows.append(
                        b.inventory_entry(
                            item,
                            qty=qty,
                            uom=uom,
                            rate=_num(row, spec, "rate"),
                            amount=_num(row, spec, "sub_total") or qty * _num(row, spec, "rate"),
                        )
                    )
            # Only the first row's total counts when every row repeats the
            # voucher total — the common shape when a source denormalises a
            # header across its lines.
            if len(rows) > 1 and _all_equal(_num(r, spec, "total") for r in rows):
                total = _num(head, spec, "total")

            debit_ledger = _text(head, spec, "debit_ledger")
            credit_ledger = _text(head, spec, "credit_ledger")
            posting = b.Posting()
            bills = [b.bill_allocation(number or key, total)] if number else []

            if taxable and (cgst or sgst or igst):
                # The source states the split, so post it rather than lumping the
                # tax into the revenue ledger: GST returns read these accounts.
                tax_side = "credit" if _is_income(resolved_type) else "debit"
                base_side = "credit" if _is_income(resolved_type) else "debit"
                party_side = "debit" if _is_income(resolved_type) else "credit"
                posting._add(
                    debit_ledger if party_side == "debit" else credit_ledger,
                    total, side=party_side, is_party=bool(party), bills=bills,
                )
                posting._add(
                    credit_ledger if base_side == "credit" else debit_ledger,
                    taxable, side=base_side,
                )
                for ledger, amount in self._gst_ledgers(resolved_type, cgst, sgst, igst):
                    posting._add(ledger, amount, side=tax_side)
            else:
                posting.debit(debit_ledger, total, is_party=_is_party(debit_ledger, party), bills=bills if _is_party(debit_ledger, party) else ())
                posting.credit(credit_ledger, total, is_party=_is_party(credit_ledger, party), bills=bills if _is_party(credit_ledger, party) else ())

            messages = self._balance(posting, number or key)
            _carry_revenue_ledger(posting.rows, item_rows)

            data: dict[str, Any] = {}
            _put(data, "REFERENCE", _text(head, spec, "reference"))
            _put(data, "PLACEOFSUPPLY", _text(head, spec, "place_of_supply"))
            record = b.voucher(
                entity.key,
                self.seq.next(),
                stage=entity.stage,
                voucher_type=resolved_type,
                voucher_number=number,
                posting_date=posting_date,
                party=party,
                narration=_text(head, spec, "narration"),
                guid=b.synth_guid(self.profile.key, "voucher", key, posting_date),
                ledger_entries=posting.rows,
                inventory_entries=item_rows,
                data=data,
            )
            if messages:
                record["_messages"] = messages
            self.result.add(entity.key, record)

    # -- vouchers: the double entry has to be rebuilt ----------------------------------

    def _shape_document(self, spec: SheetSpec, sheet: Sheet) -> None:
        children = self._children(spec)
        for row in sheet.rows:
            key = _text(row, spec, "key") or _text(row, spec, "number")
            voucher_type = _text(row, spec, "voucher_type")
            entity, resolved_type = self._voucher_entity(voucher_type)
            if entity is None:
                continue

            party = _text(row, spec, "party")
            number = _text(row, spec, "number")
            posting_date = _day(row, spec, "date")
            due_date = _day(row, spec, "due_date")
            self._note_date(posting_date)

            lines = self._item_lines(children.get("item_lines"), key)
            totals = self._line_totals(children.get("item_lines"), key)
            taxable = totals["taxable"] or _num(row, spec, "sub_total")
            cgst = totals["cgst"] or _num(row, spec, "cgst")
            sgst = totals["sgst"] or _num(row, spec, "sgst")
            igst = totals["igst"] or _num(row, spec, "igst")
            if not (cgst or sgst or igst):
                # Only a combined tax figure was given. Which heads it splits into
                # is not knowable from the file, so it is posted to the IGST head
                # and said so, rather than being invented as a CGST/SGST pair.
                combined = _num(row, spec, "tax_total")
                if combined:
                    igst = combined
            total = _num(row, spec, "total") or (taxable + cgst + sgst + igst)

            income = _is_income(resolved_type)
            party_side, other_side = ("debit", "credit") if income else ("credit", "debit")

            posting = b.Posting()
            posting._add(
                party, total, side=party_side, is_party=True,
                bills=[
                    b.bill_allocation(
                        number or key or "",
                        total,
                        credit_period=_credit_period(posting_date, due_date),
                    )
                ] if (number or key) else (),
            )
            for account, amount in self._revenue_split(
                children.get("item_lines"), key, taxable, resolved_type
            ):
                posting._add(account, amount, side=other_side)
            for ledger, amount in self._gst_ledgers(resolved_type, cgst, sgst, igst):
                posting._add(ledger, amount, side=other_side)

            # Tax withheld at source is the difference between what the document
            # is worth and what the party actually owes or is owed. It sits on the
            # party's side of the entry as a liability (purchases) or a
            # receivable (sales), and without it every bill with TDS on it fails
            # to balance by exactly the amount withheld.
            tds = _num(row, spec, "tds")
            if tds:
                posting._add(self._ledger("tds", "TDS Payable"), tds, side=party_side)

            discount = _num(row, spec, "discount")
            if discount:
                posting._add(
                    self._ledger("discount", "Discount Allowed"), discount, side=other_side
                )
            adjustment = _num(row, spec, "round_off")
            if adjustment:
                side = other_side if adjustment > ZERO else party_side
                posting._add(self._ledger("round_off", "Round Off"), abs(adjustment), side=side)

            messages = self._balance(posting, number or key)

            data: dict[str, Any] = {}
            _put(data, "REFERENCE", _text(row, spec, "reference"))
            _put(data, "SUPPLIERINVOICENO", _text(row, spec, "reference"))
            _put(data, "PLACEOFSUPPLY", _text(row, spec, "place_of_supply"))
            record = b.voucher(
                entity.key,
                self.seq.next(),
                stage=entity.stage,
                voucher_type=resolved_type,
                voucher_number=number,
                posting_date=posting_date,
                party=party,
                narration=_text(row, spec, "narration"),
                guid=b.synth_guid(self.profile.key, "voucher", key or number, posting_date),
                ledger_entries=posting.rows,
                inventory_entries=lines,
                data=data,
            )
            if messages:
                record["_messages"] = messages
            self.result.add(entity.key, record)

    def _shape_payment(self, spec: SheetSpec, sheet: Sheet) -> None:
        children = self._children(spec)
        for row in sheet.rows:
            key = _text(row, spec, "key") or _text(row, spec, "number")
            entity, resolved_type = self._voucher_entity(_text(row, spec, "voucher_type"))
            if entity is None:
                continue
            party = _text(row, spec, "party")
            bank = _text(row, spec, "bank")
            amount = _num(row, spec, "amount")
            if not party or not bank or not amount:
                continue
            posting_date = _day(row, spec, "date")
            self._note_date(posting_date)

            allocations = [
                b.bill_allocation(ref, value_, bill_type=alloc or "Agst Ref")
                for ref, value_, alloc in self._bill_refs(
                    children.get("bill_allocations"), key
                )
            ]
            receive = resolved_type == "Receipt"
            posting = b.Posting()
            if receive:
                posting.debit(bank, amount)
                posting.credit(party, amount, is_party=True, bills=allocations)
            else:
                posting.debit(party, amount, is_party=True, bills=allocations)
                posting.credit(bank, amount)

            data: dict[str, Any] = {}
            _put(data, "REFERENCE", _text(row, spec, "reference"))
            mode = _text(row, spec, "mode")
            if mode:
                data["BANKALLOCATIONS.LIST"] = [
                    b.bank_allocation(
                        instrument_no=_text(row, spec, "reference"),
                        instrument_date=posting_date,
                        transaction_type=mode,
                        amount=amount,
                    )
                ]
            self.result.add(
                entity.key,
                b.voucher(
                    entity.key,
                    self.seq.next(),
                    stage=entity.stage,
                    voucher_type=resolved_type,
                    voucher_number=_text(row, spec, "number"),
                    posting_date=posting_date,
                    party=party,
                    narration=_text(row, spec, "narration"),
                    guid=b.synth_guid(self.profile.key, "voucher", key, posting_date),
                    ledger_entries=posting.rows,
                    data=data,
                ),
            )

    def _shape_journal(self, spec: SheetSpec, sheet: Sheet) -> None:
        children = self._children(spec)
        entity, resolved_type = self._voucher_entity("Journal")
        if entity is None:  # pragma: no cover - Journal is always in the catalogue
            return
        for row in sheet.rows:
            key = _text(row, spec, "key") or _text(row, spec, "number")
            lines = self._ledger_lines(children.get("ledger_lines"), key)
            if not lines:
                continue
            posting_date = _day(row, spec, "date")
            self._note_date(posting_date)
            data: dict[str, Any] = {}
            _put(data, "REFERENCE", _text(row, spec, "reference"))
            self.result.add(
                entity.key,
                b.voucher(
                    entity.key,
                    self.seq.next(),
                    stage=entity.stage,
                    voucher_type=resolved_type,
                    voucher_number=_text(row, spec, "number"),
                    posting_date=posting_date,
                    narration=_text(row, spec, "narration"),
                    guid=b.synth_guid(self.profile.key, "journal", key, posting_date),
                    ledger_entries=lines,
                    data=data,
                ),
            )

    def _shape_transfer(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity, resolved_type = self._voucher_entity("Contra")
        if entity is None:  # pragma: no cover
            return
        for row in sheet.rows:
            source = _text(row, spec, "from_account")
            target = _text(row, spec, "to_account")
            amount = _num(row, spec, "amount")
            if not source or not target or not amount:
                continue
            posting_date = _day(row, spec, "date")
            self._note_date(posting_date)
            number = _text(row, spec, "number")
            posting = b.Posting().debit(target, amount).credit(source, amount)
            self.result.add(
                entity.key,
                b.voucher(
                    entity.key,
                    self.seq.next(),
                    stage=entity.stage,
                    voucher_type=resolved_type,
                    voucher_number=number,
                    posting_date=posting_date,
                    narration=_text(row, spec, "narration"),
                    guid=b.synth_guid(self.profile.key, "transfer", number, posting_date),
                    ledger_entries=posting.rows,
                ),
            )

    def _shape_expense(self, spec: SheetSpec, sheet: Sheet) -> None:
        """A directly-paid expense becomes a Journal, deliberately not a Payment.

        A Payment Entry needs a party, and posting one against the vendor would
        create an unallocated advance against a payable that never existed — the
        expense was paid outright, not settled against a bill. So it books as
        expense Dr / input GST Dr / bank Cr, with the vendor kept in the
        narration where it is visible but not misleading.
        """
        entity, resolved_type = self._voucher_entity("Journal")
        if entity is None:  # pragma: no cover
            return
        for row in sheet.rows:
            account = _text(row, spec, "account")
            bank = _text(row, spec, "bank")
            amount = _num(row, spec, "amount")
            if not account or not bank or not amount:
                continue
            cgst, sgst, igst = (
                _num(row, spec, "cgst"), _num(row, spec, "sgst"), _num(row, spec, "igst")
            )
            if not (cgst or sgst or igst):
                igst = _num(row, spec, "tax_total")
            total = _num(row, spec, "total") or (amount + cgst + sgst + igst)
            posting_date = _day(row, spec, "date")
            self._note_date(posting_date)
            number = _text(row, spec, "number")
            party = _text(row, spec, "party")

            posting = b.Posting()
            posting.debit(account, amount, cost_centre=_text(row, spec, "cost_centre"))
            for ledger, tax in self._gst_ledgers("Purchase", cgst, sgst, igst):
                posting.debit(ledger, tax)
            posting.credit(bank, total)
            messages = self._balance(posting, number)

            narration = " · ".join(
                part for part in (_text(row, spec, "narration"), party) if part
            )
            record = b.voucher(
                entity.key,
                self.seq.next(),
                stage=entity.stage,
                voucher_type=resolved_type,
                voucher_number=number,
                posting_date=posting_date,
                narration=narration or None,
                guid=b.synth_guid(self.profile.key, "expense", number, posting_date),
                ledger_entries=posting.rows,
            )
            if messages:
                record["_messages"] = messages
            self.result.add(entity.key, record)

    def _shape_stock_adjustment(self, spec: SheetSpec, sheet: Sheet) -> None:
        """A signed quantity delta becomes a Stock Journal, not a Physical Stock count.

        The distinction matters: Physical Stock *sets* the quantity, a Stock
        Journal *moves* it. Importing "+3 damaged" as a physical count would set
        the item to 3 rather than adding 3, and the difference only shows up as a
        wrong valuation weeks later.
        """
        entity, resolved_type = self._voucher_entity("Stock Journal")
        if entity is None:  # pragma: no cover
            return
        for row in sheet.rows:
            item = _text(row, spec, "item")
            qty = _num(row, spec, "qty")
            if not item or not qty:
                continue
            posting_date = _day(row, spec, "date")
            self._note_date(posting_date)
            number = _text(row, spec, "number")
            uom = normalise_unit(_text(row, spec, "uom") or "") or None
            line = b.inventory_entry(
                item,
                qty=abs(qty),
                uom=uom,
                amount=abs(_num(row, spec, "value")),
                godown=_text(row, spec, "godown"),
            )
            key = (
                "DESTINATIONALLINVENTORYENTRIES.LIST"
                if qty > ZERO
                else "SOURCEALLINVENTORYENTRIES.LIST"
            )
            self.result.add(
                entity.key,
                b.voucher(
                    entity.key,
                    self.seq.next(),
                    stage=entity.stage,
                    voucher_type=resolved_type,
                    voucher_number=number,
                    posting_date=posting_date,
                    narration=_text(row, spec, "narration"),
                    guid=b.synth_guid(
                        self.profile.key, "adjustment", number, item, posting_date
                    ),
                    data={key: [line]},
                ),
            )

    # -- orders: commitments, not postings ---------------------------------------------

    def _shape_order(self, spec: SheetSpec, sheet: Sheet) -> None:
        """Quotations and sales/purchase orders.

        Deliberately *not* `_shape_document`. An order posts nothing, so there is
        no double entry to rebuild and no balance to assert — but there is also
        nothing to fall back on when the line sheet is missing. A document can be
        reconstructed from its totals because the totals are the accounting; an
        order cannot, because the lines *are* the order. A row with no lines is
        therefore staged blocked with that reason on it, rather than becoming an
        order nobody can fulfil.
        """
        children = self._children(spec)
        for row in sheet.rows:
            key = _text(row, spec, "key") or _text(row, spec, "number")
            entity, resolved_type = self._voucher_entity(_text(row, spec, "voucher_type"))
            if entity is None:
                continue
            number = _text(row, spec, "number")
            party = _text(row, spec, "party")
            posting_date = _day(row, spec, "date")
            self._note_date(posting_date)

            item_rows = self._item_lines(children.get("item_lines"), key)
            messages: list[dict[str, str]] = []
            if not item_rows:
                messages.append(
                    {
                        "level": "error",
                        "message": (
                            f"'{sheet.name}' gives this order's totals but not its "
                            "lines, and an order is what was ordered. Map the "
                            "sheet holding its line items, or leave the sheet "
                            "unimported."
                        ),
                        "field": "items",
                    }
                )

            data: dict[str, Any] = {}
            _put(data, "REFERENCE", _text(row, spec, "reference"))
            _put(data, "BASICDUEDATEOFPYMT", _text(row, spec, "due_date"))
            _put(data, "PARTYGSTIN", _text(row, spec, "party_gstin"))
            _put(data, "PLACEOFSUPPLY", _text(row, spec, "place_of_supply"))
            _put(data, "COSTCENTRENAME", _text(row, spec, "cost_centre"))
            _put(data, "ORDERSTATUS", _text(row, spec, "status"))
            record = b.voucher(
                entity.key,
                self.seq.next(),
                stage=entity.stage,
                voucher_type=resolved_type,
                voucher_number=number,
                posting_date=posting_date,
                party=party,
                narration=_text(row, spec, "narration"),
                is_cancelled=_flag(row, spec, "is_cancelled"),
                is_optional=_flag(row, spec, "is_optional"),
                guid=b.synth_guid(self.profile.key, "order", key or number, posting_date),
                inventory_entries=item_rows,
                data=data,
            )
            if messages:
                record["_messages"] = messages
            self.result.add(entity.key, record)

    # -- setup masters -----------------------------------------------------------------

    def _shape_payment_terms(self, spec: SheetSpec, sheet: Sheet) -> None:
        """Rows sharing a name are the instalments of one template."""
        entity = spec.entity or "payment_terms"
        terms: dict[str, list[dict[str, Any]]] = {}
        order: list[str] = []
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            folded = normalise_header(name)
            if folded not in terms:
                terms[folded] = []
                order.append(name)
            terms[folded].append(
                {
                    "DESCRIPTION": _text(row, spec, "description") or name,
                    "CREDITDAYS": str(int(_num(row, spec, "credit_days"))),
                    "INVOICEPORTION": b._plain(_num(row, spec, "portion")),
                }
            )
        for name in order:
            rows = terms[normalise_header(name)]
            # One unstated instalment is the whole invoice. Several unstated ones
            # are split evenly rather than left at 0%, which would make the
            # template describe nothing.
            share = b._plain(Decimal(100) / Decimal(len(rows)))
            for entry in rows:
                if parse_amount(entry["INVOICEPORTION"]) <= ZERO:
                    entry["INVOICEPORTION"] = share
            self.result.add(
                entity,
                b.master(
                    entity,
                    self.seq.next(),
                    name=name,
                    data={"NAME": name, "PAYMENTTERM.LIST": rows},
                    guid=b.synth_guid(self.profile.key, "payment_terms", name),
                ),
            )

    def _shape_currency_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "currency"
        for row in sheet.rows:
            code = (_text(row, spec, "code") or "").strip().upper()
            if not code:
                continue
            data: dict[str, Any] = {"NAME": code}
            _put(data, "FORMALNAME", _text(row, spec, "name"))
            _put(data, "MAILINGNAME", _text(row, spec, "symbol"))
            rate = _num(row, spec, "rate")
            if rate:
                data["DAILYRATE"] = b._plain(rate)
            if _flag(row, spec, "is_base"):
                data["ISBASECURRENCY"] = "Yes"
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=code, data=data,
                    guid=b.synth_guid(self.profile.key, "currency", code),
                ),
            )

    def _shape_price_list_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "price_list"
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            data: dict[str, Any] = {"NAME": name}
            _put(data, "PRICELISTKIND", _text(row, spec, "applies_to"))
            _put(data, "CURRENCYNAME", _text(row, spec, "currency"))
            _put(data, "DESCRIPTION", _text(row, spec, "description"))
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=name, data=data,
                    guid=b.synth_guid(self.profile.key, "price_list", name),
                ),
            )

    def _shape_tax_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "tax_template"
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            data: dict[str, Any] = {"NAME": name, "RATE": b._plain(_num(row, spec, "rate"))}
            _put(data, "TAXTYPE", _text(row, spec, "tax_type"))
            _put(data, "APPLIESTO", _text(row, spec, "applies_to"))
            _put(data, "TAXACCOUNT", _text(row, spec, "account"))
            _put(data, "INPUTLEDGER", _text(row, spec, "input_account"))
            _put(data, "OUTPUTLEDGER", _text(row, spec, "output_account"))
            if _flag(row, spec, "is_default"):
                data["ISDEFAULT"] = "Yes"
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=name, data=data,
                    guid=b.synth_guid(self.profile.key, "tax_template", name),
                ),
            )

    def _shape_bank_account(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "bank_account"
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            self._remember_id("bank", _text(row, spec, "key"), name)
            data: dict[str, Any] = {"NAME": name}
            _put(data, "BANKNAME", _text(row, spec, "bank"))
            _put(data, "BANKACCOUNTNO", _text(row, spec, "account_no"))
            _put(data, "IFSCODE", _text(row, spec, "ifsc"))
            _put(data, "LEDGERNAME", _text(row, spec, "ledger") or name)
            _put(data, "CURRENCYNAME", _text(row, spec, "currency"))
            if _flag(row, spec, "is_default"):
                data["ISDEFAULT"] = "Yes"
            opening = _num(row, spec, "opening")
            if opening:
                data["OPENINGBALANCE"] = b._plain(opening)
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=name, data=data,
                    guid=b.synth_guid(self.profile.key, "bank_account", name),
                ),
            )

    def _shape_budget_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        """Rows sharing a budget name are the account lines of one Budget."""
        entity = spec.entity or "budget"
        budgets: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for row in sheet.rows:
            name = _text(row, spec, "name")
            account = _text(row, spec, "account")
            if not name or not account:
                continue
            folded = normalise_header(name)
            if folded not in budgets:
                head: dict[str, Any] = {"NAME": name, "BUDGETALLOCATION.LIST": []}
                _put(head, "STARTINGFROM", _text(row, spec, "from_date"))
                _put(head, "ENDINGAT", _text(row, spec, "to_date"))
                _put(head, "COSTCENTRENAME", _text(row, spec, "cost_centre"))
                budgets[folded] = head
                order.append(name)
                self._note_date(_day(row, spec, "from_date"))
                self._note_date(_day(row, spec, "to_date"))
            budgets[folded]["BUDGETALLOCATION.LIST"].append(
                {"LEDGERNAME": account, "AMOUNT": b._plain(_num(row, spec, "amount"))}
            )
        for name in order:
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=name, data=budgets[normalise_header(name)],
                    guid=b.synth_guid(self.profile.key, "budget", name),
                ),
            )

    def _shape_asset_master(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "asset"
        for row in sheet.rows:
            name = _text(row, spec, "name")
            if not name:
                continue
            purchase = _day(row, spec, "purchase_date")
            self._note_date(purchase)
            data: dict[str, Any] = {
                "NAME": name,
                "ORIGINALCOST": b._plain(_num(row, spec, "cost")),
                "ACCUMULATEDDEP": b._plain(_num(row, spec, "accumulated")),
            }
            _put(data, "ASSETCODE", _text(row, spec, "code"))
            _put(data, "ASSETGROUP", _text(row, spec, "category"))
            _put(data, "PURCHASEDATE", _text(row, spec, "purchase_date"))
            _put(data, "DEPMETHOD", _text(row, spec, "method"))
            _put(data, "DEPRATE", _text(row, spec, "rate"))
            _put(data, "USEFULLIFE", _text(row, spec, "life"))
            _put(data, "LOCATIONNAME", _text(row, spec, "location"))
            _put(data, "CUSTODIAN", _text(row, spec, "custodian"))
            wdv = _num(row, spec, "wdv")
            if wdv:
                data["WDV"] = b._plain(wdv)
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=name, data=data,
                    guid=b.synth_guid(self.profile.key, "asset", name, _text(row, spec, "code")),
                ),
            )

    # -- things that hang off a party ---------------------------------------------------

    def _remember_id(self, namespace: str, source_id: str | None, name: str) -> None:
        if source_id:
            self._aliases.setdefault(namespace, {})[normalise_header(source_id)] = name

    def _by_id(self, namespace: str, raw: str | None) -> str | None:
        """A source id resolved to the name we filed it under, or the raw value."""
        if not raw:
            return None
        return self._aliases.get(namespace, {}).get(normalise_header(raw), raw)

    def _party_name(self, row: dict[str, Any], spec: SheetSpec) -> str | None:
        """Resolve the party column, which may hold a name or the source's own id."""
        return self._by_id("party", _text(row, spec, "party"))

    def _shape_party_address(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "address"
        for row in sheet.rows:
            party = self._party_name(row, spec)
            line1 = _text(row, spec, "line1")
            if not party or not line1:
                continue
            lines = [line for line in (line1, _text(row, spec, "line2")) if line]
            title = _text(row, spec, "title") or party
            data: dict[str, Any] = {
                "NAME": title,
                "PARTYNAME": party,
                "ADDRESS.LIST": {"ADDRESS": lines},
            }
            _put(data, "ADDRESSTYPE", _text(row, spec, "address_type"))
            _put(data, "ATTENTION", _text(row, spec, "attention"))
            _put(data, "CITYNAME", _text(row, spec, "city"))
            _put(data, "LEDSTATENAME", _text(row, spec, "state"))
            _put(data, "STATECODE", _text(row, spec, "state_code"))
            _put(data, "PINCODE", _text(row, spec, "pincode"))
            _put(data, "COUNTRYNAME", _text(row, spec, "country"))
            _put(data, "LEDGERPHONE", _text(row, spec, "phone"))
            if not _active(row, spec):
                data["ISDELETED"] = "Yes"
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=title, data=data,
                    guid=b.synth_guid(self.profile.key, "address", party, title, line1),
                ),
            )

    def _shape_party_contact(self, spec: SheetSpec, sheet: Sheet) -> None:
        entity = spec.entity or "contact"
        for row in sheet.rows:
            party = self._party_name(row, spec)
            if not party:
                continue
            first = _text(row, spec, "first_name")
            last = _text(row, spec, "last_name")
            if not first:
                # A single "full name" column is the common shape; split on the
                # first space, which is what the Tally path already does.
                full = _text(row, spec, "full_name")
                if not full:
                    continue
                first, _, last_part = full.strip().partition(" ")
                last = last or last_part or None
            data: dict[str, Any] = {"NAME": first, "PARTYNAME": party, "FIRSTNAME": first}
            _put(data, "LASTNAME", last)
            _put(data, "SALUTATION", _text(row, spec, "salutation"))
            _put(data, "EMAIL", _text(row, spec, "email"))
            _put(data, "LEDGERPHONE", _text(row, spec, "phone"))
            _put(data, "LEDGERMOBILE", _text(row, spec, "mobile"))
            _put(data, "DESIGNATION", _text(row, spec, "designation"))
            if _flag(row, spec, "is_primary"):
                data["ISPRIMARY"] = "Yes"
            if not _active(row, spec):
                data["ISDELETED"] = "Yes"
            full_name = " ".join(part for part in (first, last) if part)
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=full_name, data=data,
                    guid=b.synth_guid(self.profile.key, "contact", party, full_name),
                ),
            )

    def _shape_bank_transaction(self, spec: SheetSpec, sheet: Sheet) -> None:
        """Statement lines. These post nothing — they are what reconciliation matches."""
        entity = spec.entity or "bank_transaction"
        for row in sheet.rows:
            bank = self._by_id("bank", _text(row, spec, "bank"))
            when = _day(row, spec, "date")
            amount = _num(row, spec, "amount")
            if not bank or when is None or not amount:
                continue
            self._note_date(when)
            # A statement is written from the bank's point of view, so "debit"
            # there is money leaving the account: a *withdrawal* in our terms.
            withdrawal = _is_debit(_text(row, spec, "dr_cr"), amount=amount, default=False)
            number = _text(row, spec, "number")
            reference = _text(row, spec, "reference")
            data: dict[str, Any] = {
                "NAME": number or reference or f"{bank} {when.isoformat()}",
                "BANKNAME": bank,
                "TRANSACTIONDATE": when.isoformat(),
                "DEPOSIT": b._plain(ZERO if withdrawal else abs(amount)),
                "WITHDRAWAL": b._plain(abs(amount) if withdrawal else ZERO),
            }
            _put(data, "TRANSACTIONTYPE", _text(row, spec, "txn_type"))
            _put(data, "DESCRIPTION", _text(row, spec, "description"))
            _put(data, "REFERENCENUMBER", reference)
            _put(data, "MATCHEDSTATUS", _text(row, spec, "status"))
            self.result.add(
                entity,
                b.master(
                    entity, self.seq.next(), name=data["NAME"], data=data,
                    guid=b.synth_guid(
                        self.profile.key, "bank_txn", bank, number or reference, when, amount
                    ),
                ),
            )

    # -- child sheets -----------------------------------------------------------------

    def _children(self, spec: SheetSpec) -> dict[str, tuple[ChildSpec, dict[str, list[dict]]]]:
        """Index every line sheet of this spec by its join key, once per sheet."""
        out: dict[str, Any] = {}
        for child in spec.children:
            sheet = self.book.by_name(child.sheet)
            if sheet is None:
                continue
            trimmed = resolve(child, sheet)
            gaps = missing_required(trimmed, sheet)
            if gaps:
                self.result.warnings.append(
                    f"Line sheet '{sheet.name}' was ignored: no column for "
                    f"{', '.join(gaps)}."
                )
                continue
            index: dict[str, list[dict[str, Any]]] = defaultdict(list)
            for row in sheet.rows:
                key = _text(row, trimmed, "key")
                if key:
                    index[key].append(row)
            out[child.role] = (trimmed, index)
        return out

    @staticmethod
    def _rows_for(entry: Any, key: str | None) -> tuple[ChildSpec | None, list[dict[str, Any]]]:
        if entry is None or not key:
            return None, []
        spec, index = entry
        return spec, index.get(key, [])

    def _ledger_lines(self, entry: Any, key: str | None) -> list[dict[str, Any]]:
        spec, rows = self._rows_for(entry, key)
        if spec is None:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            ledger = _text(row, spec, "ledger")
            if not ledger:
                continue
            debit, credit = _num(row, spec, "debit"), _num(row, spec, "credit")
            if not debit and not credit:
                amount = _num(row, spec, "amount")
                if not amount:
                    continue
                if _is_debit(_text(row, spec, "dr_cr"), amount=amount):
                    debit, credit = abs(amount), ZERO
                else:
                    debit, credit = ZERO, abs(amount)
            bill_ref = _text(row, spec, "bill_ref")
            out.append(
                b.ledger_entry(
                    ledger,
                    debit=debit,
                    credit=credit,
                    cost_centre=_text(row, spec, "cost_centre"),
                    bills=(
                        [
                            b.bill_allocation(
                                bill_ref,
                                debit or credit,
                                credit_period=_credit_period(None, _day(row, spec, "due_date")),
                            )
                        ]
                        if bill_ref
                        else ()
                    ),
                )
            )
        return out

    def _item_lines(self, entry: Any, key: str | None) -> list[dict[str, Any]]:
        spec, rows = self._rows_for(entry, key)
        if spec is None:
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            item = _text(row, spec, "item")
            qty = _num(row, spec, "qty")
            if not item or not qty:
                continue
            uom = normalise_unit(_text(row, spec, "uom") or "") or None
            taxable = _num(row, spec, "taxable")
            out.append(
                b.inventory_entry(
                    item,
                    qty=qty,
                    uom=uom,
                    rate=_num(row, spec, "rate"),
                    amount=taxable or _num(row, spec, "amount"),
                    godown=_text(row, spec, "godown"),
                    batch=_text(row, spec, "batch"),
                    ledger=_text(row, spec, "account"),
                    expiry=_day(row, spec, "expiry"),
                )
            )
        return out

    def _line_totals(self, entry: Any, key: str | None) -> dict[str, Decimal]:
        spec, rows = self._rows_for(entry, key)
        totals = {"taxable": ZERO, "cgst": ZERO, "sgst": ZERO, "igst": ZERO}
        if spec is None:
            return totals
        for row in rows:
            for name in totals:
                totals[name] += _num(row, spec, name)
        return totals

    def _revenue_split(
        self, entry: Any, key: str | None, fallback: Decimal, voucher_type: str
    ) -> list[tuple[str, Decimal]]:
        """Taxable value grouped by the account each line names.

        Grouped rather than one row per line so a ten-line invoice against one
        revenue account produces one posting, which is what the source's own
        ledger would show. A default revenue account is only resolved when some
        amount actually needs it.
        """
        return self._revenue_split_rows(entry, key, fallback, voucher_type)
    def _default_revenue(self, voucher_type: str) -> str:
        """The account a line falls back to when it names none of its own."""
        income = _is_income(voucher_type)
        return self._ledger("sales" if income else "purchases", "Sales" if income else "Purchases")

    def _revenue_split_rows(
        self, entry: Any, key: str | None, fallback: Decimal, voucher_type: str
    ) -> list[tuple[str, Decimal]]:
        spec, rows = self._rows_for(entry, key)
        if spec is None or not rows:
            if not fallback:
                return []
            return [(self._default_revenue(voucher_type), fallback)]

        grouped: dict[str, Decimal] = defaultdict(lambda: ZERO)
        unnamed = ZERO
        for row in rows:
            account = _text(row, spec, "account")
            amount = _num(row, spec, "taxable") or _num(row, spec, "amount")
            if account:
                grouped[account] += amount
            else:
                unnamed += amount
        booked = sum(grouped.values(), ZERO) + unnamed
        # Whatever the lines do not account for still has to go somewhere; a
        # header-level figure larger than the lines' sum is a real difference,
        # not noise.
        if fallback and abs(fallback - booked) > b.BALANCE_TOLERANCE:
            unnamed += fallback - booked
        out = [(account, amount) for account, amount in grouped.items() if amount]
        if unnamed:
            out.append((self._default_revenue(voucher_type), unnamed))
        return out

    def _bill_refs(self, entry: Any, key: str | None) -> list[tuple[str, Decimal, str | None]]:
        spec, rows = self._rows_for(entry, key)
        if spec is None:
            return []
        out = []
        for row in rows:
            ref = _text(row, spec, "bill_ref")
            if ref:
                out.append((ref, _num(row, spec, "amount"), _text(row, spec, "alloc_type")))
        return out

    def _bill_lines(self, entry: Any, key: str | None) -> list[dict[str, Any]]:
        spec, rows = self._rows_for(entry, key)
        if spec is None:
            return []
        return [
            b.bill_allocation(
                ref,
                _num(row, spec, "amount"),
                bill_type=_text(row, spec, "alloc_type") or "New Ref",
                credit_period=_credit_period(None, _day(row, spec, "due_date")),
            )
            for row in rows
            if (ref := _text(row, spec, "bill_ref"))
        ]

    def _bank_lines(self, entry: Any, key: str | None) -> list[dict[str, Any]]:
        spec, rows = self._rows_for(entry, key)
        if spec is None:
            return []
        return [
            b.bank_allocation(
                instrument_no=_text(row, spec, "instrument_no"),
                instrument_date=_day(row, spec, "instrument_date"),
                transaction_type=_text(row, spec, "txn_type"),
                bank_date=_day(row, spec, "bank_date"),
                amount=_num(row, spec, "amount"),
            )
            for row in rows
        ]

    # -- shared helpers ---------------------------------------------------------------

    def _voucher_entity(self, voucher_type: str | None) -> tuple[Any, str]:
        """Catalogue entity for a voucher type, counting the ones we cannot place."""
        name = (voucher_type or "").strip()
        if not name:
            self.result.note_unknown("(voucher with no type)")
            return None, ""
        spec = classify_voucher(name)
        if spec is None:
            self.result.note_unknown(f"Voucher type: {name}")
            return None, name
        entity = ENTITY_BY_KEY.get(spec.entity_key)
        if entity is None:  # pragma: no cover - catalogue is internally consistent
            self.result.note_unknown(f"Voucher type: {name}")
            return None, name
        return entity, name

    def _ledger(self, role: str, fallback: str) -> str:
        """A ledger a synthesised posting needs, remembered if we had to invent it."""
        name = self.profile.ledger(role, fallback)
        if normalise_header(name) not in self._known_ledgers:
            self._invented.setdefault(name, role)
        return name

    def _gst_ledgers(
        self, voucher_type: str, cgst: Decimal, sgst: Decimal, igst: Decimal
    ) -> list[tuple[str, Decimal]]:
        """GST heads for this document's side, dropping the ones it did not use.

        A head with no amount is not resolved at all, so an intra-state-only book
        never grows an IGST account it will never post to.
        """
        prefix = "output" if _is_income(voucher_type) else "input"
        side = "Output" if prefix == "output" else "Input"
        return [
            (self._ledger(f"{prefix}_{head}", f"{side} {head.upper()}"), amount)
            for head, amount in (("cgst", cgst), ("sgst", sgst), ("igst", igst))
            if amount
        ]

    def _balance(self, posting: b.Posting, reference: str | None) -> list[dict[str, str]]:
        """Check a rebuilt entry, absorbing rounding and reporting anything larger."""
        if posting.balanced:
            # Only reach for a Round Off account when there is actually a gap to
            # absorb; resolving it unconditionally created one in every book.
            if posting.difference != ZERO:
                posting.round_off(self._ledger("round_off", "Round Off"))
            return []
        difference = posting.difference
        return [
            {
                "level": "error",
                "field": "amount",
                "message": (
                    f"The double entry rebuilt for {reference or 'this document'} is "
                    f"out by {difference:.2f} (debits {posting._debit:.2f}, credits "
                    f"{posting._credit:.2f}). Its totals and line amounts disagree in "
                    "the source file, so it was not imported."
                ),
            }
        ]

    def _note_date(self, when: date | None) -> None:
        if when is not None:
            self._dates.append(when)

    def _emit_invented_ledgers(self) -> None:
        """Create the accounts a rebuilt posting needed but the file never defined.

        Without this the posting references a name no mapping row exists for, the
        importer resolves it to nothing, and the amount is dropped — an import
        that "succeeded" with a hole in it. Creating the account and saying so is
        the honest alternative.
        """
        if not self._invented:
            return
        for name, role in sorted(self._invented.items()):
            self.result.add(
                "ledger",
                b.master(
                    "ledger",
                    self.seq.next(),
                    name=name,
                    parent=_INVENTED_GROUPS.get(role, "Indirect Expenses"),
                    guid=b.synth_guid(self.profile.key, "ledger", name),
                ),
            )
        self.result.warnings.append(
            "Your export does not name an account for "
            + ", ".join(sorted(self._invented))
            + ", but its documents post to them. Those accounts were created so the "
            "entries balance — check where they sit in the Chart of Accounts."
        )

    def _finish(self) -> None:
        for _priority, record in self._opening_ledgers.values():
            self.result.add("opening_ledger", record)
        for _priority, record in self._opening_stock.values():
            self.result.add("opening_stock", record)
        if self._dates:
            self.result.from_date = min(self._dates)
            self.result.to_date = max(self._dates)
        if not self.result.total:
            self.result.warnings.append(
                "Nothing importable was found in that workbook. Check the mapping "
                "step: every sheet is either unassigned or missing a required column."
            )


#: Where an invented account lands. A GST head has to be under Duties & Taxes or
#: it never reaches the GST returns, and a round-off under anything but an
#: expense group distorts the balance sheet instead of the P&L.
_INVENTED_GROUPS = {
    "output_cgst": "Duties & Taxes",
    "output_sgst": "Duties & Taxes",
    "output_igst": "Duties & Taxes",
    "input_cgst": "Duties & Taxes",
    "input_sgst": "Duties & Taxes",
    "input_igst": "Duties & Taxes",
    "sales": "Sales Accounts",
    "purchases": "Purchase Accounts",
    "discount": "Indirect Expenses",
    "tds": "Duties & Taxes",
    "round_off": "Indirect Expenses",
}

#: Voucher types where the party is a debtor and the tax is output tax.
_INCOME_TYPES = {"sales", "credit note", "sales order", "delivery note", "rejections in"}


def _is_income(voucher_type: str) -> bool:
    return (voucher_type or "").strip().casefold() in _INCOME_TYPES


def _is_party(ledger: str | None, party: str | None) -> bool:
    return bool(ledger and party and normalise_header(ledger) == normalise_header(party))


def _clean_parent(parent: str | None) -> str | None:
    """Drop the source's word for "top of the tree"."""
    if not parent:
        return None
    text = parent.strip()
    return None if text.casefold() in ("primary", "root", "none", "-") else text


def _put(data: dict[str, Any], key: str, value_: Any) -> None:
    if value_ not in (None, ""):
        data[key] = value_


def _all_equal(values: Any) -> bool:
    seen = list(values)
    return bool(seen) and all(v == seen[0] for v in seen)


def _credit_period(posting_date: date | None, due_date: date | None) -> str | None:
    """Tally states a bill's credit term in days; the importer derives the due date from it."""
    if due_date is None:
        return None
    if posting_date is None:
        return None
    days = (due_date - posting_date).days
    return f"{days} Days" if days > 0 else None


def _attach_bills(
    ledger_rows: list[dict[str, Any]], bills: list[dict[str, Any]], party: str | None
) -> None:
    """Hang a dedicated bill-allocation sheet off the party's ledger row.

    Bill references are what make an imported payment settle an imported invoice
    rather than sit as an unallocated advance, and they only work when they are
    attached to the *party* row.
    """
    if not bills:
        return
    target = None
    if party:
        target = next(
            (r for r in ledger_rows if normalise_header(r.get("LEDGERNAME", "")) == normalise_header(party)),
            None,
        )
    if target is None:
        target = next((r for r in ledger_rows if r.get("ISPARTYLEDGER") == "Yes"), None)
    if target is None:
        return
    target["ISPARTYLEDGER"] = "Yes"
    existing = target.get("BILLALLOCATIONS.LIST") or []
    target["BILLALLOCATIONS.LIST"] = list(existing) + bills


def _carry_revenue_ledger(
    ledger_rows: list[dict[str, Any]], item_rows: list[dict[str, Any]]
) -> None:
    """Give item lines the income/expense account when the voucher names exactly one."""
    if not item_rows:
        return
    from app.services.migration.importers.vouchers import _is_tax_ledger

    candidates = [
        row.get("LEDGERNAME")
        for row in ledger_rows
        if row.get("ISPARTYLEDGER") != "Yes"
        and row.get("LEDGERNAME")
        and not _is_tax_ledger(str(row["LEDGERNAME"]))
    ]
    if len(set(candidates)) != 1:
        return
    ledger = candidates[0]
    for line in item_rows:
        if not line.get("ACCOUNTINGALLOCATIONS.LIST"):
            line["ACCOUNTINGALLOCATIONS.LIST"] = [
                {"LEDGERNAME": ledger, "AMOUNT": line.get("AMOUNT", "0")}
            ]


def normalise(book: Workbook, profile: SourceProfile) -> ParsedFile:
    """Fold a workbook into the pipeline's IR under one profile."""
    return Normaliser(book, profile).run()
