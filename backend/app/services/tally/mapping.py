"""The name book: deciding which OptiERP record a Tally name refers to.

Tally identifies everything by name ("ABC Traders", "Sales @ 18%"). OptiERP
identifies everything by UUID. Every import therefore needs an answer to "which
record is this?" for thousands of names, and the answer has to survive so the
*second* import of the same Tally company doesn't ask again.

That answer lives in ``TallyMapping`` rows, and this module is what fills them:

* :func:`auto_map` runs before every import and proposes a mapping per name,
  in confidence order — GUID we've seen before, exact name, normalised name,
  close fuzzy match, else "create a new record".
* A tester can override any proposal in the UI; :func:`set_mapping` locks it so
  no later auto-map touches it again.
* :class:`MappingBook` is the read side the importers use — an in-memory
  name -> UUID lookup loaded once per run instead of a query per row.

Match confidence is reported honestly (0-100) so a tester can sort the mapping
screen by "least sure" and check only those.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.exceptions import NotFoundError, ValidationError
from app.core.security import CurrentUser
from app.models.accounts import Account, CostCenter
from app.models.buying import Supplier
from app.models.core import UOM, Currency
from app.models.selling import Customer
from app.models.stock import Item, ItemGroup, PriceList, Warehouse
from app.models.tally import MAPPING_TARGETS, TallyMapping
from app.services.tally.catalogue import ENTITY_BY_KEY, normalise_unit

# Confidence bands, so the UI can colour them consistently.
CONFIDENCE_GUID = 100  # a mapping we saved earlier for this exact Tally GUID
CONFIDENCE_LOCKED = 100  # a human said so
CONFIDENCE_EXACT = 95  # names match character for character
CONFIDENCE_NORMALISED = 85  # match after stripping punctuation/legal suffixes
CONFIDENCE_FUZZY_MIN = 70  # below this we propose "create new" instead
CONFIDENCE_NEW = 50  # nothing matched; a new record will be created

#: Legal-form noise that stops "ABC Traders Pvt Ltd" matching "ABC Traders".
_SUFFIXES = (
    "private limited", "pvt ltd", "pvt. ltd.", "pvt limited", "p ltd",
    "limited", "ltd", "llp", "inc", "incorporated", "corporation", "corp",
    "company", "co", "and sons", "& sons", "enterprises", "enterprise",
)
_PUNCT = re.compile(r"[^\w\s]+")
_SPACES = re.compile(r"\s+")

#: Which OptiERP model backs each mapping target.
TARGET_MODELS: dict[str, tuple[type, str]] = {
    "Account": (Account, "account_name"),
    "Customer": (Customer, "customer_name"),
    "Supplier": (Supplier, "supplier_name"),
    "Item": (Item, "item_name"),
    "Item Group": (ItemGroup, "item_group_name"),
    "Warehouse": (Warehouse, "warehouse_name"),
    "Cost Center": (CostCenter, "cost_center_name"),
    "Price List": (PriceList, "price_list_name"),
}

#: Catalogue entity -> the mapping target its records resolve to.
ENTITY_TARGETS: dict[str, str] = {
    "group": "Account",
    "ledger": "Account",
    "customer": "Customer",
    "supplier": "Supplier",
    "stock_group": "Item Group",
    "stock_item": "Item",
    "godown": "Warehouse",
    "cost_centre": "Cost Center",
    "unit": "UOM",
    "currency": "Currency",
    "price_list": "Price List",
    "voucher_type": "Voucher Type",
}


def normalise_name(name: str | None) -> str:
    """Fold a name to its comparable core: lowercase, no punctuation, no legal suffix."""
    if not name:
        return ""
    text = _PUNCT.sub(" ", name.casefold())
    text = _SPACES.sub(" ", text).strip()
    for suffix in _SUFFIXES:
        if text.endswith(" " + suffix):
            text = text[: -len(suffix) - 1].strip()
    return text


def similarity(left: str, right: str) -> int:
    """0-100 similarity between two already-normalised names."""
    if not left or not right:
        return 0
    return int(SequenceMatcher(None, left, right).ratio() * 100)


# --------------------------------------------------------------------------------------
# Candidate index over existing OptiERP records
# --------------------------------------------------------------------------------------


@dataclass
class Candidate:
    id: uuid.UUID
    name: str
    normalised: str
    extra: dict[str, Any] = field(default_factory=dict)


async def load_candidates(
    db: AsyncSession, company_id: uuid.UUID, target: str
) -> list[Candidate]:
    """Existing records of ``target`` in this company, ready for matching."""
    if target == "UOM":
        rows = (await db.execute(select(UOM))).scalars().all()
        return [Candidate(r.id, r.uom_name, normalise_name(r.uom_name)) for r in rows]
    if target == "Currency":
        rows = (await db.execute(select(Currency))).scalars().all()
        candidates = []
        for row in rows:
            extra = {"symbol": row.symbol, "currency_name": row.currency_name}
            candidates.append(Candidate(row.id, row.code, normalise_name(row.code), extra))
            # Tally names currencies by symbol ("₹") or full name, not ISO code.
            candidates.append(
                Candidate(row.id, row.currency_name, normalise_name(row.currency_name), extra)
            )
        return candidates
    entry = TARGET_MODELS.get(target)
    if entry is None:
        return []
    model, name_field = entry
    rows = (
        (await db.execute(select(model).where(model.company_id == company_id))).scalars().all()
    )
    candidates = []
    for row in rows:
        extra: dict[str, Any] = {}
        if target == "Account":
            extra = {"root_type": row.root_type, "account_type": row.account_type,
                     "is_group": row.is_group}
        elif target == "Item":
            # Items are findable by either code or name — Tally only has a name.
            extra = {"item_code": row.item_code}
            candidates.append(Candidate(row.id, row.item_code, normalise_name(row.item_code), extra))
        name = getattr(row, name_field)
        candidates.append(Candidate(row.id, name, normalise_name(name), extra))
    return candidates


def best_match(
    tally_name: str, candidates: Sequence[Candidate]
) -> tuple[Candidate | None, str, int]:
    """Pick the best candidate for a Tally name.

    Returns ``(candidate, method, confidence)``; ``candidate`` is None when
    nothing cleared :data:`CONFIDENCE_FUZZY_MIN`.
    """
    if not tally_name:
        return None, "created", CONFIDENCE_NEW
    folded = tally_name.strip().casefold()
    normalised = normalise_name(tally_name)

    exact = next((c for c in candidates if c.name.strip().casefold() == folded), None)
    if exact is not None:
        return exact, "exact", CONFIDENCE_EXACT

    same_core = [c for c in candidates if c.normalised and c.normalised == normalised]
    if same_core:
        return same_core[0], "normalised", CONFIDENCE_NORMALISED

    scored = [(similarity(normalised, c.normalised), c) for c in candidates if c.normalised]
    if scored:
        score, candidate = max(scored, key=lambda pair: pair[0])
        if score >= CONFIDENCE_FUZZY_MIN:
            return candidate, "fuzzy", score
    return None, "created", CONFIDENCE_NEW


# --------------------------------------------------------------------------------------
# Persisted mappings
# --------------------------------------------------------------------------------------


async def list_mappings(
    db: AsyncSession,
    company_id: uuid.UUID,
    *,
    entity_key: str | None = None,
    unresolved_only: bool = False,
    search: str | None = None,
) -> list[TallyMapping]:
    stmt = select(TallyMapping).where(TallyMapping.company_id == company_id)
    if entity_key:
        stmt = stmt.where(TallyMapping.entity_key == entity_key)
    if unresolved_only:
        stmt = stmt.where(TallyMapping.target_id.is_(None))
    if search:
        stmt = stmt.where(TallyMapping.tally_name.ilike(f"%{search}%"))
    stmt = stmt.order_by(TallyMapping.entity_key, TallyMapping.confidence, TallyMapping.tally_name)
    return list((await db.execute(stmt)).scalars().all())


async def get_mapping(
    db: AsyncSession, company_id: uuid.UUID, entity_key: str, tally_name: str
) -> TallyMapping | None:
    stmt = select(TallyMapping).where(
        TallyMapping.company_id == company_id,
        TallyMapping.entity_key == entity_key,
        TallyMapping.tally_name == tally_name,
    )
    return (await db.execute(stmt)).scalar_one_or_none()


async def upsert_mapping(
    db: AsyncSession,
    company_id: uuid.UUID,
    user: CurrentUser,
    *,
    entity_key: str,
    tally_name: str,
    target_doctype: str,
    target_id: uuid.UUID | None = None,
    target_name: str | None = None,
    tally_guid: str | None = None,
    tally_parent: str | None = None,
    match_method: str = "auto",
    confidence: int = 0,
    attributes: dict[str, Any] | None = None,
    lock: bool = False,
) -> TallyMapping:
    """Create or refresh a mapping. Locked rows are never overwritten by auto-map."""
    if target_doctype not in MAPPING_TARGETS:
        raise ValidationError(
            f"target_doctype must be one of {', '.join(MAPPING_TARGETS)}", field="target_doctype"
        )
    existing = await get_mapping(db, company_id, entity_key, tally_name)
    if existing is not None:
        if existing.is_locked and not lock:
            return existing  # a tester's choice wins over any later proposal
        existing.target_doctype = target_doctype
        existing.target_id = target_id
        existing.target_name = target_name
        existing.match_method = match_method
        existing.confidence = CONFIDENCE_LOCKED if lock else confidence
        existing.tally_guid = tally_guid or existing.tally_guid
        existing.tally_parent = tally_parent or existing.tally_parent
        if attributes:
            existing.attributes = {**(existing.attributes or {}), **attributes}
        existing.is_locked = existing.is_locked or lock
        existing.modified_by = user.id
        return existing

    mapping = TallyMapping(
        id=uuid.uuid4(),
        company_id=company_id,
        entity_key=entity_key,
        tally_name=tally_name,
        tally_guid=tally_guid,
        tally_parent=tally_parent,
        target_doctype=target_doctype,
        target_id=target_id,
        target_name=target_name,
        match_method="manual" if lock else match_method,
        confidence=CONFIDENCE_LOCKED if lock else confidence,
        is_locked=lock,
        attributes=attributes,
        owner=user.id,
        modified_by=user.id,
    )
    db.add(mapping)
    await db.flush()
    return mapping


async def set_mapping(
    db: AsyncSession,
    company_id: uuid.UUID,
    user: CurrentUser,
    mapping_id: uuid.UUID,
    *,
    target_doctype: str,
    target_id: uuid.UUID | None,
    notes: str | None = None,
) -> TallyMapping:
    """A tester's explicit decision — locked so auto-map leaves it alone."""
    mapping = await db.get(TallyMapping, mapping_id)
    if mapping is None or mapping.company_id != company_id:
        raise NotFoundError("Tally mapping not found")
    if target_doctype not in MAPPING_TARGETS:
        raise ValidationError(
            f"target_doctype must be one of {', '.join(MAPPING_TARGETS)}", field="target_doctype"
        )
    target_name = None
    if target_id is not None and target_doctype in TARGET_MODELS:
        model, name_field = TARGET_MODELS[target_doctype]
        record = await db.get(model, target_id)
        if record is None or getattr(record, "company_id", company_id) != company_id:
            raise NotFoundError(f"{target_doctype} not found in this company")
        target_name = getattr(record, name_field)
    mapping.target_doctype = target_doctype
    mapping.target_id = target_id
    mapping.target_name = target_name
    mapping.match_method = "manual"
    mapping.confidence = CONFIDENCE_LOCKED
    mapping.is_locked = True
    if notes is not None:
        mapping.notes = notes
    mapping.modified_by = user.id
    await db.flush()
    return mapping


