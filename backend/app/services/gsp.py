"""Pluggable GSP / IRP / NIC provider abstraction — India Compliance Phase 5.

The live portal automation (push an e-invoice → IRN + signed QR, an e-way bill → EWB number
with Part-B vehicle updates, file GSTR-1/3B, pull GSTR-2B) is done through a **GSP** (GST Suvidha
Provider) or the NIC/IRP sandbox. Different tenants use different GSPs and sandbox vs production
credentials, so the integration is a **pluggable provider** selected by name from the per-company
GST Settings (``gsp_provider``); the per-tenant **credentials live in a secure store** (env / secret
manager), never in the settings blob.

This module ships the abstraction + the default ``NullProvider`` (JSON-only: a tenant with no GSP
configured still gets the offline-tool JSON from the Phase-4 generators). A real provider (e.g. an
HTTPS adapter for a specific GSP) registers via ``register_provider`` and is dropped in without
touching call sites. The abstraction sits **on top of the Phase-4 data layer**, so the JSON payload
is always available whether or not a live push happens.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.schemas.compliance import GstSettings


class GspError(Exception):
    """A GSP/IRP/NIC call failed (network, auth, or a portal rejection)."""


class GspNotConfigured(GspError):
    """No live GSP is configured for this tenant — fall back to JSON export."""


@runtime_checkable
class GspProvider(Protocol):
    """The contract every GSP adapter implements. All calls are async and raise
    ``GspError`` on failure. Payloads are the Phase-4 JSON dicts."""

    name: str

    @property
    def configured(self) -> bool: ...

    async def push_e_invoice(self, payload: dict) -> dict: ...  # → {irn, ack_no, ack_dt, signed_qr_code}

    async def cancel_e_invoice(self, irn: str, reason_code: str, remark: str) -> dict: ...

    async def push_e_way_bill(self, payload: dict) -> dict: ...  # → {ewb_no, ewb_date, valid_upto}

    async def update_vehicle(self, ewb_no: str, payload: dict) -> dict: ...  # Part-B

    async def file_return(self, gstin: str, period: str, kind: str, payload: dict) -> dict: ...

    async def fetch_gstr2b(self, gstin: str, period: str) -> dict: ...


class NullProvider:
    """Default provider: no live portal — every push raises ``GspNotConfigured`` so the caller
    degrades gracefully to returning the JSON payload for manual upload."""

    name = "none"

    @property
    def configured(self) -> bool:
        return False

    def _fail(self) -> None:
        raise GspNotConfigured(
            "No GSP/IRP is configured for this company — download the JSON and upload it on the "
            "portal (set a GSP provider + credentials to enable live push)."
        )

    async def push_e_invoice(self, payload: dict) -> dict:
        self._fail()

    async def cancel_e_invoice(self, irn: str, reason_code: str, remark: str) -> dict:
        self._fail()

    async def push_e_way_bill(self, payload: dict) -> dict:
        self._fail()

    async def update_vehicle(self, ewb_no: str, payload: dict) -> dict:
        self._fail()

    async def file_return(self, gstin: str, period: str, kind: str, payload: dict) -> dict:
        self._fail()

    async def fetch_gstr2b(self, gstin: str, period: str) -> dict:
        self._fail()


# Registry of live providers by name. Real adapters (an HTTPS client for a specific GSP,
# reading credentials from the secret store keyed by company) register here at import time.
_PROVIDERS: dict[str, type] = {}


def register_provider(name: str, cls: type) -> None:
    """Register a live GSP adapter under a name matched against ``GstSettings.gsp_provider``."""
    _PROVIDERS[name.lower()] = cls


def get_provider(settings: GstSettings) -> GspProvider:
    """Resolve the GSP provider for a tenant from its GST Settings. Unknown / unset ⇒ NullProvider."""
    name = (settings.gsp_provider or "none").strip().lower()
    if name in ("", "none"):
        return NullProvider()
    cls = _PROVIDERS.get(name)
    if cls is None:
        return NullProvider()
    return cls()
