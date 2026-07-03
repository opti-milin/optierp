"""Unit: curated trade-name → HSN alias resolution (no DB)."""

from app.core.hsn_aliases import _ALIAS_MAP, lookup_alias_codes


def test_single_word_alias_resolves():
    assert "84180000" in lookup_alias_codes("fridge")
    assert "84180000" in lookup_alias_codes("Refrigerator")  # case-insensitive


def test_multi_word_alias_matches_as_phrase():
    assert "84150000" in lookup_alias_codes("air conditioner")
    # embedded in a longer item name
    assert "84150000" in lookup_alias_codes("Voltas 1.5 ton air conditioner split")


def test_short_alias_needs_whole_word():
    # "ac" must not fire on words that merely contain it
    assert "84150000" not in lookup_alias_codes("jacket")
    assert "84150000" not in lookup_alias_codes("backup drive")
    # but does when it stands alone
    assert "84150000" in lookup_alias_codes("split ac 2 ton")


def test_common_salt_maps_to_nil_rated_heading():
    # correctness: edible/common salt is 2501.00.10 (0%), not 25010000 (5%)
    assert lookup_alias_codes("salt") == ["25010010"]


def test_no_alias_returns_empty():
    assert lookup_alias_codes("zznotacommodity") == []
    assert lookup_alias_codes("") == []
    assert lookup_alias_codes("   ") == []


def test_every_alias_code_is_eight_digits():
    for code in _ALIAS_MAP:
        assert len(code) == 8 and code.isdigit()
