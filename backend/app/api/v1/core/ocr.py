"""OCR extraction endpoints — Data Entry control (scanned invoice / PO → line items)."""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.core.security import CurrentUser, get_current_user, get_tenant_db
from app.schemas.ocr import OcrExtractRequest, OcrExtractResponse, OcrStatusResponse
from app.services import ocr as ocr_service

router = APIRouter(prefix="/ocr", tags=["core: ocr"])


@router.get(
    "/status",
    response_model=OcrStatusResponse,
    summary="OCR configuration status",
    description="Whether a vision API key is configured. Does not call the provider.",
)
async def ocr_status(
    _current_user: Annotated[CurrentUser, Depends(get_current_user)],
) -> OcrStatusResponse:
    return ocr_service.ocr_status()


@router.post(
    "/extract",
    response_model=OcrExtractResponse,
    summary="Extract line items from a scanned invoice or PO",
    description=(
        "Accepts a JPEG/PNG/WebP/GIF/PDF (base64). Calls the configured OpenAI-compatible "
        "vision API, then matches descriptions against this company's item master. "
        "Nothing is saved — review the rows in the UI and add them to the document."
    ),
)
async def extract(
    payload: OcrExtractRequest,
    current_user: Annotated[CurrentUser, Depends(get_current_user)],
    db: Annotated[AsyncSession, Depends(get_tenant_db)],
) -> OcrExtractResponse:
    company_id = current_user.company_id
    if company_id is None:
        raise ValidationError("An active company is required")
    return await ocr_service.extract_document(
        db,
        company_id=company_id,
        content_base64=payload.content_base64,
        file_name=payload.file_name,
        mime_type=payload.mime_type,
    )
