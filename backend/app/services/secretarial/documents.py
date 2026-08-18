"""Generating, versioning and rendering documents.

Two rules shape everything here.

**Nothing is stored as bytes.** A document row holds its inputs, the resolved block
tree, and the pack version that produced it; downloading re-renders. That is what makes
a five-year-old minute book still render under the wording that was in force when it was
signed (plan §2.5).

**Regeneration never overwrites.** It writes a new version that points at the old one
and marks itself current, so "what changed since the board saw it" is always answerable.
An issued document cannot be edited at all — only superseded.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialAppointment,
    SecretarialContentPack,
    SecretarialDocument,
    SecretarialEntity,
    SecretarialPerson,
)
from app.services.audit import log_audit, serialize_document
from app.services.pagination import paginate
from app.services.secretarial import blocks as block_lib
from app.services.secretarial import content as content_service
from app.services.secretarial import render_html
from app.services.secretarial.common import get_entity

_DOCTYPE = "Secretarial Document"


# --- Context assembly -------------------------------------------------------------


def _fmt_date(value: date | datetime | None) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%d %B %Y")
    return value.strftime("%d %B %Y")


async def build_context(
    db: AsyncSession, entity: SecretarialEntity, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """The variables a pack can reference.

    Master data resolves automatically; the guided form supplies the rest. Anything the
    pack asks for and does not get renders as ``[name]`` rather than a blank, so a
    missing value is visible instead of producing a sentence that reads as complete.
    """
    office = entity.registered_office if isinstance(entity.registered_office, dict) else {}
    office_line = ", ".join(
        str(office[k]) for k in ("line1", "line2", "city", "state", "pincode") if office.get(k)
    )

    directors = (
        await db.execute(
            select(SecretarialPerson.full_name, SecretarialPerson.din, SecretarialAppointment.designation)
            .join(SecretarialAppointment, SecretarialAppointment.person_id == SecretarialPerson.id)
            .where(
                SecretarialAppointment.entity_id == entity.id,
                SecretarialAppointment.ceased_on.is_(None),
                SecretarialAppointment.role_type.in_(("director", "designated_partner")),
            )
            .order_by(SecretarialAppointment.appointed_on)
        )
    ).all()

    context: dict[str, Any] = {
        "entity": {
            "name": entity.entity_name,
            "kind": entity.kind,
            "class": entity.entity_class,
            "cin": entity.cin or "",
            "llpin": entity.llpin or "",
            "registration_no": entity.registration_no or "",
            "pan": entity.pan or "",
            "registered_office": office_line,
            "email": entity.email or "",
            "phone": entity.phone or "",
            "incorporated_on": _fmt_date(entity.incorporated_on),
        },
        "directors": {
            "count": len(directors),
            "names": ", ".join(d.full_name for d in directors),
            "list": [
                {"name": d.full_name, "din": d.din or "", "designation": d.designation or ""}
                for d in directors
            ],
        },
        "today": _fmt_date(date.today()),
        # Bare aliases so simple packs can say {{ company_name }} rather than
        # {{ entity.name }} — the shorter form is what a non-programmer reaches for.
        "company_name": entity.entity_name,
        "registered_office": office_line,
        "cin": entity.cin or entity.llpin or "",
    }
    if extra:
        context.update(extra)
    return context


# --- Pack lookup ------------------------------------------------------------------


async def get_usable_pack(
    db: AsyncSession, company_id: uuid.UUID, code: str, *, on: date | None = None
) -> SecretarialContentPack:
    """The published pack for ``code``, newest effective version.

    Refuses drafts. A pack that has not been signed off by a named reviewer cannot
    produce a document — that gate is the whole reason the engine could ship before the
    statutory text was approved (plan §2.12).
    """
    rows = (
        (
            await db.execute(
                select(SecretarialContentPack)
                .where(
                    SecretarialContentPack.code == code,
                    or_(
                        SecretarialContentPack.company_id.is_(None),
                        SecretarialContentPack.company_id == company_id,
                    ),
                )
                .order_by(SecretarialContentPack.version.desc())
            )
        )
        .scalars()
        .all()
    )
    if not rows:
        raise NotFoundError(f"No content pack with code '{code}'")

    usable = [p for p in rows if content_service.is_usable(p, on)]
    if not usable:
        raise ValidationError(
            f"The '{rows[0].title}' template has not been reviewed and published yet, so it "
            f"cannot produce a document. A qualified professional must approve its wording first.",
            field="pack_code",
        )
    # Tenant packs win over the shipped catalogue at the same version.
    usable.sort(key=lambda p: (p.version, p.company_id is not None), reverse=True)
    return usable[0]


# --- Generation -------------------------------------------------------------------


async def generate(
    db: AsyncSession,
    user: CurrentUser,
    *,
    entity_id: uuid.UUID,
    pack_code: str,
    fragment: str,
    form_data: dict[str, Any] | None = None,
    extra_blocks: list[dict[str, Any]] | None = None,
    title: str | None = None,
    source_doctype: str | None = None,
    source_id: uuid.UUID | None = None,
    document_date: date | None = None,
    commit: bool = True,
) -> SecretarialDocument:
    """Render one fragment of a pack into a new version-1 document."""
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)
    pack = await get_usable_pack(db, user.company_id, pack_code)

    fragments = pack.fragments or {}
    if fragment not in fragments:
        available = ", ".join(sorted(fragments)) or "none"
        raise ValidationError(
            f"Template '{pack_code}' has no '{fragment}' section (has: {available})",
            field="fragment",
        )

    if entity.kind not in (pack.applies_to_kinds or [entity.kind]):
        raise ValidationError(
            f"'{pack.title}' does not apply to {'an LLP' if entity.kind == 'llp' else 'a company'}",
            field="pack_code",
        )

    tree = block_lib.validate_tree(fragments[fragment], where=f"{pack_code}.{fragment}")
    context = await build_context(db, entity, form_data)
    resolved = block_lib.resolve(tree, context)
    if extra_blocks:
        resolved = resolved + block_lib.validate_tree(extra_blocks, where="extra_blocks")

    doc = SecretarialDocument(
        company_id=user.company_id,
        entity_id=entity_id,
        title=title or pack.title,
        document_type=pack.event_type,
        fragment=fragment,
        pack_code=pack.code,
        pack_version=pack.version,
        pack_id=pack.id,
        generation_inputs={"form_data": form_data or {}, "extra_blocks": extra_blocks or []},
        resolved_blocks=resolved,
        compliance_meta=pack.compliance_meta,
        source_doctype=source_doctype,
        source_id=source_id,
        document_date=document_date or date.today(),
        status="draft",
        version=1,
        is_current=True,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(doc)
    await db.flush()
    # root_id is self on the first version, so the whole chain is one indexed lookup.
    doc.root_id = doc.id
    await db.flush()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=doc.id,
        action="INSERT",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"title": doc.title, "pack": f"{pack.code} v{pack.version}"},
    )
    if commit:
        await db.commit()
    return doc


async def regenerate(
    db: AsyncSession,
    document_id: uuid.UUID,
    user: CurrentUser,
    *,
    form_data: dict[str, Any] | None = None,
    change_summary: str | None = None,
) -> SecretarialDocument:
    """Re-render as a **new version**, superseding the old one.

    Picks up whatever has changed since: corrected master data, a newer published pack,
    or amended form values. The previous version stays exactly as it was, because
    somebody may have relied on it.
    """
    assert user.company_id is not None
    old = await get_document(db, document_id, user.company_id)
    entity = await get_entity(db, old.entity_id, user.company_id)

    root_id = old.root_id or old.id
    latest_version = (
        await db.execute(
            select(func.max(SecretarialDocument.version)).where(
                SecretarialDocument.root_id == root_id
            )
        )
    ).scalar_one() or old.version

    inputs = dict(old.generation_inputs or {})
    merged_form = dict(inputs.get("form_data") or {})
    if form_data:
        merged_form.update(form_data)

    pack = await get_usable_pack(db, user.company_id, old.pack_code) if old.pack_code else None
    if pack is None:
        raise ValidationError("This document was not produced from a template, so it cannot be regenerated")

    fragments = pack.fragments or {}
    if old.fragment not in fragments:
        raise ValidationError(
            f"The current version of '{pack.code}' no longer has a '{old.fragment}' section"
        )

    tree = block_lib.validate_tree(fragments[old.fragment], where=f"{pack.code}.{old.fragment}")
    context = await build_context(db, entity, merged_form)
    resolved = block_lib.resolve(tree, context)
    extra = inputs.get("extra_blocks") or []
    if extra:
        resolved = resolved + extra

    # Say what actually differs, so the version list is readable rather than a wall of
    # identical rows.
    summary = change_summary or _describe_change(old, pack, resolved)

    fresh = SecretarialDocument(
        company_id=user.company_id,
        entity_id=old.entity_id,
        title=old.title,
        document_type=old.document_type,
        fragment=old.fragment,
        pack_code=pack.code,
        pack_version=pack.version,
        pack_id=pack.id,
        generation_inputs={"form_data": merged_form, "extra_blocks": extra},
        resolved_blocks=resolved,
        compliance_meta=pack.compliance_meta,
        source_doctype=old.source_doctype,
        source_id=old.source_id,
        document_date=old.document_date,
        status="draft",
        root_id=root_id,
        version=int(latest_version) + 1,
        supersedes_id=old.id,
        is_current=True,
        change_summary=summary,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(fresh)

    # Exactly one current version per chain.
    await db.execute(
        update(SecretarialDocument)
        .where(SecretarialDocument.root_id == root_id, SecretarialDocument.id != fresh.id)
        .values(is_current=False)
    )
    await db.flush()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=fresh.id,
        action="AMEND",
        user_id=user.id,
        company_id=user.company_id,
        data_before={"version": old.version},
        data_after={"version": fresh.version, "change": summary},
    )
    await db.commit()
    return fresh


def _describe_change(
    old: SecretarialDocument, pack: SecretarialContentPack, resolved: list[dict[str, Any]]
) -> str:
    reasons: list[str] = []
    if old.pack_version != pack.version:
        reasons.append(f"template updated to v{pack.version}")
    if (old.resolved_blocks or []) != resolved:
        reasons.append("content changed")
    else:
        reasons.append("no visible change")
    return "; ".join(reasons)


# --- Lifecycle --------------------------------------------------------------------


async def get_document(
    db: AsyncSession, document_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialDocument:
    doc = await db.get(SecretarialDocument, document_id)
    if doc is None or doc.company_id != company_id:
        raise NotFoundError("Document not found")
    return doc


async def finalise(
    db: AsyncSession, document_id: uuid.UUID, user: CurrentUser, *, issue: bool = False
) -> SecretarialDocument:
    """Move draft → final, or final → issued.

    Issuing is the point of no return: an issued document can only be superseded by a
    new version, never edited, because somebody outside the company now has a copy.
    """
    assert user.company_id is not None
    doc = await get_document(db, document_id, user.company_id)
    before = serialize_document(doc)

    if doc.status == "superseded":
        raise ValidationError("This version has been superseded; work on the current one")
    if issue:
        if doc.status == "issued":
            return doc
        # Refuse to issue with holes in it — an unfilled placeholder in an issued
        # document is a factual error, not a formatting one. The check walks the whole
        # tree (list items, table cells, signature rows), not just top-level paragraphs.
        holes = block_lib.unfilled(doc.resolved_blocks or [])
        if holes:
            raise ValidationError(
                "This document still has unfilled values: "
                + ", ".join(sorted(holes))
                + ". Fill them in and regenerate before issuing."
            )
        doc.status = "issued"
        doc.issued_at = datetime.now(UTC)
    else:
        doc.status = "final"
    doc.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=_DOCTYPE,
        document_id=doc.id,
        action="SUBMIT",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(doc),
    )
    await db.commit()
    return doc


async def list_documents(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    document_type: str | None = None,
    status: str | None = None,
    source_doctype: str | None = None,
    source_id: uuid.UUID | None = None,
    current_only: bool = True,
    search: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialDocument], int]:
    stmt = select(SecretarialDocument).where(SecretarialDocument.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialDocument.entity_id == entity_id)
    if document_type:
        stmt = stmt.where(SecretarialDocument.document_type == document_type)
    if status:
        stmt = stmt.where(SecretarialDocument.status == status)
    if source_doctype:
        stmt = stmt.where(SecretarialDocument.source_doctype == source_doctype)
    if source_id:
        stmt = stmt.where(SecretarialDocument.source_id == source_id)
    if current_only:
        stmt = stmt.where(SecretarialDocument.is_current.is_(True))
    if search:
        stmt = stmt.where(SecretarialDocument.title.ilike(f"%{search.strip()}%"))
    return await paginate(db, stmt.order_by(SecretarialDocument.creation.desc()), page, page_size)


async def version_history(
    db: AsyncSession, document_id: uuid.UUID, company_id: uuid.UUID
) -> list[SecretarialDocument]:
    doc = await get_document(db, document_id, company_id)
    root_id = doc.root_id or doc.id
    return list(
        (
            await db.execute(
                select(SecretarialDocument)
                .where(
                    SecretarialDocument.company_id == company_id,
                    SecretarialDocument.root_id == root_id,
                )
                .order_by(SecretarialDocument.version.desc())
            )
        )
        .scalars()
        .all()
    )


# --- Rendering --------------------------------------------------------------------


async def render(
    db: AsyncSession, document_id: uuid.UUID, company_id: uuid.UUID, fmt: str = "pdf"
) -> tuple[bytes | str, str, str]:
    """Re-render from stored blocks. Returns (content, filename, media type)."""
    doc = await get_document(db, document_id, company_id)
    entity = await get_entity(db, doc.entity_id, company_id)

    # Anything not yet issued is watermarked, so a working draft can never be mistaken
    # for the real thing once it is on paper.
    watermark = None if doc.status == "issued" else doc.status.upper()

    kwargs = {
        "entity": entity,
        "title": doc.title,
        "blocks": doc.resolved_blocks or [],
        "version": doc.version,
        "pack_code": doc.pack_code,
        "pack_version": doc.pack_version,
        "watermark": watermark,
    }
    slug = "".join(c if c.isalnum() or c in "-_" else "-" for c in doc.title)[:60].strip("-")
    stamp = (doc.document_date or date.today()).isoformat()

    if fmt == "html":
        return render_html.render_html(**kwargs), f"{slug}-{stamp}.html", "text/html"
    return render_html.render_pdf(**kwargs), f"{slug}-{stamp}.pdf", "application/pdf"
