"""OCR line-item extraction (Data Entry).

Upload a scanned invoice/PO → OpenAI-compatible vision API → review rows → append to the grid.
"""

# OCR line-item extraction

Wire the Data Entry **OCR** tab to a real extract API.

## Behaviour

1. `POST /api/v1/ocr/extract` accepts a JPEG/PNG/WebP/GIF/PDF (base64, same pattern as Tally upload).
2. Service calls an **OpenAI-compatible** vision chat-completions endpoint (`OCR_API_BASE` + `/chat/completions`, or the full Azure URL).
3. Parsed lines are matched against the tenant item master (exact code, exact name, then fuzzy).
4. Nothing is persisted. The UI shows a review table; the user adds selected rows to the document grid.

`GET /api/v1/ocr/status` reports whether a key is configured (no provider call).

## Config (env only — no secrets in code)

| Variable | Default | Notes |
|---|---|---|
| `OCR_PROVIDER` | `openai` | `openai` \| `azure` \| `disabled` |
| `OCR_API_KEY` | empty | Required. Empty → `ocr_not_configured` (Extract disabled in UI). |
| `OCR_API_BASE` | `https://api.openai.com/v1` | For Azure, set the **full** chat-completions URL including `api-version`. |
| `OCR_MODEL` | `gpt-4o-mini` | Any vision-capable chat model the provider accepts. |

Docker Compose reads these from the **repo-root** `.env` (or the shell) and passes them into the backend container. Local uvicorn reads `backend/.env`.

Restart the backend after changing the key.

## Manual check

1. Set `OCR_API_KEY` and restart the API.
2. Open a draft Sales Invoice → **Import** → **OCR**.
3. Upload a photo of an invoice item table → **Extract**.
4. Review matched/unmatched rows → **Add selected to document**.
