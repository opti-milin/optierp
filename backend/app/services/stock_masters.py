"""Module 03 masters: Item Group, Warehouse, Item, Price List, Item Price."""

import uuid
from datetime import date

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.stock import Bin, Item, ItemGroup, ItemPrice, PriceList, SerialNo, Warehouse
from app.schemas.stock import (
    ItemCreate,
    ItemGroupCreate,
    ItemPriceCreate,
    ItemUpdate,
    PriceListCreate,
    WarehouseCreate,
    WarehouseUpdate,
)
from app.services.accounts_common import get_company
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.stock_common import get_item, resolve_item_rate


# --- item groups -----------------------------------------------------------------


async def create_item_group(db: AsyncSession, payload: ItemGroupCreate, user: CurrentUser) -> ItemGroup:
    company = await get_company(db, user.company_id)
    if payload.parent_item_group_id is not None:
        parent = await db.get(ItemGroup, payload.parent_item_group_id)
        if parent is None or parent.company_id != company.id:
            raise NotFoundError("Parent item group not found")
        if not parent.is_group:
            raise ValidationError("Parent item group must be a group node", field="parent_item_group_id")
    group = ItemGroup(
        company_id=company.id,
        item_group_name=payload.item_group_name,
        parent_item_group_id=payload.parent_item_group_id,
        is_group=payload.is_group,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(group)
    await db.flush()
    await db.commit()
    return group


async def list_item_groups(db: AsyncSession, company_id: uuid.UUID | None) -> list[ItemGroup]:
    stmt = (
        select(ItemGroup)
        .where(ItemGroup.company_id == company_id)
        .order_by(ItemGroup.item_group_name)
    )
    return list((await db.execute(stmt)).scalars().all())


# --- warehouses -------------------------------------------------------------------


async def create_warehouse(db: AsyncSession, payload: WarehouseCreate, user: CurrentUser) -> Warehouse:
    company = await get_company(db, user.company_id)
    if payload.parent_warehouse_id is not None:
        parent = await db.get(Warehouse, payload.parent_warehouse_id)
        if parent is None or parent.company_id != company.id:
            raise NotFoundError("Parent warehouse not found")
        if not parent.is_group:
            raise ValidationError("Parent warehouse must be a group node", field="parent_warehouse_id")
    warehouse = Warehouse(
        company_id=company.id,
        warehouse_name=payload.warehouse_name,
        parent_warehouse_id=payload.parent_warehouse_id,
        is_group=payload.is_group,
        warehouse_type=payload.warehouse_type,
        account_id=payload.account_id,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(warehouse)
    await db.flush()
    await db.commit()
    return warehouse


async def list_warehouses(db: AsyncSession, company_id: uuid.UUID | None) -> list[Warehouse]:
    stmt = (
        select(Warehouse)
        .where(Warehouse.company_id == company_id)
        .order_by(Warehouse.warehouse_name)
    )
    return list((await db.execute(stmt)).scalars().all())


async def get_warehouse_by_id(
    db: AsyncSession, warehouse_id: uuid.UUID, company_id: uuid.UUID | None
) -> Warehouse:
    warehouse = await db.get(Warehouse, warehouse_id)
    if warehouse is None or warehouse.company_id != company_id:
        raise NotFoundError("Warehouse not found")
    return warehouse


async def update_warehouse(
    db: AsyncSession, warehouse_id: uuid.UUID, payload: WarehouseUpdate, user: CurrentUser
) -> Warehouse:
    warehouse = await get_warehouse_by_id(db, warehouse_id, user.company_id)
    data = payload.model_dump(exclude_unset=True)
    if data.get("parent_warehouse_id"):
        parent = await db.get(Warehouse, data["parent_warehouse_id"])
        if parent is None or parent.company_id != warehouse.company_id:
            raise NotFoundError("Parent warehouse not found")
        if not parent.is_group:
            raise ValidationError("Parent warehouse must be a group node", field="parent_warehouse_id")
        if parent.id == warehouse.id:
            raise ValidationError("A warehouse cannot be its own parent", field="parent_warehouse_id")
    for field_name, value in data.items():
        setattr(warehouse, field_name, value)
    warehouse.modified_by = user.id
    await db.flush()
    await db.commit()
    return await get_warehouse_by_id(db, warehouse.id, user.company_id)


# --- items -------------------------------------------------------------------------


async def create_item(db: AsyncSession, payload: ItemCreate, user: CurrentUser) -> Item:
    company = await get_company(db, user.company_id)
    if payload.valuation_method == "FIFO":
        # MANUAL_REVIEW: FIFO queue valuation is deferred; Moving Average only.
        raise ValidationError(
            "FIFO valuation is not available yet — use Moving Average", field="valuation_method"
        )
    existing = await db.scalar(
        select(Item).where(Item.company_id == company.id, Item.item_code == payload.item_code)
    )
    if existing is not None:
        raise ValidationError(f"Item code '{payload.item_code}' already exists", field="item_code")
    item = Item(
        company_id=company.id,
        item_code=payload.item_code,
        item_name=payload.item_name or payload.item_code,
        description=payload.description,
        item_group_id=payload.item_group_id,
        stock_uom=payload.stock_uom,
        purchase_uom=payload.purchase_uom,
        purchase_uom_factor=payload.purchase_uom_factor,
        sales_uom=payload.sales_uom,
        sales_uom_factor=payload.sales_uom_factor,
        has_serial_no=payload.has_serial_no,
        has_batch_no=payload.has_batch_no,
        is_stock_item=payload.is_stock_item,
        is_sales_item=payload.is_sales_item,
        is_purchase_item=payload.is_purchase_item,
        valuation_method=payload.valuation_method,
        standard_rate=payload.standard_rate,
        valuation_rate=payload.valuation_rate,
        income_account_id=payload.income_account_id,
        expense_account_id=payload.expense_account_id,
        item_tax_template_id=payload.item_tax_template_id,
        default_warehouse_id=payload.default_warehouse_id,
        reorder_level=payload.reorder_level,
        reorder_qty=payload.reorder_qty,
        lead_time_days=payload.lead_time_days,
        brand=payload.brand,
        barcode=payload.barcode,
        is_fixed_asset=payload.is_fixed_asset,
        asset_category_id=payload.asset_category_id,
        hsn_sac_code=payload.hsn_sac_code,
        gst_treatment=payload.gst_treatment,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(item)
    await db.flush()
    await log_audit(
        db, doctype="Item", document_id=item.id, action="INSERT",
        user_id=user.id, company_id=company.id,
    )
    await db.commit()
    # re-select (populate_existing) so the joined item_group load runs —
    # returning the identity-mapped instance would lazy-load on serialisation
    return await get_item(db, item.id, company.id, allow_disabled=True)


async def update_item(
    db: AsyncSession, item_id: uuid.UUID, payload: ItemUpdate, user: CurrentUser
) -> Item:
    if user.company_id is None:
        raise ValidationError("An active company is required")
    item = await get_item(db, item_id, user.company_id, allow_disabled=True)
    data = payload.model_dump(exclude_unset=True)
    # Tracking flags can't be toggled once the item carries stock: turning serial
    # tracking on would leave existing In-Stock units with no serials (the count
    # rule then makes them un-deliverable), and either flag flips the validation
    # every subsequent line must satisfy. Block the toggle while stock is on hand.
    toggling_serial = "has_serial_no" in data and data["has_serial_no"] != item.has_serial_no
    toggling_batch = "has_batch_no" in data and data["has_batch_no"] != item.has_batch_no
    if toggling_serial or toggling_batch:
        # count bins with a non-zero balance, NOT a net SUM — a SUM nets +10 in WH-A
        # against -10 in WH-B (reachable when negative stock is allowed) to zero and
        # would wrongly let the toggle through while real stock is on hand
        on_hand_bins = await db.scalar(
            select(func.count()).select_from(Bin).where(
                Bin.company_id == item.company_id, Bin.item_id == item.id, Bin.actual_qty != 0
            )
        )
        serial_count = await db.scalar(
            select(func.count()).select_from(SerialNo).where(
                SerialNo.company_id == item.company_id, SerialNo.item_id == item.id
            )
        )
        if (on_hand_bins or 0) > 0 or (serial_count or 0) > 0:
            flag = "serial-number" if toggling_serial else "batch"
            raise ValidationError(
                f"Cannot change {flag} tracking while the item has stock on hand "
                "or existing serial numbers. Zero out the stock first.",
                field="has_serial_no" if toggling_serial else "has_batch_no",
            )
    for field_name, value in data.items():
        setattr(item, field_name, value)
    item.modified_by = user.id
    await db.flush()
    await log_audit(
        db, doctype="Item", document_id=item.id, action="UPDATE",
        user_id=user.id, company_id=item.company_id,
    )
    await db.commit()
    # re-select so the (possibly changed) item_group eager load is fresh
    return await get_item(db, item.id, user.company_id, allow_disabled=True)


async def list_items(
    db: AsyncSession, company_id: uuid.UUID | None, page: int = 1, page_size: int = 20,
    search: str | None = None, include_disabled: bool = False,
) -> tuple[list[Item], int]:
    stmt = select(Item).where(Item.company_id == company_id).order_by(Item.item_code)
    if not include_disabled:
        stmt = stmt.where(Item.disabled.is_(False))
    if search:
        like = f"%{search}%"
        stmt = stmt.where(Item.item_code.ilike(like) | Item.item_name.ilike(like))
    return await paginate(db, stmt, page, page_size)


# --- price lists ----------------------------------------------------------------------


async def create_price_list(db: AsyncSession, payload: PriceListCreate, user: CurrentUser) -> PriceList:
    company = await get_company(db, user.company_id)
    price_list = PriceList(
        company_id=company.id,
        price_list_name=payload.price_list_name,
        currency=(payload.currency or company.default_currency).upper(),
        buying=payload.buying,
        selling=payload.selling,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(price_list)
    await db.flush()
    await db.commit()
    return price_list


async def list_price_lists(db: AsyncSession, company_id: uuid.UUID | None) -> list[PriceList]:
    stmt = (
        select(PriceList)
        .where(PriceList.company_id == company_id)
        .order_by(PriceList.price_list_name)
    )
    return list((await db.execute(stmt)).scalars().all())


async def create_item_price(db: AsyncSession, payload: ItemPriceCreate, user: CurrentUser) -> ItemPrice:
    company = await get_company(db, user.company_id)
    item = await get_item(db, payload.item_id, company.id)
    price_list = await db.get(PriceList, payload.price_list_id)
    if price_list is None or price_list.company_id != company.id:
        raise NotFoundError("Price list not found")
    duplicate = await db.scalar(
        select(ItemPrice).where(
            ItemPrice.item_id == item.id,
            ItemPrice.price_list_id == price_list.id,
            ItemPrice.valid_from.is_(None)
            if payload.valid_from is None
            else ItemPrice.valid_from == payload.valid_from,
        )
    )
    if duplicate is not None:
        raise ValidationError(
            f"A price for '{item.item_code}' on '{price_list.price_list_name}' with the "
            f"same start date already exists",
            field="valid_from",
        )
    price = ItemPrice(
        company_id=company.id,
        item_id=item.id,
        price_list_id=price_list.id,
        price_list_rate=payload.price_list_rate,
        currency=price_list.currency,
        valid_from=payload.valid_from,
        valid_upto=payload.valid_upto,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(price)
    await db.flush()
    await db.commit()
    return price


async def list_item_prices(
    db: AsyncSession, company_id: uuid.UUID | None, item_id: uuid.UUID | None = None
) -> list[ItemPrice]:
    stmt = select(ItemPrice).where(ItemPrice.company_id == company_id).order_by(ItemPrice.creation)
    if item_id is not None:
        stmt = stmt.where(ItemPrice.item_id == item_id)
    return list((await db.execute(stmt)).scalars().all())


async def get_item_rate(
    db: AsyncSession, company_id: uuid.UUID | None, item_id: uuid.UUID,
    *, buying: bool, price_list_id: uuid.UUID | None = None, on_date: date | None = None,
):
    if company_id is None:
        raise ValidationError("An active company is required")
    item = await get_item(db, item_id, company_id)
    rate, source = await resolve_item_rate(
        db, item, buying=buying, price_list_id=price_list_id, on_date=on_date
    )
    return item, rate, source
