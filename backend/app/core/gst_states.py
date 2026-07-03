"""GST state codes (the first 2 digits of a GSTIN) → state name.

Used to derive a GSTIN's state and the ``NN-State Name`` label the GST portal expects.
Single source of truth, reused across the India compliance layer (GST Settings,
place-of-supply defaulting on invoices, GSTR-1/3B).

Note on terminology: this derives the **state of a given GSTIN**. A company's own GSTIN
gives its *registered state*; a transaction's *place of supply* is the **recipient's**
state — a per-invoice value (Phase 1), not the supplier's state computed here.
"""

import re

# Statutory GST state/UT codes (incl. merged DNHDD = 26, new AP = 37, Ladakh = 38,
# Other Territory = 97, Centre = 99). 25/28 are legacy but kept for old data.
GST_STATE_CODES: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "25": "Daman and Diu",  # legacy (merged into 26)
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "28": "Andhra Pradesh",  # legacy (pre-bifurcation; new AP is 37)
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
    "99": "Centre Jurisdiction",
}


# How an item/line is treated under GST — drives the GSTR-1 bucket + whether tax applies.
GST_TREATMENTS = ("Taxable", "Nil-Rated", "Exempt", "Non-GST")
# Single source of truth for schema validators (keep them from drifting from GST_TREATMENTS).
GST_TREATMENT_PATTERN = "^(" + "|".join(re.escape(t) for t in GST_TREATMENTS) + ")$"


def state_code_of(gstin: str | None) -> str | None:
    """The 2-digit state code of a GSTIN, or None if it isn't a valid 15-char GSTIN."""
    if not gstin:
        return None
    g = gstin.strip()
    if len(g) != 15 or not g[:2].isdigit():
        return None
    return g[:2]


def state_of_gstin(gstin: str | None) -> str | None:
    """The state name for a GSTIN's state code (``None`` if unknown/invalid)."""
    code = state_code_of(gstin)
    return GST_STATE_CODES.get(code) if code else None


def gst_state_label_of(gstin: str | None) -> str | None:
    """The portal-style ``NN-State Name`` label (``"27-Maharashtra"``) for a GSTIN's state.

    For a company's own GSTIN this is its *registered* state. The per-invoice *place of
    supply* (recipient's state) reuses this formatter on the recipient's GSTIN (Phase 1).
    """
    code = state_code_of(gstin)
    if code is None:
        return None
    name = GST_STATE_CODES.get(code)
    return f"{code}-{name}" if name else None
