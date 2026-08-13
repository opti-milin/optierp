"""Module 02 masters service: accounts CRUD, customers/suppliers (stubs),
fiscal years, tax categories, tax templates."""

import re
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.exceptions import DuplicateError, NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.accounts import (
    ROOT_TYPE_REPORT,
    CM_CLASSES,
    Account,
    FiscalYear,
    TaxCategory,
    TaxTemplate,
    TaxTemplateDetail,
)
from app.models.buying import Supplier
from app.models.core import Company
from app.models.selling import Customer
from app.schemas.accounts import (
    AccountCreate,
    AccountUpdate,
    CustomerCreate,
    SupplierCreate,
    TaxCategoryCreate,
    TaxTemplateCreate,
    TaxTemplateUpdate,
)
from app.services.audit import log_audit
from app.services.pagination import paginate


def _slugify(label: str) -> str:
    slug = re.sub(r"[^a-z0-9_]+", "_", label.lower()).strip("_")
    return slug or "node"


def _validate_cm_class(cm_class: str | None, report_type: str, is_group: bool) -> str | None:
    """cm_class is only meaningful on P&L leaf accounts."""
    if cm_class is None or cm_class == "":
        return None
    if cm_class not in CM_CLASSES:
        raise ValidationError(
            f"cm_class must be one of {', '.join(CM_CLASSES)}",
            field="cm_class",
        )
    if report_type != "Profit and Loss":
        raise ValidationError(
            "cm_class is only allowed on Profit and Loss accounts",
            field="cm_class",
        )
    if is_group:
        raise ValidationError("cm_class is only allowed on leaf accounts", field="cm_class")
    return cm_class


# --- Account CRUD (tree-aware) ------------------------------------------------------


async def create_account(db: AsyncSession, payload: AccountCreate, user: CurrentUser) -> Account:
    parent = await db.get(Account, payload.parent_account_id)
    if parent is None or parent.company_id != user.company_id:
        raise NotFoundError("Parent account not found")
    if not parent.is_group:
        raise ValidationError("Parent account must be a group account", field="parent_account_id")

    duplicate = await db.scalar(
        select(Account).where(
            Account.company_id == user.company_id,
            Account.account_name == payload.account_name,
            Account.parent_account_id == parent.id,
        )
    )
    if duplicate:
        raise DuplicateError("An account with this name already exists here", field="account_name")

    cm_class = _validate_cm_class(payload.cm_class, ROOT_TYPE_REPORT[parent.root_type], payload.is_group)

    account = Account(
        id=uuid.uuid4(),
        company_id=parent.company_id,
        account_name=payload.account_name,
        account_number=payload.account_number,
        parent_account_id=parent.id,
        root_type=parent.root_type,
        report_type=ROOT_TYPE_REPORT[parent.root_type],
        account_type=payload.account_type,
        cm_class=cm_class,
        is_group=payload.is_group,
        account_currency=payload.account_currency or parent.account_currency,
        path=f"{parent.path}.{_slugify(payload.account_name)}",
        owner=user.id,
    )
    db.add(account)
    await db.flush()
    await log_audit(
        db, doctype="Account", document_id=account.id, action="INSERT",
        user_id=user.id, company_id=account.company_id,
    )
    await db.commit()
    await db.refresh(account)
    return account


async def _rename_account_path(db: AsyncSession, account: Account, new_name: str) -> None:
    """Recompute this account's ltree ``path`` from its parent + new name slug,
    and rewrite the path prefix on every descendant. Account trees are small, so
    the rewrite is done in Python within the open transaction."""
    old_path = str(account.path)
    new_slug = _slugify(new_name)
    if account.parent_account_id is not None:
        parent = await db.get(Account, account.parent_account_id)
        new_path = f"{parent.path}.{new_slug}"
    else:
        new_path = new_slug
    if new_path == old_path:
        return
    rows = (
        await db.execute(select(Account).where(Account.company_id == account.company_id))
    ).scalars().all()
    for r in rows:
        rp = str(r.path)
        if rp == old_path:
            r.path = new_path
        elif rp.startswith(old_path + "."):
            r.path = new_path + rp[len(old_path):]


