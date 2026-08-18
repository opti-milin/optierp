"""The publish gate (plan §2.12).

One rule, enforced in one place: **only ``published`` content reaches a tenant's
calendar or a generated document.** Everything else — draft rules seeded so the pipeline
is testable, half-reviewed wording, retired versions after an amendment — is visible to
whoever is authoring it and invisible to the machinery that produces legal effect.

That is what lets engineering ship the whole engine before a Company Secretary has
approved a single word, without any risk that unreviewed text lands in a client's file.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from typing import TypeVar

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import SecretarialComplianceRule, SecretarialContentPack
from app.schemas.secretarial import ComplianceRuleReviewIn
from app.services.audit import log_audit, serialize_document

T = TypeVar("T", SecretarialComplianceRule, SecretarialContentPack)

PUBLISHED = "published"

# draft → reviewed → approved → published, with retire available from anywhere live.
_ALLOWED_TRANSITIONS: dict[str, set[str]] = {
    "draft": {"reviewed", "retired"},
    "reviewed": {"approved", "draft", "retired"},
    "approved": {"published", "reviewed", "retired"},
    "published": {"retired"},
    "retired": {"draft"},
}


def is_usable(row: SecretarialComplianceRule | SecretarialContentPack, on: date | None = None) -> bool:
    """Whether this version may be used for real work on ``on`` (default: today)."""
    if row.review_status != PUBLISHED or row.superseded_by_id is not None:
        return False
    if getattr(row, "is_active", True) is False:
        return False
    if row.effective_from and (on or date.today()) < row.effective_from:
        return False
    return True


async def published_rules(
    db: AsyncSession, company_id: uuid.UUID, *, on: date | None = None
) -> list[SecretarialComplianceRule]:
    """Rules the calendar may act on: the system catalogue plus this tenant's own."""
    stmt = select(SecretarialComplianceRule).where(
        or_(
            SecretarialComplianceRule.company_id.is_(None),
            SecretarialComplianceRule.company_id == company_id,
        ),
        SecretarialComplianceRule.review_status == PUBLISHED,
        SecretarialComplianceRule.superseded_by_id.is_(None),
        SecretarialComplianceRule.is_active.is_(True),
    )
    rows = list((await db.execute(stmt)).scalars().all())
    return [r for r in rows if is_usable(r, on)]


async def list_rules(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    review_status: str | None = None,
    include_unpublished: bool = True,
) -> list[SecretarialComplianceRule]:
    """Authoring view — shows drafts, which ``published_rules`` deliberately hides."""
    stmt = select(SecretarialComplianceRule).where(
        or_(
            SecretarialComplianceRule.company_id.is_(None),
            SecretarialComplianceRule.company_id == company_id,
        )
    )
    if review_status:
        stmt = stmt.where(SecretarialComplianceRule.review_status == review_status)
    elif not include_unpublished:
        stmt = stmt.where(SecretarialComplianceRule.review_status == PUBLISHED)
    stmt = stmt.order_by(SecretarialComplianceRule.code, SecretarialComplianceRule.version)
    return list((await db.execute(stmt)).scalars().all())


async def review_status_counts(db: AsyncSession, company_id: uuid.UUID) -> dict[str, int]:
    """How far the legal-content workstream has got — surfaced on the workspace so
    'the code works' is never mistaken for 'the module is ready'."""
    from sqlalchemy import func

    rows = (
        await db.execute(
            select(SecretarialComplianceRule.review_status, func.count())
            .where(
                or_(
                    SecretarialComplianceRule.company_id.is_(None),
                    SecretarialComplianceRule.company_id == company_id,
                )
            )
            .group_by(SecretarialComplianceRule.review_status)
        )
    ).all()
    return {status: int(count) for status, count in rows}


async def _advance(
    db: AsyncSession,
    row: T,
    payload: ComplianceRuleReviewIn,
    user: CurrentUser,
    doctype: str,
) -> T:
    target = payload.review_status
    if target != row.review_status and target not in _ALLOWED_TRANSITIONS.get(row.review_status, set()):
        raise ValidationError(
            f"Cannot move {doctype.lower()} from '{row.review_status}' to '{target}'. "
            f"The path is draft → reviewed → approved → published.",
            field="review_status",
        )

    reviewer = payload.reviewer_name or row.reviewer_name
    reviewed_on = payload.reviewed_on or row.reviewed_on
    if target == PUBLISHED and not (reviewer and reviewed_on):
        raise ValidationError(
            "Publishing requires the name of the person who reviewed the wording and "
            "the date they did it — statutory text is never published anonymously.",
            field="reviewer_name",
        )

    before = serialize_document(row)
    row.review_status = target
    if payload.reviewer_name:
        row.reviewer_name = payload.reviewer_name
    if payload.reviewer_credential:
        row.reviewer_credential = payload.reviewer_credential
    if reviewed_on:
        row.reviewed_on = reviewed_on
    if payload.effective_from:
        row.effective_from = payload.effective_from
    if target == PUBLISHED:
        row.reviewed_by = user.id
        row.published_at = datetime.now(UTC)
        if not row.effective_from:
            row.effective_from = date.today()
    row.modified_by = user.id

    await db.flush()
    await log_audit(
        db,
        doctype=doctype,
        document_id=row.id,
        action="UPDATE",
        user_id=user.id,
        company_id=user.company_id,
        data_before=before,
        data_after=serialize_document(row),
    )
    await db.commit()
    return row


async def review_rule(
    db: AsyncSession, rule_id: uuid.UUID, payload: ComplianceRuleReviewIn, user: CurrentUser
) -> SecretarialComplianceRule:
    rule = await db.get(SecretarialComplianceRule, rule_id)
    if rule is None or (rule.company_id is not None and rule.company_id != user.company_id):
        raise NotFoundError("Compliance rule not found")
    return await _advance(db, rule, payload, user, "Secretarial Compliance Rule")


async def review_pack(
    db: AsyncSession, pack_id: uuid.UUID, payload: ComplianceRuleReviewIn, user: CurrentUser
) -> SecretarialContentPack:
    pack = await db.get(SecretarialContentPack, pack_id)
    if pack is None or (pack.company_id is not None and pack.company_id != user.company_id):
        raise NotFoundError("Content pack not found")
    return await _advance(db, pack, payload, user, "Secretarial Content Pack")
