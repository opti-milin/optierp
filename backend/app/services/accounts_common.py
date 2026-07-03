"""Shared helpers for Module 02 document services."""

import uuid
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.models.accounts import PurchaseInvoice, SalesInvoice
from app.models.buying import Supplier
from app.models.core import Company
from app.models.selling import Customer

ZERO = Decimal("0")

# ERPNext v15 default naming series per voucher
NAMING_SERIES = {
    "Journal Entry": "ACC-JV-.YYYY.-",
    "Sales Invoice": "ACC-SINV-.YYYY.-",
    "Purchase Invoice": "ACC-PINV-.YYYY.-",
    "Payment Entry": "ACC-PAY-.YYYY.-",
    "Period Closing Voucher": "ACC-PCV-.YYYY.-",
    "Bank Transaction": "ACC-BTN-.YYYY.-",
}


async def get_company(db: AsyncSession, company_id: uuid.UUID | None) -> Company:
    if company_id is None:
        raise ValidationError("An active company is required")
    company = await db.get(Company, company_id)
    if company is None:
        raise NotFoundError("Company not found")
    return company


async def get_customer(db: AsyncSession, customer_id: uuid.UUID, company_id: uuid.UUID) -> Customer:
    customer = await db.get(Customer, customer_id)
    if customer is None or customer.company_id != company_id:
        raise NotFoundError("Customer not found")
    if customer.disabled:
        raise ValidationError("Customer is disabled", field="customer_id")
    return customer


async def get_supplier(db: AsyncSession, supplier_id: uuid.UUID, company_id: uuid.UUID) -> Supplier:
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None or supplier.company_id != company_id:
        raise NotFoundError("Supplier not found")
    if supplier.disabled:
        raise ValidationError("Supplier is disabled", field="supplier_id")
    return supplier


def get_receivable_account(company: Company, customer: Customer) -> uuid.UUID:
    account_id = customer.receivable_account_id or company.default_receivable_account_id
    if account_id is None:
        raise ValidationError(
            "No receivable account configured (customer or company default)", field="debit_to_id"
        )
    return account_id


def get_payable_account(company: Company, supplier: Supplier) -> uuid.UUID:
    account_id = supplier.payable_account_id or company.default_payable_account_id
    if account_id is None:
        raise ValidationError(
            "No payable account configured (supplier or company default)", field="credit_to_id"
        )
    return account_id


from sqlalchemy import func, select  # noqa: E402


async def item_tax_rates(db: AsyncSession, payload_items: list) -> dict[uuid.UUID, dict]:
    """Map each invoice line's item to its Item Tax Template rates
    ({item_id: {tax_account_head_id: rate}}), so the taxes engine can apply a
    per-item GST rate. Items without a template are absent (use the row rate)."""
    from app.models.accounts import ItemTaxTemplateDetail
    from app.models.stock import Item

    item_ids = {i.item_id for i in payload_items if getattr(i, "item_id", None) is not None}
    if not item_ids:
        return {}
    tmpl_by_item = dict(
        (
            await db.execute(
                select(Item.id, Item.item_tax_template_id).where(
                    Item.id.in_(item_ids), Item.item_tax_template_id.isnot(None)
                )
            )
        ).all()
    )
    if not tmpl_by_item:
        return {}
    rates_by_tmpl: dict[uuid.UUID, dict] = {}
    rows = (
        await db.execute(
            select(
                ItemTaxTemplateDetail.template_id,
                ItemTaxTemplateDetail.account_head_id,
                ItemTaxTemplateDetail.rate,
            ).where(ItemTaxTemplateDetail.template_id.in_(set(tmpl_by_item.values())))
        )
    ).all()
    for tid, account_head_id, rate in rows:
        rates_by_tmpl.setdefault(tid, {})[account_head_id] = Decimal(rate)
    return {iid: rates_by_tmpl.get(tid, {}) for iid, tid in tmpl_by_item.items()}


