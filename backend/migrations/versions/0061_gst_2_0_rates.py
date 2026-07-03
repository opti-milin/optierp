"""GST 2.0 rate rationalisation (effective 22 Sep 2025) for the HSN master.

The 56th GST Council abolished the 12% and 28% slabs and added a 40% slab for
sin/luxury goods. This re-rates the existing ``hsn_codes`` rows to match the
regenerated seed data (rate-only — descriptions are untouched, so no duplicates):

* 12% → 18% for apparel/footwear (ch 61-64; the >threshold tier), else → 5%
* 28% → 18%
* → 40% for tobacco (ch 24), pan masala (2106.90.20) and aerated/sugary/
  caffeinated beverages (2202), regardless of prior rate.

Note: engine-capacity/value-specific luxury (large cars 8703, big bikes 8711,
private aircraft) is NOT auto-set to 40% here — those need the CBIC notification's
sub-heading detail; set them manually. The downgrade cannot restore the original
12%/28% split (the merge is lossy), so it is a documented no-op.

Revision ID: 0061_gst_2_0_rates
Revises: 0060_hsn_codes
Create Date: 2026-07-02
"""

from typing import Sequence, Union

from alembic import op

revision: str = "0061_gst_2_0_rates"
down_revision: Union[str, None] = "0060_hsn_codes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Order matters: garment 12→18 before the general 12→5; the 40% overrides
    # run last so they win regardless of the interim rate.
    op.execute(
        "UPDATE hsn_codes SET gst_rate = 18 "
        "WHERE gst_rate = 12 AND left(hsn_code, 2) IN ('61','62','63','64')"
    )
    op.execute("UPDATE hsn_codes SET gst_rate = 5 WHERE gst_rate = 12")
    op.execute("UPDATE hsn_codes SET gst_rate = 18 WHERE gst_rate = 28")
    op.execute(
        "UPDATE hsn_codes SET gst_rate = 40 "
        "WHERE left(hsn_code, 2) = '24' OR hsn_code = '21069020' OR left(hsn_code, 4) = '2202'"
    )


def downgrade() -> None:
    # The 12/28 → 5/18/40 merge is not reversible (the original slab is lost).
    pass