async def update_account(
    db: AsyncSession, account_id: uuid.UUID, payload: AccountUpdate, user: CurrentUser
) -> Account:
    """Partial update: rename (cascades the ltree path), account number/type/
    currency, and freeze/disable. Structural fields (root_type, parent, is_group)
    are intentionally not editable here."""
    account = await db.get(Account, account_id)
    if account is None or account.company_id != user.company_id:
        raise NotFoundError("Account not found")

    if payload.account_name is not None and payload.account_name != account.account_name:
        duplicate = await db.scalar(
            select(Account).where(
                Account.company_id == user.company_id,
                Account.account_name == payload.account_name,
                Account.parent_account_id == account.parent_account_id,
                Account.id != account.id,
            )
        )
        if duplicate:
            raise DuplicateError("An account with this name already exists here", field="account_name")
        await _rename_account_path(db, account, payload.account_name)
        account.account_name = payload.account_name

    if payload.account_number is not None:
        account.account_number = payload.account_number or None
    if payload.account_type is not None:
        account.account_type = payload.account_type or None
    if "cm_class" in payload.model_fields_set:
        # Explicit null clears classification; omitted leaves unchanged.
        account.cm_class = _validate_cm_class(
            payload.cm_class, account.report_type, account.is_group
        )
    if payload.account_currency is not None:
        account.account_currency = payload.account_currency or None
    if payload.freeze_account is not None:
        account.freeze_account = payload.freeze_account
    if payload.disabled is not None:
        account.disabled = payload.disabled

    await db.flush()
    await log_audit(
        db, doctype="Account", document_id=account.id, action="UPDATE",
        user_id=user.id, company_id=account.company_id,
    )
    await db.commit()
    await db.refresh(account)
    return account


# --- Customers / Suppliers (stub masters for invoicing) ------------------------------


async def create_customer(db: AsyncSession, payload: CustomerCreate, user: CurrentUser) -> Customer:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    if await db.scalar(
        select(Customer).where(
            Customer.company_id == user.company_id, Customer.customer_name == payload.customer_name
        )
    ):
        raise DuplicateError("Customer already exists", field="customer_name")
    customer = Customer(company_id=user.company_id, owner=user.id, **payload.model_dump())
    db.add(customer)
    await db.commit()
    await db.refresh(customer)
    return customer


async def list_customers(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 50
) -> tuple[list[Customer], int]:
    stmt = (
        select(Customer)
        .where(Customer.company_id == company_id, Customer.disabled.is_(False))
        .order_by(Customer.customer_name)
    )
    return await paginate(db, stmt, page, page_size)


async def create_supplier(db: AsyncSession, payload: SupplierCreate, user: CurrentUser) -> Supplier:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    if await db.scalar(
        select(Supplier).where(
            Supplier.company_id == user.company_id, Supplier.supplier_name == payload.supplier_name
        )
    ):
        raise DuplicateError("Supplier already exists", field="supplier_name")
    supplier = Supplier(company_id=user.company_id, owner=user.id, **payload.model_dump())
    db.add(supplier)
    await db.commit()
    await db.refresh(supplier)
    return supplier


async def list_suppliers(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 50
) -> tuple[list[Supplier], int]:
    stmt = (
        select(Supplier)
        .where(Supplier.company_id == company_id, Supplier.disabled.is_(False))
        .order_by(Supplier.supplier_name)
    )
    return await paginate(db, stmt, page, page_size)


# --- Fiscal years ------------------------------------------------------------------------


async def list_fiscal_years(db: AsyncSession, company_id: uuid.UUID | None) -> list[FiscalYear]:
    if company_id is None:
        raise ValidationError("An active company is required")
    stmt = (
        select(FiscalYear)
        .where(FiscalYear.company_id == company_id)
        .order_by(FiscalYear.year_start_date.desc())
    )
    return list((await db.execute(stmt)).scalars().all())


# --- Tax categories ------------------------------------------------------------------------


async def create_tax_category(
    db: AsyncSession, payload: TaxCategoryCreate, user: CurrentUser
) -> TaxCategory:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    if await db.scalar(
        select(TaxCategory).where(
            TaxCategory.company_id == user.company_id, TaxCategory.title == payload.title
        )
    ):
        raise DuplicateError("Tax category already exists", field="title")
    category = TaxCategory(company_id=user.company_id, owner=user.id, **payload.model_dump())
    db.add(category)
    await db.commit()
    await db.refresh(category)
    return category


