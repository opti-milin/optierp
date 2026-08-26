"""Tally export parser — XML (native) and CSV/TSV (Day Book, Trial Balance).

Tally's own "Export Data" produces an ``<ENVELOPE>`` whose body holds a stream
of ``<TALLYMESSAGE>`` blocks, one per master or voucher::

    <ENVELOPE>
      <BODY><IMPORTDATA><REQUESTDATA>
        <TALLYMESSAGE><GROUP NAME="Sundry Debtors">...</GROUP></TALLYMESSAGE>
        <TALLYMESSAGE><LEDGER NAME="ABC Traders">...</LEDGER></TALLYMESSAGE>
        <TALLYMESSAGE><VOUCHER VCHTYPE="Sales" ...>...</VOUCHER></TALLYMESSAGE>
      </REQUESTDATA></IMPORTDATA></BODY>
    </ENVELOPE>

Three things make real-world Tally files awkward, and each has a defence here:

1. **Encoding.** Tally writes UTF-16LE or its own mangled ISO-8859-1 depending
   on version and export option. :func:`decode_tally_bytes` sniffs it.
2. **Entity-ish escapes.** Tally emits raw ``&`` and ``&#4;`` control chars that
   break a strict XML parser. :func:`sanitise_xml` repairs them.
3. **Repeated tags.** ``<ALLLEDGERENTRIES.LIST>`` appears once per line; a naive
   dict conversion keeps only the last. :func:`element_to_dict` collects repeats
   into lists.

The parser is deliberately dependency-free (stdlib ``xml.etree`` + ``csv``) and
returns plain dicts. Interpretation — what a record *means* — belongs to
``catalogue`` and the importers, not here.
"""

from __future__ import annotations

import csv
import io
import re
from datetime import date
from decimal import Decimal
from typing import Any
from xml.etree import ElementTree

from app.core.exceptions import ValidationError
from app.services.migration.catalogue import ENTITY_BY_KEY, classify_voucher
from app.services.migration.sources.ir import (
    ParsedFile,
    as_list,
    parse_amount,
    parse_bool,
    parse_date,
    parse_int,
    # Tally-facing alias for parse_date. Callers in this package import their
    # helpers from the source module they are parsing, so it has to be part of
    # this module's surface — without it `from ...tally_xml import
    # parse_tally_date` in the voucher importer raises at call time, not import
    # time, and only on vouchers that carry a reference date.
    parse_tally_date as parse_tally_date,  # noqa: PLC0414 - deliberate re-export
    text_of,
)

# Tally control characters (&#4; and friends) that no XML parser accepts.
_BAD_ENTITIES = re.compile(r"&#(?:[0-8]|1[1-2]|1[4-9]|2[0-9]|3[01]);")
# A bare & that is not already a well-formed entity.
_BARE_AMP = re.compile(r"&(?!(?:[a-zA-Z][a-zA-Z0-9]{0,7}|#\d{1,5}|#x[0-9a-fA-F]{1,4});)")


# --------------------------------------------------------------------------------------
# Decoding + sanitising
# --------------------------------------------------------------------------------------


