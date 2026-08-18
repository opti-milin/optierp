"""Token-scoped access for people with no account.

Directors never log in. That is a deliberate architectural decision (plan §11): a
director account would be a support and security burden with no upside, and the thing a
director actually needs — view these papers, record this consent — is a single-purpose
capability, not an identity.

So this is the module's second authentication path, and the only one. It exists because
every other session gets its tenant from a JWT, and a director has none. The order of
operations matters:

1. Look the token up on a session with **no tenant context** — the token table is the
   one place that must be readable before we know whose data we are about to touch.
2. Verify: right purpose, not expired, not revoked.
3. *Then* set the tenant context from the token's own company, so RLS applies for the
   rest of the request exactly as it would for a logged-in user.

Getting that order wrong would either break the lookup or let a token read across
tenants, so it is centralised here rather than repeated per endpoint.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.database import get_db, set_company_context
from app.core.exceptions import AppError
from app.models.secretarial import SecretarialPortalEvent, SecretarialPortalToken

# Long enough that guessing is hopeless, short enough to survive an email client's
# line wrapping without being mangled.
TOKEN_BYTES = 32

DEFAULT_TTL_DAYS = 45


class PortalTokenInvalid(AppError):
    """Deliberately vague to the caller: an attacker learns nothing about whether a
    token existed, was revoked, or merely expired."""

    status_code = 403
    code = "ERR_PORTAL_TOKEN_INVALID"


def generate_token() -> tuple[str, str]:
    """Returns (raw token for the link, sha256 to store).

    The raw value exists only in the email. Storing the hash means a database leak does
    not hand over the ability to cast consents.
    """
    raw = secrets.token_urlsafe(TOKEN_BYTES)
    return raw, hash_token(raw)


def hash_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def default_expiry(days: int = DEFAULT_TTL_DAYS) -> datetime:
    return datetime.now(UTC) + timedelta(days=days)


async def resolve_token(
    db: AsyncSession, raw: str, *, purpose: str | None = None
) -> SecretarialPortalToken:
    """Find and validate a token. Runs before any tenant context is set."""
    if not raw or len(raw) > 200:
        raise PortalTokenInvalid("This link is not valid.")

    # Hash comparison, so the lookup itself is constant-time with respect to the secret.
    token = await db.scalar(
        select(SecretarialPortalToken).where(
            SecretarialPortalToken.token_hash == hash_token(raw)
        )
    )
    if token is None:
        raise PortalTokenInvalid("This link is not valid.")
    if purpose and token.purpose != purpose:
        # A view link must never be usable to cast a consent.
        raise PortalTokenInvalid("This link is not valid for that action.")
    if token.revoked_at is not None:
        raise PortalTokenInvalid("This link has been withdrawn. Please contact the company.")
    if token.expires_at <= datetime.now(UTC):
        raise PortalTokenInvalid("This link has expired. Please ask for a fresh one.")
    return token


async def record_event(
    db: AsyncSession,
    token: SecretarialPortalToken,
    event: str,
    request: Request | None = None,
    payload: dict | None = None,
) -> None:
    """Append to the evidence trail. Every hit, not just the decisive one — "the
    director opened it twice and consented on the second visit" is exactly the kind of
    detail a dispute turns on."""
    db.add(
        SecretarialPortalEvent(
            company_id=token.company_id,
            token_id=token.id,
            event=event,
            ip_address=(request.client.host if request and request.client else None),
            user_agent=(request.headers.get("user-agent", "")[:300] if request else None),
            payload=payload,
        )
    )
    token.last_used_at = datetime.now(UTC)
    token.use_count = (token.use_count or 0) + 1
    await db.flush()


class PortalSession:
    """What a portal endpoint gets: the token, and a session scoped to its tenant."""

    def __init__(self, db: AsyncSession, token: SecretarialPortalToken, request: Request) -> None:
        self.db = db
        self.token = token
        self.request = request

    @property
    def company_id(self) -> uuid.UUID:
        return self.token.company_id

    @property
    def entity_id(self) -> uuid.UUID:
        return self.token.entity_id


def portal_session(purpose: str | None = None):
    """Dependency factory: resolves ``token`` from the path, then scopes the session.

    Usage: ``session: Annotated[PortalSession, Depends(portal_session("consent_respond"))]``
    """

    async def _resolve(
        token: str,
        request: Request,
        db: Annotated[AsyncSession, Depends(get_db)],
    ) -> PortalSession:
        # Step 1: unscoped lookup. portal_tokens carries an RLS policy for authenticated
        # use, but this session has no tenant yet — the query runs as the app role with
        # no app.company_id set, which the policy treats as matching nothing. So the
        # lookup is done before arming the context, and the row is fetched by its unique
        # hash rather than by tenant.
        resolved = await resolve_token(db, token, purpose=purpose)
        # Step 2: from here on, behave exactly like a logged-in request for that tenant.
        await set_company_context(db, resolved.company_id)
        return PortalSession(db, resolved, request)

    return _resolve