def _gstin_state(value: str | None) -> str | None:
    """Two-digit state code of a 15-char GSTIN, else None."""
    s = (value or "").strip()
    return s[:2] if len(s) == 15 and s[:2].isdigit() else None


async def _hsn_rates(db: AsyncSession, codes: set[str]) -> dict[str, Decimal]:
    """Map each HSN code to its GST rate from the reference master. A code with
    several rows (e.g. fresh 0% vs frozen 12%) picks the most common rate,
    breaking ties toward the higher rate."""
    from app.models.core import HsnCode

    if not codes:
        return {}
    rows = (
        await db.execute(
            select(HsnCode.hsn_code, HsnCode.gst_rate, func.count())
            .where(HsnCode.hsn_code.in_(codes))
            .group_by(HsnCode.hsn_code, HsnCode.gst_rate)
        )
    ).all()
    best: dict[str, tuple[int, Decimal]] = {}  # code -> (count, rate)
    for code, rate, count in rows:
        rate = Decimal(rate)
        current = best.get(code)
        if current is None or (count, rate) > current:
            best[code] = (count, rate)
    return {code: rate for code, (count, rate) in best.items()}


def _eff_hsn(item, m) -> str:
    """The effective HSN for a line: its own ``hsn_sac_code`` override (per-line HSN lookup)
    if present, else the item master's. Null-safe when there is no item master."""
    return (getattr(item, "hsn_sac_code", None) or (m.hsn_sac_code if m else None)) or ""


def _line_key(item, idx: int):
    """A per-line key for rate overrides. For a master line this is the ``item_id`` — byte
    identical to the historical item_id keying, so those lines behave exactly as before. A
    **free-text** line (``item_id`` is None) gets a synthetic ``("__line__", idx)`` key so
    several free-text lines never collide on the shared ``None`` key."""
    iid = getattr(item, "item_id", None)
    return iid if iid is not None else ("__line__", idx)


def _line_treatment(item, m):
    """``(effective HSN, gst_treatment)`` for a line, or ``None`` if it carries no GST context.
    A **free-text** line (no item master) is treated as *Taxable* iff it carries its own
    ``hsn_sac_code`` — so a hand-typed item with an HSN still gets GST — otherwise it is left
    untaxed (there is no Item row to read a Nil/Exempt/Non-GST treatment from)."""
    hsn = _eff_hsn(item, m)
    if m is not None:
        return hsn, m.gst_treatment
    return (hsn, "Taxable") if getattr(item, "hsn_sac_code", None) else None


