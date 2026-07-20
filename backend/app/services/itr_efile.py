"""Pluggable Income-tax e-filing provider — ITR Phase 6.

Mirrors the GST GSP pattern (``app.services.gsp``): the Phase-2/4 JSON pack is always
available; live push is a named provider selected from Income Tax Settings
(``itr_efile_provider``). Credentials live out-of-band (env / secret store), never in
the settings blob.

Ships:
- ``NullProvider`` — JSON-only degrade (default when unset / ``none``)
- ``SandboxStubProvider`` — registered as ``sandbox``; no HTTPS, deterministic fake ack
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import ValidationError
from app.schemas.compliance import IncomeTaxSettings, ItrEfileResult
from app.services.income_tax_settings import get_income_tax_settings
from app.services.itr_export import build_itr_pack


class ItrEfileError(Exception):
    """An e-filing call failed (auth, network, or portal rejection)."""


class ItrEfileNotConfigured(ItrEfileError):
    """No live e-file provider configured — fall back to JSON export."""


@runtime_checkable
class ItrEfileProvider(Protocol):
    """Contract every ITR e-file adapter implements."""

    name: str

    @property
    def configured(self) -> bool: ...

    async def submit_return(self, pack: dict) -> dict:
        """Submit an ITR handoff pack. → {ack_no, acknowledgement_date, status, form}."""
        ...


class NullProvider:
    """Default: no live portal — caller degrades to returning the JSON pack."""

    name = "none"

    @property
    def configured(self) -> bool:
        return False

    async def submit_return(self, pack: dict) -> dict:
        raise ItrEfileNotConfigured(
            "No ITR e-file provider is configured — download the JSON/CSV and file on the "
            "Income-tax portal (set itr_efile_provider + credentials to enable live push)."
        )


class SandboxStubProvider:
    """Deterministic sandbox ack — proves the seam without portal HTTPS or DSC."""

    name = "sandbox"

    @property
    def configured(self) -> bool:
        return True

    async def submit_return(self, pack: dict) -> dict:
        ref = str(pack.get("computation_ref") or pack.get("assessment_year") or "pack")
        digest = hashlib.sha256(ref.encode()).hexdigest()[:10].upper()
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return {
            "ack_no": f"SANDBOX-ACK-{digest}",
            "acknowledgement_date": now,
            "status": "Accepted",
            "form": pack.get("form") or "ITR-6",
            "mode": "sandbox",
            "message": "Sandbox stub — not filed with the Income-tax portal.",
        }


_PROVIDERS: dict[str, type] = {}


def register_provider(name: str, cls: type) -> None:
    """Register a live adapter under a name matched against ``IncomeTaxSettings.itr_efile_provider``."""
    _PROVIDERS[name.lower()] = cls


def get_provider(settings: IncomeTaxSettings) -> ItrEfileProvider:
    """Resolve the e-file provider for a tenant. Unknown / unset ⇒ NullProvider."""
    name = (settings.itr_efile_provider or "none").strip().lower()
    if name in ("", "none"):
        return NullProvider()
    cls = _PROVIDERS.get(name)
    if cls is None:
        return NullProvider()
    return cls()


register_provider("sandbox", SandboxStubProvider)


async def efile_computation(
    db: AsyncSession,
    *,
    company_id: uuid.UUID,
    doc_id: uuid.UUID,
) -> ItrEfileResult:
    """Build the entity ITR pack and optionally push via the configured provider."""
    pack = await build_itr_pack(db, doc_id, company_id)
    settings = await get_income_tax_settings(db, company_id)
    provider = get_provider(settings)
    payload = pack.payload

    if not provider.configured:
        return ItrEfileResult(
            status="generated",
            provider=provider.name,
            payload=payload,
            result=None,
            message=(
                "No ITR e-file provider configured — download this JSON and upload it "
                "on the Income-tax portal (set itr_efile_provider to 'sandbox' to trial)."
            ),
        )
    try:
        result = await provider.submit_return(payload)
    except ItrEfileError as exc:
        raise ValidationError(str(exc)) from exc
    return ItrEfileResult(
        status="pushed",
        provider=provider.name,
        payload=payload,
        result=result,
        message=f"Submitted via {provider.name}.",
    )
