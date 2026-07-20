"""Module 01 — Core / Setup Pydantic schemas (Create / Update / Response / ListItem)."""

import uuid
from datetime import date

from pydantic import BaseModel, EmailStr, Field, field_validator

from app.schemas.common import DocumentMeta, ORMModel

# --- Company -------------------------------------------------------------------


class CompanyCreate(BaseModel):
    company_name: str = Field(min_length=1, max_length=140)
    abbr: str = Field(min_length=1, max_length=10)
    default_currency: str = Field(min_length=3, max_length=3)
    country_code: str | None = Field(default=None, min_length=2, max_length=2)
    tax_id: str | None = None
    pan: str | None = Field(default=None, max_length=10)
    tan: str | None = Field(default=None, max_length=10)
    domain: str | None = None
    date_of_establishment: date | None = None
    parent_company_id: uuid.UUID | None = None
    # COA template key: "standard" | "in_standard" | "ae_uae_standard" | ...
    chart_of_accounts_template: str = "standard"
    create_default_fiscal_year: bool = True

    @field_validator("abbr")
    @classmethod
    def abbr_no_spaces(cls, v: str) -> str:
        if " " in v:
            raise ValueError("Abbreviation must not contain spaces")
        return v.upper()

    @field_validator("default_currency", "country_code")
    @classmethod
    def upper_codes(cls, v: str | None) -> str | None:
        return v.upper() if v else v


class CompanyUpdate(BaseModel):
    company_name: str | None = Field(default=None, min_length=1, max_length=140)
    tax_id: str | None = None
    pan: str | None = Field(default=None, max_length=10)
    tan: str | None = Field(default=None, max_length=10)
    domain: str | None = None
    date_of_establishment: date | None = None
    default_receivable_account_id: uuid.UUID | None = None
    default_payable_account_id: uuid.UUID | None = None
    default_cash_account_id: uuid.UUID | None = None
    default_bank_account_id: uuid.UUID | None = None
    default_income_account_id: uuid.UUID | None = None
    default_expense_account_id: uuid.UUID | None = None
    round_off_account_id: uuid.UUID | None = None
    write_off_account_id: uuid.UUID | None = None
    exchange_gain_loss_account_id: uuid.UUID | None = None
    default_cost_center_id: uuid.UUID | None = None
    enabled: bool | None = None


class CompanyResponse(DocumentMeta):
    company_name: str
    abbr: str
    country_code: str | None
    default_currency: str
    tax_id: str | None
    pan: str | None = None
    tan: str | None = None
    domain: str | None
    is_group: bool
    parent_company_id: uuid.UUID | None
    date_of_establishment: date | None
    chart_of_accounts_template: str | None
    default_receivable_account_id: uuid.UUID | None
    default_payable_account_id: uuid.UUID | None
    default_cash_account_id: uuid.UUID | None
    default_bank_account_id: uuid.UUID | None
    default_income_account_id: uuid.UUID | None
    default_expense_account_id: uuid.UUID | None
    round_off_account_id: uuid.UUID | None
    write_off_account_id: uuid.UUID | None
    exchange_gain_loss_account_id: uuid.UUID | None
    default_cost_center_id: uuid.UUID | None
    enabled: bool


class CompanyListItem(ORMModel):
    id: uuid.UUID
    company_name: str
    abbr: str
    default_currency: str
    country_code: str | None
    enabled: bool


# --- User ----------------------------------------------------------------------


class UserCreate(BaseModel):
    email: EmailStr
    first_name: str = Field(min_length=1, max_length=140)
    last_name: str | None = None
    password: str = Field(min_length=8, max_length=128)
    language: str = "en"
    time_zone: str | None = None
    default_company_id: uuid.UUID | None = None
    roles: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    first_name: str | None = Field(default=None, min_length=1, max_length=140)
    last_name: str | None = None
    password: str | None = Field(default=None, min_length=8, max_length=128)
    language: str | None = None
    time_zone: str | None = None
    default_company_id: uuid.UUID | None = None
    is_active: bool | None = None
    roles: list[str] | None = None


class UserResponse(DocumentMeta):
    email: EmailStr
    first_name: str
    last_name: str | None
    full_name: str
    is_active: bool
    language: str
    time_zone: str | None
    default_company_id: uuid.UUID | None
    role_names: list[str] = Field(default_factory=list)


class UserListItem(ORMModel):
    id: uuid.UUID
    email: EmailStr
    first_name: str
    last_name: str | None
    is_active: bool


# --- Role / Permissions ----------------------------------------------------------


class RoleCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str | None = None


class RoleResponse(DocumentMeta):
    name: str
    description: str | None
    is_system: bool
    disabled: bool


class RolePermissionUpsert(BaseModel):
    role: str
    doctype: str
    can_read: bool = False
    can_write: bool = False
    can_create: bool = False
    can_delete: bool = False
    can_submit: bool = False
    can_cancel: bool = False
    can_amend: bool = False
    can_print: bool = False
    can_email: bool = False
    can_report: bool = False
    if_owner: bool = False
    company_id: uuid.UUID | None = None


class RolePermissionResponse(DocumentMeta, RolePermissionUpsert):
    pass


# --- Currency / Country / UOM ----------------------------------------------------


class CurrencyResponse(DocumentMeta):
    code: str
    currency_name: str
    symbol: str | None
    fraction: str | None
    fraction_units: int
    number_format: str | None
    enabled: bool


class CurrencyExchangeCreate(BaseModel):
    date: date
    from_currency: str = Field(min_length=3, max_length=3)
    to_currency: str = Field(min_length=3, max_length=3)
    exchange_rate: float = Field(gt=0)
    for_buying: bool = True
    for_selling: bool = True


class CurrencyExchangeResponse(DocumentMeta):
    date: date
    from_currency: str
    to_currency: str
    exchange_rate: float
    for_buying: bool
    for_selling: bool


class CountryResponse(DocumentMeta):
    code: str
    country_name: str
    time_zone: str | None


class UOMCreate(BaseModel):
    uom_name: str = Field(min_length=1, max_length=140)
    must_be_whole_number: bool = False


class UOMResponse(DocumentMeta):
    uom_name: str
    must_be_whole_number: bool
    enabled: bool


class UOMConversionCreate(BaseModel):
    category: str | None = None
    from_uom: str
    to_uom: str
    value: float = Field(gt=0)


class UOMConversionResponse(DocumentMeta):
    category: str | None
    from_uom: str
    to_uom: str
    value: float


# --- Naming Series ----------------------------------------------------------------


class NamingSeriesPreviewRequest(BaseModel):
    pattern: str = Field(min_length=1, max_length=80, examples=["SINV-.YYYY.-"])


class NamingSeriesPreviewResponse(BaseModel):
    pattern: str
    next_name: str


class NamingSeriesResponse(DocumentMeta):
    series_prefix: str
    pattern: str
    current_value: int
    company_id: uuid.UUID


# --- Workflow ----------------------------------------------------------------------


class WorkflowStateIn(BaseModel):
    state: str
    state_docstatus: int = Field(ge=0, le=2, default=0)
    allow_edit_role: str | None = None
    next_action_role: str | None = None
    idx: int = 0


class WorkflowTransitionIn(BaseModel):
    from_state: str
    action: str
    to_state: str
    allowed_role: str
    condition: str | None = None
    idx: int = 0


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    doctype: str
    initial_state: str
    is_active: bool = False
    send_email_alert: bool = False
    states: list[WorkflowStateIn]
    transitions: list[WorkflowTransitionIn]


class WorkflowStateResponse(DocumentMeta, WorkflowStateIn):
    workflow_id: uuid.UUID


class WorkflowTransitionResponse(DocumentMeta, WorkflowTransitionIn):
    workflow_id: uuid.UUID


class WorkflowResponse(DocumentMeta):
    name: str
    doctype: str
    initial_state: str
    is_active: bool
    send_email_alert: bool
    states: list[WorkflowStateResponse]
    transitions: list[WorkflowTransitionResponse]


# --- System Settings ------------------------------------------------------------------


class SystemSettingUpsert(BaseModel):
    key: str = Field(min_length=1, max_length=140)
    value: dict | list | str | int | float | bool | None = None
    company_id: uuid.UUID | None = None


class SystemSettingResponse(DocumentMeta):
    key: str
    value: dict | list | str | int | float | bool | None
    company_id: uuid.UUID | None


# --- Accounts stubs (owned by Module 02, needed for company defaults) -------------------


class AccountListItem(ORMModel):
    id: uuid.UUID
    account_name: str
    account_number: str | None
    parent_account_id: uuid.UUID | None
    root_type: str
    report_type: str
    account_type: str | None
    is_group: bool
    account_currency: str | None
    freeze_account: bool
    disabled: bool
    path: str


class FiscalYearResponse(DocumentMeta):
    year: str
    year_start_date: date
    year_end_date: date
    is_closed: bool
    company_id: uuid.UUID
