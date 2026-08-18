"""Does this obligation apply to this company?

Phase 1 answered that from the entity's class alone. This is the upgrade the plan calls
the differentiator (§18): a predicate evaluated against entity attributes **and
ledger-derived financial facts**, so CSR applies because the net profit actually crossed
five crore, not because somebody remembered to tick a box.

The predicate is data, not code, so a reviewer can read a rule's applicability without
reading Python:

    {"all": [
      {"eq": ["entity.kind", "company"]},
      {"any": [
        {"gte": ["facts.net_worth", 5000000000]},
        {"gte": ["facts.turnover", 10000000000]},
        {"gte": ["facts.net_profit", 50000000]}
      ]}
    ]}

**Unknown facts do not silently pass.** If a threshold cannot be evaluated because the
number is missing, the rule comes back ``unknown`` rather than ``False`` — the calendar
then flags it for a human instead of quietly deciding the company is exempt. Getting
that backwards is how a compliance tool tells someone they have nothing to file.
"""

from __future__ import annotations

from typing import Any

from app.models.secretarial import SecretarialEntity

# Phase-1 shape, still honoured so existing seeded rules keep working.
_LEGACY_KEYS = {"kinds", "classes", "exclude_classes", "listed"}


class Verdict:
    """Three-valued: applies, does not apply, or cannot tell."""

    APPLIES = "applies"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


def _resolve(path: str, context: dict[str, Any]) -> Any:
    node: Any = context
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
        if node is None:
            return None
    return node


def _compare(op: str, left: Any, right: Any) -> bool | None:
    """None means 'cannot tell' and propagates upwards."""
    if left is None:
        return None
    try:
        if op == "eq":
            return left == right
        if op == "ne":
            return left != right
        if op == "in":
            return left in (right or [])
        if op == "not_in":
            return left not in (right or [])
        if op == "gte":
            return float(left) >= float(right)
        if op == "gt":
            return float(left) > float(right)
        if op == "lte":
            return float(left) <= float(right)
        if op == "lt":
            return float(left) < float(right)
        if op == "is_true":
            return bool(left)
        if op == "is_false":
            return not bool(left)
    except (TypeError, ValueError):
        return None
    return None


def evaluate(predicate: dict[str, Any] | None, context: dict[str, Any]) -> tuple[str, list[str]]:
    """Evaluate a predicate. Returns (verdict, reasons).

    Reasons are written for a human reading the calendar — "net profit 6.2 Cr is at or
    above 5 Cr" beats "clause 2 matched".
    """
    if not predicate:
        return Verdict.APPLIES, ["No conditions — applies to every entity."]

    if _LEGACY_KEYS & set(predicate):
        return _evaluate_legacy(predicate, context)

    result, reasons = _walk(predicate, context)
    if result is None:
        return Verdict.UNKNOWN, reasons
    return (Verdict.APPLIES if result else Verdict.NOT_APPLICABLE), reasons


def _walk(node: dict[str, Any], context: dict[str, Any]) -> tuple[bool | None, list[str]]:
    if "all" in node:
        reasons: list[str] = []
        unknown = False
        for child in node["all"]:
            value, child_reasons = _walk(child, context)
            reasons.extend(child_reasons)
            if value is False:
                # One definite failure settles an AND, whatever else is unknown.
                return False, reasons
            if value is None:
                unknown = True
        return (None if unknown else True), reasons

    if "any" in node:
        reasons = []
        unknown = False
        for child in node["any"]:
            value, child_reasons = _walk(child, context)
            reasons.extend(child_reasons)
            if value is True:
                return True, reasons
            if value is None:
                unknown = True
        return (None if unknown else False), reasons

    if "not" in node:
        value, reasons = _walk(node["not"], context)
        return (None if value is None else not value), reasons

    for op, operands in node.items():
        if not isinstance(operands, list) or not operands:
            continue
        path = operands[0]
        expected = operands[1] if len(operands) > 1 else None
        actual = _resolve(str(path), context)
        outcome = _compare(op, actual, expected)
        return outcome, [_describe(path, op, actual, expected, outcome)]

    return None, ["Unreadable condition."]


_OP_WORDS = {
    "gte": "is at or above",
    "gt": "is above",
    "lte": "is at or below",
    "lt": "is below",
    "eq": "is",
    "ne": "is not",
    "in": "is one of",
    "not_in": "is not one of",
    "is_true": "is set",
    "is_false": "is not set",
}


def _describe(path: Any, op: str, actual: Any, expected: Any, outcome: bool | None) -> str:
    label = str(path).replace("entity.", "").replace("facts.", "").replace("_", " ")
    if actual is None:
        return f"{label} is not known, so this condition cannot be checked."
    words = _OP_WORDS.get(op, op)
    shown_actual = _money(actual) if isinstance(actual, (int, float)) else actual
    shown_expected = _money(expected) if isinstance(expected, (int, float)) else expected
    verdict = "yes" if outcome else "no"
    if op in ("is_true", "is_false"):
        return f"{label} {words}: {verdict}."
    return f"{label} {shown_actual} {words} {shown_expected}: {verdict}."


def _money(value: float | int) -> str:
    """Indian readers think in lakh and crore; 50000000 is not a legible threshold."""
    v = float(value)
    if abs(v) >= 1_00_00_000:
        return f"{v / 1_00_00_000:,.2f} Cr"
    if abs(v) >= 1_00_000:
        return f"{v / 1_00_000:,.2f} L"
    return f"{v:,.0f}"


def _evaluate_legacy(predicate: dict[str, Any], context: dict[str, Any]) -> tuple[str, list[str]]:
    """The Phase-1 static filter, kept so seeded rules keep working unchanged."""
    entity = context.get("entity", {})
    reasons: list[str] = []

    kinds = predicate.get("kinds")
    if kinds and entity.get("kind") not in kinds:
        return Verdict.NOT_APPLICABLE, [f"Applies to {', '.join(kinds)} only."]

    classes = predicate.get("classes")
    if classes and entity.get("class") not in classes:
        return Verdict.NOT_APPLICABLE, [f"Applies to {', '.join(classes)} only."]

    excluded = predicate.get("exclude_classes")
    if excluded and entity.get("class") in excluded:
        return Verdict.NOT_APPLICABLE, [f"Does not apply to {entity.get('class')}."]

    listed = predicate.get("listed")
    if listed is not None and bool(entity.get("listed")) != bool(listed):
        return Verdict.NOT_APPLICABLE, [
            "Applies to listed companies only." if listed else "Applies to unlisted companies only."
        ]

    reasons.append("Matches this entity's type and class.")
    return Verdict.APPLIES, reasons


def build_context(entity: SecretarialEntity, facts: dict[str, Any] | None = None) -> dict[str, Any]:
    """Everything a predicate may reference."""
    return {
        "entity": {
            "kind": entity.kind,
            "class": entity.entity_class,
            "listed": bool(entity.is_listed),
            "status": entity.status,
            "has_books_here": entity.linked_company_id is not None,
        },
        "facts": {k: v for k, v in (facts or {}).items() if k not in ("fy", "source", "computed_at")},
    }
