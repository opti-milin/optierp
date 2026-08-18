"""Block tree → HTML → PDF.

The first of two renderers over the same tree (plan §2.6). A DOCX renderer is the
planned second; it will consume exactly these blocks, which is why nothing here leaks
HTML back into the pack format.

Everything is escaped. Pack text is authored by humans and event forms are filled in by
users, so a stray ``<`` must render as a ``<`` and never as markup.
"""

from __future__ import annotations

from html import escape
from typing import Any

from markupsafe import Markup

from app.core.pdf import html_to_pdf, render_print_format

TEMPLATE = "documents/secretarial_document.html"


def _text(value: Any) -> str:
    return escape(str(value if value is not None else ""))


def render_block(block: dict[str, Any]) -> str:
    """One block to an HTML fragment. Unknown types render nothing rather than raising:
    a block tree is validated on load, so anything odd here is already a bug upstream
    and swallowing it beats failing a document the user is waiting for.

    Returns ``Markup`` because the print environment autoescapes: every piece of text
    inside has already been escaped by hand, so the assembled fragment is safe, but
    Jinja would otherwise escape the tags too and print raw HTML onto the page."""
    return Markup(_render_block(block))  # noqa: S704 — every interpolation is escaped below


def _render_block(block: dict[str, Any]) -> str:
    kind = block.get("type")

    if kind == "heading":
        level = min(max(int(block.get("level", 2)), 1), 4)
        tag = f"h{min(level + 1, 4)}"  # h1 is reserved for the document title
        return f"<{tag}>{_text(block.get('text'))}</{tag}>"

    if kind == "paragraph":
        return f"<p>{_text(block.get('text'))}</p>"

    if kind == "quote":
        return f'<div class="resolution">{_text(block.get("text"))}</div>'

    if kind == "list":
        tag = "ol" if block.get("ordered") else "ul"
        items = "".join(f"<li>{_text(i)}</li>" for i in block.get("items", []))
        return f"<{tag}>{items}</{tag}>"

    if kind == "table":
        head = "".join(f"<th>{_text(c)}</th>" for c in block.get("columns", []))
        body = "".join(
            "<tr>" + "".join(f"<td>{_text(c)}</td>" for c in row) + "</tr>"
            for row in block.get("rows", [])
        )
        return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"

    if kind == "key_values":
        rows = "".join(
            f'<tr><td class="k">{_text(pair[0])}</td><td>{_text(pair[1] if len(pair) > 1 else "")}</td></tr>'
            for pair in block.get("pairs", [])
            if isinstance(pair, (list, tuple)) and pair
        )
        return f'<table class="kv">{rows}</table>'

    if kind == "signature_grid":
        cells = []
        for sig in block.get("signatories", []):
            name = _text(sig.get("name"))
            designation = _text(sig.get("designation") or "")
            din = sig.get("din")
            meta = designation + (f" · DIN {_text(din)}" if din else "")
            cells.append(
                f'<td><div class="sig-line"><span class="sig-name">{name}</span>'
                f'<br /><span class="sig-meta">{meta}</span></div></td>'
            )
        # Two per row keeps signature lines wide enough to actually sign on.
        rows = "".join(
            "<tr>" + "".join(cells[i : i + 2]) + "</tr>" for i in range(0, len(cells), 2)
        )
        return f'<table class="signatures">{rows}</table>'

    if kind == "divider":
        return '<hr class="divider" />'
    if kind == "spacer":
        return '<div class="spacer"></div>'
    if kind == "page_break":
        return '<div class="page-break"></div>'

    return ""


def _entity_context(entity: Any) -> dict[str, Any]:
    office = entity.registered_office or {}
    parts = [
        office.get(k)
        for k in ("line1", "line2", "city", "state", "pincode")
        if isinstance(office, dict) and office.get(k)
    ]
    return {
        "entity_name": entity.entity_name,
        "kind": entity.kind,
        "registration_no": entity.registration_no,
        "registered_office_line": ", ".join(str(p) for p in parts) if parts else None,
        "email": entity.email,
        "phone": entity.phone,
    }


def render_html(
    *,
    entity: Any,
    title: str,
    blocks: list[dict[str, Any]],
    version: int | None = None,
    pack_code: str | None = None,
    pack_version: int | None = None,
    watermark: str | None = None,
) -> str:
    """Assemble the full page. ``watermark`` marks anything not yet issued."""
    return render_print_format(
        TEMPLATE,
        {
            "entity": _entity_context(entity),
            "document": {
                "title": title,
                "version": version,
                "pack_code": pack_code,
                "pack_version": pack_version,
            },
            "blocks": blocks,
            "render_block": render_block,
            "watermark": watermark,
            # `generated_on` is injected by render_print_format itself — passing it
            # here too is a duplicate-keyword TypeError, not an override.
        },
    )


def render_pdf(**kwargs: Any) -> bytes:
    """Same page as PDF. Raises ``PDFEngineUnavailable`` (501) if WeasyPrint is absent,
    which is the existing convention rather than a silent fallback to HTML."""
    return html_to_pdf(render_html(**kwargs))


__all__ = ["render_block", "render_html", "render_pdf"]
