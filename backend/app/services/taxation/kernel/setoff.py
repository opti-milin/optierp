"""Loss set-off — pure functions over character nets + brought-forward losses."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.taxation.kernel.money import ZERO, money, q

# Statutory-ish order for corporate: unabsorbed dep and business loss against ordinary.
_SETOFF_ORDER = (
    "UnabsorbedDep",
    "Business",
    "Speculation",
    "STCG",
    "LTCG",
    "OS",
)


@dataclass(frozen=True, slots=True)
class BroughtForwardLoss:
    ledger_id: str
    setoff_group: str
    loss_kind: str
    amount_remaining: Decimal
    origin_ay_code: str
    expires_after_ay: str | None = None


@dataclass(frozen=True, slots=True)
class SetoffApplication:
    ledger_id: str
    loss_kind: str
    against_character: str
    amount: Decimal
    sequence: int


@dataclass(frozen=True, slots=True)
class SetoffResult:
    character_nets: dict[str, Decimal]
    applications: tuple[SetoffApplication, ...]
    total_set_off: Decimal


def _can_setoff(loss_kind: str, character: str, setoff_group: str) -> bool:
    if loss_kind in ("UnabsorbedDep", "Business"):
        return character in ("ORDINARY",) or setoff_group == "ORDINARY"
    if loss_kind == "STCG":
        return setoff_group == "STCG" or character.startswith("STCG")
    if loss_kind == "LTCG":
        return setoff_group == "LTCG" or character.startswith("LTCG")
    if loss_kind == "OS":
        return setoff_group == "OS" or character in ("DIVIDEND", "OS")
    return False


def apply_loss_setoff(
    character_nets: dict[str, Decimal],
    losses: tuple[BroughtForwardLoss, ...],
    *,
    current_ay: str,
) -> SetoffResult:
    """Apply BF losses in order against positive character nets; returns new nets + apps."""
    nets = {k: q(money(v)) for k, v in character_nets.items()}
    remaining = {
        loss.ledger_id: q(money(loss.amount_remaining))
        for loss in losses
        if loss.amount_remaining > ZERO
        and (loss.expires_after_ay is None or loss.expires_after_ay >= current_ay)
    }
    apps: list[SetoffApplication] = []
    seq = 0

    ordered = sorted(
        losses,
        key=lambda L: (
            _SETOFF_ORDER.index(L.loss_kind) if L.loss_kind in _SETOFF_ORDER else 99,
            L.origin_ay_code,
        ),
    )

    for loss in ordered:
        bal = remaining.get(loss.ledger_id, ZERO)
        if bal <= ZERO:
            continue
        for character, amt in list(nets.items()):
            if amt <= ZERO:
                continue
            if not _can_setoff(loss.loss_kind, character, loss.setoff_group):
                continue
            use = min(bal, amt)
            if use <= ZERO:
                continue
            nets[character] = q(amt - use)
            bal = q(bal - use)
            remaining[loss.ledger_id] = bal
            apps.append(
                SetoffApplication(
                    ledger_id=loss.ledger_id,
                    loss_kind=loss.loss_kind,
                    against_character=character,
                    amount=use,
                    sequence=seq,
                )
            )
            seq += 1
            if bal <= ZERO:
                break

    total = q(sum((a.amount for a in apps), ZERO))
    return SetoffResult(character_nets=nets, applications=tuple(apps), total_set_off=total)
