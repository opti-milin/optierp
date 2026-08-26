"""The intermediate representation every source adapter produces.

One shape feeds the whole pipeline. A Tally XML export, a Zoho Books workbook and
a hand-filled OptiERP template all arrive here as the same thing:

``ParsedFile.records``
    ``{catalogue entity key: [record, ...]}``.
``record``
    ``{_entity_key, _sequence, guid, name, parent, data, ...}`` where ``data`` is
    a **Tally-XML-shaped dict** — ``ALLLEDGERENTRIES.LIST``,
    ``ALLINVENTORYENTRIES.LIST``, ``BILLALLOCATIONS.LIST`` and friends.

Why Tally's shape rather than a neutral one? Because it was here first and the
importers are written against it: ~4,000 lines that already post GL and stock
through the real services, dedupe by GUID, and roll back by cancellation. A
neutral IR would have bought nothing but a translation layer on both sides. The
cost is that a non-Tally adapter has to speak Tally's vocabulary — which
:mod:`app.services.migration.sources.builders` exists to make painless.

Sign convention, inherited and non-negotiable: in a ledger entry a **negative
amount is a debit**. Adapters convert once, here, so no importer has to guess.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

ZERO = Decimal("0")


# --------------------------------------------------------------------------------------
# Value coercion
# --------------------------------------------------------------------------------------

#: Every date spelling seen across Tally exports and the spreadsheet dialects of
#: Tally, Zoho Books, Busy and Marg. Ordered most-specific first: ``%Y%m%d`` must
#: beat ``%Y-%m-%d`` and unambiguous ISO must beat the day/month-first pair,
#: because ``03/04/2025`` is genuinely ambiguous and Indian software means 3 April.
_DATE_FORMATS = (
    "%Y%m%d",
    "%Y-%m-%d",
    "%Y/%m/%d",
    "%d-%b-%Y",
    "%d-%B-%Y",
    "%d-%m-%Y",
    "%d/%m/%Y",
    "%d.%m.%Y",
    "%d-%b-%y",
    "%d/%m/%y",
    "%m/%d/%Y",  # last resort: only reached when the day-first reading is invalid
)


def parse_date(value: Any) -> date | None:
    """Read a date from anything a source might hand us.

    openpyxl gives real ``datetime`` objects for date-formatted cells and plain
    strings for text-formatted ones; the same column often mixes both when a
    human has edited part of it.
    """
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    # "2025-04-01 00:00:00" — a datetime that lost its type on the way through
    # CSV or a string-formatted spreadsheet cell.
    if " " in text and len(text) > 10:
        text = text.split(" ", 1)[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


#: Backwards-compatible alias — this used to be the only date reader there was.
parse_tally_date = parse_date


def parse_amount(value: Any) -> Decimal:
    """Read a signed amount, tolerating currency symbols and thousands separators.

    Sign convention: **negative = debit, positive = credit** in ledger entries
    (the opposite of what most people expect), so callers must not assume.
    """
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    text = str(value).strip()
    if not text:
        return ZERO
    # "₹ -1,234.56" / "(-)1234.56" / "1,234.56 Dr" / "(1,234.56)"
    negative = (
        text.startswith("-")
        or "(-)" in text
        or text.rstrip().endswith("Dr")
        or (text.startswith("(") and text.endswith(")"))
    )
    cleaned = re.sub(r"[^0-9.]", "", text.replace("(-)", ""))
    if not cleaned or cleaned == ".":
        return ZERO
    try:
        amount = Decimal(cleaned)
    except InvalidOperation:
        return ZERO
    return -amount if negative and amount > ZERO else amount


def parse_int(value: Any) -> int | None:
    """Read an integer field. Tally pads these (``" 2"``), so strip first."""
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


#: What every source spells "true" as. Zoho writes ``Yes``/``true``; Tally writes
#: ``Yes``; a hand-filled template writes whatever the person felt like.
_TRUE = {"yes", "true", "1", "y", "t"}


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().casefold() in _TRUE


# --------------------------------------------------------------------------------------
# Reading the IR
# --------------------------------------------------------------------------------------


def as_list(value: Any) -> list[Any]:
    """One child arrives as a dict and many as a list — normalise to a list."""
    if value is None:
        return []
    return value if isinstance(value, list) else [value]


def text_of(node: Any, *keys: str, default: str | None = None) -> str | None:
    """Read the first present key from a node, tolerating ``{'#text': ...}``."""
    if not isinstance(node, dict):
        return node if isinstance(node, str) and node else default
    for key in keys:
        if key in node:
            value = node[key]
            if isinstance(value, dict):
                value = value.get("#text")
            if isinstance(value, list):
                value = value[0] if value else None
                if isinstance(value, dict):
                    value = value.get("#text")
            if value not in (None, ""):
                return str(value)
    return default


# --------------------------------------------------------------------------------------
# Parse result
# --------------------------------------------------------------------------------------


class ParsedFile:
    """What one upload contained, bucketed by catalogue entity key."""

    def __init__(self) -> None:
        self.records: dict[str, list[dict[str, Any]]] = {}
        self.company_name: str | None = None
        self.company_guid: str | None = None
        self.from_date: date | None = None
        self.to_date: date | None = None
        #: Entities present in the file that the catalogue does not cover. For a
        #: workbook these are sheet names; for XML, element tags.
        self.unknown_tags: dict[str, int] = {}
        #: ``{sheet: (row count, why)}`` — sheets read and counted but deliberately
        #: not imported. Separate from ``unknown_tags`` because "we do not support
        #: this yet, here is why" is a different answer from "we did not recognise
        #: this", and a migration report that conflates them is not much of a report.
        self.skipped_sheets: dict[str, tuple[int, str]] = {}
        self.warnings: list[str] = []
        #: Which source app and profile produced this, when the adapter knows.
        #: Stored on the session so the wizard can say "detected Zoho Books".
        self.source_app: str | None = None
        self.source_profile: str | None = None
        #: The resolved sheet -> entity / column -> field map, so a re-parse
        #: reproduces this run exactly rather than re-detecting.
        self.sheet_map: dict[str, Any] | None = None

    def add(self, entity_key: str, record: dict[str, Any]) -> None:
        self.records.setdefault(entity_key, []).append(record)

    def note_unknown(self, tag: str, count: int = 1) -> None:
        self.unknown_tags[tag] = self.unknown_tags.get(tag, 0) + count

    @property
    def total(self) -> int:
        return sum(len(rows) for rows in self.records.values())
