"""Stock master endpoints — Module 03: item groups, warehouses, items,
price lists, item prices, rate resolution."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.permissions import require_permission
from app.core.security import CurrentUser, get_tenant_db
from app.schemas.common import ListResponse
from app.schemas.stock import (
    ItemAlternativeOption,
    ItemCreate,
    ItemGroupCreate,
    ItemGroupResponse,
    ItemListItem,
    ItemPriceCreate,
    ItemPriceResponse,
    ItemRateResponse,
    ItemResponse,
    ItemUpdate,
    PriceListCreate,
    PriceListResponse,
    WarehouseCreate,
    WarehouseResponse,
    WarehouseUpdate,
)
from app.services import stock_masters as service
from app.services.stock_common import get_item as get_item_master

router = APIRouter(tags=["stock: masters"])


@router.post("/item-groups", response_model=ItemGroupResponse, status_code=201,
             summary="Create an Item Group",
             description="Tree node; set is_group=true for parent nodes.")
async def create_item_group(
    payload: ItemGroupCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Item Group", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ItemGroupResponse:
    return ItemGroupResponse.model_validate(await service.create_item_group(db, payload, current_user))


@router.get("/item-groups", response_model=list[ItemGroupResponse],
            summary="List Item Groups", description="Full tree, alphabetical.")
async def list_item_groups(
    current_user: Annotated[CurrentUser, Depends(require_permission("Item Group", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ItemGroupResponse]:
    return [
        ItemGroupResponse.model_validate(g)
        for g in await service.list_item_groups(db, current_user.company_id)
    ]


@router.post("/warehouses", response_model=WarehouseResponse, status_code=201,
             summary="Create a Warehouse",
             description="Tree node; account_id overrides the company inventory account.")
async def create_warehouse(
    payload: WarehouseCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Warehouse", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WarehouseResponse:
    return WarehouseResponse.model_validate(await service.create_warehouse(db, payload, current_user))


@router.get("/warehouses", response_model=list[WarehouseResponse],
            summary="List Warehouses", description="Full tree, alphabetical.")
async def list_warehouses(
    current_user: Annotated[CurrentUser, Depends(require_permission("Warehouse", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[WarehouseResponse]:
    return [
        WarehouseResponse.model_validate(w)
        for w in await service.list_warehouses(db, current_user.company_id)
    ]


@router.get("/warehouses/{warehouse_id}", response_model=WarehouseResponse,
            summary="Get a Warehouse", description="Full warehouse record.")
async def get_warehouse(
    warehouse_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Warehouse", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WarehouseResponse:
    return WarehouseResponse.model_validate(
        await service.get_warehouse_by_id(db, warehouse_id, current_user.company_id)
    )


@router.patch("/warehouses/{warehouse_id}", response_model=WarehouseResponse,
              summary="Update a Warehouse", description="Partial update (name, type, account, parent, disabled).")
async def update_warehouse(
    warehouse_id: uuid.UUID,
    payload: WarehouseUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Warehouse", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> WarehouseResponse:
    return WarehouseResponse.model_validate(
        await service.update_warehouse(db, warehouse_id, payload, current_user)
    )


@router.post("/items", response_model=ItemResponse, status_code=201,
             summary="Create an Item",
             description="Example: `{'item_code': 'WIDGET-01', 'stock_uom': 'Nos', "
                         "'standard_rate': 250}`")
async def create_item(
    payload: ItemCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Item", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ItemResponse:
    return ItemResponse.model_validate(await service.create_item(db, payload, current_user))


@router.get("/items", response_model=ListResponse[ItemListItem],
            summary="List Items", description="Paginated; search matches code and name.")
async def list_items(
    current_user: Annotated[CurrentUser, Depends(require_permission("Item", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200)] = 20,
    search: str | None = None,
    include_disabled: bool = False,
) -> ListResponse[ItemListItem]:
    items, total = await service.list_items(
        db, current_user.company_id, page, page_size, search, include_disabled
    )
    return ListResponse(
        items=[ItemListItem.model_validate(i) for i in items],
        total=total, page=page, page_size=page_size,
    )


@router.get("/items/{item_id}", response_model=ItemResponse,
            summary="Get an Item", description="Full item master record.")
async def get_item(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Item", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ItemResponse:
    from app.services.stock_common import get_item as _get_item

    if current_user.company_id is None:
        from app.core.exceptions import ValidationError

        raise ValidationError("An active company is required")
    return ItemResponse.model_validate(
        await _get_item(db, item_id, current_user.company_id, allow_disabled=True)
    )


@router.patch("/items/{item_id}", response_model=ItemResponse,
              summary="Update an Item", description="Partial update of master fields.")
async def update_item(
    item_id: uuid.UUID,
    payload: ItemUpdate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Item", "write"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ItemResponse:
    return ItemResponse.model_validate(await service.update_item(db, item_id, payload, current_user))


@router.get("/items/{item_id}/rate", response_model=ItemRateResponse,
            summary="Resolve the default rate for an item",
            description="Item Price (buying/selling list) first, then the item master "
                        "(standard rate for selling; last purchase rate for buying).")
async def get_item_rate(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Item", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    buying: bool = False,
    price_list_id: uuid.UUID | None = None,
    on_date: date | None = None,
) -> ItemRateResponse:
    item, rate, source = await service.get_item_rate(
        db, current_user.company_id, item_id,
        buying=buying, price_list_id=price_list_id, on_date=on_date,
    )
    return ItemRateResponse(
        item_id=item.id, rate=rate, source=source, uom=item.stock_uom,
        item_name=item.item_name, description=item.description,
    )


@router.get(
    "/items/{item_id}/alternatives",
    response_model=list[ItemAlternativeOption],
    summary="List Item Alternatives for an item",
    description="Substitutes allowed on Work Order Finish when the BOM line has "
                "Allow Alternative Item. Manage pairs under /m/item-alternative.",
)
async def list_item_alternatives(
    item_id: uuid.UUID,
    current_user: Annotated[CurrentUser, Depends(require_permission("Item", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[ItemAlternativeOption]:
    await get_item_master(db, item_id, current_user.company_id)
    from app.services import item_alternative as alt_svc

    alts = await alt_svc.list_alternatives_for_item(db, current_user.company_id, item_id)
    return [
        ItemAlternativeOption(
            id=a.id, item_code=a.item_code, item_name=a.item_name, stock_uom=a.stock_uom
        )
        for a in alts
    ]


@router.post("/price-lists", response_model=PriceListResponse, status_code=201,
             summary="Create a Price List", description="Mark as buying and/or selling.")
async def create_price_list(
    payload: PriceListCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Price List", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> PriceListResponse:
    return PriceListResponse.model_validate(await service.create_price_list(db, payload, current_user))


@router.get("/price-lists", response_model=list[PriceListResponse],
            summary="List Price Lists", description="All price lists for the company.")
async def list_price_lists(
    current_user: Annotated[CurrentUser, Depends(require_permission("Price List", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> list[PriceListResponse]:
    return [
        PriceListResponse.model_validate(p)
        for p in await service.list_price_lists(db, current_user.company_id)
    ]


@router.post("/item-prices", response_model=ItemPriceResponse, status_code=201,
             summary="Create an Item Price",
             description="Rate for an item on a price list, with optional validity window.")
async def create_item_price(
    payload: ItemPriceCreate,
    current_user: Annotated[CurrentUser, Depends(require_permission("Item Price", "create"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> ItemPriceResponse:
    return ItemPriceResponse.model_validate(await service.create_item_price(db, payload, current_user))


@router.get("/item-prices", response_model=list[ItemPriceResponse],
            summary="List Item Prices", description="Optionally filtered by item.")
async def list_item_prices(
    current_user: Annotated[CurrentUser, Depends(require_permission("Item Price", "read"))],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
    item_id: uuid.UUID | None = None,
) -> list[ItemPriceResponse]:
    return [
        ItemPriceResponse.model_validate(p)
        for p in await service.list_item_prices(db, current_user.company_id, item_id)
    ]
