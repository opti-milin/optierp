"""India compliance schemas — GST Settings (per-company config the GST layer reads),
the HSN → GST-rate lookup result, and the GST returns (GSTR-1 / GSTR-3B)."""

import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, field_validator

REGISTRATION_TYPES = ("Regular", "Composition")
FILING_CADENCES = ("Monthly", "QRMP")


class HsnCodeMatch(BaseModel):
    """One match from the HSN "search by product name / code" lookup.

    ``gst_rate`` is the standard slab (0/5/12/18/28); the Item form maps it to the
    matching *GST {rate}%* Item Tax Template so the rate applies on invoices, and
    copies ``hsn_code`` + ``gst_treatment`` onto the item."""

    model_config = {"from_attributes": True}

    hsn_code: str
    description: str
    gst_rate: Decimal
    gst_treatment: str
    chapter: int | None = None
    schedule: str | None = None


class GstSettings(BaseModel):
    """Per-company GST configuration. Policy fields are stored; ``gstin`` and ``gst_state``
    are **derived from the Company** on read (single source of truth = ``Company.tax_id``)
    and ignored on save."""

    # --- stored policy ---
    registration_type: str = "Regular"  # Regular | Composition
    filing_cadence: str = "Monthly"  # Monthly | QRMP — only meaningful for Regular dealers
    # (Composition files CMP-08/GSTR-4; that flow lands in a later phase)
    e_invoice_applicable: bool = False  # generate e-invoice (IRN/QR) for B2B
    e_way_bill_applicable: bool = False  # generate e-way bills for goods movement
    is_sez: bool = False  # company is an SEZ unit
    # GSP/IRP/NIC integration (Phase 5). The provider NAME selects a pluggable adapter;
    # its per-tenant CREDENTIALS live in a secure store (env / secret manager), never in
    # this settings blob. Empty / "none" ⇒ JSON-only (the offline-tool export).
    gsp_provider: str | None = None

    # --- derived (read-only; from the Company's GSTIN) ---
    gstin: str | None = None
    gst_state: str | None = None  # the company's REGISTERED state, "27-Maharashtra"
    # (NOT the transaction place-of-supply, which is the recipient's state — a per-invoice field)

    @field_validator("registration_type")
    @classmethod
    def _valid_registration(cls, v: str) -> str:
        if v not in REGISTRATION_TYPES:
            raise ValueError(f"registration_type must be one of {REGISTRATION_TYPES}")
        return v

    @field_validator("filing_cadence")
    @classmethod
    def _valid_cadence(cls, v: str) -> str:
        if v not in FILING_CADENCES:
            raise ValueError(f"filing_cadence must be one of {FILING_CADENCES}")
        return v


# ---------------------------------------------------------------------------
# GST Returns — GSTR-1 (outward supplies) + GSTR-3B (summary) — Phase 2.
#
# Read-only monthly returns computed from submitted Sales/Purchase Invoices.
# All money is base currency (INR). Amounts net credit notes (returns carry
# negative amounts), matching what the GST portal expects.
# ---------------------------------------------------------------------------


class Gstr1Invoice(BaseModel):
    """One outward document in the B2B / B2C-Large / credit-note sections."""

    invoice_id: uuid.UUID
    name: str
    posting_date: date
    counterparty_gstin: str | None = None  # recipient GSTIN (registered docs)
    invoice_value: Decimal  # grand total (INR)
    place_of_supply: str  # portal "NN-State" label
    reverse_charge: bool
    rate: Decimal  # overall GST rate on the document
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal
    is_return: bool = False  # credit/debit note


class Gstr1B2B(BaseModel):
    """B2B block: one registered recipient (GSTIN) with its invoices."""

    gstin: str
    party_name: str | None = None
    invoices: list[Gstr1Invoice]
    taxable_value: Decimal
    total_tax: Decimal


class Gstr1B2CS(BaseModel):
    """B2C-Small: consolidated (POS × rate) supplies to unregistered persons —
    intra-state, plus low-value inter-state (≤ the B2CL threshold)."""

    place_of_supply: str
    supply_type: str  # INTRA | INTER
    rate: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal


class Gstr1Hsn(BaseModel):
    """HSN-wise summary row (legally required in GSTR-1)."""

    hsn_code: str | None = None
    description: str | None = None
    uqc: str | None = None  # unit quantity code (from line UOM)
    qty: Decimal
    rate: Decimal
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal


class Gstr1DocSummary(BaseModel):
    """Document issued summary (nature of document = tax invoice / credit note)."""

    nature: str  # "Invoices for outward supply" | "Credit Note"
    from_no: str
    to_no: str
    total_count: int
    cancelled: int
    net_issued: int


class Gstr1Totals(BaseModel):
    taxable_value: Decimal
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    cess: Decimal
    invoice_count: int


class Gstr1Report(BaseModel):
    """GSTR-1 outward-supply return for a filing period."""

    gstin: str | None = None
    filing_period: str  # "MMYYYY"
    from_date: date
    to_date: date
    b2b: list[Gstr1B2B]
    b2cl: list[Gstr1Invoice]  # inter-state B2C above the B2CL threshold (invoice-wise)
    b2cs: list[Gstr1B2CS]
    cdnr: list[Gstr1Invoice]  # credit/debit notes to registered recipients
    cdnur: list[Gstr1Invoice]  # credit/debit notes to unregistered recipients
    hsn: list[Gstr1Hsn]
    docs: list[Gstr1DocSummary]
    totals: Gstr1Totals


class Gstr3bTaxRow(BaseModel):
    """A section-3.1 outward row (or the net-payable line)."""

    label: str
    taxable_value: Decimal
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal


class Gstr3bInterStateRow(BaseModel):
    """Section 3.2 — inter-state outward supply to an unregistered person, by POS."""

    place_of_supply: str
    taxable_value: Decimal
    igst: Decimal


class Gstr3bItcRow(BaseModel):
    """A section-4 (eligible ITC) row."""

    label: str
    igst: Decimal
    cgst: Decimal
    sgst: Decimal
    cess: Decimal


class Gstr3bReport(BaseModel):
    """GSTR-3B summary return for a filing period."""

    gstin: str | None = None
    filing_period: str  # "MMYYYY"
    from_date: date
    to_date: date
    outward: list[Gstr3bTaxRow]  # section 3.1 (a..e)
    inter_state_unreg: list[Gstr3bInterStateRow]  # section 3.2
    itc: list[Gstr3bItcRow]  # section 4 (eligible ITC)
    net_tax_payable: Gstr3bTaxRow  # 3.1 output tax − eligible ITC (informational)
