"""The block tree — the one interface every document format renders from.

Plan §2.6. A content pack's fragments are **structured blocks**, never HTML strings.
That discipline is the whole reason a Word renderer can be added later without
rewriting every pack: HTML→DOCX conversion produces documents no Company Secretary
can edit cleanly, so the tree has to stay format-neutral from the start.

A block is a plain dict so packs are JSON and reviewable by a non-programmer:

    {"type": "heading", "level": 2, "text": "NOTICE OF BOARD MEETING"}
    {"type": "paragraph", "text": "Notice is hereby given that ..."}
    {"type": "list", "ordered": true, "items": ["To consider ...", "To approve ..."]}
    {"type": "table", "columns": ["Sr.", "Name"], "rows": [["1", "..."]]}
    {"type": "signature_grid", "signatories": [{"name": "...", "designation": "..."}]}
    {"type": "spacer"} · {"type": "page_break"} · {"type": "divider"}

Text carries ``{{ variable }}`` placeholders resolved against a flat context before
rendering, so substitution happens once, here, rather than in each renderer.
"""

from __future__ import annotations

import re
from typing import Any

BLOCK_TYPES = frozenset(
    {
        "heading",
        "paragraph",
        "list",
        "table",
        "signature_grid",
        "key_values",
        "quote",
        "spacer",
        "divider",
        "page_break",
    }
)

# Fragments a pack may define. Notice, minutes and attendance render from the same
# agenda blocks, which is what keeps their numbering and wording identical.
FRAGMENT_NAMES = frozenset(
    {
        "notice",
        "notes_to_agenda",
        "minutes_narration",
        "resolution",
        "ctc",
        "letter",
        "attendance",
        "explanatory_statement",
    }
)

_PLACEHOLDER = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class BlockError(ValueError):
    """A pack's block tree is malformed. Raised at load time, never at render time."""


def validate_tree(blocks: Any, *, where: str = "fragment") -> list[dict[str, Any]]:
    """Check a block list and return it. Fails loudly, and only on load.

    A pack with a bad tree must never reach a document; catching it when the pack is
    saved means a reviewer sees the error, not a client.
    """
    if not isinstance(blocks, list):
        raise BlockError(f"{where}: expected a list of blocks, got {type(blocks).__name__}")

    for i, block in enumerate(blocks):
        at = f"{where}[{i}]"
        if not isinstance(block, dict):
            raise BlockError(f"{at}: expected an object, got {type(block).__name__}")
        kind = block.get("type")
        if kind not in BLOCK_TYPES:
            raise BlockError(
                f"{at}: unknown block type {kind!r}. Valid: {', '.join(sorted(BLOCK_TYPES))}"
            )

        if kind == "heading":
            _require(block, "text", at)
            level = block.get("level", 2)
            if not isinstance(level, int) or not 1 <= level <= 4:
                raise BlockError(f"{at}: heading level must be 1-4")
        elif kind in ("paragraph", "quote"):
            _require(block, "text", at)
        elif kind == "list":
            items = block.get("items")
            if not isinstance(items, list) or not all(isinstance(x, str) for x in items):
                raise BlockError(f"{at}: list needs an 'items' array of strings")
        elif kind == "table":
            cols = block.get("columns")
            rows = block.get("rows", [])
            if not isinstance(cols, list) or not cols:
                raise BlockError(f"{at}: table needs a non-empty 'columns' array")
            if not isinstance(rows, list):
                raise BlockError(f"{at}: table 'rows' must be an array")
            for r, row in enumerate(rows):
                if not isinstance(row, list):
                    raise BlockError(f"{at}.rows[{r}]: each row must be an array")
        elif kind == "key_values":
            pairs = block.get("pairs")
            if not isinstance(pairs, list):
                raise BlockError(f"{at}: key_values needs a 'pairs' array of [label, value]")
        elif kind == "signature_grid":
            if not isinstance(block.get("signatories", []), list):
                raise BlockError(f"{at}: signature_grid 'signatories' must be an array")

    return blocks


