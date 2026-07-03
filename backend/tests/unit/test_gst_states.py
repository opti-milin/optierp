"""Unit: GST state-code derivation from a GSTIN (pure, no DB)."""

from app.core.gst_states import gst_state_label_of, state_code_of, state_of_gstin

VALID = "27ABCDE1234F1Z5"  # Maharashtra (state code 27)


def test_state_code_of_valid():
    assert state_code_of(VALID) == "27"


def test_state_code_of_invalid():
    assert state_code_of(None) is None
    assert state_code_of("") is None
    assert state_code_of("27ABC") is None  # too short
    assert state_code_of("XXABCDE1234F1Z5") is None  # non-digit state code


def test_state_of_gstin():
    assert state_of_gstin(VALID) == "Maharashtra"
    assert state_of_gstin("07AAACX1234A1Z0") == "Delhi"
    assert state_of_gstin("99ABCDE1234F1Z5") == "Centre Jurisdiction"
    assert state_of_gstin("XXABCDE1234F1Z5") is None


def test_gst_state_label():
    assert gst_state_label_of(VALID) == "27-Maharashtra"
    assert gst_state_label_of("33AAAAA0000A1Z5") == "33-Tamil Nadu"
    assert gst_state_label_of(None) is None