async def auto_gst_from_items(
    db: AsyncSession, *, company: Company, party_gstin: str | None,
    place_of_supply: str | None, payload_items: list,
    item_rates: dict[uuid.UUID, dict] | None = None, is_sales: bool = True,
    force_inter: bool = False,
) -> tuple[list, dict]:
    """Derive GST tax rows + per-item rate overrides from the line items when no
    document-level tax template resolved — the "the item's HSN carries the rate,
    so GST just applies" path.

    Each line's total GST rate comes from its Item Tax Template if set, else the
    HSN master (by ``hsn_sac_code``); a non-Taxable ``gst_treatment`` is 0%. On
    the **sales** side the tax splits into CGST+SGST (intra-state) or IGST
    (inter-state) using the Output GST accounts; on the **purchase** side it is a
    single Input GST line at the full rate (the input tax credit head). Returns
    ``([], {})`` when GST can't be derived (missing GST accounts, or no taxable
    line), leaving the caller's zero-tax behaviour unchanged.

    Called ONLY when the document would otherwise carry no tax, so it never alters
    documents that already resolve a template.
    """
    from app.models.accounts import Account
    from app.models.stock import Item
    from app.schemas.accounts import TaxRowIn

    # Composition dealer → Bill of Supply, no HSN-derived output GST on the sales side.
    # (Mirrors resolve_tax_template so preview == create; purchase/RCM stays taxed.)
    if is_sales:
        from app.services.gst_settings import is_composition

        if await is_composition(db, company.id):
            return [], {}

    item_ids = {i.item_id for i in payload_items if getattr(i, "item_id", None) is not None}
    # a free-text line (no item_id) still gets HSN-derived GST if it carries its own HSN
    free_text_hsn = any(
        getattr(i, "item_id", None) is None and getattr(i, "hsn_sac_code", None) for i in payload_items
    )
    if not item_ids and not free_text_hsn:
        return [], {}

    # line item GST context (HSN + treatment + whether it has its own template), by item id
    meta: dict = {}
    if item_ids:
        meta = {
            r.id: r
            for r in (
                await db.execute(
                    select(
                        Item.id, Item.hsn_sac_code, Item.gst_treatment, Item.item_tax_template_id
                    ).where(Item.id.in_(item_ids))
                )
            ).all()
        }

    taxable_hsns = set()
    for item in payload_items:
        t = _line_treatment(item, meta.get(getattr(item, "item_id", None)))
        if t and t[1] == "Taxable":
            taxable_hsns.add(t[0])
    hsn_rate = await _hsn_rates(db, {h for h in taxable_hsns if h})

    # intra vs inter: party state vs company state (GSTIN first, then place of supply).
    # ``force_inter`` (SEZ/Export with payment) always books IGST — a zero-rated supply is
    # deemed inter-state u/s 7(5) IGST Act, even to an SEZ in the supplier's own state.
    company_state = _gstin_state(company.tax_id)
    party_state = _gstin_state(party_gstin)
    if party_state is None and place_of_supply and place_of_supply[:2].isdigit():
        party_state = place_of_supply[:2]
    inter = force_inter or bool(company_state and party_state and company_state != party_state)

    # Each non-template line's total GST rate: HSN rate if Taxable, else 0.
    # Every such line gets an EXPLICIT override (incl. 0), so a Nil/Exempt line
    # never falls back to a non-zero row rate. Template lines keep their own
    # item_tax_rate (computed by the caller) and are left untouched here.
    totals: dict = {}
    has_template = False
    for idx, item in enumerate(payload_items):
        m = meta.get(getattr(item, "item_id", None))
        t = _line_treatment(item, m)
        if t is None:
            continue
        if m is not None and m.item_tax_template_id is not None:
            has_template = True
            continue
        hsn, treatment = t
        rate = hsn_rate.get(hsn) if treatment == "Taxable" else None
        totals[_line_key(item, idx)] = rate if rate and rate > 0 else Decimal(0)

    if not any(v > 0 for v in totals.values()) and not has_template:
        return [], {}  # nothing taxable — leave the invoice tax-free

    # resolve the GST accounts for this company (Tax accounts named *GST*)
    async def _account(*needles: str) -> uuid.UUID | None:
        stmt = select(Account.id).where(
            Account.company_id == company.id, Account.account_type == "Tax"
        )
        for n in needles:
            stmt = stmt.where(Account.account_name.ilike(f"%{n}%"))
        return await db.scalar(stmt.order_by(Account.account_name).limit(1))

    if not is_sales:
        # Purchase: a single Input GST (input tax credit) line at the full rate.
        input_gst = await _account("Input GST")
        if input_gst is None:
            return [], {}
        heads = [("Input GST", input_gst, Decimal(1))]
    elif inter:
        igst = await _account("Output", "IGST") or await _account("IGST")
        if igst is None:
            return [], {}
        heads = [("IGST", igst, Decimal(1))]
    else:
        cgst = await _account("Output", "CGST") or await _account("CGST")
        sgst = await _account("Output", "SGST") or await _account("SGST")
        if cgst is None or sgst is None:
            return [], {}
        heads = [("CGST", cgst, Decimal("0.5")), ("SGST", sgst, Decimal("0.5"))]

    # split each HSN-derived line total across the heads (per-line overrides drive
    # the amounts; every taxable line has one). Keyed by _line_key (item_id for master
    # lines, a synthetic per-index key for free-text lines).
    item_rate_override = {
        key: {acc: total * share for _, acc, share in heads} for key, total in totals.items()
    }
    # Row rate per head = the common per-head rate when every taxable line agrees
    # (template lines contribute via ``item_rates``), else 0 for a mixed-slab bill.
    existing = item_rates or {}

    def _row_rate(acc: uuid.UUID) -> Decimal:
        vals = set()
        for idx, item in enumerate(payload_items):
            k = _line_key(item, idx)
            r = item_rate_override.get(k, {}).get(acc)
            if r is None:
                r = existing.get(k, {}).get(acc)
            if r and r > 0:
                vals.add(Decimal(r))
        return next(iter(vals)) if len(vals) == 1 else Decimal(0)

    tax_rows = [
        TaxRowIn(charge_type="On Net Total", rate=_row_rate(acc), account_head_id=acc, description=label)
        for label, acc, _ in heads
    ]
    return tax_rows, item_rate_override


