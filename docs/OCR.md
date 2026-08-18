# OCR line-item extraction

The transaction **Import** dialog (Sales Invoice, Purchase Invoice, orders, delivery notes, receipts) can read line items from a **photo or PDF** of an invoice or purchase order.

Nothing is posted automatically. You review the extracted rows, fix any unmatched item codes, then add them to the document grid.

## Setup

The API calls an **OpenAI-compatible vision** endpoint. Set these on the backend (env only — never commit a real key):

| Variable | Default | Meaning |
|---|---|---|
| `OCR_PROVIDER` | `openai` | `openai`, `azure`, or `disabled` |
| `OCR_API_KEY` | *(empty)* | Required. Leave empty to keep Extract disabled. |
| `OCR_API_BASE` | `https://api.openai.com/v1` | For Azure, use the **full** chat-completions URL (including `api-version`). |
| `OCR_MODEL` | `gpt-4o-mini` | Any vision-capable chat model your provider accepts. |

**Docker:** put `OCR_API_KEY=...` in a **repo-root** `.env` (Compose substitutes it into the backend service), then restart:

```powershell
docker compose up -d --build backend
```

**Local uvicorn:** copy `backend/.env.example` → `backend/.env` and fill `OCR_API_KEY`.

Supported files: JPEG, PNG, WebP, GIF, PDF, up to 10 MB. Scanned PDFs need a provider that accepts PDF file input (OpenAI vision does); if yours does not, upload a page photo instead.

## What the API does

1. `GET /api/v1/ocr/status` — whether a key is configured (no provider call).
2. `POST /api/v1/ocr/extract` — `{ file_name, content_base64, mime_type }` → extracted lines plus a match against this company's item master (exact item code, exact name, then fuzzy). Unmatched lines are still returned so you can type the code.

The file is not stored. Adding rows to the document is the same path as CSV import.

## Errors you may see

| Code | Meaning |
|---|---|
| `ocr_not_configured` | `OCR_API_KEY` is empty or `OCR_PROVIDER=disabled` |
| `ocr_unsupported_type` | Not an image or PDF |
| `ocr_file_too_large` | Over 10 MB |
| `ocr_auth` | Provider rejected the key |
| `ocr_timeout` / `ocr_unreachable` | Network / slow provider |
| `ocr_bad_response` | Model did not return readable JSON — try a clearer scan |
