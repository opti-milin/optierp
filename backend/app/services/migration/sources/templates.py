"""Generating the OptiERP import template.

The template is the answer to "my accounting package is not on your list". It is
built from :data:`~.builtin.OPTIERP_TEMPLATE` and :data:`~.profiles.KINDS`, so it
cannot describe a column the importer does not read or omit one it requires —
the file people fill in and the code that reads it come from the same table.

Two deliberate choices:

**The data sheets ship empty.** Example rows inside a sheet somebody is about to
paste into are a liability: forget to delete them and "ACME Traders" becomes a
real customer with a real opening balance. Examples live on the ``Fields`` sheet
instead, where they can be read but never imported.

**Every column carries its own note.** A header comment says what the column is,
whether it is required, and what format it wants, so the person filling it in
does not have to keep the documentation open beside the spreadsheet.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any

from app.core.exceptions import ValidationError
from app.services.migration.sources.builtin import OPTIERP_TEMPLATE, TEMPLATE_MARKER_SHEET
from app.services.migration.sources.profiles import CHILD_FIELDS, KINDS, SheetSpec

#: Bumped when the template's shape changes in a way a filled-in older copy would
#: not survive. Stamped into the marker sheet so an upload can say so.
TEMPLATE_VERSION = "2"

FILENAME = "OptiERP-import-template.xlsx"

#: What each field kind wants, in the words of somebody filling in a spreadsheet.
_FORMAT_HINT = {
    "date": "Date (YYYY-MM-DD)",
    "amount": "Number, no currency symbol",
    "qty": "Number",
    "bool": "Yes / No",
    "choice": "Dr or Cr",
    "text": "Text",
}

#: A worked example per field, shown on the Fields sheet. Anything not named here
#: falls back to its format hint, which is still more use than an empty cell.
_EXAMPLES = {
    "account": "Sales",
    "account name": "ABC Traders",
    "account number": "50200012345678",
    "accumulated depreciation": "120000.00",
    "address line 1": "Unit 4, Nehru Industrial Estate",
    "address line 2": "Andheri East",
    "address title": "ABC Traders",
    "address type": "Billing",
    "asset category": "Plant & Machinery",
    "asset code": "FA-001",
    "asset name": "CNC Lathe",
    "attention": "Accounts Payable",
    "bank account": "HDFC Current",
    "bank account no": "50200012345678",
    "bank id": "BANK-1",
    "bank name": "HDFC Bank",
    "budget amount": "1200000.00",
    "budget name": "Marketing 2025-26",
    "city": "Mumbai",
    "company or individual": "Company",
    "cost centre": "Head Office",
    "country": "India",
    "credit limit": "500000.00",
    "currency": "INR",
    "currency name": "Indian Rupee",
    "days": "30",
    "deposit or withdrawal": "Deposit",
    "depreciation method": "Straight Line",
    "depreciation rate pct": "15",
    "description": "Wireless optical mouse, USB-C",
    "designation": "Accounts Manager",
    "email": "accounts@abctraders.example",
    "exchange rate": "83.50",
    "expected date": "2025-04-15",
    "first name": "Priya",
    "ifsc": "HDFC0001234",
    "iso code": "USD",
    "last name": "Nair",
    "ledger account": "HDFC Bank",
    "location": "Plant 1",
    "mobile": "9820012345",
    "notes": "Key account, quarterly review",
    "order number": "SO-0001",
    "order type": "Sales Order",
    "party group": "Wholesale",
    "party id": "CUS001",
    "payment terms": "Net 30",
    "period from": "2025-04-01",
    "period to": "2026-03-31",
    "phone": "02261234567",
    "pincode": "400069",
    "portion pct": "100",
    "price list name": "Retail Price",
    "primary": "Yes",
    "rate pct": "18",
    "reference number": "UTR-88213",
    "registered name": "ABC Traders Private Limited",
    "salutation": "Ms.",
    "sales or purchase": "Sales",
    "selling or buying": "Selling",
    "state code": "27",
    "statement line id": "STMT-00001",
    "tax account": "Output CGST",
    "tax category": "Registered",
    "tax name": "GST 18%",
    "tax type": "GST",
    "term name": "Net 30",
    "territory": "West",
    "useful life years": "10",
    "amount": "11800.00",
    "bill reference": "INV-0001",
    "cgst": "900.00",
    "credit": "11800.00",
    "credit days": "30",
    "date": "2025-04-01",
    "debit": "11800.00",
    "dr cr": "Dr",
    "gst rate": "18",
    "gstin": "27AAACO1234F1Z5",
    "hsn sac": "84716060",
    "igst": "1800.00",
    "income expense account": "Sales",
    "item code": "ITM-001",
    "item group": "IT Accessories",
    "item name": "Wireless Mouse",
    "opening balance": "84123.00",
    "pan": "AAACO1234F",
    "parent group": "Sundry Debtors",
    "party": "ABC Traders",
    "party type": "customer",
    "purchase rate": "430.00",
    "quantity": "10",
    "rate": "650.00",
    "selling rate": "650.00",
    "sgst": "900.00",
    "taxable value": "10000.00",
    "total": "11800.00",
    "unit": "Nos",
    "voucher id": "V0001",
    "voucher number": "SAL/25-04/0001",
    "voucher type": "Sales",
    "warehouse": "Main Warehouse",
}


def _title(folded: str) -> str:
    """``"account name"`` -> ``"Account Name"`` — folds back on read, so it round-trips."""
    return " ".join(word.capitalize() for word in folded.split())


def _fields_for(spec: SheetSpec) -> list[Any]:
    return list(KINDS[spec.kind].fields)


def _columns(spec: SheetSpec, fields: list[Any]) -> list[tuple[str, Any]]:
    """``(header, field spec)`` in the profile's own order, required first."""
    by_name = {f.name: f for f in fields}
    ordered: list[tuple[str, Any]] = []
    for name, aliases in spec.columns.items():
        field = by_name.get(name)
        if field is None or not aliases:
            continue
        ordered.append((_title(aliases[0]), field))
    ordered.sort(key=lambda pair: (not pair[1].required, pair[1].name))
    return ordered


