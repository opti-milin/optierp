"""OCR line-item extraction — request/response for the Data Entry control."""

from decimal import Decimal

from pydantic import BaseModel, Field


class OcrExtractRequest(BaseModel):
    """A scanned invoice or PO, base64-encoded (same upload pattern as Tally)."""

    file_name: str = Field(max_length=255)
    content_base64: str = Field(min_length=1)
    mime_type: str | None = None


class OcrLine(BaseModel):
    """One extracted row, optionally matched to the tenant item master."""

    item_code: str
    item_name: str
    description: str
    qty: Decimal
    rate: Decimal | None = None
    uom: str | None = None
    matched: bool = False
    match_confidence: int = 0


class OcrExtractResponse(BaseModel):
    lines: list[OcrLine]
    warnings: list[str] = Field(default_factory=list)
    document_type: str | None = None
    party_name: str | None = None
    document_number: str | None = None
    document_date: str | None = None
    provider: str
    model: str


class OcrStatusResponse(BaseModel):
    configured: bool
    provider: str
    model: str