async def delete_mapping(db: AsyncSession, company_id: uuid.UUID, mapping_id: uuid.UUID) -> None:
    mapping = await db.get(TallyMapping, mapping_id)
    if mapping is None or mapping.company_id != company_id:
        raise NotFoundError("Tally mapping not found")
    await db.delete(mapping)


# --------------------------------------------------------------------------------------
# Auto-mapping
# --------------------------------------------------------------------------------------


@dataclass
class AutoMapResult:
    entity_key: str
    matched: int = 0
    to_create: int = 0
    locked: int = 0
    low_confidence: int = 0


async def auto_map_entity(
    db: AsyncSession,
    company_id: uuid.UUID,
    user: CurrentUser,
    entity_key: str,
    names: Iterable[tuple[str, str | None, str | None]],
) -> AutoMapResult:
    """Propose a mapping for every Tally name of one entity.

    ``names`` yields ``(tally_name, tally_guid, tally_parent)``. Existing locked
    mappings are left untouched and counted separately.
    """
    target = ENTITY_TARGETS.get(entity_key)
    result = AutoMapResult(entity_key=entity_key)
    if target is None:
        return result

    candidates = await load_candidates(db, company_id, target)
    by_guid = {
        m.tally_guid: m
        for m in await list_mappings(db, company_id, entity_key=entity_key)
        if m.tally_guid
    }

    seen: set[str] = set()
    for tally_name, tally_guid, tally_parent in names:
        cleaned = (tally_name or "").strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)

        existing = await get_mapping(db, company_id, entity_key, cleaned)
        if existing is not None and existing.is_locked:
            result.locked += 1
            continue

        # A GUID we've mapped before beats any name comparison — Tally names get
        # renamed, GUIDs don't.
        prior = by_guid.get(tally_guid) if tally_guid else None
        if prior is not None and prior.target_id is not None:
            await upsert_mapping(
                db, company_id, user,
                entity_key=entity_key, tally_name=cleaned, tally_guid=tally_guid,
                tally_parent=tally_parent, target_doctype=prior.target_doctype,
                target_id=prior.target_id, target_name=prior.target_name,
                match_method="guid", confidence=CONFIDENCE_GUID,
            )
            result.matched += 1
            continue

        if target == "UOM":
            # UOMs match on Tally's symbol vocabulary, not on fuzzy text.
            wanted = normalise_unit(cleaned) or cleaned
            match = next((c for c in candidates if c.name.casefold() == wanted.casefold()), None)
            candidate, method, confidence = (
                (match, "exact", CONFIDENCE_EXACT) if match else (None, "created", CONFIDENCE_NEW)
            )
        else:
            candidate, method, confidence = best_match(cleaned, candidates)

        await upsert_mapping(
            db, company_id, user,
            entity_key=entity_key, tally_name=cleaned, tally_guid=tally_guid,
            tally_parent=tally_parent, target_doctype=target,
            target_id=candidate.id if candidate else None,
            target_name=candidate.name if candidate else cleaned,
            match_method=method, confidence=confidence,
        )
        if candidate is not None:
            result.matched += 1
            if confidence < CONFIDENCE_NORMALISED:
                result.low_confidence += 1
        else:
            result.to_create += 1
    return result


