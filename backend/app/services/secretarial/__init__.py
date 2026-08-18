"""Module 13 — Company Secretarial & Governance services.

Import the sub-modules explicitly rather than star-importing: several of them export
functions with the same shape (``list_*``, ``update_*``), and the qualified call site
(``compliance.list_items`` vs ``registers.list_rows``) is the readable one.
"""

from app.services.secretarial import (  # noqa: F401
    common,
    compliance,
    content,
    engagement,
    entity,
    files,
    persons,
    registers,
    roster,
    workspace,
)

__all__ = [
    "common",
    "compliance",
    "content",
    "engagement",
    "entity",
    "files",
    "persons",
    "registers",
    "roster",
    "workspace",
]
