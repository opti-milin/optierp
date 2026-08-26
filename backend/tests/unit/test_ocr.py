"""OCR extraction — parse, match, and provider-error mapping. No network, no DB."""

import base64
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.exceptions import ValidationError
from app.services import ocr
from app.services.ocr import CatalogItem


def _catalog() -> list[CatalogItem]:
    return [
        CatalogItem("FG-GEARBOX", "Gearbox Assembly", "Nos", Decimal("15000")),
        CatalogItem("RM-STEEL", "Steel Plate", "Kg", Decimal("80")),
        CatalogItem("SVC-INSTALL", "Installation Service", "Nos", Decimal("2500")),
    ]


def test_sniff_mime_from_magic_bytes():
    assert ocr.sniff_mime(b"%PDF-1.4 ...", "x.bin", None) == "application/pdf"
    assert ocr.sniff_mime(b"\xff\xd8\xff\xe0rest", "x.bin", None) == "image/jpeg"
    png = b"\x89PNG\r\n\x1a\n" + b"xxxx"
    assert ocr.sniff_mime(png, "x.bin", None) == "image/png"


def test_sniff_mime_falls_back_to_extension():
    assert ocr.sniff_mime(b"not-magic", "scan.WEBP", None) == "image/webp"


def test_sniff_mime_rejects_unknown():
    with pytest.raises(ValidationError) as exc:
        ocr.sniff_mime(b"hello", "notes.txt", "text/plain")
    assert exc.value.code == "ocr_unsupported_type"


def test_decode_upload_rejects_empty_and_bad_base64():
    with pytest.raises(ValidationError) as exc:
        ocr.decode_upload("@@@@", max_bytes=100, file_name="a.png", mime_type="image/png")
    assert exc.value.code == "ocr_bad_base64"

    with pytest.raises(ValidationError) as exc:
        ocr.decode_upload("", max_bytes=100, file_name="a.png", mime_type="image/png")
    assert exc.value.code in {"ocr_empty", "ocr_bad_base64"}


def test_parse_model_json_strips_fences_and_prose():
    fenced = '```json\n{"lines": [{"description": "Bolt", "qty": 2, "rate": 10}]}\n```'
    assert ocr.parse_model_json(fenced)["lines"][0]["description"] == "Bolt"

    prose = 'Here you go:\n{"document_type": "invoice", "lines": []}\nThanks'
    assert ocr.parse_model_json(prose)["document_type"] == "invoice"


def test_parse_model_json_rejects_garbage():
    with pytest.raises(ValidationError) as exc:
        ocr.parse_model_json("not json at all")
    assert exc.value.code == "ocr_bad_response"


def test_match_catalog_exact_code_and_fuzzy_name():
    catalog = _catalog()
    hit, conf = ocr.match_catalog_item("fg-gearbox", "whatever", catalog)
    assert hit is not None and hit.item_code == "FG-GEARBOX"
    assert conf == 95

    hit, conf = ocr.match_catalog_item(None, "gearbox assembly", catalog)
    assert hit is not None and hit.item_code == "FG-GEARBOX"
    assert conf == 90

    hit, conf = ocr.match_catalog_item(None, "gearbox assy", catalog)
    assert hit is not None and hit.item_code == "FG-GEARBOX"
    assert conf >= ocr.FUZZY_MIN

    hit, _ = ocr.match_catalog_item(None, "completely unrelated widget", catalog)
    assert hit is None


def test_lines_from_payload_matches_and_keeps_unmatched():
    payload = {
        "lines": [
            {"item_code": "FG-GEARBOX", "description": "Gearbox", "qty": 2, "rate": 14900},
            {"description": "Mystery widget", "qty": "3 Nos", "rate": "99.50"},
            {"description": "", "qty": 1},  # skipped — nothing to identify
        ]
    }
    lines = ocr.lines_from_payload(payload, _catalog())
    assert len(lines) == 2
    assert lines[0].matched is True
    assert lines[0].item_code == "FG-GEARBOX"
    assert lines[0].qty == Decimal("2")
    assert lines[0].rate == Decimal("14900")
    assert lines[1].matched is False
    assert lines[1].item_code == "Mystery widget"
    assert lines[1].qty == Decimal("3")
    assert lines[1].rate == Decimal("99.50")


