"""Deciding which profile a workbook is.

Detection is a convenience, never an authority: whatever it decides, the tester
sees the resulting sheet-by-sheet mapping and can change any of it before a
single row is staged. So the goal here is not to be clever — it is to be *right
or obviously unsure*, because a confident wrong answer costs more than no answer.

That shapes the scoring:

* A profile's ``signature`` sheets are near-proof. The OptiERP template stamps a
  marker sheet, which is proof outright.
* Otherwise the score is per-sheet column overlap, weighted by how many of the
  profile's *required* fields each sheet actually finds. A workbook with a sheet
  called ``Invoices`` that shares no columns with Zoho's is not Zoho.
* Below :data:`MIN_CONFIDENCE` we return the custom profile with everything
  unassigned, and say so, rather than importing a Busy export as a Tally one.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.services.migration.sources.builtin import (
    BUILTIN_PROFILES,
    CUSTOM,
    TEMPLATE_MARKER_SHEET,
)
from app.services.migration.sources.profiles import (
    SheetSpec,
    SourceProfile,
    missing_required,
    resolve,
)
from app.services.migration.sources.workbook import Sheet, Workbook, normalise_header

#: Below this the match is not worth acting on. A profile that finds its
#: signature sheets clears it easily; one matching on a couple of generic column
#: names like "date" and "amount" does not.
MIN_CONFIDENCE = 55


@dataclass
class SheetMatch:
    """How one sheet of the workbook lined up with one profile's expectation."""

    sheet: str
    spec: SheetSpec | None
    matched_columns: int = 0
    expected_columns: int = 0
    missing: list[str] = field(default_factory=list)
    rows: int = 0

    @property
    def usable(self) -> bool:
        return self.spec is not None and not self.missing


def _child_sheets(profile: SourceProfile) -> set[str]:
    """Sheet names consumed as line sheets rather than in their own right.

    A line sheet is not "unmatched" just because it has no top-level spec — it is
    read through its parent. Reporting it as unmatched made a perfect Zoho match
    look like it had five sheets it could not understand.
    """
    return {
        normalise_header(child.sheet)
        for spec in profile.sheets
        for child in spec.children
    }


@dataclass
class Detection:
    """A ranked candidate: this profile, this confidence, this per-sheet fit."""

    profile: SourceProfile
    confidence: int
    matches: list[SheetMatch] = field(default_factory=list)
    unmatched_sheets: list[str] = field(default_factory=list)

    @property
    def mapped_sheets(self) -> int:
        return sum(1 for m in self.matches if m.spec is not None)

    @property
    def importable_sheets(self) -> int:
        return sum(
            1 for m in self.matches
            if m.spec is not None and m.spec.kind != "reference" and not m.missing
        )

    def summary(self) -> dict[str, object]:
        return {
            "profile": self.profile.key,
            "label": self.profile.label,
            "app": self.profile.app,
            "confidence": self.confidence,
            "mapped_sheets": self.mapped_sheets,
            "importable_sheets": self.importable_sheets,
            "unmatched_sheets": list(self.unmatched_sheets),
        }


def _match_sheet(spec: SheetSpec, sheet: Sheet) -> SheetMatch:
    trimmed = resolve(spec, sheet)
    expected = len(spec.columns)
    return SheetMatch(
        sheet=sheet.name,
        spec=spec,
        matched_columns=len(trimmed.columns),
        expected_columns=expected,
        missing=missing_required(trimmed, sheet),
        rows=len(sheet),
    )


def score(profile: SourceProfile, book: Workbook) -> Detection:
    """How well this profile explains this workbook, 0-100."""
    matches: list[SheetMatch] = []
    unmatched: list[str] = []
    children = _child_sheets(profile)

    for sheet in book.sheets:
        spec = profile.sheet_for(sheet.name)
        if spec is None:
            if normalise_header(sheet.name) not in children:
                unmatched.append(sheet.name)
            continue
        matches.append(_match_sheet(spec, sheet))

    if not matches:
        return Detection(profile=profile, confidence=0, unmatched_sheets=unmatched)

    present = {normalise_header(s.name) for s in book.sheets}
    signature = [normalise_header(s) for s in profile.signature]

    # The template marker is a claim the file makes about itself, and only our own
    # generator writes it — so it settles the question rather than contributing to
    # a score.
    if normalise_header(TEMPLATE_MARKER_SHEET) in present and TEMPLATE_MARKER_SHEET in profile.signature:
        return Detection(profile=profile, confidence=100, matches=matches, unmatched_sheets=unmatched)

    signature_hits = sum(1 for name in signature if name in present)
    signature_score = (signature_hits / len(signature)) if signature else 0.0

    # How much of what the profile expected to find, it found — measured on the
    # sheets that actually matter, not the reference ones.
    real = [m for m in matches if m.spec is not None and m.spec.kind != "reference"]
    if real:
        column_score = sum(
            (m.matched_columns / m.expected_columns) if m.expected_columns else 0.0
            for m in real
        ) / len(real)
        blocked = sum(1 for m in real if m.missing)
        completeness = 1.0 - (blocked / len(real))
    else:
        column_score = completeness = 0.0

    # A signature match is the strongest evidence; column overlap confirms it;
    # completeness catches the case where the sheet names line up but the columns
    # inside have been renamed past recognition.
    confidence = int(round(100 * (0.5 * signature_score + 0.3 * column_score + 0.2 * completeness)))
    return Detection(
        profile=profile,
        confidence=min(confidence, 99),
        matches=matches,
        unmatched_sheets=unmatched,
    )


def rank(book: Workbook, *, extra: tuple[SourceProfile, ...] = ()) -> list[Detection]:
    """Every candidate profile, best first."""
    candidates = [score(p, book) for p in (*extra, *BUILTIN_PROFILES)]
    candidates.sort(key=lambda d: (d.confidence, d.importable_sheets), reverse=True)
    return candidates


def detect(book: Workbook, *, extra: tuple[SourceProfile, ...] = ()) -> Detection:
    """The profile to use, or the custom one when nothing is convincing.

    ``extra`` carries this company's saved profiles, which are tried alongside
    the built-ins: a company that has already mapped its Busy export should have
    that recognised on the next upload without touching the wizard.
    """
    ranked = rank(book, extra=extra)
    best = ranked[0] if ranked else None
    if best is not None and best.confidence >= MIN_CONFIDENCE:
        return best
    return Detection(
        profile=CUSTOM,
        confidence=0,
        matches=[SheetMatch(sheet=s.name, spec=None, rows=len(s)) for s in book.sheets],
        unmatched_sheets=book.names,
    )