def decode_tally_bytes(raw: bytes) -> str:
    """Decode a Tally export, sniffing the encoding Tally actually used."""
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    # UTF-16LE without a BOM shows up as NUL bytes in the ASCII prefix.
    head = raw[:200]
    if head.count(b"\x00") > len(head) // 4:
        return raw.decode("utf-16-le", errors="replace")
    for encoding in ("utf-8", "cp1252", "iso-8859-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def sanitise_xml(text: str) -> str:
    """Make a Tally XML string parseable: strip control entities, escape bare &."""
    text = _BAD_ENTITIES.sub("", text)
    text = _BARE_AMP.sub("&amp;", text)
    # Tally sometimes emits a stray BOM mid-file when concatenating exports.
    return text.replace("﻿", "").strip()


# --------------------------------------------------------------------------------------
# XML -> dict
# --------------------------------------------------------------------------------------


def element_to_dict(element: ElementTree.Element) -> Any:
    """Convert a Tally element into nested dicts, collecting repeated tags into lists.

    Attributes are folded in with an ``@`` prefix so ``<VOUCHER VCHTYPE="Sales">``
    surfaces as ``{"@VCHTYPE": "Sales", ...}``.
    """
    children = list(element)
    if not children:
        text = (element.text or "").strip()
        if element.attrib:
            node: dict[str, Any] = {f"@{k}": v for k, v in element.attrib.items()}
            if text:
                node["#text"] = text
            return node
        return text

    node = {f"@{k}": v for k, v in element.attrib.items()}
    for child in children:
        value = element_to_dict(child)
        tag = child.tag
        if tag in node:
            existing = node[tag]
            if isinstance(existing, list):
                existing.append(value)
            else:
                node[tag] = [existing, value]
        else:
            node[tag] = value
    text = (element.text or "").strip()
    if text:
        node["#text"] = text
    return node


# --------------------------------------------------------------------------------------
# XML parsing
# --------------------------------------------------------------------------------------

# <TALLYMESSAGE> child tag -> catalogue entity key, for masters.
_MASTER_TAGS: dict[str, str] = {
    "GROUP": "group",
    "LEDGER": "ledger",
    "CURRENCY": "currency",
    "UNIT": "unit",
    "STOCKGROUP": "stock_group",
    "STOCKCATEGORY": "stock_category",
    "STOCKITEM": "stock_item",
    "GODOWN": "godown",
    "COSTCATEGORY": "cost_category",
    "COSTCENTRE": "cost_centre",
    "VOUCHERTYPE": "voucher_type",
    "BUDGET": "budget",
    "PRICELIST": "price_list",
}


def parse_xml(text: str) -> ParsedFile:
    """Parse a Tally XML export into catalogue-keyed records."""
    result = ParsedFile()
    cleaned = sanitise_xml(text)
    if not cleaned:
        raise ValidationError("The uploaded file is empty")
    try:
        root = ElementTree.fromstring(cleaned)
    except ElementTree.ParseError as exc:  # pragma: no cover - defensive
        raise ValidationError(
            f"Could not read this as Tally XML ({exc}). Export from Tally with "
            "'Export > Format: XML' and upload the .xml file unmodified."
        ) from None

    _read_envelope_header(root, result)

    sequence = 0
    for message in root.iter("TALLYMESSAGE"):
        for child in message:
            sequence += 1
            tag = child.tag.upper()
            if tag == "VOUCHER":
                record = _voucher_record(child, sequence)
                if record is None:
                    result.unknown_tags["VOUCHER"] = result.unknown_tags.get("VOUCHER", 0) + 1
                    continue
                result.add(record["_entity_key"], record)
            elif tag in _MASTER_TAGS:
                entity = _MASTER_TAGS[tag]
                record = _master_record(child, entity, sequence)
                result.add(entity, record)
                # A ledger or stock item carries its opening balance on the same
                # element. Emit it as its own record so it becomes an opening
                # document at the migration cut-off rather than being lost —
                # without these the imported books start at zero and every
                # balance, ageing and stock quantity is wrong from day one.
                opening = _opening_record(record, entity, sequence)
                if opening is not None:
                    result.add(opening["_entity_key"], opening)
            elif tag == "COMPANY":
                if not _read_company_message(child, result):
                    # Carried neither a name nor a GUID, so it told us nothing.
                    # Count it rather than consuming it silently.
                    result.unknown_tags[tag] = result.unknown_tags.get(tag, 0) + 1
            else:
                result.unknown_tags[tag] = result.unknown_tags.get(tag, 0) + 1

    # TallyPrime identifies the company only in the <COMPANY> trailer; older
    # Day Book exports omit it entirely. Either way every record GUID carries
    # the company UUID as its prefix, so fall back to that.
    if not result.company_guid:
        for rows in result.records.values():
            for row in rows:
                guid = _company_guid_from_record_guid(row.get("guid"))
                if guid:
                    result.company_guid = guid
                    break
            if result.company_guid:
                break

    if not result.total:
        result.warnings.append(
            "No Tally masters or vouchers were found. Check that the export "
            "included 'All Masters' and/or 'Day Book' for the period."
        )
    return result


def _read_envelope_header(root: ElementTree.Element, result: ParsedFile) -> None:
    """Pull company name and period off the export header when Tally wrote one."""
    for tag, setter in (
        ("SVCURRENTCOMPANY", "company_name"),
        ("SVFROMDATE", "from_date"),
        ("SVTODATE", "to_date"),
    ):
        node = root.find(f".//{tag}")
        if node is None or not (node.text or "").strip():
            continue
        value: Any = node.text.strip()
        if setter.endswith("_date"):
            value = parse_date(value)
        if value:
            setattr(result, setter, value)
    if not result.company_name:
        node = root.find(".//BODY/IMPORTDATA/REQUESTDESC/STATICVARIABLES/SVCURRENTCOMPANY")
        if node is not None and node.text:
            result.company_name = node.text.strip()


def _read_company_message(
    element: ElementTree.Element, result: ParsedFile
) -> bool:
    """Read company identity out of a ``<COMPANY>`` message.

    Two shapes exist in the wild and they disagree about what ``NAME`` means:

    *TallyPrime* closes an export with a remote-company trailer, where ``NAME``
    is the company **GUID** and the display name lives in ``REMOTECMPNAME``::

        <COMPANY>
          <REMOTECMPINFO.LIST MERGE="Yes">
            <NAME>6c5c39d3-8d04-455d-85f9-f53464f81e71</NAME>
            <REMOTECMPNAME>OptiReach Import Test</REMOTECMPNAME>
          </REMOTECMPINFO.LIST>
        </COMPANY>

    *Tally.ERP 9* master exports instead put the display name straight on the
    element, as an attribute or a direct child.

    Reading the first shape with the second's rules yields ``None`` for both
    fields — and, because the header was parsed earlier, assigning that ``None``
    unconditionally would *erase* a company name we had already resolved. So
    every assignment here is guarded on a non-empty value.

    Returns True when the message contributed a name or a GUID.
    """
    data = element_to_dict(element)
    remote = as_list(data.get("REMOTECMPINFO.LIST"))
    name = guid = None

    if remote:
        info = remote[0]
        # In this shape NAME is the GUID; only trust it if it looks like one.
        candidate = text_of(info, "NAME")
        guid = candidate if _is_company_guid(candidate) else None
        name = text_of(info, "REMOTECMPNAME")

    if not name:
        name = element.get("NAME") or text_of(data, "REMOTECMPNAME", "NAME")
    if not guid:
        guid = text_of(data, "GUID")
        guid = guid if _is_company_guid(guid) else None

    if name:
        result.company_name = name
    if guid:
        result.company_guid = guid
    return bool(name or guid)


# A Tally record GUID is the company UUID, a dash, then a per-record counter:
# ``6c5c39d3-8d04-455d-85f9-f53464f81e71-00000002``.
_RECORD_GUID = re.compile(
    r"^([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12})(?:-[0-9a-fA-F]+)?$"
)


def _is_company_guid(value: str | None) -> bool:
    """True when ``value`` is a bare Tally company UUID (no record suffix)."""
    if not value:
        return False
    match = _RECORD_GUID.match(value.strip())
    return bool(match) and match.group(1) == value.strip()


def _company_guid_from_record_guid(value: str | None) -> str | None:
    """Pull the company UUID off a record GUID, ignoring the record counter."""
    if not value:
        return None
    match = _RECORD_GUID.match(value.strip())
    return match.group(1) if match else None


def _master_record(element: ElementTree.Element, entity_key: str, sequence: int) -> dict[str, Any]:
    data = element_to_dict(element)
    if not isinstance(data, dict):
        data = {"#text": data}
    name = element.get("NAME") or text_of(data, "NAME", "LANGUAGENAME.LIST") or ""
    if not name:
        # Tally nests the display name under LANGUAGENAME.LIST/NAME.LIST/NAME.
        lang = data.get("LANGUAGENAME.LIST")
        if isinstance(lang, dict):
            name = text_of(lang.get("NAME.LIST") or {}, "NAME") or ""
    return {
        "_entity_key": entity_key,
        "_sequence": sequence,
        "guid": text_of(data, "GUID"),
        "name": name.strip(),
        # PARENT is the parent; RESERVEDNAME is not. For Tally's built-in
        # masters RESERVEDNAME holds the master's *own* canonical name, so
        # preferring it made every reserved group its own parent — "Bank
        # Accounts under Bank Accounts" — and collapsed the imported Chart of
        # Accounts into 28 self-parented roots instead of Tally's tree.
        "parent": (text_of(data, "PARENT") or "").strip() or None,
        #: Set when Tally considers this one of its built-in masters. Survives a
        #: rename, so it classifies a group the user relabelled.
        "reserved_name": (element.get("RESERVEDNAME") or "").strip() or None,
        "data": data,
    }


#: Which master carries which kind of opening balance.
_OPENING_ENTITY = {"ledger": "opening_ledger", "stock_item": "opening_stock"}


def _opening_record(
    master: dict[str, Any], entity_key: str, sequence: int
) -> dict[str, Any] | None:
    """Split a master's opening balance out into its own record, or None.

    Tally hangs the opening balance off the master element itself rather than
    shipping it as a voucher, so it has to be lifted out here. A ledger's is a
    plain signed amount; a stock item's is a quantity ("10 Nos") with its own
    value and rate.
    """
    opening_key = _OPENING_ENTITY.get(entity_key)
    if opening_key is None:
        return None
    data = master.get("data")
    if not isinstance(data, dict):
        return None

    balance = text_of(data, "OPENINGBALANCE")
    value = text_of(data, "OPENINGVALUE")
    if not balance and not value:
        return None
    # Tally writes a zero opening as "0" or "0.00" on every master; those are
    # noise, not data, and would otherwise stage thousands of empty rows.
    if parse_amount(balance) == Decimal("0") and parse_amount(value) == Decimal("0"):
        return None

    return {
        "_entity_key": opening_key,
        "_sequence": sequence,
        "guid": (master.get("guid") or None),
        "name": master.get("name"),
        "parent": master.get("parent"),
        # Kept as written: a ledger balance is signed (negative = debit, as in
        # every other Tally amount) and a stock balance carries its unit.
        "opening_balance": balance,
        "opening_value": value,
        "opening_rate": text_of(data, "OPENINGRATE"),
        "data": data,
    }


def _voucher_record(element: ElementTree.Element, sequence: int) -> dict[str, Any] | None:
    """Build a voucher record, resolving its catalogue entity via the voucher type."""
    data = element_to_dict(element)
    if not isinstance(data, dict):
        return None
    voucher_type = (
        element.get("VCHTYPE") or text_of(data, "VOUCHERTYPENAME", "VCHTYPE") or ""
    ).strip()
    spec = classify_voucher(voucher_type, text_of(data, "PARENTVOUCHERTYPE"))
    if spec is None:
        return None
    entity = ENTITY_BY_KEY.get(spec.entity_key)
    return {
        "_entity_key": spec.entity_key,
        "_sequence": sequence,
        "_stage": entity.stage if entity else 50,
        "guid": text_of(data, "GUID"),
        "voucher_type": voucher_type,
        "voucher_number": text_of(data, "VOUCHERNUMBER"),
        "date": parse_date(text_of(data, "DATE")),
        "party": text_of(data, "PARTYLEDGERNAME", "PARTYNAME", "BASICBUYERNAME"),
        "narration": text_of(data, "NARRATION"),
        "is_cancelled": parse_bool(text_of(data, "ISCANCELLED")),
        "is_optional": parse_bool(text_of(data, "ISOPTIONAL")),
        # Tally's own change-tracking keys. ALTERID is a monotonic counter that
        # Tally bumps on every edit — it is the mechanism Tally itself uses for
        # incremental sync, so keeping the highest one we have seen turns "import
        # everything again and hope" into "fetch what changed since N". VCHKEY is
        # the durable voucher identity that survives renumbering, which makes it
        # the right key for spotting an amended voucher rather than a new one.
        "alter_id": parse_int(text_of(data, "ALTERID")),
        "master_id": parse_int(text_of(data, "MASTERID")),
        "vch_key": (element.get("VCHKEY") or text_of(data, "VCHKEY") or "").strip() or None,
        "remote_id": (element.get("REMOTEID") or text_of(data, "REMOTEID") or "").strip() or None,
        "data": data,
    }


# --------------------------------------------------------------------------------------
# CSV parsing (Day Book / Ledger / Trial Balance exports)
# --------------------------------------------------------------------------------------

#: Column-header synonyms across Tally's CSV/Excel exports, lower-cased.
_CSV_ALIASES: dict[str, tuple[str, ...]] = {
    "date": ("date", "voucher date", "dt"),
    "voucher_type": ("voucher type", "vch type", "type"),
    "voucher_number": ("voucher no", "voucher no.", "vch no", "vch no.", "voucher number"),
    "party": ("particulars", "party", "party name", "ledger", "ledger name", "account"),
    "debit": ("debit", "dr", "debit amount"),
    "credit": ("credit", "cr", "credit amount"),
    "amount": ("amount", "value", "gross total"),
    "narration": ("narration", "remarks", "note"),
    "quantity": ("quantity", "qty", "billed qty"),
    "rate": ("rate", "price"),
    "item": ("item", "stock item", "item name"),
}


def _header_index(headers: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    for position, header in enumerate(headers):
        key = (header or "").strip().casefold()
        for canonical, aliases in _CSV_ALIASES.items():
            if key in aliases and canonical not in index:
                index[canonical] = position
    return index


def parse_csv(text: str, *, entity_key: str = "voucher_journal") -> ParsedFile:
    """Parse a Tally CSV/TSV Day Book export.

    CSV is the fallback for testers who cannot produce XML. It carries far less
    than XML (no GUIDs, no bill-wise details, no godown split), so every row
    lands with a warning telling the tester what was lost.
    """
    result = ParsedFile()
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]

    rows = list(csv.reader(io.StringIO(text), dialect))
    # Tally prefixes exports with title/company/period lines; the header row is
    # the first one that yields at least two recognised columns.
    header_row = -1
    index: dict[str, int] = {}
    for position, row in enumerate(rows[:25]):
        candidate = _header_index([cell for cell in row])
        if len(candidate) >= 2:
            header_row, index = position, candidate
            break
    if header_row < 0:
        raise ValidationError(
            "Could not find a recognisable header row. Export the Day Book from "
            "Tally with column headers, or use the XML format for a full import."
        )

    def cell(row: list[str], key: str) -> str | None:
        position = index.get(key)
        if position is None or position >= len(row):
            return None
        value = (row[position] or "").strip()
        return value or None

    sequence = 0
    carried_date: date | None = None
    for row in rows[header_row + 1 :]:
        if not any((c or "").strip() for c in row):
            continue
        sequence += 1
        posting_date = parse_date(cell(row, "date")) or carried_date
        carried_date = posting_date or carried_date
        voucher_type = cell(row, "voucher_type") or "Journal"
        spec = classify_voucher(voucher_type)
        key = spec.entity_key if spec else entity_key
        debit = parse_amount(cell(row, "debit"))
        credit = parse_amount(cell(row, "credit"))
        result.add(
            key,
            {
                "_entity_key": key,
                "_sequence": sequence,
                "_source": "csv",
                "guid": None,
                "voucher_type": voucher_type,
                "voucher_number": cell(row, "voucher_number"),
                "date": posting_date,
                "party": cell(row, "party"),
                "narration": cell(row, "narration"),
                "is_cancelled": False,
                "data": {
                    "_csv_row": {
                        canonical: cell(row, canonical) for canonical in index
                    },
                    "DEBIT": str(abs(debit)),
                    "CREDIT": str(abs(credit)),
                    "AMOUNT": str(parse_amount(cell(row, "amount")) or debit or credit),
                },
            },
        )

    if result.total:
        result.warnings.append(
            "CSV imports carry no GUIDs, bill-wise references or godown splits. "
            "Re-export as XML if you need payment allocations and stock detail."
        )
    return result


# --------------------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------------------


def parse(raw: bytes, *, file_name: str | None = None) -> tuple[ParsedFile, str, str]:
    """Parse an uploaded Tally export.

    Returns ``(parsed, source_type, decoded_text)``. The decoded text is stored
    on the session so a session can be re-parsed later without re-uploading.
    """
    text = decode_tally_bytes(raw)
    stripped = text.lstrip()
    looks_like_xml = stripped.startswith("<") or "<ENVELOPE" in stripped[:2000].upper()
    if looks_like_xml:
        return parse_xml(text), "XML", text
    if (file_name or "").lower().endswith((".csv", ".txt", ".tsv")) or "," in stripped[:500]:
        return parse_csv(text), "CSV", text
    raise ValidationError(
        "Unrecognised file. Upload a Tally XML export (.xml) or a Day Book CSV export (.csv)."
    )
