"""Module 05 — Selling Pydantic schemas (shares order bases with buying)."""

import uuid
from datetime import date
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.accounts import InvoiceTaxResponse
from app.schemas.buying import OrderCreateBase, OrderItemIn, OrderItemResponse, OrderResponseBase

OrderType = Literal["Sales", "Maintenance", "Shopping Cart"]

# --- quotation -------------------------------------------------------------------------


class QuotationCreate(OrderCreateBase):
    customer_id: uuid.UUID
    valid_till: date | None = None
    order_type: OrderType = "Sales"
    terms: str | None = None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    coupon_code: str | None = None
    shipping_rule_id: uuid.UUID | None = None
    # More Info (selling)
    campaign_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    territory_id: uuid.UUID | None = None
    customer_group_id: uuid.UUID | None = None
    sales_partner_id: uuid.UUID | None = None


class QuotationItemResponse(OrderItemResponse):
    pass


class QuotationResponse(OrderResponseBase):
    customer_id: uuid.UUID
    customer_name: str | None = None
    valid_till: date | None
    order_type: str
    terms: str | None = None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    territory_id: uuid.UUID | None = None
    customer_group_id: uuid.UUID | None = None
    sales_partner_id: uuid.UUID | None = None
    shipping_rule_id: uuid.UUID | None = None
    items: list[QuotationItemResponse]
    taxes: list[InvoiceTaxResponse]
    # transient: filled by submit / check-fulfillment (credit + CTP warnings)
    warnings: list[str] = Field(default_factory=list)


# --- sales order -----------------------------------------------------------------------


class SalesOrderCreate(OrderCreateBase):
    customer_id: uuid.UUID
    delivery_date: date | None = None
    order_type: OrderType = "Sales"
    po_no: str | None = None
    po_date: date | None = None
    terms: str | None = None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    set_warehouse_id: uuid.UUID | None = None
    quotation_id: uuid.UUID | None = None
    coupon_code: str | None = None
    shipping_rule_id: uuid.UUID | None = None
    # More Info (selling)
    campaign_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    territory_id: uuid.UUID | None = None
    customer_group_id: uuid.UUID | None = None
    sales_partner_id: uuid.UUID | None = None


class SalesOrderItemResponse(OrderItemResponse):
    delivery_date: date | None = None
    delivered_qty: Decimal
    billed_amt: Decimal
    quotation_item_id: uuid.UUID | None = None


class SalesOrderResponse(OrderResponseBase):
    customer_id: uuid.UUID
    customer_name: str | None = None
    delivery_date: date | None
    order_type: str
    po_no: str | None = None
    po_date: date | None = None
    terms: str | None = None
    customer_address_id: uuid.UUID | None = None
    shipping_address_id: uuid.UUID | None = None
    contact_person_id: uuid.UUID | None = None
    campaign_id: uuid.UUID | None = None
    source_id: uuid.UUID | None = None
    territory_id: uuid.UUID | None = None
    customer_group_id: uuid.UUID | None = None
    sales_partner_id: uuid.UUID | None = None
    shipping_rule_id: uuid.UUID | None = None
    set_warehouse_id: uuid.UUID | None
    quotation_id: uuid.UUID | None
    per_delivered: Decimal
    per_billed: Decimal
    items: list[SalesOrderItemResponse]
    taxes: list[InvoiceTaxResponse]
    # transient: filled by the submit endpoint (credit-limit check, Module 05 rule 3)
    warnings: list[str] = Field(default_factory=list)


# --- order fulfillment (CTP + cost) ----------------------------------------------------


class OrderFulfillmentShortfallOut(BaseModel):
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    shortfall_qty: Decimal
    estimated_buy_cost: Decimal


class OrderFulfillmentLineOut(BaseModel):
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    qty: Decimal
    delivery_date: date | None = None
    warehouse_id: uuid.UUID | None = None
    bom_id: uuid.UUID | None = None
    bom_name: str | None = None
    on_time: bool | None = None
    earliest_promise_date: date | None = None
    bom_cost_per_unit: Decimal
    estimated_cost: Decimal
    selling_amount: Decimal
    estimated_margin: Decimal
    estimated_margin_pct: Decimal | None = None
    shortfalls: list[OrderFulfillmentShortfallOut] = []
    notes: list[str] = []
    skipped: bool = False


class OrderFulfillmentOut(BaseModel):
    """Capable-to-promise + BOM cost estimate for a Quotation or Sales Order."""

    mode: str
    can_fulfill_on_time: bool | None = None
    earliest_promise_date: date | None = None
    estimated_cost: Decimal
    estimated_selling_amount: Decimal
    estimated_margin: Decimal
    estimated_margin_pct: Decimal | None = None
    lines: list[OrderFulfillmentLineOut] = []
    warnings: list[str] = []
    hard_block_reasons: list[str] = []
    planning_dashboard_path: str


class OrderFulfillmentPreviewIn(BaseModel):
    """Pre-save fulfillment check from draft line payload."""

    items: list[OrderItemIn]
    delivery_date: date | None = None  # SO header / QTN uses valid_till mapped by caller
    set_warehouse_id: uuid.UUID | None = None
    as_of: date | None = None


# --- Phase 9 delivery estimate ---------------------------------------------------------


class DeliveryStageOut(BaseModel):
    stage: str
    status: str
    source_type: str | None = None
    source_id: uuid.UUID | None = None
    source_name: str | None = None
    qty: Decimal | None = None
    planned_date: date | None = None
    actual_date: date | None = None
    notes: str | None = None


class DeliveryLineEstimateOut(BaseModel):
    sales_order_item_id: uuid.UUID
    item_id: uuid.UUID
    item_code: str | None = None
    item_name: str | None = None
    qty: Decimal
    pending_qty: Decimal
    promised_date: date | None = None
    earliest_promise_date: date | None = None
    suggested_delivery_date: date | None = None
    manufacturing_start_date: date | None = None
    materials_ready_by: date | None = None
    on_time: bool | None = None
    slack_days: int | None = None
    health: str
    stages: list[DeliveryStageOut] = []
    notes: list[str] = []


class SalesOrderDeliveryEstimateOut(BaseModel):
    """Phase 9: live SO delivery timeline (MR → PO → WO → DN) + suggested date."""

    sales_order_id: uuid.UUID
    sales_order_name: str | None = None
    as_of: date
    promised_date: date | None = None
    earliest_promise_date: date | None = None
    suggested_delivery_date: date | None = None
    on_time: bool | None = None
    slack_days: int | None = None
    health: str
    lines: list[DeliveryLineEstimateOut] = []
    notes: list[str] = []

