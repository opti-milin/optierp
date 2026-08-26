"""Reading a spreadsheet into sheets of rows — and nothing more.

This module has no opinion about what a column *means*. It answers only "what
does this file physically contain", so that :mod:`.detect` and :mod:`.normalise`
can argue about meaning against a stable, already-cleaned structure.

Three things make real-world exports awkward, and each has a defence here:

1. **Banner rows.** Almost every accounting package prefixes a sheet with the
   company name, the report title and the period before the actual header row.
   :func:`_find_header` scores candidate rows instead of assuming row 1.
2. **Mixed cell types.** The same column arrives as a real ``datetime`` in one
   row and the string ``"2025-04-01 00:00:00"`` in the next, because someone
   opened the file and retyped a cell. Values are coerced once, on read.
3. **Size.** A five-year Day Book export is a genuinely large workbook, and
   openpyxl's default mode builds the whole thing in memory. Everything here
   streams, and a hard cell budget turns "the container died" into a sentence
   telling the user to split the export by period.
"""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any, Iterator

from app.core.exceptions import ValidationError

#: Anything past this and we ask for a narrower export rather than trying. Chosen
#: to comfortably clear the biggest realistic single-year dump (a 500-voucher
#: workbook is ~40k cells) while staying inside a request's memory budget.
MAX_CELLS = 4_000_000
#: Rows kept per sheet as a preview for the mapping wizard.
SAMPLE_ROWS = 5
#: How far down a sheet we look for the header before giving up on it.
HEADER_SEARCH_ROWS = 25

XLSX_SUFFIXES = (".xlsx", ".xlsm")
CSV_SUFFIXES = (".csv", ".tsv", ".txt")

_SPACES = re.compile(r"[\s_]+")
_NON_WORD = re.compile(r"[^a-z0-9 ]+")


def normalise_header(value: Any) -> str:
    """Fold a column header to its comparable core.

    ``"Voucher No."``, ``"voucher_no"`` and ``"VOUCHER NO"`` are the same column
    to a human and must be to us as well — profiles list aliases in exactly this
    folded form, so the alias table stays readable.
    """
    text = str(value or "").strip().casefold()
    text = _NON_WORD.sub(" ", text.replace("_", " "))
    return _SPACES.sub(" ", text).strip()


def _coerce(value: Any) -> Any:
    """Normalise one cell. Dates become ``date``, numbers ``Decimal``, blanks ``None``."""
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, bool):
        return value
    if isinstance(value, datetime):
        # openpyxl hands back midnight datetimes for date-formatted cells. A real
        # time-of-day is meaningful (an audit-trail timestamp), so only the
        # midnight case collapses to a plain date.
        return value.date() if value.time() == time.min else value
    if isinstance(value, date):
        return value
    if isinstance(value, float):
        # Excel stores every number as a float, so 18 arrives as 18.0 and money
        # arrives with binary-float noise. Decimal(str(...)) keeps the digits the
        # user actually sees rather than the float's shortest repr.
        return Decimal(str(value))
    if isinstance(value, int):
        return Decimal(value)
    return value


@dataclass
class Sheet:
    """One tab of a workbook, header resolved and rows cleaned."""

    name: str
    headers: list[str]
    rows: list[dict[str, Any]]
    #: Headers as written in the file, positionally aligned with ``headers``.
    #: The wizard shows these; profiles match on the folded ``headers``.
    raw_headers: list[str] = field(default_factory=list)
    #: Row number (1-based) the header was found on. Surfaced when detection
    #: fails, because "we read row 7 as your header" is the useful thing to say.
    header_row: int = 1
    #: True when no row scored as a header — the sheet is data we cannot read.
    headerless: bool = False

    def __len__(self) -> int:
        return len(self.rows)

    @property
    def sample(self) -> list[dict[str, Any]]:
        return self.rows[:SAMPLE_ROWS]

    def has(self, *headers: str) -> bool:
        """True when every folded header is present."""
        present = set(self.headers)
        return all(h in present for h in headers)

    def overlap(self, headers: set[str]) -> int:
        return len(set(self.headers) & headers)