async def reverse_charge_tax_rows(
    db: AsyncSession, *, company: Company, party_gstin: str | None,
    place_of_supply: str | None, payload_items: list,
    item_rates: dict[uuid.UUID, dict] | None = None,
) -> tuple[list, dict[uuid.UUID, dict]]:
    """Reverse-charge (RCM) purchase tax rows: the supplier charges no GST — the buyer
    self-assesses it. We book the GST as **both** an input-tax-credit (``Input GST``, an
    *Add* row → Dr ITC) **and** an output liability (``Output CGST/SGST`` intra or
    ``Output IGST`` inter, *Deduct* rows → Cr liability). The two sides net to zero on the
    payable (the supplier is paid the base only), while the GL carries Dr ITC / Cr liability
    and the returns pick up 3.1(d) + ITC 4(A)(3).

    Kept self-contained (mirrors ``auto_gst_from_items``' rate/account logic) so the normal
    auto-GST path is untouched. Returns ``([], {})`` when RCM GST can't be derived (no GST
    accounts / nothing taxable), leaving the caller's behaviour unchanged.
    """
    from app.models.accounts import Account
    from app.models.stock import Item
    from app.schemas.accounts import TaxRowIn

    item_ids = {i.item_id for i in payload_items if getattr(i, "item_id", None) is not None}
    free_text_hsn = any(
        getattr(i, "item_id", None) is None and getattr(i, "hsn_sac_code", None) for i in payload_items
    )
    if not item_ids and not free_text_hsn:
        return [], {}
    meta: dict = {}
    if item_ids:
        meta = {
            r.id: r
            for r in (
                await db.execute(
                    select(Item.id, Item.hsn_sac_code, Item.gst_treatment).where(Item.id.in_(item_ids))
                )
            ).all()
        }

    taxable_hsns = set()
    for item in payload_items:
        t = _line_treatment(item, meta.get(getattr(item, "item_id", None)))
        if t and t[1] == "Taxable":
            taxable_hsns.add(t[0])
    hsn_rate = await _hsn_rates(db, {h for h in taxable_hsns if h})

    company_state = _gstin_state(company.tax_id)
    party_state = _gstin_state(party_gstin)
    if party_state is None and place_of_supply and place_of_supply[:2].isdigit():
        party_state = place_of_supply[:2]
    inter = bool(company_state and party_state and company_state != party_state)

    totals: dict = {}
    for idx, item in enumerate(payload_items):
        m = meta.get(getattr(item, "item_id", None))
        t = _line_treatment(item, m)
        if t is None:
            continue
        hsn, treatment = t
        rate = hsn_rate.get(hsn) if treatment == "Taxable" else None
        totals[_line_key(item, idx)] = rate if rate and rate > 0 else Decimal(0)
    if not any(v > 0 for v in totals.values()):
        return [], {}

    async def _account(*needles: str) -> uuid.UUID | None:
        stmt = select(Account.id).where(Account.company_id == company.id, Account.account_type == "Tax")
        for n in needles:
            stmt = stmt.where(Account.account_name.ilike(f"%{n}%"))
        return await db.scalar(stmt.order_by(Account.account_name).limit(1))

    input_gst = await _account("Input GST")
    if input_gst is None:
        return [], {}
    if inter:
        igst = await _account("Output", "IGST") or await _account("IGST")
        if igst is None:
            return [], {}
        out_heads = [("Output IGST (RCM)", igst, Decimal(1))]
    else:
        cgst = await _account("Output", "CGST") or await _account("CGST")
        sgst = await _account("Output", "SGST") or await _account("SGST")
        if cgst is None or sgst is None:
            return [], {}
        out_heads = [("Output CGST (RCM)", cgst, Decimal("0.5")), ("Output SGST (RCM)", sgst, Decimal("0.5"))]

    # Per-item overrides: Input GST carries the full rate (Add); each output head its share
    # (Deduct). Keyed by _line_key so free-text lines don't collide on None.
    item_rate_override: dict = {}
    for key, total in totals.items():
        row = {input_gst: total}
        for _, acc, share in out_heads:
            row[acc] = total * share
        item_rate_override[key] = row

    existing = item_rates or {}

    def _row_rate(acc: uuid.UUID) -> Decimal:
        vals = set()
        for idx, item in enumerate(payload_items):
            k = _line_key(item, idx)
            r = item_rate_override.get(k, {}).get(acc)
            if r is None:
                r = existing.get(k, {}).get(acc)
            if r and r > 0:
                vals.add(Decimal(r))
        return next(iter(vals)) if len(vals) == 1 else Decimal(0)

    tax_rows = [
        TaxRowIn(charge_type="On Net Total", rate=_row_rate(input_gst), account_head_id=input_gst,
                 description="Input GST (RCM)", add_deduct_tax="Add", category="Total"),
    ]
    tax_rows += [
        TaxRowIn(charge_type="On Net Total", rate=_row_rate(acc), account_head_id=acc,
                 description=label, add_deduct_tax="Deduct", category="Total")
        for label, acc, _ in out_heads
    ]
    return tax_rows, item_rate_override


