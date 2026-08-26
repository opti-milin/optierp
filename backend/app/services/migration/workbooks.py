"""The spreadsheet side of a migration session: mapping, re-parsing, saved profiles.

A Tally XML upload needs no mapping step — the format *is* the mapping. A
spreadsheet does, because "which sheet is the invoice list" has no answer the
file can give. This module is that step:

* :func:`mapping_view` — everything the wizard renders: the detected profile and
  its rivals, each sheet with its real headers and a few sample values, what the
  current mapping claims each sheet and column is, and what is still missing.
* :func:`apply_mapping` — take the tester's edits, rebuild the profile, and
  **re-parse from the stored upload**. Editing a mapping without re-parsing would
  leave staging rows describing a mapping nobody chose.
* :func:`save_profile` — remember the shape for next time, company-scoped.

The wizard can only ever produce a profile the normaliser can honour: the kinds,
the fields and the child roles it offers all come from
:mod:`~app.services.migration.sources.profiles`, and anything it sends back is
validated against the same tables before it is stored.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.migration import MigrationImport, MigrationSourceProfile
from app.services.migration import runner
from app.services.migration.catalogue import ENTITIES
from app.services.migration.sources import decode_payload, detect, workbook
from app.services.migration.sources.builtin import BUILTIN_BY_KEY, BUILTIN_PROFILES, CUSTOM
from app.services.migration.sources.profiles import (
    CHILD_FIELDS,
    KINDS,
    SourceProfile,
    missing_required,
    resolve,
)

#: Sample values shown per column in the wizard. Enough to recognise a column at
#: a glance, few enough that the payload stays small on a 40-sheet workbook.
SAMPLES = 3

_KEY = re.compile(r"[^a-z0-9]+")


def _slug(label: str) -> str:
    return _KEY.sub("-", label.strip().casefold()).strip("-")[:60] or "profile"


# --------------------------------------------------------------------------------------
# Saved profiles
# --------------------------------------------------------------------------------------


async def list_saved(db: AsyncSession, company_id: uuid.UUID) -> list[MigrationSourceProfile]:
    rows = await db.execute(
        select(MigrationSourceProfile)
        .where(MigrationSourceProfile.company_id == company_id)
        .order_by(MigrationSourceProfile.use_count.desc(), MigrationSourceProfile.label)
    )
    return list(rows.scalars().all())


async def saved_profiles(
    db: AsyncSession, company_id: uuid.UUID
) -> tuple[SourceProfile, ...]:
    """This company's saved shapes, ready to detect against.

    A saved profile that no longer deserialises — written by an older build, or
    edited into something the normaliser dropped support for — is skipped rather
    than raised. One stale row must not make every upload fail; the row is still
    visible in the profile list, where its own load error is reported.
    """
    out: list[SourceProfile] = []
    for row in await list_saved(db, company_id):
        try:
            out.append(SourceProfile.from_dict(row.definition, saved=True))
        except ValidationError:
            continue
    return tuple(out)


async def save_profile(
    db: AsyncSession,
    user: CurrentUser,
    *,
    label: str,
    definition: dict[str, Any],
    source_app: str | None = None,
    notes: str | None = None,
    key: str | None = None,
) -> MigrationSourceProfile:
    """Store a mapping as a reusable, named profile for this company."""
    profile = SourceProfile.from_dict(definition, saved=True)
    slug = key or _slug(label)
    if slug in BUILTIN_BY_KEY:
        raise ValidationError(
            f"'{slug}' is the name of a built-in mapping. Give yours a different name.",
            field="label",
        )
    payload = {**profile.to_dict(), "key": slug, "label": label, "app": source_app or profile.app}
    # Detection scores a profile mostly on whether its *signature* sheets are
    # present, and a mapping built in the wizard has no signature of its own — so
    # without this a saved profile could never out-score the built-ins and the
    # next upload of the same file would land back on "custom". The sheets the
    # tester actually mapped are exactly what identifies the shape.
    if not payload.get("signature"):
        payload["signature"] = [
            sheet.sheet for sheet in profile.sheets if sheet.kind != "reference"
        ]

    existing = (
        await db.execute(
            select(MigrationSourceProfile).where(
                MigrationSourceProfile.company_id == user.company_id,
                MigrationSourceProfile.key == slug,
            )
        )
    ).scalars().first()

    if existing is not None:
        existing.label = label
        existing.source_app = source_app or profile.app
        existing.definition = payload
        existing.notes = notes
        existing.modified_by = user.id
        row = existing
    else:
        row = MigrationSourceProfile(
            id=uuid.uuid4(),
            company_id=user.company_id,
            key=slug,
            label=label,
            source_app=source_app or profile.app,
            definition=payload,
            notes=notes,
            owner=user.id,
            modified_by=user.id,
        )
        db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


async def delete_profile(db: AsyncSession, company_id: uuid.UUID, profile_id: uuid.UUID) -> None:
    row = await db.get(MigrationSourceProfile, profile_id)
    if row is None or row.company_id != company_id:
        raise NotFoundError("Source profile not found")
    await db.delete(row)
    await db.commit()


async def note_use(db: AsyncSession, company_id: uuid.UUID, key: str | None) -> None:
    """Count a successful run against the profile that parsed it."""
    if not key or key in BUILTIN_BY_KEY:
        return
    row = (
        await db.execute(
            select(MigrationSourceProfile).where(
                MigrationSourceProfile.company_id == company_id,
                MigrationSourceProfile.key == key,
            )
        )
    ).scalars().first()
    if row is not None:
        row.use_count += 1


# --------------------------------------------------------------------------------------
# Resolving the profile a session is using
# --------------------------------------------------------------------------------------


async def session_profile(
    db: AsyncSession, session: MigrationImport
) -> SourceProfile | None:
    """The mapping this session was parsed with, exactly as it was parsed.

    Read from the session's own stored ``sheet_map`` rather than looked up by key,
    because the tester may have edited it after detection — and a re-parse has to
    reproduce *their* mapping, not the built-in it started from.
    """
    if session.sheet_map:
        try:
            return SourceProfile.from_dict(
                session.sheet_map, saved=session.source_profile not in BUILTIN_BY_KEY
            )
        except ValidationError:
            pass
    if session.source_profile and session.source_profile in BUILTIN_BY_KEY:
        return BUILTIN_BY_KEY[session.source_profile]
    saved = await saved_profiles(db, session.company_id)
    return next((p for p in saved if p.key == session.source_profile), None)


def is_spreadsheet(session: MigrationImport) -> bool:
    return (session.source_type or "").upper() == "XLSX"


def _book(session: MigrationImport) -> workbook.Workbook:
    raw = decode_payload(session.payload, session.payload_encoding)
    return workbook.read(raw, file_name=session.file_name)


# --------------------------------------------------------------------------------------
# The wizard payload
# --------------------------------------------------------------------------------------


def _catalogue_entities() -> list[dict[str, Any]]:
    """Entities a master or opening sheet may be assigned to."""
    return [
        {
            "key": spec.key,
            "label": spec.label,
            "target": spec.target,
            "module": spec.module,
            "stage": spec.stage,
        }
        for spec in ENTITIES
        if spec.stage < 50
    ]


def _kind_catalogue() -> list[dict[str, Any]]:
    return [
        {
            "key": kind.key,
            "label": kind.label,
            "notes": kind.notes,
            "child_roles": list(kind.child_roles),
            "fields": [
                {"name": f.name, "label": f.label, "required": f.required, "kind": f.kind}
                for f in kind.fields
            ],
        }
        for kind in KINDS.values()
    ]


def _samples(sheet: workbook.Sheet, header: str) -> list[str]:
    out: list[str] = []
    for row in sheet.rows:
        raw = row.get(header)
        if raw in (None, ""):
            continue
        out.append(str(raw)[:60])
        if len(out) >= SAMPLES:
            break
    return out


def _sheet_view(
    sheet: workbook.Sheet,
    profile: SourceProfile,
    child_index: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    spec = profile.sheet_for(sheet.name)
    view: dict[str, Any] = {
        "name": sheet.name,
        "rows": len(sheet),
        "header_row": sheet.header_row,
        "columns": [
            {
                "header": sheet.headers[i],
                "label": sheet.raw_headers[i] if i < len(sheet.raw_headers) else sheet.headers[i],
                "samples": _samples(sheet, sheet.headers[i]),
            }
            for i in range(len(sheet.headers))
        ],
        "assigned": None,
        "parent": None,
        "missing": [],
    }

    folded = workbook.normalise_header(sheet.name)
    if spec is None:
        if folded in child_index:
            parent, role = child_index[folded]
            view["parent"] = {"sheet": parent, "role": role}
        return view

    trimmed = resolve(spec, sheet)
    view["assigned"] = {
        "kind": spec.kind,
        "entity": spec.entity,
        "key": spec.key,
        "reason": spec.reason,
        "constants": dict(spec.constants),
        # Reported as the *found* header per field, which is what the wizard's
        # dropdowns bind to; the alias list behind it is a profile-authoring
        # detail nobody editing a mapping needs to see.
        "columns": {name: aliases[0] for name, aliases in trimmed.columns.items()},
        "children": [
            {
                "sheet": child.sheet,
                "role": child.role,
                "key": child.key,
                "constants": dict(child.constants),
                "columns": {n: a[0] for n, a in child.columns.items() if a},
            }
            for child in spec.children
        ],
    }
    view["missing"] = missing_required(trimmed, sheet)
    return view


def mapping_view(
    session: MigrationImport,
    profile: SourceProfile | None,
    saved: tuple[SourceProfile, ...] = (),
) -> dict[str, Any]:
    """Everything the mapping step renders, for one session."""
    if not is_spreadsheet(session):
        raise ValidationError(
            "This import is not a spreadsheet, so it has no sheet mapping. Tally "
            "XML and CSV carry their own structure."
        )
    book = _book(session)
    chosen = profile or CUSTOM
    ranked = detect.rank(book, extra=saved)

    child_index: dict[str, tuple[str, str]] = {}
    for spec in chosen.sheets:
        for child in spec.children:
            child_index[workbook.normalise_header(child.sheet)] = (spec.sheet, child.role)

    return {
        "source_type": session.source_type,
        "source_app": session.source_app,
        "profile": {
            "key": chosen.key,
            "label": chosen.label,
            "app": chosen.app,
            "notes": chosen.notes,
            "saved": chosen.saved,
        },
        "candidates": [
            {
                "key": d.profile.key,
                "label": d.profile.label,
                "app": d.profile.app,
                "saved": d.profile.saved,
                "confidence": d.confidence,
                "importable_sheets": d.importable_sheets,
            }
            for d in ranked[:5]
        ],
        "kinds": _kind_catalogue(),
        "child_fields": {
            role: [
                {"name": f.name, "label": f.label, "required": f.required, "kind": f.kind}
                for f in fields
            ]
            for role, fields in CHILD_FIELDS.items()
        },
        "entities": _catalogue_entities(),
        "defaults": dict(chosen.defaults),
        "sheets": [_sheet_view(sheet, chosen, child_index) for sheet in book.sheets],
        "unreadable_sheets": dict(book.skipped),
    }


# --------------------------------------------------------------------------------------
# Applying an edit
# --------------------------------------------------------------------------------------


async def apply_mapping(
    db: AsyncSession,
    session: MigrationImport,
    user: CurrentUser,
    *,
    definition: dict[str, Any] | None = None,
    profile_key: str | None = None,
) -> MigrationImport:
    """Re-parse the stored upload under a different or edited mapping.

    Either a whole ``definition`` (the wizard's edits) or a ``profile_key``
    (picking a different built-in or saved profile wholesale). Re-parsing
    replaces the staging set, which is why it refuses once a run has happened —
    the documents already created would no longer match their staging rows.
    """
    if not is_spreadsheet(session):
        raise ValidationError(
            "Only spreadsheet imports have a sheet mapping to change."
        )
    if session.status in ("Importing",):
        raise ValidationError("This import is currently running")
    if session.status in ("Imported", "Partially Imported"):
        raise ValidationError(
            "This import has already been run. Roll it back before changing its mapping."
        )

    if definition is not None:
        profile = SourceProfile.from_dict(definition, saved=True)
    elif profile_key:
        profile = BUILTIN_BY_KEY.get(profile_key)
        if profile is None:
            profile = next(
                (p for p in await saved_profiles(db, session.company_id) if p.key == profile_key),
                None,
            )
        if profile is None:
            raise ValidationError(f"No source profile named '{profile_key}'", field="profile")
    else:
        raise ValidationError("Send either a mapping definition or a profile key")

    await runner.parse_import(db, session, user, profile=profile)
    await db.commit()
    return session


def catalogue() -> dict[str, Any]:
    """Built-in mappings and the vocabulary behind them, for the sources screen."""
    return {
        "profiles": [
            {
                "key": p.key,
                "label": p.label,
                "app": p.app,
                "notes": p.notes,
                "saved": False,
                "sheets": [
                    {
                        "sheet": s.sheet,
                        "kind": s.kind,
                        "entity": s.entity,
                        "reason": s.reason,
                        "children": [
                            {"sheet": c.sheet, "role": c.role} for c in s.children
                        ],
                    }
                    for s in p.sheets
                ],
            }
            for p in BUILTIN_PROFILES
        ],
        "kinds": _kind_catalogue(),
    }