@dataclass
class Workbook:
    """A whole upload: every sheet, plus what we could not read."""

    sheets: list[Sheet]
    #: Sheets skipped entirely (empty, or no readable header).
    skipped: dict[str, str] = field(default_factory=dict)

    def by_name(self, name: str) -> Sheet | None:
        folded = normalise_header(name)
        for sheet in self.sheets:
            if normalise_header(sheet.name) == folded:
                return sheet
        return None

    @property
    def names(self) -> list[str]:
        return [s.name for s in self.sheets]

    @property
    def total_rows(self) -> int:
        return sum(len(s) for s in self.sheets)


# --------------------------------------------------------------------------------------
# Header detection
# --------------------------------------------------------------------------------------


def _score_header(row: list[Any], following: list[list[Any]]) -> int:
    """How much this row looks like a header rather than data or a banner.

    A header row is mostly non-empty text cells, has few duplicates, and — the
    part that actually separates it from a title row — is followed by rows of
    similar width. A banner like ``["Tally Solutions", None, None, None]`` is one
    wide-ish text cell over a lot of nothing, and scores badly on all three.
    """
    cells = [c for c in row if c not in (None, "")]
    if len(cells) < 2:
        return 0
    textish = sum(1 for c in cells if isinstance(c, str))
    labels = [normalise_header(c) for c in cells]
    duplicates = len(labels) - len(set(labels))
    width = len(cells)
    # Data rows underneath should fill roughly the same columns.
    support = 0
    for candidate in following:
        filled = sum(1 for c in candidate if c not in (None, ""))
        if filled >= max(2, width // 2):
            support += 1
    # Duplicates count against, but mildly: an export that repeats a heading
    # ("Amount" twice) is a nuisance, not evidence the row is data. The strong
    # signal is `support` — data rows of the same width underneath.
    return textish * 2 + support * 3 - duplicates * 2


def _find_header(rows: list[list[Any]]) -> int:
    """Index of the header row within ``rows``, or -1 when none looks like one."""
    best, best_score = -1, 0
    for position, row in enumerate(rows[:HEADER_SEARCH_ROWS]):
        score = _score_header(row, rows[position + 1 : position + 4])
        if score > best_score:
            best, best_score = position, score
    # A lone banner row over empty space can still score a little; require enough
    # evidence that we are not about to read a title as a set of columns.
    if best_score >= 6:
        return best

    # ...unless the sheet is *only* headings. A template sheet the user has not
    # filled in yet is exactly that, and a narrow one ("Warehouses": two columns,
    # no rows) scores too low on width alone. Rejecting those made a perfectly
    # good blank template look like it had unreadable sheets.
    filled = [i for i, row in enumerate(rows) if any(c not in (None, "") for c in row)]
    if len(filled) == 1:
        cells = [c for c in rows[filled[0]] if c not in (None, "")]
        labels = [normalise_header(c) for c in cells]
        if cells and all(isinstance(c, str) for c in cells) and len(set(labels)) == len(labels):
            return filled[0]
    return -1


def _dedupe(headers: list[str]) -> list[str]:
    """Two columns called "Amount" must stay distinguishable: the second becomes "amount 2"."""
    seen: dict[str, int] = {}
    out: list[str] = []
    for header in headers:
        if not header:
            header = "column"
        count = seen.get(header, 0) + 1
        seen[header] = count
        out.append(header if count == 1 else f"{header} {count}")
    return out


def _build_sheet(name: str, rows: list[list[Any]]) -> Sheet | str:
    """Turn raw cell rows into a :class:`Sheet`, or return why we could not."""
    if not any(any(c not in (None, "") for c in row) for row in rows):
        return "the sheet is empty"
    position = _find_header(rows)
    if position < 0:
        return "no header row could be identified"

    raw = [("" if c is None else str(c).strip()) for c in rows[position]]
    # Trailing empty header cells are Excel's padding, not columns.
    while raw and not raw[-1]:
        raw.pop()
    headers = _dedupe([normalise_header(c) for c in raw])

    records: list[dict[str, Any]] = []
    for row in rows[position + 1 :]:
        values = [_coerce(c) for c in row[: len(headers)]]
        if not any(v not in (None, "") for v in values):
            continue
        values += [None] * (len(headers) - len(values))
        records.append(dict(zip(headers, values)))

    return Sheet(
        name=name,
        headers=headers,
        rows=records,
        raw_headers=raw,
        header_row=position + 1,
    )


# --------------------------------------------------------------------------------------
# Readers
# --------------------------------------------------------------------------------------


def _read_xlsx(raw: bytes) -> Workbook:
    try:
        import openpyxl
    except ImportError:  # pragma: no cover - dependency is declared
        raise ValidationError(
            "Spreadsheet import is not available on this server (openpyxl is not "
            "installed). Export as CSV or Tally XML instead."
        ) from None

    try:
        book = openpyxl.load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
    except Exception as exc:  # openpyxl raises a zoo of exception types
        raise ValidationError(
            f"Could not read this as an Excel workbook ({exc}). Re-save it as "
            ".xlsx from Excel or LibreOffice and upload it again."
        ) from None

    sheets: list[Sheet] = []
    skipped: dict[str, str] = {}
    budget = MAX_CELLS
    try:
        for worksheet in book.worksheets:
            rows: list[list[Any]] = []
            for row in worksheet.iter_rows(values_only=True):
                budget -= len(row)
                if budget < 0:
                    raise ValidationError(
                        "That workbook is too large to import in one go. Export a "
                        "narrower date range and import the periods one at a time "
                        "— masters first, then each period's transactions."
                    )
                rows.append(list(row))
            built = _build_sheet(worksheet.title, rows)
            if isinstance(built, str):
                skipped[worksheet.title] = built
            else:
                sheets.append(built)
    finally:
        book.close()

    if not sheets:
        raise ValidationError(
            "No readable sheets were found in that workbook. Each sheet needs a "
            "row of column headings above its data."
        )
    return Workbook(sheets=sheets, skipped=skipped)


def _decode_text(raw: bytes) -> str:
    """Decode a text upload, sniffing the encoding the exporting app used."""
    if raw[:3] == b"\xef\xbb\xbf":
        return raw[3:].decode("utf-8")
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    for encoding in ("utf-8", "cp1252", "iso-8859-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_csv(text: str, *, name: str = "Sheet1") -> Workbook:
    try:
        dialect: Any = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = [list(row) for row in csv.reader(io.StringIO(text), dialect)]
    built = _build_sheet(name, rows)
    if isinstance(built, str):
        raise ValidationError(
            f"Could not read that CSV: {built}. The first rows must include a line "
            "of column headings."
        )
    return Workbook(sheets=[built])


def read(raw: bytes, *, file_name: str | None = None) -> Workbook:
    """Read an uploaded spreadsheet into sheets of rows."""
    if not raw:
        raise ValidationError("The uploaded file is empty")
    if looks_like_xlsx(raw):
        return _read_xlsx(raw)
    name = (file_name or "").casefold()
    if name.endswith(CSV_SUFFIXES):
        return _read_csv(_decode_text(raw), name=file_name or "Sheet1")
    raise ValidationError(
        "Unrecognised spreadsheet. Upload an .xlsx workbook or a .csv export."
    )


def looks_like_xlsx(raw: bytes) -> bool:
    """True when the bytes are a ZIP container, which every .xlsx is.

    Sniffed rather than trusted from the extension: browsers and email clients
    rename files freely, and an .xlsx that arrives as ``export.xls`` is still an
    .xlsx. ``PK\\x05\\x06`` / ``PK\\x07\\x08`` are the empty and spanned variants.
    """
    return raw[:2] == b"PK" and raw[2:4] in (b"\x03\x04", b"\x05\x06", b"\x07\x08")


def iter_values(sheet: Sheet, *headers: str) -> Iterator[Any]:
    """Yield the first present value among ``headers`` for each row."""
    for row in sheet.rows:
        for header in headers:
            if row.get(header) is not None:
                yield row[header]
                break
        else:
            yield None