@dataclass
class AdvanceGst:
    """Inclusive GST computed on a bare advance amount, with the GL heads to post it."""

    control_id: uuid.UUID  # 'GST on Advances' control (Dr at receipt, Cr on adjustment)
    output_rows: list  # [(output-head account id, amount), ...] — Cr at receipt
    inter: bool
    cgst: Decimal
    sgst: Decimal
    igst: Decimal
    total: Decimal


async def advance_gst_from_amount(
    db: AsyncSession, *, company: Company, party_gstin: str | None,
    place_of_supply: str | None, advance_amount: Decimal, rate: Decimal, inclusive: bool = True,
) -> "AdvanceGst | None":
    """GST on a lump-sum advance (a bare advance has no HSN, so ``rate`` is explicit).

    Treated as **GST-inclusive** (the money received already contains the tax) — so
    ``tax = amount × rate / (100 + rate)`` — split CGST/SGST intra-state or IGST inter-state.
    Returns ``None`` (book nothing) when the rate is zero or the 'GST on Advances' control /
    Output GST accounts can't be resolved — the same fail-safe as the auto-GST engine."""
    from app.models.accounts import Account

    rate = Decimal(rate or 0)
    if rate <= 0 or advance_amount <= 0:
        return None

    company_state = _gstin_state(company.tax_id)
    party_state = _gstin_state(party_gstin)
    if party_state is None and place_of_supply and place_of_supply[:2].isdigit():
        party_state = place_of_supply[:2]
    inter = bool(company_state and party_state and company_state != party_state)

    async def _tax_account(*needles: str) -> uuid.UUID | None:
        stmt = select(Account.id).where(Account.company_id == company.id, Account.account_type == "Tax")
        for n in needles:
            stmt = stmt.where(Account.account_name.ilike(f"%{n}%"))
        return await db.scalar(stmt.order_by(Account.account_name).limit(1))

    # control = a leaf account named like 'GST on Advances' (holds the tax until the invoice clears it)
    control_id = await db.scalar(
        select(Account.id)
        .where(
            Account.company_id == company.id,
            Account.is_group.is_(False),
            Account.account_name.ilike("%advance%"),
            Account.account_name.ilike("%gst%"),
        )
        .order_by(Account.account_name)
        .limit(1)
    )
    if control_id is None:
        return None

    total = (advance_amount * rate / (100 + rate)) if inclusive else (advance_amount * rate / 100)
    total = total.quantize(Decimal("0.01"))
    if total <= 0:
        return None

    if inter:
        igst_id = await _tax_account("Output", "IGST") or await _tax_account("IGST")
        if igst_id is None:
            return None
        return AdvanceGst(control_id, [(igst_id, total)], True, ZERO, ZERO, total, total)
    cgst_id = await _tax_account("Output", "CGST") or await _tax_account("CGST")
    sgst_id = await _tax_account("Output", "SGST") or await _tax_account("SGST")
    if cgst_id is None or sgst_id is None:
        return None
    cgst = (total / 2).quantize(Decimal("0.01"))
    sgst = total - cgst
    return AdvanceGst(control_id, [(cgst_id, cgst), (sgst_id, sgst)], False, cgst, sgst, ZERO, total)


