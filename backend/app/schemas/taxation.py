"""Pydantic schemas for the read-only statutory catalogue API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

_PAISE = Decimal("0.01")


def _quantize_money(value: Decimal) -> Decimal:
    return value.quantize(_PAISE, rounding=ROUND_HALF_UP)


def _round_decimals_in_tree(obj: Any) -> Any:
    """Recursively format Decimal values to 2 places for JSON responses."""
    if isinstance(obj, dict):
        return {k: _round_decimals_in_tree(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_round_decimals_in_tree(v) for v in obj]
    if isinstance(obj, Decimal):
        return format(_quantize_money(obj), "f")
    return obj


class CatalogueModel(BaseModel):
    """Tax API base — all Decimal money/rate figures round to 2 decimal places."""

    model_config = ConfigDict(from_attributes=True)

    @model_validator(mode="after")
    def _quantize_decimal_fields(self) -> CatalogueModel:
        for name in self.model_fields:
            val = getattr(self, name)
            if isinstance(val, Decimal):
                object.__setattr__(self, name, _quantize_money(val))
        return self

    @model_serializer(mode="wrap")
    def _serialize_money(self, handler: Any, info: Any) -> Any:
        data = handler(self)
        if getattr(info, "mode", None) == "json":
            return _round_decimals_in_tree(data)
        return data


class TaxInModel(BaseModel):
    """Request bodies — incoming Decimal figures quantized to 2dp."""

    @model_validator(mode="after")
    def _quantize_decimal_fields(self) -> TaxInModel:
        for name in self.model_fields:
            val = getattr(self, name)
            if isinstance(val, Decimal):
                object.__setattr__(self, name, _quantize_money(val))
        return self


class AssessmentYearOut(CatalogueModel):
    code: str
    ay_start: date
    ay_end: date
    fy_start: date
    fy_end: date
    prev_ay_code: str | None = None


class AssesseeClassOut(CatalogueModel):
    code: str
    title: str
    default_itr_form: str | None = None
    pan_4th_chars: str = ""


class TaxRegimeOut(CatalogueModel):
    code: str
    title: str
    assessee_class_code: str
    is_default: bool
    election_irrevocable: bool
    election_form: str | None = None


class IncomeCharacterOut(CatalogueModel):
    code: str
    title: str
    surcharge_cap_percent: Decimal | None = None
    rebate_eligible: bool
    setoff_group: str | None = None
    loss_carry_years: int | None = None


class RateBandOut(CatalogueModel):
    seq: int
    lower: Decimal
    upper: Decimal | None = None
    rate_percent: Decimal
    fixed_amount: Decimal = Decimal("0")


class RateScheduleOut(CatalogueModel):
    code: str
    assessee_class_code: str
    regime_code: str
    income_character_code: str | None = None
    age_category: str = "General"
    condition_expr: str | None = None
    schedule_kind: str
    remarks: str | None = None
    bands: list[RateBandOut] = Field(default_factory=list)


class SurchargeBandOut(CatalogueModel):
    seq: int
    lower: Decimal
    upper: Decimal | None = None
    rate_percent: Decimal


class SurchargeScheduleOut(CatalogueModel):
    code: str
    assessee_class_code: str
    regime_code: str | None = None
    marginal_relief_method: str
    capped_characters: list[str] = Field(default_factory=list)
    remarks: str | None = None
    bands: list[SurchargeBandOut] = Field(default_factory=list)


class CessRuleOut(CatalogueModel):
    code: str
    rate_percent: Decimal
    base: str
    remarks: str | None = None


class RebateRuleOut(CatalogueModel):
    code: str
    section_code: str
    assessee_class_code: str
    regime_code: str
    max_taxable_income: Decimal
    max_rebate_amount: Decimal
    marginal_relief_enabled: bool
    excluded_characters: list[str] = Field(default_factory=list)


class ProvisionOut(CatalogueModel):
    section_code: str
    title: str
    act_reference: str | None = None
    stage: str
    default_effect: str
    itr_schedule: str | None = None


class DueDateRuleOut(CatalogueModel):
    code: str
    rule_kind: str
    assessee_class_code: str | None = None
    seq: int
    due_month: int | None = None
    due_day: int | None = None
    percent_of_tax: Decimal | None = None
    label: str | None = None


class DepreciationBlockOut(CatalogueModel):
    block_code: str
    title: str
    rate_percent: Decimal
    additional_depreciation_eligible: bool


class FinanceActVersionOut(CatalogueModel):
    ay_code: str
    version: str
    enacted_on: date | None = None
    source_ref: str | None = None
    is_current: bool


# --- Tier 2: tenant tax configuration ---


class TaxRegistrationOut(CatalogueModel):
    id: uuid.UUID
    pan: str | None = None
    tan: str | None = None
    cin: str | None = None
    assessee_class_code: str
    residential_status: str
    incorporation_date: date | None = None
    nature_of_business_codes: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    default_assessment_year: str | None = None
    itr_efile_provider: str | None = None
    remarks: str | None = None


class TaxRegistrationUpsert(TaxInModel):
    pan: str | None = None
    tan: str | None = None
    cin: str | None = None
    assessee_class_code: str = "Company"
    residential_status: str = "Resident"
    incorporation_date: date | None = None
    nature_of_business_codes: list[str] = Field(default_factory=list)
    jurisdiction: str | None = None
    default_assessment_year: str | None = None
    itr_efile_provider: str | None = None
    remarks: str | None = None


class TaxRegimeElectionOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    regime_code: str
    assessee_class_code: str
    elected_on: date | None = None
    form_ack_no: str | None = None
    irrevocable: bool
    remarks: str | None = None
    docstatus: int = 0


class TaxRegimeElectionCreate(TaxInModel):
    ay_code: str
    regime_code: str
    assessee_class_code: str
    elected_on: date | None = None
    form_ack_no: str | None = None
    irrevocable: bool = False
    remarks: str | None = None


class TaxPolicyOverrideOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    override_kind: str
    target_code: str
    reason: str
    params: dict = Field(default_factory=dict)
    disabled: bool = False


class TaxPolicyOverrideCreate(TaxInModel):
    ay_code: str
    override_kind: str
    target_code: str
    reason: str
    params: dict = Field(default_factory=dict)
    disabled: bool = False


# --- Tier 3: tax computations ---


class TaxComputationIncomeLineIn(TaxInModel):
    seq: int | None = None
    head: str = "PGBP"
    income_character_code: str = "ORDINARY"
    sub_ref: str | None = None
    gross: Decimal = Decimal("0")
    deductions: Decimal = Decimal("0")
    net: Decimal | None = None
    source_doc_type: str | None = None
    source_doc_id: uuid.UUID | None = None


class TaxComputationIncomeLineOut(CatalogueModel):
    id: uuid.UUID
    seq: int
    head: str
    income_character_code: str
    sub_ref: str | None = None
    gross: Decimal
    deductions: Decimal
    net: Decimal
    source_doc_type: str | None = None
    source_doc_id: uuid.UUID | None = None


class TaxComputationAdjustmentLineIn(TaxInModel):
    # Echoed back by the workspace editor. A row carrying a run_id is the engine's audit
    # record of a saved run, never user input, so it is dropped rather than re-applied.
    run_id: uuid.UUID | None = None
    provision_section_code: str | None = None
    rule_code: str | None = None
    section_code: str = ""
    stage: str = "PGBP"
    description: str | None = None
    direction: str = "Add"
    amount: Decimal = Decimal("0")
    override_amount: Decimal | None = None
    status: str = "Manual"


class TaxComputationAdjustmentLineOut(CatalogueModel):
    id: uuid.UUID
    run_id: uuid.UUID | None = None
    provision_section_code: str | None = None
    rule_code: str | None = None
    section_code: str
    stage: str
    description: str | None = None
    direction: str
    amount: Decimal
    override_amount: Decimal | None = None
    final_amount: Decimal
    status: str
    explanation: dict = Field(default_factory=dict)


class TaxComputationResultOut(CatalogueModel):
    id: uuid.UUID
    run_id: uuid.UUID
    taxable_income: Decimal
    tax_normal: Decimal
    tax_mat: Decimal | None = None
    tax_applied_basis: str
    tax_before_rebate: Decimal
    rebate_amount: Decimal
    surcharge_before_relief: Decimal
    marginal_relief_amount: Decimal
    surcharge_amount: Decimal
    cess_amount: Decimal
    interest_234a: Decimal
    interest_234b: Decimal
    interest_234c: Decimal
    credits_total: Decimal
    total_tax: Decimal
    net_payable: Decimal
    breakdown: dict = Field(default_factory=dict)


class TaxComputationRunOut(CatalogueModel):
    id: uuid.UUID
    run_no: int
    trigger: str
    engine_version: str
    finance_act_version_id: uuid.UUID
    ruleset_hash: str
    input_hash: str
    duration_ms: int | None = None
    user_id: uuid.UUID | None = None
    superseded_at: datetime | None = None
    creation: datetime | None = None
    result: TaxComputationResultOut | None = None


class TaxComputationCreate(TaxInModel):
    ay_code: str
    assessee_class_code: str | None = None
    regime_code: str | None = None
    filing_type: str = "Original"
    revises_computation_id: uuid.UUID | None = None
    from_date: date | None = None
    to_date: date | None = None
    remarks: str | None = None
    book_profit_115jb: Decimal | None = None
    return_filed_date: date | None = None
    audit_applicable: bool = True
    itr_due_date_override: date | None = None
    income_lines: list[TaxComputationIncomeLineIn] = Field(default_factory=list)
    adjustment_lines: list[TaxComputationAdjustmentLineIn] = Field(default_factory=list)


class TaxComputationUpdate(TaxInModel):
    filing_type: str | None = None
    remarks: str | None = None
    book_profit_115jb: Decimal | None = None
    return_filed_date: date | None = None
    audit_applicable: bool | None = None
    itr_due_date_override: date | None = None
    income_lines: list[TaxComputationIncomeLineIn] | None = None
    adjustment_lines: list[TaxComputationAdjustmentLineIn] | None = None


class TaxComputationOut(CatalogueModel):
    id: uuid.UUID
    name: str
    ay_code: str
    assessee_class_code: str
    regime_election_id: uuid.UUID | None = None
    regime_code: str
    filing_type: str
    revises_computation_id: uuid.UUID | None = None
    from_date: date
    to_date: date
    finance_act_version_id: uuid.UUID
    status: str
    remarks: str | None = None
    current_run_id: uuid.UUID | None = None
    book_profit_115jb: Decimal | None = None
    return_filed_date: date | None = None
    audit_applicable: bool = True
    itr_due_date_override: date | None = None
    provision_expense_account_id: uuid.UUID | None = None
    provision_liability_account_id: uuid.UUID | None = None
    provision_gl_posted: bool = False
    docstatus: int = 0
    income_lines: list[TaxComputationIncomeLineOut] = Field(default_factory=list)
    adjustment_lines: list[TaxComputationAdjustmentLineOut] = Field(default_factory=list)


class TaxComputationRunCreate(TaxInModel):
    trigger: str = "manual"


class TaxComputationSubmit(TaxInModel):
    post_provision: bool = True
    provision_expense_account_id: uuid.UUID | None = None
    provision_liability_account_id: uuid.UUID | None = None


# --- Tier 3: challans + credits + 26AS ---


class TaxChallanCreate(TaxInModel):
    ay_code: str
    challan_type: str
    bsr_code: str
    challan_serial: str
    deposit_date: date
    amount: Decimal
    cin: str | None = None
    major_head: str = "0021"
    minor_head: str = "100"
    bank_account_id: uuid.UUID | None = None
    tax_payable_account_id: uuid.UUID | None = None
    computation_id: uuid.UUID | None = None
    remarks: str | None = None


class TaxChallanUpdate(TaxInModel):
    challan_type: str | None = None
    bsr_code: str | None = None
    challan_serial: str | None = None
    cin: str | None = None
    deposit_date: date | None = None
    major_head: str | None = None
    minor_head: str | None = None
    amount: Decimal | None = None
    bank_account_id: uuid.UUID | None = None
    tax_payable_account_id: uuid.UUID | None = None
    computation_id: uuid.UUID | None = None
    remarks: str | None = None


class TaxChallanOut(CatalogueModel):
    id: uuid.UUID
    name: str
    ay_code: str
    challan_type: str
    bsr_code: str
    challan_serial: str
    cin: str | None = None
    deposit_date: date
    major_head: str
    minor_head: str
    amount: Decimal
    bank_account_id: uuid.UUID
    tax_payable_account_id: uuid.UUID
    computation_id: uuid.UUID | None = None
    remarks: str | None = None
    docstatus: int = 0


class TaxCreditEntryCreate(TaxInModel):
    ay_code: str
    credit_kind: str
    amount_credited: Decimal
    amount_claimed: Decimal | None = None
    computation_id: uuid.UUID | None = None
    deductor_tan: str | None = None
    deductor_name: str | None = None
    section_code: str | None = None
    challan_id: uuid.UUID | None = None
    source_refs: dict = Field(default_factory=dict)
    remarks: str | None = None


class TaxCreditEntryOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    credit_kind: str
    computation_id: uuid.UUID | None = None
    deductor_tan: str | None = None
    deductor_name: str | None = None
    section_code: str | None = None
    amount_credited: Decimal
    amount_claimed: Decimal
    challan_id: uuid.UUID | None = None
    reconciliation_status: str
    portal_amount: Decimal | None = None
    source_refs: dict = Field(default_factory=dict)
    remarks: str | None = None
    docstatus: int = 0


class Tax26asReconRequest(TaxInModel):
    ay_code: str
    form26as: dict
    computation_id: uuid.UUID | None = None


class Tax26asReconRowOut(CatalogueModel):
    bucket: str
    deductor_tan: str | None = None
    section_code: str | None = None
    books_amount: str
    portal_amount: str
    credit_id: str | None = None


class Tax26asReconOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    computation_id: uuid.UUID | None = None
    status: str
    books_total: Decimal
    portal_total: Decimal
    difference: Decimal
    payload_hash: str
    matched: int = 0
    mismatch: int = 0
    only_in_books: int = 0
    only_in_26as: int = 0
    rows: list[Tax26asReconRowOut] = Field(default_factory=list)
    creation: datetime | None = None


# --- Tier 3 Phase 6: depreciation / loss CF / MAT credit ---


class TaxDepreciationMovementOut(CatalogueModel):
    id: uuid.UUID
    movement_type: str
    amount: Decimal
    asset_id: uuid.UUID | None = None
    put_to_use_date: date | None = None
    half_rate: bool = False
    remarks: str | None = None


class TaxDepreciationRegisterOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    block_code: str
    rate_percent: Decimal
    opening_wdv: Decimal
    additions_full: Decimal
    additions_half: Decimal
    deletions: Decimal
    depreciation_amount: Decimal
    additional_depreciation_amount: Decimal
    closing_wdv: Decimal
    remarks: str | None = None
    movements: list[TaxDepreciationMovementOut] = Field(default_factory=list)


class TaxDepreciationSyncRequest(TaxInModel):
    ay_code: str
    opening_wdv_overrides: dict[str, Decimal] | None = None


class TaxLossCarryForwardCreate(TaxInModel):
    origin_ay_code: str
    setoff_group: str = "ORDINARY"
    loss_kind: str  # Business | UnabsorbedDep | Speculation | STCG | LTCG | OS
    amount: Decimal
    expires_after_ay: str | None = None
    computation_id: uuid.UUID | None = None
    remarks: str | None = None


class TaxLossCarryForwardOut(CatalogueModel):
    id: uuid.UUID
    origin_ay_code: str
    expires_after_ay: str | None = None
    setoff_group: str
    loss_kind: str
    entry_kind: str
    amount: Decimal
    amount_remaining: Decimal
    computation_id: uuid.UUID | None = None
    remarks: str | None = None


class TaxLossSetoffEntryOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    computation_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    ledger_id: uuid.UUID
    against_character: str
    amount_set_off: Decimal
    sequence: int
    explanation: dict = Field(default_factory=dict)


class MatCreditLedgerOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    entry_kind: str
    amount: Decimal
    tax_mat: Decimal | None = None
    tax_normal: Decimal | None = None
    expires_after_ay: str | None = None
    computation_id: uuid.UUID | None = None
    run_id: uuid.UUID | None = None
    remarks: str | None = None

# --- Phase 7: advance-tax calendar + interest + reminders ---


class AdvanceTaxInstalmentOut(CatalogueModel):
    seq: int
    code: str
    label: str
    due_date: date
    cumulative_percent: Decimal
    required_cumulative: Decimal
    paid_to_date: Decimal
    shortfall: Decimal
    suggested_payment: Decimal
    status: str


class AdvanceTaxCalendarOut(CatalogueModel):
    ay_code: str
    estimated_tax_net: Decimal
    instalments: list[AdvanceTaxInstalmentOut] = Field(default_factory=list)


class Interest234PreviewRequest(TaxInModel):
    ay_code: str
    assessed_tax: Decimal
    computation_id: uuid.UUID | None = None
    return_filed_date: date | None = None
    audit_applicable: bool = True
    itr_due_date_override: date | None = None
    as_of_date: date | None = None


class Interest234PreviewOut(CatalogueModel):
    interest_234a: Decimal
    interest_234b: Decimal
    interest_234c: Decimal
    total_interest: Decimal
    assessed_tax: Decimal
    tax_after_tds: Decimal
    advance_tax_paid: Decimal
    breakdown: dict = Field(default_factory=dict)


class TaxComplianceReminderOut(CatalogueModel):
    id: uuid.UUID
    ay_code: str
    rule_code: str
    rule_kind: str
    due_date: date
    sent_at: datetime | None = None
    channel: str
    status: str
    shortfall_amount: Decimal | None = None
    recipients: list = Field(default_factory=list)
    error: str | None = None


# --- Phase 8: ITR filings ---


class ItrFieldMapOut(CatalogueModel):
    canonical_field: str
    cbdt_json_path: str
    transform: str | None = None


class ItrFormOut(CatalogueModel):
    id: uuid.UUID
    form_code: str
    schema_version: str
    title: str
    assessee_class_codes: list = Field(default_factory=list)
    field_maps: list[ItrFieldMapOut] = Field(default_factory=list)


class TaxFilingGenerateIn(TaxInModel):
    computation_id: uuid.UUID
    form_code: str | None = None
    allow_draft: bool = False
    remarks: str | None = None


class TaxFilingAckIn(TaxInModel):
    ack_no: str
    filed_on: date | None = None
    verification_mode: str | None = "EVC"
    provider_response: dict = Field(default_factory=dict)


class TaxFilingChainIn(TaxInModel):
    revises_computation_id: uuid.UUID
    filing_type: str  # Revised | Belated | Updated
    ay_code: str | None = None
    remarks: str | None = None


class TaxFilingOut(CatalogueModel):
    id: uuid.UUID
    name: str
    computation_id: uuid.UUID
    run_id: uuid.UUID | None = None
    ay_code: str
    form_code: str
    schema_version: str
    filing_type: str
    revises_filing_id: uuid.UUID | None = None
    payload: dict = Field(default_factory=dict)
    payload_sha256: str
    canonical_snapshot: dict = Field(default_factory=dict)
    status: str
    ack_no: str | None = None
    filed_on: date | None = None
    verification_mode: str | None = None
    provider: str | None = None
    provider_response: dict = Field(default_factory=dict)
    remarks: str | None = None
    docstatus: int = 0
    creation: datetime | None = None
    modified: datetime | None = None


# =====================================================================================
# Unified CA workspace — BFF aggregate, non-persisting preview, lookups, templates,
# structured validation and the Statement of Total Income.
#
# Contract frozen in docs/plans/income_tax_ca_workspace.plan.md §3. Served by
# app/api/v1/tax/workspace.py; all logic lives in app/services/taxation/.
# =====================================================================================

SectionStatus = Literal["not-started", "in-progress", "complete", "has-errors"]
IssueSeverity = Literal["blocking", "advisory"]


class TaxOptionOut(CatalogueModel):
    """One choice in a searchable select. ``label`` is always plain business English."""

    value: str
    label: str
    group: str | None = None
    hint: str | None = None
    statutory_ref: str | None = None
    meta: dict[str, str] = Field(default_factory=dict)


class TaxLookupsOut(CatalogueModel):
    """Every dropdown the Income Tax workspace needs, in one round trip."""

    ay_code: str | None = None
    assessment_years: list[TaxOptionOut] = Field(default_factory=list)
    entity_classes: list[TaxOptionOut] = Field(default_factory=list)
    tax_regimes: list[TaxOptionOut] = Field(default_factory=list)
    filing_types: list[TaxOptionOut] = Field(default_factory=list)
    income_heads: list[TaxOptionOut] = Field(default_factory=list)
    income_characters: list[TaxOptionOut] = Field(default_factory=list)
    adjustment_provisions: list[TaxOptionOut] = Field(default_factory=list)
    adjustment_stages: list[TaxOptionOut] = Field(default_factory=list)
    adjustment_directions: list[TaxOptionOut] = Field(default_factory=list)
    depreciation_blocks: list[TaxOptionOut] = Field(default_factory=list)
    loss_kinds: list[TaxOptionOut] = Field(default_factory=list)
    setoff_groups: list[TaxOptionOut] = Field(default_factory=list)
    challan_types: list[TaxOptionOut] = Field(default_factory=list)
    major_heads: list[TaxOptionOut] = Field(default_factory=list)
    minor_heads: list[TaxOptionOut] = Field(default_factory=list)
    bank_branch_codes: list[TaxOptionOut] = Field(default_factory=list)
    credit_kinds: list[TaxOptionOut] = Field(default_factory=list)
    deduction_sections: list[TaxOptionOut] = Field(default_factory=list)
    deductors: list[TaxOptionOut] = Field(default_factory=list)
    bank_accounts: list[TaxOptionOut] = Field(default_factory=list)
    tax_payable_accounts: list[TaxOptionOut] = Field(default_factory=list)
    tax_expense_accounts: list[TaxOptionOut] = Field(default_factory=list)
    itr_forms: list[TaxOptionOut] = Field(default_factory=list)
    verification_modes: list[TaxOptionOut] = Field(default_factory=list)


class TaxWorkspaceContextOut(CatalogueModel):
    """Drives context-aware field visibility on the client — one typed object, no ad-hoc flags."""

    ay_code: str
    ay_label: str
    previous_ay_code: str | None = None
    financial_year_label: str
    fy_start: date
    fy_end: date
    entity_class_code: str
    entity_class_label: str
    regime_code: str
    regime_label: str
    regime_statutory_ref: str | None = None
    regime_irrevocable: bool = False
    concessional_regime: bool = False
    forfeited_incentives: list[str] = Field(default_factory=list)
    mat_applicable: bool = False
    presumptive: bool = False
    audit_applicable: bool = True
    filing_type: str = "Original"
    itr_form_code: str | None = None
    return_due_date: date | None = None
    is_draft: bool = True
    is_submitted: bool = False
    is_cancelled: bool = False
    currency: str = "INR"


class TaxValidationIssueOut(CatalogueModel):
    """One reviewable issue. ``section`` is a workspace section key so the UI can jump to it."""

    severity: IssueSeverity
    code: str
    message: str
    section: str
    field: str | None = None
    hint: str | None = None
    row_index: int | None = None


class TaxValidationOut(CatalogueModel):
    ok: bool = True
    blocking_count: int = 0
    advisory_count: int = 0
    issues: list[TaxValidationIssueOut] = Field(default_factory=list)


class TaxSectionStatusOut(CatalogueModel):
    key: str
    label: str
    status: SectionStatus = "not-started"
    summary: str | None = None
    row_count: int = 0
    blocking_count: int = 0
    advisory_count: int = 0


class TaxPreviewRequest(TaxInModel):
    """Unsaved editor overlay for a live, non-persisting recompute.

    Every field is optional; ``None`` means "use what is persisted". The explicit
    ``apply_book_profit`` flag exists so the client can clear the book profit
    (send ``book_profit_115jb=None`` with ``apply_book_profit=True``).
    """

    income_lines: list[TaxComputationIncomeLineIn] | None = None
    adjustment_lines: list[TaxComputationAdjustmentLineIn] | None = None
    book_profit_115jb: Decimal | None = None
    apply_book_profit: bool = False
    return_filed_date: date | None = None
    audit_applicable: bool | None = None
    itr_due_date_override: date | None = None
    tax_depreciation_total: Decimal | None = None
    as_of_date: date | None = None


class TaxPreviewLineOut(CatalogueModel):
    """One drill-down row on the live result rail."""

    key: str
    label: str
    amount: Decimal
    kind: str = "tax"
    statutory_ref: str | None = None
    explain: str | None = None
    emphasis: str = "normal"


class TaxPreviewOut(CatalogueModel):
    """Result of a dry-run compute. ``persisted`` is always false, by contract."""

    ay_code: str
    gross_total_income: Decimal = Decimal("0")
    total_income: Decimal = Decimal("0")
    losses_set_off: Decimal = Decimal("0")
    tax_depreciation_claimed: Decimal = Decimal("0")
    tax_on_total_income: Decimal = Decimal("0")
    rebate_amount: Decimal = Decimal("0")
    surcharge_before_relief: Decimal = Decimal("0")
    marginal_relief_amount: Decimal = Decimal("0")
    surcharge_amount: Decimal = Decimal("0")
    cess_amount: Decimal = Decimal("0")
    tax_normal: Decimal = Decimal("0")
    tax_mat: Decimal | None = None
    tax_applied_basis: str = "Normal"
    mat_credit_created: Decimal = Decimal("0")
    mat_credit_utilised: Decimal = Decimal("0")
    interest_234a: Decimal = Decimal("0")
    interest_234b: Decimal = Decimal("0")
    interest_234c: Decimal = Decimal("0")
    total_interest: Decimal = Decimal("0")
    credits_total: Decimal = Decimal("0")
    total_tax: Decimal = Decimal("0")
    net_payable: Decimal = Decimal("0")
    refund_due: Decimal = Decimal("0")
    lines: list[TaxPreviewLineOut] = Field(default_factory=list)
    breakdown: dict = Field(default_factory=dict)
    engine_version: str = ""
    ruleset_hash: str = ""
    input_hash: str = ""
    persisted: bool = False
    matches_current_run: bool = False
    notes: list[str] = Field(default_factory=list)


class TaxStatementRowOut(CatalogueModel):
    key: str
    label: str
    amount: Decimal | None = None
    statutory_ref: str | None = None
    indent: int = 0
    emphasis: str = "normal"
    note: str | None = None


class TaxStatementSectionOut(CatalogueModel):
    key: str
    title: str
    rows: list[TaxStatementRowOut] = Field(default_factory=list)


class TaxStatementOut(CatalogueModel):
    """Statement of Total Income + Tax Computation, in the order a reviewer reads it."""

    ay_code: str
    previous_ay_code: str | None = None
    entity_label: str
    pan: str | None = None
    regime_label: str
    basis_applied: str = "Normal"
    sections: list[TaxStatementSectionOut] = Field(default_factory=list)
    variance_previous_year: list[TaxStatementRowOut] = Field(default_factory=list)
    variance_books: list[TaxStatementRowOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class TaxTemplateAdjustmentOut(CatalogueModel):
    section_code: str
    label: str
    stage: str = "PGBP"
    direction: str = "Add"
    hint: str | None = None


class TaxTemplateOut(CatalogueModel):
    """A starting preset for a new computation — system-supplied, not tenant data."""

    code: str
    title: str
    description: str
    entity_class_code: str
    regime_code: str
    regime_statutory_ref: str | None = None
    audit_applicable: bool = True
    mat_applicable: bool = True
    requires_book_profit: bool = False
    forfeited_incentives: list[str] = Field(default_factory=list)
    income_heads: list[str] = Field(default_factory=list)
    suggested_adjustments: list[TaxTemplateAdjustmentOut] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    available: bool = True
    unavailable_reason: str | None = None
    recommended: bool = False


class TaxTemplateApplyIn(TaxInModel):
    template_code: str
    ay_code: str
    book_profit_115jb: Decimal | None = None
    copy_from_previous_year: bool = True
    populate_from_books: bool = False
    filing_type: str = "Original"
    remarks: str | None = None


class TaxCopyPreviousYearIn(TaxInModel):
    source_ay_code: str | None = None
    copy_income_heads: bool = True
    copy_adjustments: bool = True
    carry_depreciation_blocks: bool = True
    overwrite_existing: bool = False


class TaxCopyPreviousYearOut(CatalogueModel):
    source_ay_code: str | None = None
    source_computation_id: uuid.UUID | None = None
    income_lines_copied: int = 0
    adjustment_lines_copied: int = 0
    depreciation_blocks_carried: int = 0
    brought_forward_losses_available: int = 0
    notes: list[str] = Field(default_factory=list)
    computation: TaxComputationOut


class TaxPopulateFromBooksIn(TaxInModel):
    set_book_profit: bool = True
    replace_income_heads: bool = True
    add_accounting_depreciation_addback: bool = True


class TaxPopulateFromBooksOut(CatalogueModel):
    ay_code: str
    fy_start: date
    fy_end: date
    book_profit: Decimal = Decimal("0")
    accounting_depreciation: Decimal = Decimal("0")
    income_lines_written: int = 0
    adjustment_lines_written: int = 0
    source: str = "gl_trial_balance"
    notes: list[str] = Field(default_factory=list)
    computation: TaxComputationOut


class TaxYearSummaryOut(CatalogueModel):
    """One row on the Income Tax landing screen."""

    ay_code: str
    ay_label: str
    financial_year_label: str
    computation_id: uuid.UUID | None = None
    computation_name: str | None = None
    status: str = "Not started"
    docstatus: int | None = None
    regime_code: str | None = None
    regime_label: str | None = None
    total_income: Decimal | None = None
    net_payable: Decimal | None = None
    taxes_paid: Decimal = Decimal("0")
    return_due_date: date | None = None
    next_due_label: str | None = None
    next_due_date: date | None = None
    next_action: str = "Start the computation"
    next_action_section: str = "overview"
    blocking_count: int = 0
    advisory_count: int = 0
    filing_status: str | None = None
    ack_no: str | None = None
    is_current: bool = False


class TaxWorkspaceBootstrapOut(CatalogueModel):
    """Cold start for the landing screen and the workspace: one call, no waterfall."""

    current_ay_code: str
    default_ay_code: str
    registration: TaxRegistrationOut | None = None
    registration_complete: bool = False
    years: list[TaxYearSummaryOut] = Field(default_factory=list)
    templates: list[TaxTemplateOut] = Field(default_factory=list)
    lookups: TaxLookupsOut = Field(default_factory=TaxLookupsOut)


class TaxWorkspaceOut(CatalogueModel):
    """BFF aggregate — everything the unified workspace renders, in one round trip."""

    computation: TaxComputationOut
    context: TaxWorkspaceContextOut
    registration: TaxRegistrationOut | None = None
    election: TaxRegimeElectionOut | None = None
    result: TaxComputationResultOut | None = None
    runs: list[TaxComputationRunOut] = Field(default_factory=list)
    depreciation_registers: list[TaxDepreciationRegisterOut] = Field(default_factory=list)
    depreciation_total: Decimal = Decimal("0")
    losses: list[TaxLossCarryForwardOut] = Field(default_factory=list)
    setoff_entries: list[TaxLossSetoffEntryOut] = Field(default_factory=list)
    mat_credits: list[MatCreditLedgerOut] = Field(default_factory=list)
    mat_credit_available: Decimal = Decimal("0")
    credits: list[TaxCreditEntryOut] = Field(default_factory=list)
    challans: list[TaxChallanOut] = Field(default_factory=list)
    reconciliations: list[Tax26asReconOut] = Field(default_factory=list)
    advance_tax: AdvanceTaxCalendarOut | None = None
    filings: list[TaxFilingOut] = Field(default_factory=list)
    sections: list[TaxSectionStatusOut] = Field(default_factory=list)
    validation: TaxValidationOut = Field(default_factory=TaxValidationOut)
    lookups: TaxLookupsOut = Field(default_factory=TaxLookupsOut)