async def list_tax_categories(
    db: AsyncSession, company_id: uuid.UUID | None
) -> list[TaxCategory]:
    stmt = (
        select(TaxCategory)
        .where(TaxCategory.company_id == company_id, TaxCategory.disabled.is_(False))
        .order_by(TaxCategory.title)
    )
    return list((await db.execute(stmt)).scalars().all())


def _is_gstin(value: str | None) -> bool:
    """A GSTIN is 15 chars starting with a 2-digit state code. Reject foreign/partial
    tax_ids so they fall back to the manual tax category instead of mis-deriving the
    place of supply from arbitrary leading characters."""
    s = (value or "").strip()
    return len(s) == 15 and s[:2].isdigit()


async def resolve_tax_template(
    db: AsyncSession,
    company_id: uuid.UUID,
    kind: str,
    tax_category_id: uuid.UUID | None,
    *,
    party_gstin: str | None = None,
) -> TaxTemplate | None:
    """Pick the tax template for an invoice (erpnext get_party_details).

    Resolution order: the GSTIN-derived place of supply (intra CGST+SGST vs inter
    IGST), then the party's manual tax category, then the company default (untagged),
    then no taxes. The GSTIN only OVERRIDES the manual category when the manual one
    *contradicts* the GSTIN's state — a consistent manual choice (e.g. a specific
    Reverse-Charge category on the same side) is honoured.
    """
    # A composition dealer issues a Bill of Supply — NO output GST on the sales side.
    # Suppress the sales tax template here so nothing downstream adds GST (purchases,
    # incl. reverse charge, are unaffected — composition dealers still self-assess RCM).
    if kind == "sales":
        from app.services.gst_settings import is_composition

        if await is_composition(db, company_id):
            return None

    # the party's manual tax category, if set and enabled
    manual = await db.get(TaxCategory, tax_category_id) if tax_category_id is not None else None
    if manual is not None and manual.disabled:
        manual = None

    derived_id: uuid.UUID | None = None
    if _is_gstin(party_gstin):
        company_gstin = await db.scalar(select(Company.tax_id).where(Company.id == company_id))
        if _is_gstin(company_gstin):
            inter = party_gstin.strip()[:2] != company_gstin.strip()[:2]
            # only derive (overriding the manual tag) when the manual one is absent
            # or points at the opposite place of supply
            if manual is None or manual.is_inter_state != inter:
                derived_id = await db.scalar(
                    select(TaxCategory.id)
                    .where(
                        TaxCategory.company_id == company_id,
                        TaxCategory.is_inter_state.is_(inter),
                        TaxCategory.disabled.is_(False),
                    )
                    .order_by(TaxCategory.title)  # deterministic if several share the flag
                    .limit(1)
                )

    candidates: list[uuid.UUID] = []
    if derived_id is not None:
        candidates.append(derived_id)
    if manual is not None and manual.id not in candidates:
        candidates.append(manual.id)

    base = (
        select(TaxTemplate)
        .options(selectinload(TaxTemplate.details))
        .where(
            TaxTemplate.company_id == company_id,
            TaxTemplate.kind == kind,
            TaxTemplate.disabled.is_(False),
        )
    )
    for category_id in candidates:
        template = await db.scalar(base.where(TaxTemplate.tax_category_id == category_id))
        if template is not None:
            return template
    return await db.scalar(
        base.where(TaxTemplate.is_default.is_(True), TaxTemplate.tax_category_id.is_(None))
    )


# --- Tax templates ---------------------------------------------------------------------