async def compute_doc_tax_preview(
    db: AsyncSession, *, company: Company, party, kind: str, items: list,
    place_of_supply: str | None = None, conversion_rate: Decimal = Decimal(1),
    apply_discount_on: str = "Grand Total",
    additional_discount_percentage: Decimal = Decimal(0), discount_amount: Decimal = Decimal(0),
    is_reverse_charge: bool = False, gst_category: str = "Regular", export_with_payment: bool = False,
):
    """Compute the GST + totals a transaction document (invoice / order / quotation)
    would apply, WITHOUT persisting — for the draft form's live preview. Mirrors the
    create path: resolve the party's tax template (by ``kind``) else derive GST from
    each line's HSN, apply per-item Item-Tax-Template overrides, run the engine.
    ``party`` is a Customer (sales) or Supplier (purchase); both carry ``tax_id`` +
    ``tax_category_id``.

    On a **reverse-charge purchase** (``is_reverse_charge`` on the purchase side) the GST is
    self-assessed, so — exactly like ``create_purchase_invoice`` — it is booked as Input GST
    (Add) + Output GST (Deduct) rows that net the payable to the base. The Deduct rows are
    returned with a **negative** amount so the draft's totals show the supplier is paid the
    base only (no GST added to the payable)."""
    from app.core.gst_states import gst_state_label_of
    from app.schemas.accounts import InvoiceTaxLinePreview, InvoiceTaxPreview, TaxRowIn
    from app.services.accounts_masters import resolve_tax_template
    from app.services.taxes_and_totals import ItemRow, TaxRow, calculate_taxes_and_totals

    is_sales = kind == "sales"
    # Zero-rated sales (SEZ/Export u/s 16 IGST Act): under LUT/bond → NO tax; with payment →
    # IGST (always inter-state). Mirrors create_sales_invoice so preview == create.
    zero_rated = is_sales and gst_category in ("SEZ", "Export")
    lut = zero_rated and not export_with_payment
    template = (
        None if zero_rated
        else await resolve_tax_template(db, company.id, kind, party.tax_category_id, party_gstin=party.tax_id)
    )
    tax_rows_in = (
        [
            TaxRowIn(charge_type=d.charge_type, rate=d.rate, tax_amount=d.tax_amount, row_id=d.row_id,
                     account_head_id=d.account_head_id, cost_center_id=d.cost_center_id,
                     description=d.description, included_in_print_rate=d.included_in_print_rate)
            for d in template.details
        ]
        if template is not None else []
    )
    item_rates = await item_tax_rates(db, items)
    # Reverse-charge purchase: self-assess GST (Input Add + Output Deduct), overriding any
    # resolved template — mirrors create_purchase_invoice so preview == create.
    if not is_sales and is_reverse_charge:
        rcm_rows, rcm_overrides = await reverse_charge_tax_rows(
            db, company=company, party_gstin=party.tax_id, place_of_supply=place_of_supply,
            payload_items=items, item_rates=item_rates,
        )
        if rcm_rows:
            tax_rows_in = rcm_rows
            for iid, heads in rcm_overrides.items():
                item_rates.setdefault(iid, {}).update(heads)
    if not tax_rows_in and not lut:
        auto_rows, auto_overrides = await auto_gst_from_items(
            db, company=company, party_gstin=party.tax_id, place_of_supply=place_of_supply,
            payload_items=items, item_rates=item_rates, is_sales=is_sales,
            force_inter=(zero_rated and export_with_payment),
        )
        if auto_rows:
            tax_rows_in = auto_rows
            for iid, heads in auto_overrides.items():
                item_rates.setdefault(iid, {}).update(heads)

    def _rate(i):
        plr = getattr(i, "price_list_rate", None)
        return plr if plr is not None else i.rate

    engine_items = [
        ItemRow(
            qty=i.qty, rate=_rate(i), price_list_rate=_rate(i),
            discount_percentage=getattr(i, "discount_percentage", 0) or 0,
            discount_amount=getattr(i, "discount_amount", 0) or 0,
            item_tax_rate=item_rates.get(_line_key(i, idx), {}),
        )
        for idx, i in enumerate(items)
    ]
    engine_taxes = [
        TaxRow(charge_type=t.charge_type, rate=t.rate, tax_amount=t.tax_amount, row_id=t.row_id,
               included_in_print_rate=t.included_in_print_rate, account_head_id=t.account_head_id,
               add_deduct_tax=getattr(t, "add_deduct_tax", "Add"),
               category=getattr(t, "category", "Total"))
        for t in tax_rows_in
    ]
    totals = calculate_taxes_and_totals(
        engine_items, engine_taxes, conversion_rate=conversion_rate,
        apply_discount_on=apply_discount_on,
        additional_discount_percentage=additional_discount_percentage,
        discount_amount=discount_amount, is_purchase=not is_sales,
    )
    return InvoiceTaxPreview(
        net_total=totals.net_total,
        total_taxes_and_charges=totals.total_taxes_and_charges,
        grand_total=totals.grand_total,
        place_of_supply=(
            place_of_supply or gst_state_label_of(party.tax_id) or gst_state_label_of(company.tax_id)
        ),
        taxes=[
            InvoiceTaxLinePreview(
                description=t.description or "", rate=et.rate,
                # a Deduct (RCM self-assessed liability) reduces the payable → show it negative
                tax_amount=(-et.tax_amount if getattr(t, "add_deduct_tax", "Add") == "Deduct"
                            else et.tax_amount),
            )
            for t, et in zip(tax_rows_in, engine_taxes)
        ],
    )