def build(entities: tuple[str, ...] = ()) -> bytes:
    """The template workbook as bytes.

    ``entities`` narrows it to the sheets a tester actually needs — somebody
    importing only a customer list should not be handed a nine-sheet workbook.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.comments import Comment
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter
    except ImportError:  # pragma: no cover - dependency is declared
        raise ValidationError(
            "Template download is not available on this server (openpyxl is not "
            "installed)."
        ) from None

    wanted = {e.strip() for e in entities if e.strip()}
    book = Workbook()
    book.remove(book.active)

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    title_font = Font(bold=True, size=12)

    readme = book.create_sheet("README")
    fields_sheet = book.create_sheet("Fields")

    written: list[tuple[SheetSpec, list[tuple[str, Any]]]] = []
    for spec in OPTIERP_TEMPLATE.sheets:
        if spec.kind == "reference":
            continue
        if wanted and spec.entity not in wanted:
            # A narrowed download is a narrowed download: the voucher sheets have
            # no entity of their own, and shipping them anyway is exactly what
            # somebody asking for "just the accounts and parties" did not want.
            continue
        columns = _columns(spec, _fields_for(spec))
        sheet = book.create_sheet(spec.sheet)
        for index, (header, field) in enumerate(columns, start=1):
            cell = sheet.cell(row=1, column=index, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(vertical="center")
            note = f"{field.label}\n{'Required' if field.required else 'Optional'}\n"
            note += _FORMAT_HINT.get(field.kind, "Text")
            cell.comment = Comment(note, "OptiERP")
            sheet.column_dimensions[get_column_letter(index)].width = max(14, len(header) + 4)
        sheet.freeze_panes = "A2"
        written.append((spec, columns))

        for child in spec.children:
            child_columns = _columns(child, list(CHILD_FIELDS.get(child.role, ())))
            child_sheet = book.create_sheet(child.sheet)
            for index, (header, field) in enumerate(child_columns, start=1):
                cell = child_sheet.cell(row=1, column=index, value=header)
                cell.font = header_font
                cell.fill = header_fill
                note = f"{field.label}\n{'Required' if field.required else 'Optional'}\n"
                note += _FORMAT_HINT.get(field.kind, "Text")
                cell.comment = Comment(note, "OptiERP")
                child_sheet.column_dimensions[get_column_letter(index)].width = max(
                    14, len(header) + 4
                )
            child_sheet.freeze_panes = "A2"
            written.append((child, child_columns))

    _write_fields(fields_sheet, written, header_font, header_fill)
    _write_readme(readme, written, title_font)

    marker = book.create_sheet(TEMPLATE_MARKER_SHEET)
    marker["A1"], marker["B1"] = "Key", "Value"
    for offset, (key, value) in enumerate(
        (
            ("template", "OptiERP import template"),
            ("version", TEMPLATE_VERSION),
            ("profile", OPTIERP_TEMPLATE.key),
            ("generated", date.today().isoformat()),
        ),
        start=2,
    ):
        marker[f"A{offset}"], marker[f"B{offset}"] = key, value
    marker.sheet_state = "hidden"

    # README first, marker last — the tester opens onto the instructions.
    book.move_sheet(readme, offset=-len(book.sheetnames))

    buffer = io.BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def _write_fields(sheet: Any, written: list[Any], header_font: Any, header_fill: Any) -> None:
    headers = ("Sheet", "Column", "Required", "Format", "What it is", "Example")
    for index, header in enumerate(headers, start=1):
        cell = sheet.cell(row=1, column=index, value=header)
        cell.font = header_font
        cell.fill = header_fill
    widths = (22, 26, 10, 26, 46, 22)
    from openpyxl.utils import get_column_letter

    for index, width in enumerate(widths, start=1):
        sheet.column_dimensions[get_column_letter(index)].width = width

    row = 2
    for spec, columns in written:
        for header, field in columns:
            folded = header.casefold()
            sheet.cell(row=row, column=1, value=spec.sheet)
            sheet.cell(row=row, column=2, value=header)
            sheet.cell(row=row, column=3, value="Yes" if field.required else "")
            sheet.cell(row=row, column=4, value=_FORMAT_HINT.get(field.kind, "Text"))
            sheet.cell(row=row, column=5, value=field.label)
            sheet.cell(
                row=row, column=6,
                value=_EXAMPLES.get(folded, ""),
            )
            row += 1
    sheet.freeze_panes = "A2"


def _write_readme(sheet: Any, written: list[Any], title_font: Any) -> None:
    from openpyxl.styles import Alignment

    sheet.column_dimensions["A"].width = 100
    lines: list[tuple[str, bool]] = [
        ("OptiERP import template", True),
        ("", False),
        (
            "Paste your data under the headings on each sheet. Leave a sheet empty "
            "if you have nothing for it — nothing is required except the sheets you "
            "actually use.",
            False,
        ),
        ("", False),
        ("Order matters when you run the import, not when you fill this in:", True),
        ("  1. Accounts, Items, Warehouses and Cost Centres — the things vouchers refer to.", False),
        ("  2. Opening_Balances and Opening_Stock — where your books stood on the cut-off date.", False),
        ("  3. Vouchers, with their lines on Voucher_Ledgers and Voucher_Items.", False),
        ("", False),
        ("Rules worth knowing:", True),
        (
            "  * Voucher ID ties a voucher to its lines. Any text will do as long as it is "
            "unique in the file and the same on the header and its line rows.",
            False,
        ),
        (
            "  * A voucher must balance: the Debit column and the Credit column of its "
            "Voucher_Ledgers rows must add up to the same figure.",
            False,
        ),
        (
            "  * Put the party (customer or supplier) on its own Voucher_Ledgers row. That "
            "is what makes an invoice appear in their ageing, and a payment settle it.",
            False,
        ),
        (
            "  * Tax goes on its own row too, named for the tax it is (CGST Output, IGST "
            "Input, and so on). Tax amounts are imported exactly as you write them — "
            "OptiERP does not recalculate them.",
            False,
        ),
        (
            "  * Dates are YYYY-MM-DD. Amounts are plain numbers: no currency symbols, no "
            "thousands separators.",
            False,
        ),
        ("", False),
        ("The Fields sheet lists every column, whether it is required, and an example.", False),
        (
            "Do not rename or delete the hidden _OptiERP sheet — it is how the importer "
            "recognises this as a template and skips the mapping step.",
            False,
        ),
        ("", False),
        ("Sheets in this template:", True),
    ]
    for spec, columns in written:
        kind = getattr(spec, "kind", None)
        label = (
            KINDS[kind].label if kind else getattr(spec, "role", "").replace("_", " ").title()
        )
        lines.append((f"  * {spec.sheet} — {label} ({len(columns)} columns)", False))

    for index, (text, bold) in enumerate(lines, start=1):
        cell = sheet.cell(row=index, column=1, value=text)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        if bold:
            cell.font = title_font
