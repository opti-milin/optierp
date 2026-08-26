"""Extract line items from a scanned invoice or PO via an OpenAI-compatible vision API.

The router is thin: this module validates the upload, calls the provider, parses the
JSON, and matches descriptions/codes against the tenant item master. Nothing is
persisted — the UI reviews the rows and appends them to the document grid.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.exceptions import ValidationError
from app.core.logging import get_logger
from app.models.stock import Item
from app.schemas.ocr import OcrExtractResponse, OcrLine, OcrStatusResponse

logger = get_logger(__name__)

ALLOWED_MIME = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
        "image/gif",
        "application/pdf",
    }
)
EXT_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".gif": "image/gif",
    ".pdf": "application/pdf",
}
FUZZY_MIN = 70
CATALOG_LIMIT = 5000

EXTRACT_PROMPT = """Extract commercial line items from this invoice or purchase order.
Return ONLY valid JSON (no markdown) with this shape:
{
  "document_type": "invoice" | "purchase_order" | "unknown",
  "party_name": string | null,
  "document_number": string | null,
  "document_date": "YYYY-MM-DD" | null,
  "currency": string | null,
  "lines": [
    {
      "item_code": string | null,
      "description": string,
      "qty": number,
      "rate": number | null,
      "uom": string | null,
      "amount": number | null
    }
  ]
}
Rules:
- qty is the billed quantity (positive).
- rate is the unit price excluding tax when you can tell; otherwise the printed rate.
- Skip headers, tax-summary rows, round-off, freight-only rows, and totals.
- item_code is a printed SKU / item code, not an HSN/SAC tax code.
- description is the product or service name as printed.
- If there are no line items, return lines: [].
"""

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE)
_LEADING_NUMBER = re.compile(r"[-+]?\d+(?:[.,]\d+)?")


class ChatComplete(Protocol):
    async def __call__(self, messages: list[dict[str, Any]], settings: Settings) -> str: ...


@dataclass(frozen=True, slots=True)
class CatalogItem:
    item_code: str
    item_name: str
    stock_uom: str
    standard_rate: Decimal


def ocr_status(settings: Settings | None = None) -> OcrStatusResponse:
    cfg = settings or get_settings()
    return OcrStatusResponse(
        configured=_is_configured(cfg),
        provider=cfg.ocr_provider,
        model=cfg.ocr_model,
    )


def _is_configured(settings: Settings) -> bool:
    provider = (settings.ocr_provider or "").strip().lower()
    if provider in {"", "disabled", "none", "off"}:
        return False
    return bool(settings.ocr_api_key.strip())


def sniff_mime(content: bytes, file_name: str, declared: str | None) -> str:
    """Prefer magic bytes, then the declared type, then the file extension."""
    if content.startswith(b"%PDF"):
        return "application/pdf"
    if content.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if content.startswith(b"GIF87a") or content.startswith(b"GIF89a"):
        return "image/gif"
    if len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    declared_mime = (declared or "").split(";", 1)[0].strip().lower()
    if declared_mime in ALLOWED_MIME:
        return declared_mime
    ext_mime = EXT_MIME.get(Path(file_name).suffix.lower())
    if ext_mime is not None:
        return ext_mime
    raise ValidationError(
        "Upload a JPEG, PNG, WebP, GIF, or PDF of the invoice or PO.",
        field="file_name",
        code="ocr_unsupported_type",
    )


def decode_upload(
    content_base64: str, *, max_bytes: int, file_name: str, mime_type: str | None
) -> tuple[bytes, str]:
    # Base64 is ~4/3 the payload; reject the wire size first so we don't decode a bomb.
    if len(content_base64) > max_bytes * 2:
        raise ValidationError(
            f"That file is too large (max {max_bytes // (1024 * 1024)} MB).",
            field="content_base64",
            code="ocr_file_too_large",
        )
    try:
        raw = base64.b64decode(content_base64, validate=True)
    except (binascii.Error, ValueError):
        raise ValidationError(
            "content_base64 is not valid base64", field="content_base64", code="ocr_bad_base64"
        ) from None
    if not raw:
        raise ValidationError("The uploaded file is empty.", field="content_base64", code="ocr_empty")
    if len(raw) > max_bytes:
        raise ValidationError(
            f"That file is too large (max {max_bytes // (1024 * 1024)} MB).",
            field="content_base64",
            code="ocr_file_too_large",
        )
    return raw, sniff_mime(raw, file_name, mime_type)


def parse_model_json(text: str) -> dict[str, Any]:
    """Parse the model reply; tolerate optional markdown fences and leading prose."""
    cleaned = _FENCE.sub("", text.strip()).strip()
    if not cleaned:
        raise ValidationError(
            "The OCR service returned an empty response.", code="ocr_empty_response"
        )
    try:
        parsed: Any = json.loads(cleaned)
    except json.JSONDecodeError:
        start, end = cleaned.find("{"), cleaned.rfind("}")
        if start == -1 or end <= start:
            raise ValidationError(
                "The OCR service did not return JSON we could read. Try a clearer scan.",
                code="ocr_bad_response",
            ) from None
        try:
            parsed = json.loads(cleaned[start : end + 1])
        except json.JSONDecodeError:
            raise ValidationError(
                "The OCR service did not return JSON we could read. Try a clearer scan.",
                code="ocr_bad_response",
            ) from None
    if not isinstance(parsed, dict):
        raise ValidationError(
            "The OCR service returned JSON that was not an object.", code="ocr_bad_response"
        )
    return parsed


def _to_decimal(value: Any) -> Decimal | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int | float):
        return Decimal(str(value))
    text = str(value).strip().replace(",", "")
    match = _LEADING_NUMBER.search(text)
    if match is None:
        return None
    try:
        return Decimal(match.group(0))
    except InvalidOperation:
        return None


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]+", " ", text.casefold())).strip()


def _similarity(left: str, right: str) -> int:
    if not left or not right:
        return 0
    return int(SequenceMatcher(None, left, right).ratio() * 100)


def match_catalog_item(
    item_code: str | None, description: str, catalog: Sequence[CatalogItem]
) -> tuple[CatalogItem | None, int]:
    """Exact item_code, then exact name, then fuzzy name/code. Confidence 0–100."""
    code = (item_code or "").strip()
    desc = (description or "").strip()
    if not catalog:
        return None, 0

    if code:
        folded = code.casefold()
        for item in catalog:
            if item.item_code.casefold() == folded:
                return item, 95

    desc_folded = desc.casefold()
    if desc_folded:
        for item in catalog:
            if item.item_name.casefold() == desc_folded or item.item_code.casefold() == desc_folded:
                return item, 90

    needle = _normalise(code or desc)
    if not needle:
        return None, 0
    best: CatalogItem | None = None
    best_score = 0
    for item in catalog:
        score = max(
            _similarity(needle, _normalise(item.item_code)),
            _similarity(needle, _normalise(item.item_name)),
        )
        if score > best_score:
            best, best_score = item, score
    if best is not None and best_score >= FUZZY_MIN:
        return best, best_score
    return None, 0


def lines_from_payload(payload: dict[str, Any], catalog: Sequence[CatalogItem]) -> list[OcrLine]:
    raw_lines = payload.get("lines")
    if raw_lines is None:
        raw_lines = []
    if not isinstance(raw_lines, list):
        raise ValidationError("The OCR service returned lines that were not a list.", code="ocr_bad_response")

    out: list[OcrLine] = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        description = str(raw.get("description") or raw.get("item_name") or "").strip()
        extracted_code = str(raw.get("item_code") or "").strip() or None
        qty = _to_decimal(raw.get("qty"))
        if qty is None or qty <= 0:
            qty = Decimal("1")
        rate = _to_decimal(raw.get("rate"))
        if rate is not None and rate < 0:
            rate = None
        uom = str(raw.get("uom") or "").strip() or None
        if not description and not extracted_code:
            continue
        hit, confidence = match_catalog_item(extracted_code, description, catalog)
        if hit is not None:
            out.append(
                OcrLine(
                    item_code=hit.item_code,
                    item_name=hit.item_name,
                    description=description or hit.item_name,
                    qty=qty,
                    rate=rate if rate is not None else hit.standard_rate,
                    uom=uom or hit.stock_uom,
                    matched=True,
                    match_confidence=confidence,
                )
            )
            continue
        fallback_code = (extracted_code or description)[:140]
        out.append(
            OcrLine(
                item_code=fallback_code,
                item_name=description or fallback_code,
                description=description or fallback_code,
                qty=qty,
                rate=rate,
                uom=uom,
                matched=False,
                match_confidence=0,
            )
        )
    return out


def _user_content(mime: str, b64: str, file_name: str) -> list[dict[str, Any]]:
    prompt = {"type": "text", "text": EXTRACT_PROMPT}
    if mime == "application/pdf":
        return [
            prompt,
            {
                "type": "file",
                "file": {
                    "filename": file_name or "document.pdf",
                    "file_data": f"data:application/pdf;base64,{b64}",
                },
            },
        ]
    return [
        prompt,
        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
    ]


def _chat_url(settings: Settings) -> str:
    base = settings.ocr_api_base.rstrip("/")
    if (settings.ocr_provider or "").strip().lower() == "azure":
        return base
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _chat_headers(settings: Settings) -> dict[str, str]:
    key = settings.ocr_api_key.strip()
    if (settings.ocr_provider or "").strip().lower() == "azure":
        return {"api-key": key, "Content-Type": "application/json"}
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _message_text(data: dict[str, Any]) -> str:
    choices = data.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ValidationError(
            "The OCR service returned no choices. Try again, or use a clearer scan.",
            code="ocr_bad_response",
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    if not isinstance(message, dict):
        raise ValidationError("The OCR service returned an empty message.", code="ocr_bad_response")
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") in {"text", "output_text"}:
                parts.append(str(part.get("text") or ""))
            elif isinstance(part, str):
                parts.append(part)
        return "".join(parts)
    raise ValidationError("The OCR service returned an empty message.", code="ocr_bad_response")


def _provider_error(status_code: int, body: str) -> ValidationError:
    snippet = body[:240].strip() if body else ""
    if status_code in {401, 403}:
        return ValidationError(
            "The OCR API key was rejected. Check OCR_API_KEY.",
            code="ocr_auth",
        )
    if status_code == 429:
        return ValidationError(
            "The OCR service rate-limited this request. Wait a moment and try again.",
            code="ocr_rate_limited",
        )
    if status_code == 413:
        return ValidationError("That file is too large for the OCR service.", code="ocr_file_too_large")
    detail = "The OCR service could not read this file."
    if snippet:
        detail = f"{detail} ({snippet})"
    return ValidationError(detail, code="ocr_provider_error")


async def openai_complete(messages: list[dict[str, Any]], settings: Settings) -> str:
    url = _chat_url(settings)
    payload = {
        "model": settings.ocr_model,
        "temperature": 0,
        "max_tokens": 4096,
        "messages": messages,
    }
    timeout = httpx.Timeout(settings.ocr_timeout_seconds)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(url, headers=_chat_headers(settings), json=payload)
    except httpx.TimeoutException as exc:
        raise ValidationError(
            "The OCR service timed out. Try a smaller or clearer scan.",
            code="ocr_timeout",
        ) from exc
    except httpx.HTTPError as exc:
        raise ValidationError(
            "Could not reach the OCR service. Check OCR_API_BASE and network access.",
            code="ocr_unreachable",
        ) from exc
    if response.status_code >= 400:
        logger.warning(
            "ocr_provider_http_error",
            status_code=response.status_code,
            provider=settings.ocr_provider,
        )
        raise _provider_error(response.status_code, response.text)
    try:
        data = response.json()
    except json.JSONDecodeError as exc:
        raise ValidationError(
            "The OCR service returned a non-JSON body.", code="ocr_bad_response"
        ) from exc
    if not isinstance(data, dict):
        raise ValidationError("The OCR service returned unexpected JSON.", code="ocr_bad_response")
    return _message_text(data)


async def _load_catalog(db: AsyncSession, company_id: uuid.UUID) -> list[CatalogItem]:
    stmt = (
        select(Item.item_code, Item.item_name, Item.stock_uom, Item.standard_rate)
        .where(Item.company_id == company_id, Item.disabled.is_(False))
        .order_by(Item.item_code)
        .limit(CATALOG_LIMIT)
    )
    rows = (await db.execute(stmt)).all()
    return [
        CatalogItem(
            item_code=row.item_code,
            item_name=row.item_name,
            stock_uom=row.stock_uom,
            standard_rate=row.standard_rate if row.standard_rate is not None else Decimal("0"),
        )
        for row in rows
    ]


async def extract_document(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    content_base64: str,
    file_name: str,
    mime_type: str | None,
    settings: Settings | None = None,
    complete: ChatComplete | None = None,
) -> OcrExtractResponse:
    cfg = settings or get_settings()
    if not _is_configured(cfg):
        raise ValidationError(
            "OCR is not configured. Set OCR_API_KEY (OpenAI-compatible vision) on the API and retry.",
            code="ocr_not_configured",
        )
    raw, mime = decode_upload(
        content_base64, max_bytes=cfg.ocr_max_bytes, file_name=file_name, mime_type=mime_type
    )
    catalog = await _load_catalog(db, company_id)
    b64 = base64.b64encode(raw).decode("ascii")
    messages = [{"role": "user", "content": _user_content(mime, b64, file_name)}]
    completer = complete or openai_complete
    text = await completer(messages, cfg)
    payload = parse_model_json(text)
    lines = lines_from_payload(payload, catalog)
    unmatched = sum(1 for line in lines if not line.matched)
    warnings: list[str] = []
    if not lines:
        warnings.append("No line items were found. Try a clearer scan of the item table.")
    elif unmatched:
        warnings.append(
            f"{unmatched} line{'s' if unmatched != 1 else ''} did not match the item master "
            "— review the code before adding."
        )
    logger.info(
        "ocr_extracted",
        file_name=file_name,
        mime=mime,
        bytes=len(raw),
        line_count=len(lines),
        unmatched=unmatched,
        provider=cfg.ocr_provider,
        model=cfg.ocr_model,
    )
    return OcrExtractResponse(
        lines=lines,
        warnings=warnings,
        document_type=_optional_str(payload.get("document_type")),
        party_name=_optional_str(payload.get("party_name")),
        document_number=_optional_str(payload.get("document_number")),
        document_date=_optional_str(payload.get("document_date")),
        provider=cfg.ocr_provider,
        model=cfg.ocr_model,
    )


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
