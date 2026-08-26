"""Reading a spreadsheet: header sniffing, value coercion, and the limits.

These are the failures that cost the most in a real migration, because they are
silent: a banner row read as headings, a date read as text, a number that keeps
its float noise. Each one produces an import that looks like it worked.
"""

import io
from datetime import date
from decimal import Decimal

import pytest

from app.core.exceptions import ValidationError
from app.services.migration.sources import workbook as wb


def build(sheets: dict[str, list[list]]) -> bytes:
    """A real .xlsx in memory, so the tests exercise the openpyxl path."""
    from openpyxl import Workbook

    book = Workbook()
    book.remove(book.active)
    for name, rows in sheets.items():
        sheet = book.create_sheet(name)
        for row in rows:
            sheet.append(row)
    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


# --- header folding ------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw,folded",
    [
        ("Voucher No.", "voucher no"),
        ("voucher_no", "voucher no"),
        ("  VOUCHER   NO  ", "voucher no"),
        ("HSN/SAC", "hsn sac"),
        ("Amount (₹)", "amount"),
        (None, ""),
    ],
)
def test_headers_fold_to_the_same_key_however_they_are_written(raw, folded):
    assert wb.normalise_header(raw) == folded


# --- header row detection ------------------------------------------------------------


def test_the_header_row_is_found_below_an_exporters_banner():
    # Almost every accounting package writes its own name, the report title and
    # the period above the real headings. Reading row 1 gets you a one-column
    # sheet called "Acme Traders Pvt Ltd".
    raw = build({
        "Day Book": [
            ["Acme Traders Pvt Ltd", None, None],
            ["Day Book", None, None],
            ["1-Apr-2025 to 31-Mar-2026", None, None],
            [],
            ["Date", "Particulars", "Amount"],
            ["2025-04-01", "Sales", 1000],
            ["2025-04-02", "Purchase", 500],
        ]
    })
    sheet = wb.read(raw).sheets[0]
    assert sheet.headers == ["date", "particulars", "amount"]
    assert sheet.header_row == 5
    assert len(sheet) == 2


def test_a_sheet_of_headings_with_no_data_is_still_readable():
    # This is a blank OptiERP template sheet. Rejecting it made a perfectly good
    # template look like it had unreadable sheets.
    raw = build({"Warehouses": [["Warehouse Name", "Parent Warehouse"]]})
    book = wb.read(raw)
    assert book.skipped == {}
    assert book.sheets[0].headers == ["warehouse name", "parent warehouse"]
    assert len(book.sheets[0]) == 0


def test_a_sheet_with_no_headings_is_reported_not_guessed():
    raw = build({
        "Ledgers": [["Name", "Group"], ["Cash", "Cash-in-Hand"], ["HDFC", "Bank Accounts"]],
        "Notes": [["just prose"], ["more prose"], ["and more"]],
    })
    book = wb.read(raw)
    assert book.names == ["Ledgers"]
    assert "Notes" in book.skipped


def test_a_workbook_with_nothing_readable_at_all_is_refused():
    raw = build({"Notes": [["just prose"], ["more prose"]]})
    with pytest.raises(ValidationError) as excinfo:
        wb.read(raw, file_name="notes.xlsx")
    assert "column headings" in str(excinfo.value)


def test_two_columns_with_the_same_heading_stay_distinguishable():
    raw = build({"S": [["Amount", "Amount"], [10, 20], [30, 40], [50, 60]]})
    sheet = wb.read(raw).sheets[0]
    assert sheet.headers == ["amount", "amount 2"]
    assert sheet.rows[0] == {"amount": Decimal("10"), "amount 2": Decimal("20")}


# --- value coercion ------------------------------------------------------------------


def test_dates_numbers_and_blanks_are_coerced_once_on_read():
    from datetime import datetime

    raw = build({
        "S": [
            ["Date", "Qty", "Rate", "Note"],
            [datetime(2025, 4, 1), 3, 1234.56, "  spaced  "],
            ["2025-04-02 00:00:00", 0, 0.1, "   "],
        ]
    })
    rows = wb.read(raw).sheets[0].rows
    assert rows[0]["date"] == date(2025, 4, 1)
    assert rows[0]["qty"] == Decimal("3")
    # Decimal(str(float)) keeps the digits the user sees, not the float's binary tail.
    assert rows[0]["rate"] == Decimal("1234.56")
    assert rows[0]["note"] == "spaced"
    # A blank-looking cell is None, so "is this column mapped" never turns on whitespace.
    assert rows[1]["note"] is None
    # A datetime that lost its type on the way through a text-formatted cell.
    assert rows[1]["date"] == "2025-04-02 00:00:00"


def test_an_all_blank_row_is_not_a_record():
    raw = build({"S": [["A", "B"], [1, 2], [None, None], ["", ""], [3, 4]]})
    assert len(wb.read(raw).sheets[0]) == 2


# --- format sniffing -----------------------------------------------------------------


def test_an_xlsx_is_recognised_by_its_bytes_not_its_extension():
    # Browsers and mail clients rename files freely; an .xlsx that arrives as
    # "export.xls" is still an .xlsx.
    raw = build({"S": [["A", "B"], [1, 2]]})
    assert wb.looks_like_xlsx(raw)
    assert wb.read(raw, file_name="export.xls").sheets[0].headers == ["a", "b"]


def test_a_csv_reads_as_a_single_sheet():
    csv = b"Name,Group,Opening\nCash,Cash-in-Hand,250000\nHDFC,Bank Accounts,600000\n"
    book = wb.read(csv, file_name="ledgers.csv")
    assert len(book.sheets) == 1
    assert book.sheets[0].headers == ["name", "group", "opening"]
    assert len(book.sheets[0]) == 2


def test_a_semicolon_delimited_csv_is_sniffed():
    csv = b"Name;Group\nCash;Cash-in-Hand\nHDFC;Bank Accounts\n"
    assert wb.read(csv, file_name="l.csv").sheets[0].headers == ["name", "group"]


def test_something_that_is_neither_is_refused_with_a_usable_message():
    with pytest.raises(ValidationError) as excinfo:
        wb.read(b"\x89PNG\r\n\x1a\n", file_name="screenshot.png")
    assert "xlsx" in str(excinfo.value).lower()


def test_an_empty_upload_is_refused():
    with pytest.raises(ValidationError):
        wb.read(b"", file_name="empty.xlsx")


def test_a_workbook_too_large_to_hold_asks_for_a_narrower_export(monkeypatch):
    monkeypatch.setattr(wb, "MAX_CELLS", 4)
    raw = build({"S": [["A", "B"], [1, 2], [3, 4], [5, 6]]})
    with pytest.raises(ValidationError) as excinfo:
        wb.read(raw, file_name="big.xlsx")
    assert "narrower date range" in str(excinfo.value)
