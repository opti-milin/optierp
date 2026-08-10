"""Unit tests for the option-label contract the workspace dropdowns and grids rely on.

The depreciation grid shows a block's business name in its own column and the rate in
another, so ``meta["title"]`` must carry the bare title while ``label`` carries the
rate-suffixed form used by dropdowns. A block code must never be the visible label.
"""

from __future__ import annotations

from decimal import Decimal

from app.models import statutory as stat
from app.services.taxation.lookups import _block_options


def _block(block_code: str, title: str, rate: str, *, additional: bool = False) -> stat.DepreciationBlock:
    return stat.DepreciationBlock(
        block_code=block_code,
        title=title,
        rate_percent=Decimal(rate),
        additional_depreciation_eligible=additional,
    )


def test_block_option_label_is_business_title_with_rate() -> None:
    (option,) = _block_options([_block("PLANT_15", "Plant and machinery — general", "15.0000")])

    assert option.value == "PLANT_15"
    assert option.label == "Plant and machinery — general — 15%"
    assert option.statutory_ref == "Section 32"


def test_block_option_meta_carries_bare_title_for_grid_columns() -> None:
    (option,) = _block_options([_block("BUILDING_10", "Buildings — other", "10.0000")])

    # The grid renders meta["title"]; it must not repeat the rate its own column shows.
    assert option.meta["title"] == "Buildings — other"
    assert "%" not in option.meta["title"]
    assert option.meta["rate_percent"] == "10"


def test_block_option_never_shows_a_bare_code_to_the_user() -> None:
    options = _block_options(
        [
            _block("PLANT_40", "Computers / software / wind mills etc.", "40.0000"),
            _block("FURNITURE_10", "Furniture and fittings", "10.0000"),
        ]
    )

    for option in options:
        assert option.label != option.value
        assert option.meta["title"] != option.value


def test_block_option_reports_additional_depreciation_eligibility() -> None:
    eligible, not_eligible = _block_options(
        [
            _block("PLANT_15", "Plant and machinery — general", "15.0000", additional=True),
            _block("BUILDING_10", "Buildings — other", "10.0000"),
        ]
    )

    assert eligible.meta["additional_depreciation_eligible"] == "true"
    assert not_eligible.meta["additional_depreciation_eligible"] == "false"