def _require(block: dict[str, Any], field: str, at: str) -> None:
    if not isinstance(block.get(field), str):
        raise BlockError(f"{at}: '{field}' is required and must be a string")


def placeholders(blocks: list[dict[str, Any]]) -> set[str]:
    """Every ``{{ name }}`` used anywhere in the tree.

    Used to tell an author which variables a pack actually needs, and to warn when a
    guided form would leave one unfilled.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.update(_PLACEHOLDER.findall(value))
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)

    walk(blocks)
    return found


def _lookup(context: dict[str, Any], dotted: str) -> Any:
    """Resolve ``entity.name`` style paths against a nested context."""
    node: Any = context
    for part in dotted.split("."):
        if isinstance(node, dict):
            node = node.get(part)
        else:
            node = getattr(node, part, None)
        if node is None:
            return None
    return node


def resolve(blocks: list[dict[str, Any]], context: dict[str, Any], *, strict: bool = False) -> list[dict[str, Any]]:
    """Substitute placeholders throughout the tree, returning a new tree.

    Unresolved names become ``[name]`` rather than an empty gap, so a missing value is
    visible on the page instead of producing a sentence that reads as complete but
    says something false. ``strict`` raises instead, for a pre-issue check.
    """
    missing: set[str] = set()

    def sub(text: str) -> str:
        def one(match: re.Match[str]) -> str:
            name = match.group(1)
            value = _lookup(context, name)
            if value is None or value == "":
                missing.add(name)
                return f"[{name}]"
            return str(value)

        return _PLACEHOLDER.sub(one, text)

    def walk(value: Any) -> Any:
        if isinstance(value, str):
            return sub(value)
        if isinstance(value, list):
            return [walk(v) for v in value]
        if isinstance(value, dict):
            return {k: walk(v) for k, v in value.items()}
        return value

    out = [walk(b) for b in blocks]
    if strict and missing:
        raise BlockError(
            "Cannot issue with unfilled values: " + ", ".join(sorted(missing))
        )
    return out


# The sentinel `resolve()` leaves behind for a value it could not fill. Matching the
# placeholder name grammar rather than any bracket keeps legitimate prose — "[sic]", a
# citation, a bracketed aside — from being mistaken for a hole.
_UNFILLED = re.compile(r"\[([a-zA-Z0-9_.]+)\]")


def unfilled(blocks: list[dict[str, Any]]) -> set[str]:
    """Names still showing as ``[name]`` anywhere in an already-resolved tree.

    ``resolve()`` substitutes at generation time and writes ``[name]`` where it had
    nothing to put, so this reads the result rather than re-resolving: by the time a
    document is being issued the inputs are long gone, and the tree is the record.
    """
    found: set[str] = set()

    def walk(value: Any) -> None:
        if isinstance(value, str):
            found.update(_UNFILLED.findall(value))
        elif isinstance(value, list):
            for v in value:
                walk(v)
        elif isinstance(value, dict):
            for v in value.values():
                walk(v)

    walk(blocks)
    return found


def agenda_blocks(items: list[dict[str, Any]], *, style: str = "notice") -> list[dict[str, Any]]:
    """Render agenda items as blocks — the shared source for notice and minutes.

    ``style='notice'`` lists the business to be transacted; ``style='minutes'`` narrates
    each item followed by its resolution. Both walk the same ordered list, which is what
    guarantees item 4 in the notice is item 4 in the minutes.
    """
    blocks: list[dict[str, Any]] = []

    if style == "notice":
        blocks.append(
            {
                "type": "list",
                "ordered": True,
                "items": [item.get("title", "") for item in items],
            }
        )
        return blocks

    for idx, item in enumerate(items, start=1):
        blocks.append({"type": "heading", "level": 3, "text": f"{idx}. {item.get('title', '')}"})
        if item.get("body"):
            blocks.append({"type": "paragraph", "text": item["body"]})
        if item.get("resolution_text"):
            blocks.append({"type": "quote", "text": item["resolution_text"]})
    return blocks
