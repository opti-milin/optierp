"""s.186 loans and investments, and the s.123 dividend check.

Both are the same shape and both are the module's real argument for living inside an ERP:
they are statutory questions that cannot be answered without the books. A standalone
secretarial product asks the client "what are your free reserves?" and believes the
answer. Here the ledger is one join away, and where it is not — a managed client whose
books are elsewhere — the typed figures are used and the answer says so.

The discipline that matters in both functions: **a missing figure yields `unknown`, never
`ok`.** Telling a board they are within the s.186 ceiling because a number was absent, or
that a dividend is covered when accumulated losses were never entered, is worse than
saying nothing. An unlawful dividend under s.123 is recoverable from the directors
personally; the cost of a false "fine" is not symmetric with the cost of a false "check
this".
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.secretarial import (
    SecretarialEntity,
    SecretarialMeeting,
    SecretarialS186Entry,
    SecretarialS186Limit,
)
from app.services.audit import log_audit
from app.services.pagination import paginate
from app.services.secretarial import financial_facts as facts_service
from app.services.secretarial.common import current_fy, get_entity

ENTRY_DOCTYPE = "Secretarial s.186 Entry"
LIMIT_DOCTYPE = "Secretarial s.186 Entry"

# s.186(11) — the ceiling does not bite on a loan or investment made by a holding company
# to its wholly-owned subsidiary, or on a guarantee given for it.
EXEMPT_RELATIONS = ("wholly_owned_subsidiary", "wholly-owned subsidiary", "wos")


def _d(value: Any) -> Decimal | None:
    if value is None:
        return None
    return Decimal(str(value))


def compute_limit(
    *,
    paid_up_capital: Decimal | None,
    free_reserves: Decimal | None,
    securities_premium: Decimal | None,
) -> dict[str, Any]:
    """s.186(2) — the higher of the two statutory ceilings, and what is missing.

    > no company shall … give any loan … exceeding sixty per cent of its paid-up share
    > capital, free reserves and securities premium account or one hundred per cent of
    > its free reserves and securities premium account, whichever is more

    Absent figures are named rather than treated as zero: zero free reserves and unknown
    free reserves give very different ceilings, and only one of them is safe to act on.
    """
    gaps: list[str] = []
    if paid_up_capital is None:
        gaps.append("Paid-up share capital is not recorded for this year")
    if free_reserves is None:
        gaps.append("Free reserves are not recorded for this year")
    if securities_premium is None:
        # Many private companies genuinely have none, so this is the one figure it is
        # fair to read as nil — but say so, rather than let it pass unremarked.
        gaps.append("Securities premium is not recorded; treated as nil")

    premium = securities_premium or Decimal(0)

    sixty = None
    hundred = None
    if paid_up_capital is not None and free_reserves is not None:
        sixty = (paid_up_capital + free_reserves + premium) * Decimal("0.60")
    if free_reserves is not None:
        hundred = free_reserves + premium

    candidates = [c for c in (sixty, hundred) if c is not None]
    effective = max(candidates) if candidates else None

    return {
        "paid_up_capital": paid_up_capital,
        "free_reserves": free_reserves,
        "securities_premium": securities_premium,
        "limit_sixty_pct": sixty,
        "limit_hundred_pct": hundred,
        "effective_limit": effective,
        "gaps": gaps,
    }


async def refresh_limit(
    db: AsyncSession, entity: SecretarialEntity, fy: str, *, user: CurrentUser | None = None
) -> SecretarialS186Limit:
    """Recompute this year's ceiling from whatever source the facts come from.

    Stored, not recomputed on read, for the reason the SS-1 notice dates are stored: the
    ceiling that governs a loan made in October is the one that existed in October. A
    refresh writes today's figures; it does not retroactively change what an entry made
    six months ago was judged against — those verdicts are frozen on the entry itself.
    """
    facts = await facts_service.get_facts(db, entity, fy)
    computed = compute_limit(
        paid_up_capital=_d(facts.get("paid_up_capital")),
        free_reserves=_d(facts.get("free_reserves")),
        securities_premium=_d(facts.get("securities_premium")),
    )

    row = await db.scalar(
        select(SecretarialS186Limit).where(
            SecretarialS186Limit.entity_id == entity.id, SecretarialS186Limit.fy == fy
        )
    )
    if row is None:
        row = SecretarialS186Limit(company_id=entity.company_id, entity_id=entity.id, fy=fy)
        db.add(row)

    for field in (
        "paid_up_capital",
        "free_reserves",
        "securities_premium",
        "limit_sixty_pct",
        "limit_hundred_pct",
        "effective_limit",
    ):
        setattr(row, field, computed[field])
    row.gaps = computed["gaps"]
    row.source = "ledger" if facts.get("source") == "auto" else "manual"
    row.computed_on = date.today()
    if user:
        row.modified_by = user.id
    await db.flush()
    return row


async def set_limit_overrides(
    db: AsyncSession, user: CurrentUser, entity_id: uuid.UUID, payload: dict[str, Any]
) -> SecretarialS186Limit:
    """Type the figures in, or attach the special resolution that lifts the cap.

    A managed client's free reserves have to be entered somewhere, and s.186(3) lets a
    special resolution authorise borrowing beyond the ceiling entirely — that resolution
    is a real meeting in this system, so it is linked rather than ticked.
    """
    assert user.company_id is not None
    entity = await get_entity(db, entity_id, user.company_id)
    fy = payload["fy"]

    row = await db.scalar(
        select(SecretarialS186Limit).where(
            SecretarialS186Limit.entity_id == entity_id, SecretarialS186Limit.fy == fy
        )
    )
    if row is None:
        row = SecretarialS186Limit(company_id=user.company_id, entity_id=entity_id, fy=fy)
        db.add(row)

    meeting_id = payload.get("special_resolution_meeting_id")
    if meeting_id:
        meeting = await db.get(SecretarialMeeting, meeting_id)
        if meeting is None or meeting.company_id != user.company_id:
            raise NotFoundError("Meeting not found")
        if meeting.meeting_type not in ("agm", "egm"):
            raise ValidationError(
                "A special resolution under s.186(3) is passed by the members, so it "
                f"belongs to a general meeting — that one is a {meeting.meeting_type} meeting."
            )
        row.special_resolution_on = payload.get("special_resolution_on") or (
            meeting.held_at.date() if meeting.held_at else None
        )
    row.special_resolution_meeting_id = meeting_id

    for field in ("paid_up_capital", "free_reserves", "securities_premium", "notes"):
        if payload.get(field) is not None:
            setattr(row, field, payload[field])

    computed = compute_limit(
        paid_up_capital=row.paid_up_capital,
        free_reserves=row.free_reserves,
        securities_premium=row.securities_premium,
    )
    row.limit_sixty_pct = computed["limit_sixty_pct"]
    row.limit_hundred_pct = computed["limit_hundred_pct"]
    row.effective_limit = computed["effective_limit"]
    row.gaps = computed["gaps"]
    row.source = "manual"
    row.computed_on = date.today()
    row.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=LIMIT_DOCTYPE,
        document_id=row.id,
        action="set_limit",
        user_id=user.id,
        company_id=user.company_id,
        data_after={"fy": fy, "effective_limit": str(row.effective_limit)},
    )
    _ = entity
    return row


async def current_exposure(
    db: AsyncSession, entity_id: uuid.UUID, *, exclude_entry_id: uuid.UUID | None = None
) -> Decimal:
    """What the register currently adds up to.

    Only outstanding entries count, and exempt counterparties (s.186(11)) are left out —
    a loan to a wholly-owned subsidiary consumes no headroom, so counting it would report
    a breach that does not exist.
    """
    rows = (
        await db.execute(
            select(SecretarialS186Entry.amount, SecretarialS186Entry.party_relation).where(
                SecretarialS186Entry.entity_id == entity_id,
                SecretarialS186Entry.status == "outstanding",
                *([SecretarialS186Entry.id != exclude_entry_id] if exclude_entry_id else []),
            )
        )
    ).all()
    return sum(
        (
            Decimal(amount)
            for amount, relation in rows
            if (relation or "").strip().lower() not in EXEMPT_RELATIONS
        ),
        Decimal(0),
    )


async def limit_status(
    db: AsyncSession, entity: SecretarialEntity, fy: str, *, refresh: bool = False
) -> dict[str, Any]:
    """The stored ceiling plus what the register currently uses of it."""
    row = await db.scalar(
        select(SecretarialS186Limit).where(
            SecretarialS186Limit.entity_id == entity.id, SecretarialS186Limit.fy == fy
        )
    )
    if row is None or refresh:
        row = await refresh_limit(db, entity, fy)

    exposure = await current_exposure(db, entity.id)
    limit = row.effective_limit

    if row.special_resolution_meeting_id:
        verdict = "lifted"
        headroom = None
    elif limit is None:
        verdict = "unknown"
        headroom = None
    elif exposure > limit:
        verdict = "exceeded"
        headroom = limit - exposure
    else:
        verdict = "within"
        headroom = limit - exposure

    return {
        "id": row.id,
        "entity_id": entity.id,
        "fy": fy,
        "paid_up_capital": row.paid_up_capital,
        "free_reserves": row.free_reserves,
        "securities_premium": row.securities_premium,
        "limit_sixty_pct": row.limit_sixty_pct,
        "limit_hundred_pct": row.limit_hundred_pct,
        "effective_limit": limit,
        "special_resolution_meeting_id": row.special_resolution_meeting_id,
        "special_resolution_on": row.special_resolution_on,
        "source": row.source,
        "computed_on": row.computed_on,
        "gaps": row.gaps,
        "notes": row.notes,
        "exposure": exposure,
        "headroom": headroom,
        "verdict": verdict,
    }


# --- The register -----------------------------------------------------------------


async def get_entry(
    db: AsyncSession, entry_id: uuid.UUID, company_id: uuid.UUID
) -> SecretarialS186Entry:
    row = await db.get(SecretarialS186Entry, entry_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("s.186 register entry not found")
    return row


async def create_entry(
    db: AsyncSession, user: CurrentUser, payload: dict[str, Any]
) -> SecretarialS186Entry:
    """Add a loan, guarantee, security or investment — checked against the ceiling.

    Above the ceiling without a special resolution the entry is refused, and the message
    carries the numbers: "this would take the total to X against a limit of Y" is
    actionable, "422 limit exceeded" is not. The verdict is then frozen onto the entry, so
    a later refresh of the figures does not rewrite what the board was told at the time.
    """
    assert user.company_id is not None
    entity = await get_entity(db, payload["entity_id"], user.company_id)

    fy = payload.get("fy") or current_fy(payload["made_on"], entity.fy_end_mmdd)
    status = await limit_status(db, entity, fy)

    amount = Decimal(payload["amount"])
    relation = (payload.get("party_relation") or "").strip().lower()
    exempt = relation in EXEMPT_RELATIONS
    lifted = bool(status["special_resolution_meeting_id"] or payload.get("special_resolution_meeting_id"))

    check: dict[str, Any] = {
        "fy": fy,
        "limit": str(status["effective_limit"]) if status["effective_limit"] is not None else None,
        "exposure_before": str(status["exposure"]),
        "exposure_after": str(Decimal(status["exposure"]) + (Decimal(0) if exempt else amount)),
        "exempt": exempt,
        "special_resolution": lifted,
        "source": status["source"],
    }

    if exempt:
        check["verdict"] = "exempt"
    elif lifted:
        check["verdict"] = "lifted"
    elif status["effective_limit"] is None:
        check["verdict"] = "unknown"
        check["reasons"] = status["gaps"]
    else:
        after = Decimal(status["exposure"]) + amount
        limit = Decimal(status["effective_limit"])
        if after > limit:
            raise ValidationError(
                f"s.186(2): this would take the company's loans, guarantees and "
                f"investments to {after:,.2f} against a ceiling of {limit:,.2f}. "
                "Pass a special resolution under s.186(3) and link it on the limits "
                "screen, or reduce the amount."
            )
        check["verdict"] = "within"

    entry = SecretarialS186Entry(
        company_id=user.company_id,
        status="outstanding",
        limit_check=check,
        owner=user.id,
        modified_by=user.id,
        **{**payload, "fy": fy},
    )
    db.add(entry)
    await db.flush()

    await log_audit(
        db,
        doctype=ENTRY_DOCTYPE,
        document_id=entry.id,
        action="create",
        user_id=user.id,
        company_id=user.company_id,
        data_after={
            "entry_type": entry.entry_type,
            "party": entry.party_name,
            "amount": str(entry.amount),
            "limit_check": check,
        },
    )
    return entry


async def update_entry(
    db: AsyncSession, user: CurrentUser, entry_id: uuid.UUID, changes: dict[str, Any]
) -> SecretarialS186Entry:
    assert user.company_id is not None
    entry = await get_entry(db, entry_id, user.company_id)

    if changes.get("status") == "repaid" and not (changes.get("repaid_on") or entry.repaid_on):
        raise ValidationError("When was it repaid?", field="repaid_on")
    if changes.get("repaid_on") and not changes.get("status"):
        changes["status"] = "repaid"

    for field, value in changes.items():
        if hasattr(entry, field):
            setattr(entry, field, value)
    entry.modified_by = user.id
    await db.flush()

    await log_audit(
        db,
        doctype=ENTRY_DOCTYPE,
        document_id=entry.id,
        action="update",
        user_id=user.id,
        company_id=user.company_id,
        data_after={k: str(v) for k, v in changes.items()},
    )
    return entry


async def list_entries(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_id: uuid.UUID | None = None,
    entry_type: str | None = None,
    status: str | None = None,
    fy: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> tuple[list[SecretarialS186Entry], int]:
    stmt = select(SecretarialS186Entry).where(SecretarialS186Entry.company_id == company_id)
    if entity_id:
        stmt = stmt.where(SecretarialS186Entry.entity_id == entity_id)
    if entry_type:
        stmt = stmt.where(SecretarialS186Entry.entry_type == entry_type)
    if status:
        stmt = stmt.where(SecretarialS186Entry.status == status)
    if fy:
        stmt = stmt.where(SecretarialS186Entry.fy == fy)
    stmt = stmt.order_by(SecretarialS186Entry.made_on.desc())
    return await paginate(db, stmt, page, page_size)


# --- s.123 dividend ---------------------------------------------------------------


async def dividend_check(
    db: AsyncSession, entity: SecretarialEntity, fy: str, *, proposed: Decimal | None = None
) -> dict[str, Any]:
    """Is there enough distributable profit to declare this dividend?

    s.123(1): dividend is declared out of the profits of the year after providing for
    depreciation, or out of accumulated profits after the same, or both. Accumulated
    losses come off first.

    What this does *not* do is claim to be the whole test. Transfer to reserves, the
    s.123(3) interim-dividend cap, and unpaid-dividend-account compliance are not modelled
    here, and the reasons list says so — a check that quietly implied it had covered
    everything would be the more dangerous artefact.
    """
    if not fy:
        fy = current_fy(date.today(), entity.fy_end_mmdd)

    facts = await facts_service.get_facts(db, entity, fy)
    reasons: list[str] = []

    current_profit = _d(facts.get("net_profit"))
    # Accumulated profit is not one of the eight canonical facts, so it comes from the
    # manual row's free reserves where that is all there is. Named, not assumed.
    accumulated = _d(facts.get("free_reserves"))
    losses = None

    if current_profit is None:
        reasons.append("Profit for the year is not recorded, so the s.123 test cannot be run")
    if accumulated is None:
        reasons.append("Accumulated profits (free reserves) are not recorded")

    distributable = None
    if current_profit is not None or accumulated is not None:
        distributable = (current_profit or Decimal(0)) + (accumulated or Decimal(0)) - (
            losses or Decimal(0)
        )

    if current_profit is None or accumulated is None:
        verdict = "unknown"
    elif proposed is None:
        verdict = "unknown"
        reasons.append("No dividend amount proposed yet")
    elif Decimal(proposed) > (distributable or Decimal(0)):
        verdict = "exceeded"
        reasons.append(
            f"Proposed {Decimal(proposed):,.2f} against distributable profits of "
            f"{(distributable or Decimal(0)):,.2f}"
        )
    else:
        verdict = "ok"
        reasons.append(
            f"Proposed {Decimal(proposed):,.2f} is within distributable profits of "
            f"{(distributable or Decimal(0)):,.2f}"
        )

    reasons.append(
        "Not covered by this check: transfer to reserves, the s.123(3) interim-dividend "
        "ceiling, and the unpaid-dividend account."
    )

    return {
        "entity_id": entity.id,
        "fy": fy,
        "current_profit": current_profit,
        "accumulated_profit": accumulated,
        "accumulated_losses": losses,
        "depreciation_provided": None,
        "distributable": distributable,
        "proposed": Decimal(proposed) if proposed is not None else None,
        "verdict": verdict,
        "reasons": reasons,
        "source": facts.get("source", "missing"),
    }


def as_jsonb(check: dict[str, Any]) -> dict[str, Any]:
    """Flatten a check for storage in a JSONB column.

    ``Decimal`` and ``UUID`` do not survive asyncpg's JSONB encoder, and the frozen
    verdict on a capital event is exactly the thing that must not fail to persist.
    """
    return {
        key: (str(value) if isinstance(value, (Decimal, uuid.UUID)) else value)
        for key, value in check.items()
    }