def base_payable_total(invoice: SalesInvoice | PurchaseInvoice) -> Decimal:
    """The base-currency amount the party owes (grand total plus rounding)."""
    return invoice.base_grand_total + (invoice.rounding_adjustment * invoice.conversion_rate)


def set_invoice_status(invoice: SalesInvoice | PurchaseInvoice, today: date | None = None) -> None:
    """Derive status from docstatus / outstanding / due date (ERPNext set_status)."""
    today = today or date.today()
    if invoice.docstatus == 0:
        invoice.status = "Draft"
    elif invoice.docstatus == 2:
        invoice.status = "Cancelled"
    elif invoice.is_return:
        invoice.status = "Return"
    elif invoice.outstanding_amount <= ZERO:
        invoice.status = "Paid"
    elif invoice.due_date and invoice.due_date < today:
        invoice.status = "Overdue"
    elif invoice.outstanding_amount < base_payable_total(invoice):
        invoice.status = "Partly Paid"
    else:
        invoice.status = "Unpaid"


def require_draft(docstatus: int) -> None:
    if docstatus != 0:
        raise ValidationError("Only draft documents can be modified", code="ERR_DOCSTATUS")


def require_submitted(docstatus: int) -> None:
    if docstatus != 1:
        raise ValidationError("Document is not submitted", code="ERR_DOCSTATUS")
