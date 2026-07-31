"""Manufacturing Settings — outbound_delivery_days sanitize + update key allow-list."""

from app.services.manufacturing_common import MFG_SETTINGS_DEFAULTS, _sanitize_mfg_settings


def test_sanitize_preserves_outbound_delivery_days() -> None:
    out = _sanitize_mfg_settings({"outbound_delivery_days": 5})
    assert out["outbound_delivery_days"] == 5


def test_sanitize_clamps_outbound_delivery_days() -> None:
    assert _sanitize_mfg_settings({"outbound_delivery_days": -3})["outbound_delivery_days"] == 0
    assert _sanitize_mfg_settings({"outbound_delivery_days": 999})["outbound_delivery_days"] == 365
    assert _sanitize_mfg_settings({"outbound_delivery_days": "7"})["outbound_delivery_days"] == 7
    assert _sanitize_mfg_settings({"outbound_delivery_days": "nope"})["outbound_delivery_days"] == 0


def test_outbound_key_is_in_defaults_allow_list() -> None:
    """PUT /manufacturing/settings only persists keys present in MFG_SETTINGS_DEFAULTS."""
    assert "outbound_delivery_days" in MFG_SETTINGS_DEFAULTS
    assert MFG_SETTINGS_DEFAULTS["outbound_delivery_days"] == 0