def test_lines_from_payload_defaults_qty_and_uses_standard_rate():
    payload = {"lines": [{"description": "Gearbox Assembly"}]}
    lines = ocr.lines_from_payload(payload, _catalog())
    assert lines[0].qty == Decimal("1")
    assert lines[0].rate == Decimal("15000")
    assert lines[0].uom == "Nos"


def test_ocr_status_unconfigured_without_key():
    settings = SimpleNamespace(ocr_provider="openai", ocr_api_key="  ", ocr_model="gpt-4o-mini")
    status = ocr.ocr_status(settings)  # type: ignore[arg-type]
    assert status.configured is False


def test_ocr_status_disabled_provider():
    settings = SimpleNamespace(ocr_provider="disabled", ocr_api_key="sk-test", ocr_model="x")
    status = ocr.ocr_status(settings)  # type: ignore[arg-type]
    assert status.configured is False


@pytest.mark.asyncio
async def test_extract_document_uses_injected_completer():
    settings = SimpleNamespace(
        ocr_provider="openai",
        ocr_api_key="sk-test",
        ocr_model="gpt-4o-mini",
        ocr_max_bytes=1024,
        ocr_api_base="https://api.openai.com/v1",
        ocr_timeout_seconds=5,
    )

    async def fake_complete(messages, _settings):
        assert messages[0]["content"][1]["type"] == "image_url"
        return '{"document_type":"invoice","party_name":"Acme","lines":[{"description":"Gearbox Assembly","qty":1,"rate":100}]}'

    class FakeResult:
        def all(self):
            return [
                SimpleNamespace(
                    item_code="FG-GEARBOX",
                    item_name="Gearbox Assembly",
                    stock_uom="Nos",
                    standard_rate=Decimal("15000"),
                )
            ]

    class FakeDb:
        async def execute(self, _stmt):
            return FakeResult()

    jpeg = b"\xff\xd8\xff\xe0" + b"\x00" * 20

    result = await ocr.extract_document(
        FakeDb(),  # type: ignore[arg-type]
        company_id=uuid4(),
        content_base64=base64.b64encode(jpeg).decode("ascii"),
        file_name="scan.jpg",
        mime_type="image/jpeg",
        settings=settings,  # type: ignore[arg-type]
        complete=fake_complete,
    )
    assert result.party_name == "Acme"
    assert result.lines[0].item_code == "FG-GEARBOX"
    assert result.lines[0].matched is True
    assert result.warnings == []


@pytest.mark.asyncio
async def test_extract_document_not_configured():
    settings = SimpleNamespace(
        ocr_provider="openai",
        ocr_api_key="",
        ocr_model="gpt-4o-mini",
        ocr_max_bytes=1024,
    )
    with pytest.raises(ValidationError) as exc:
        await ocr.extract_document(
            None,  # type: ignore[arg-type]
            company_id=uuid4(),
            content_base64="QQ==",
            file_name="a.jpg",
            mime_type="image/jpeg",
            settings=settings,  # type: ignore[arg-type]
        )
    assert exc.value.code == "ocr_not_configured"


def test_provider_error_codes():
    assert ocr._provider_error(401, "").code == "ocr_auth"
    assert ocr._provider_error(429, "").code == "ocr_rate_limited"
    assert ocr._provider_error(500, "upstream boom").code == "ocr_provider_error"


def test_chat_url_and_headers():
    openai = SimpleNamespace(
        ocr_provider="openai",
        ocr_api_base="https://api.openai.com/v1",
        ocr_api_key="sk-x",
    )
    assert ocr._chat_url(openai) == "https://api.openai.com/v1/chat/completions"  # type: ignore[arg-type]
    assert ocr._chat_headers(openai)["Authorization"] == "Bearer sk-x"  # type: ignore[arg-type]

    azure = SimpleNamespace(
        ocr_provider="azure",
        ocr_api_base="https://ex.openai.azure.com/openai/deployments/x/chat/completions?api-version=2024-08-01-preview",
        ocr_api_key="az-key",
    )
    assert ocr._chat_url(azure) == azure.ocr_api_base  # type: ignore[arg-type]
    assert ocr._chat_headers(azure)["api-key"] == "az-key"  # type: ignore[arg-type]