# --------------------------------------------------------------------------------------
# Read side used by the importers
# --------------------------------------------------------------------------------------


class MappingBook:
    """In-memory name -> record lookup for one import run.

    Loaded once at the start of a run; importers register newly created records
    back into it so later rows (and later entities) resolve without a round trip.
    """

    def __init__(self, company_id: uuid.UUID) -> None:
        self.company_id = company_id
        self._by_entity: dict[str, dict[str, TallyMapping]] = {}
        #: names we could not resolve, reported on the session
        self.unresolved: set[tuple[str, str]] = set()

    @classmethod
    async def load(cls, db: AsyncSession, company_id: uuid.UUID) -> "MappingBook":
        book = cls(company_id)
        for mapping in await list_mappings(db, company_id):
            book._put(mapping.entity_key, mapping.tally_name, mapping)
        return book

    def _put(self, entity_key: str, name: str, mapping: TallyMapping) -> None:
        bucket = self._by_entity.setdefault(entity_key, {})
        bucket[(name or "").strip().casefold()] = mapping

    def get(self, entity_key: str, name: str | None) -> TallyMapping | None:
        if not name:
            return None
        return self._by_entity.get(entity_key, {}).get(name.strip().casefold())

    def resolve(self, entity_key: str, name: str | None) -> uuid.UUID | None:
        """The OptiERP id for a Tally name, or None (recorded as unresolved)."""
        mapping = self.get(entity_key, name)
        if mapping is None or mapping.target_id is None:
            if name:
                self.unresolved.add((entity_key, name.strip()))
            return None
        return mapping.target_id

    def bind(
        self,
        db_mapping: TallyMapping | None,
        entity_key: str,
        name: str,
        target_id: uuid.UUID,
        target_name: str | None = None,
    ) -> None:
        """Record that a Tally name now points at a real record."""
        if db_mapping is not None:
            db_mapping.target_id = target_id
            db_mapping.target_name = target_name or name
            if db_mapping.match_method == "created":
                db_mapping.confidence = CONFIDENCE_EXACT
            self._put(entity_key, name, db_mapping)
            return
        placeholder = TallyMapping(
            company_id=self.company_id,
            entity_key=entity_key,
            tally_name=name,
            target_doctype=ENTITY_TARGETS.get(entity_key, "Account"),
            target_id=target_id,
            target_name=target_name or name,
            match_method="created",
            confidence=CONFIDENCE_EXACT,
        )
        self._put(entity_key, name, placeholder)

    # Convenience accessors used throughout the importers ------------------------------
    def account(self, name: str | None) -> uuid.UUID | None:
        return self.resolve("ledger", name) or self.resolve("group", name)

    def customer(self, name: str | None) -> uuid.UUID | None:
        return self.resolve("customer", name)

    def supplier(self, name: str | None) -> uuid.UUID | None:
        return self.resolve("supplier", name)

    def item(self, name: str | None) -> uuid.UUID | None:
        return self.resolve("stock_item", name)

    def warehouse(self, name: str | None) -> uuid.UUID | None:
        return self.resolve("godown", name)

    def cost_center(self, name: str | None) -> uuid.UUID | None:
        return self.resolve("cost_centre", name)

    def party_type_of(self, ledger_name: str | None) -> str | None:
        """Is this Tally ledger a Customer, a Supplier, or neither?"""
        if self.get("customer", ledger_name) is not None:
            return "Customer"
        if self.get("supplier", ledger_name) is not None:
            return "Supplier"
        return None


def entity_label(entity_key: str) -> str:
    spec = ENTITY_BY_KEY.get(entity_key)
    return spec.label if spec else entity_key