async def create_tax_template(
    db: AsyncSession, payload: TaxTemplateCreate, user: CurrentUser
) -> TaxTemplate:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    if await db.scalar(
        select(TaxTemplate).where(
            TaxTemplate.company_id == user.company_id,
            TaxTemplate.title == payload.title,
            TaxTemplate.kind == payload.kind,
        )
    ):
        raise DuplicateError("Tax template already exists", field="title")

    if payload.tax_category_id is not None:
        category = await db.get(TaxCategory, payload.tax_category_id)
        if category is None or category.company_id != user.company_id:
            raise NotFoundError("Tax category not found")

    template = TaxTemplate(
        id=uuid.uuid4(),
        company_id=user.company_id,
        title=payload.title,
        kind=payload.kind,
        is_default=payload.is_default,
        tax_category_id=payload.tax_category_id,
        owner=user.id,
    )
    db.add(template)
    await db.flush()
    for idx, row in enumerate(payload.details, start=1):
        db.add(
            TaxTemplateDetail(
                template_id=template.id,
                idx=idx,
                charge_type=row.charge_type,
                rate=row.rate,
                tax_amount=row.tax_amount,
                row_id=row.row_id,
                account_head_id=row.account_head_id,
                cost_center_id=row.cost_center_id,
                description=row.description,
                add_deduct_tax=row.add_deduct_tax,
                category=row.category,
                included_in_print_rate=row.included_in_print_rate,
            )
        )
    await db.commit()
    return await get_tax_template(db, template.id, user.company_id)


async def get_tax_template(
    db: AsyncSession, template_id: uuid.UUID, company_id: uuid.UUID | None
) -> TaxTemplate:
    template = await db.scalar(
        select(TaxTemplate)
        .options(selectinload(TaxTemplate.details))
        .where(TaxTemplate.id == template_id, TaxTemplate.company_id == company_id)
    )
    if template is None:
        raise NotFoundError("Tax template not found")
    return template


async def list_tax_templates(
    db: AsyncSession, company_id: uuid.UUID | None, kind: str | None = None
) -> list[TaxTemplate]:
    stmt = (
        select(TaxTemplate)
        .options(selectinload(TaxTemplate.details))
        .where(TaxTemplate.company_id == company_id, TaxTemplate.disabled.is_(False))
        .order_by(TaxTemplate.title)
    )
    if kind:
        stmt = stmt.where(TaxTemplate.kind == kind)
    return list((await db.execute(stmt)).scalars().all())


async def update_tax_template(
    db: AsyncSession, template_id: uuid.UUID, payload: TaxTemplateUpdate, user: CurrentUser
) -> TaxTemplate:
    """Replace a template's header + rows. ``kind`` is immutable. Existing
    documents copy their tax rows at creation, so editing a template never
    rewrites posted ledgers — it only affects future use."""
    template = await get_tax_template(db, template_id, user.company_id)
    if payload.title != template.title and await db.scalar(
        select(TaxTemplate).where(
            TaxTemplate.company_id == user.company_id,
            TaxTemplate.title == payload.title,
            TaxTemplate.kind == template.kind,
            TaxTemplate.id != template.id,
        )
    ):
        raise DuplicateError("Tax template already exists", field="title")

    if payload.tax_category_id is not None:
        category = await db.get(TaxCategory, payload.tax_category_id)
        if category is None or category.company_id != user.company_id:
            raise NotFoundError("Tax category not found")

    template.title = payload.title
    template.is_default = payload.is_default
    template.tax_category_id = payload.tax_category_id

    for existing in list(template.details):
        await db.delete(existing)
    await db.flush()
    for idx, row in enumerate(payload.details, start=1):
        db.add(
            TaxTemplateDetail(
                template_id=template.id,
                idx=idx,
                charge_type=row.charge_type,
                rate=row.rate,
                tax_amount=row.tax_amount,
                row_id=row.row_id,
                account_head_id=row.account_head_id,
                cost_center_id=row.cost_center_id,
                description=row.description,
                add_deduct_tax=row.add_deduct_tax,
                category=row.category,
                included_in_print_rate=row.included_in_print_rate,
            )
        )
    await db.commit()
    return await get_tax_template(db, template.id, user.company_id)


async def delete_tax_template(
    db: AsyncSession, template_id: uuid.UUID, user: CurrentUser
) -> None:
    """Hard-delete a template + its rows. Safe for posted documents — they hold
    their own copied tax rows (no stored FK back to the template)."""
    template = await get_tax_template(db, template_id, user.company_id)
    for existing in list(template.details):
        await db.delete(existing)
    await db.delete(template)
    await db.commit()
