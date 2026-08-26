"""Source adapters — everything that turns an uploaded file into the pipeline's IR.

Each adapter's only job is to produce a :class:`~.ir.ParsedFile`: records bucketed
by catalogue entity key, each carrying a Tally-XML-shaped ``data`` dict.
Everything downstream — staging, the name book, the importers, dedupe, rollback —
reads only that shape, so a new source app is a new adapter and nothing else.

``ir``
    The shape itself, plus the value coercion every adapter needs.
``tally_xml``
    Tally's own XML export, and its Day Book CSV fallback.
``workbook``
    Any ``.xlsx``/``.csv`` as sheets of rows, with no opinion about meaning.
``profiles`` + ``builtin`` + ``detect``
    What a workbook's sheets and columns *mean*, and how we guess which.
``builders`` + ``normalise``
    Folding those rows into the IR, including rebuilding a double entry the
    source only implied.

:func:`parse` is the one entry point the runner calls.
"""

from __future__ import annotations

import base64
import binascii

from app.core.exceptions import ValidationError
from app.services.migration.sources import (
    builders,
    builtin,
    detect,
    ir,
    normalise,
    profiles,
    tally_xml,
    workbook,
)
from app.services.migration.sources.ir import ParsedFile
from app.services.migration.sources.profiles import SourceProfile

__all__ = [
    "builders",
    "builtin",
    "detect",
    "ir",
    "normalise",
    "parse",
    "profiles",
    "tally_xml",
    "workbook",
    "ParsedFile",
    "decode_payload",
    "encode_payload",
]

#: ``source_type`` values stored on the session.
XML = "XML"
CSV = "CSV"
XLSX = "XLSX"

#: How the session stores the upload so it can be re-parsed later. Text sources
#: keep their decoded text (readable in the DB, and how this always worked); a
#: workbook is binary, so it is kept base64.
TEXT = "text"
BASE64 = "base64"


def encode_payload(raw: bytes, source_type: str, decoded: str) -> tuple[str, str]:
    """``(payload, payload_encoding)`` to store on the session."""
    if source_type == XLSX:
        return base64.b64encode(raw).decode("ascii"), BASE64
    return decoded, TEXT


def decode_payload(payload: str | None, encoding: str | None) -> bytes:
    """The original bytes back, whichever way they were stored."""
    if payload is None:
        raise ValidationError("This import has no stored file to re-parse")
    if (encoding or TEXT) == BASE64:
        try:
            return base64.b64decode(payload, validate=True)
        except (binascii.Error, ValueError):
            raise ValidationError(
                "The stored copy of this workbook is unreadable. Upload the file again."
            ) from None
    return payload.encode("utf-8")


def parse(
    raw: bytes,
    *,
    file_name: str | None = None,
    profile: SourceProfile | None = None,
    saved_profiles: tuple[SourceProfile, ...] = (),
) -> tuple[ParsedFile, str, str]:
    """Parse an upload into the IR.

    Returns ``(parsed, source_type, decoded_text)`` — the same contract the XML
    parser has always had, so the runner does not care which adapter ran.

    ``profile`` forces a specific workbook mapping, which is what a re-parse after
    the tester edits the mapping does. Without it the profile is detected, with
    the company's own saved profiles ranked alongside the built-ins.
    """
    if not raw:
        raise ValidationError("The uploaded file is empty")

    if workbook.looks_like_xlsx(raw) or (file_name or "").casefold().endswith(
        workbook.XLSX_SUFFIXES
    ):
        book = workbook.read(raw, file_name=file_name)
        chosen, confidence = _pick(book, profile, saved_profiles)
        parsed = normalise.normalise(book, chosen)
        _explain(parsed, book, chosen, confidence, forced=profile is not None)
        return parsed, XLSX, ""

    # Text: Tally's own XML, its Day Book CSV, or a CSV from anywhere else. XML is
    # unambiguous; a CSV could be either a Tally Day Book or one sheet of some
    # other application's export, so the profiled path gets first refusal and the
    # Tally reader stays the fallback it has always been.
    text = tally_xml.decode_tally_bytes(raw)
    stripped = text.lstrip()
    if stripped.startswith("<") or "<ENVELOPE" in stripped[:2000].upper():
        return tally_xml.parse_xml(text), XML, text

    if profile is not None:
        book = workbook.read(raw, file_name=file_name)
        parsed = normalise.normalise(book, profile)
        _explain(parsed, book, profile, 100, forced=True)
        return parsed, CSV, text

    if (file_name or "").casefold().endswith(workbook.CSV_SUFFIXES) or "," in stripped[:500]:
        return tally_xml.parse_csv(text), CSV, text

    raise ValidationError(
        "Unrecognised file. Upload a Tally XML export (.xml), an Excel workbook "
        "(.xlsx) exported from your current system, or a CSV."
    )


def _pick(
    book: workbook.Workbook,
    forced: SourceProfile | None,
    saved: tuple[SourceProfile, ...],
) -> tuple[SourceProfile, int]:
    if forced is not None:
        return forced, 100
    found = detect.detect(book, extra=saved)
    return found.profile, found.confidence


def _explain(
    parsed: ParsedFile,
    book: workbook.Workbook,
    profile: SourceProfile,
    confidence: int,
    *,
    forced: bool,
) -> None:
    """Put the detection story into the parse log, where a tester will read it."""
    if forced:
        parsed.warnings.append(
            f"Parsed with the '{profile.label}' mapping you chose, covering "
            f"{len(book.sheets)} sheet(s)."
        )
    elif profile.key == builtin.CUSTOM.key:
        parsed.warnings.append(
            "No built-in mapping matched this workbook, so nothing has been staged "
            "yet. Open the Mapping step, tell us what each sheet is, and re-parse — "
            "then save it as a profile so the next file from this system is "
            "recognised automatically."
        )
    else:
        parsed.warnings.append(
            f"Detected a {profile.label} workbook ({confidence}% confident) across "
            f"{len(book.sheets)} sheet(s). Check the Mapping step before you run "
            "the import if anything looks wrong."
        )
    for name, reason in book.skipped.items():
        # A documentation sheet the profile already knows to ignore is not news.
        spec = profile.sheet_for(name)
        if spec is not None and spec.kind == "reference":
            continue
        parsed.warnings.append(f"Sheet '{name}' was not read: {reason}.")
