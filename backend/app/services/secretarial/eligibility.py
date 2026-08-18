"""Rule 5 — which matters may not be decided by circulation.

Rule 5 of the Companies (Meetings of Board and its Powers) Rules, 2014 lists powers the
Board may exercise only at a meeting. Trying to pass one of them by circulation produces
a resolution that is void, and nobody notices until it matters.

So this is a **blocking** check placed before the irreversible step, not a warning after
it. The plan calls this pattern guardrail-before-action (§10.2): the user is stopped
while the mistake is still cheap, and told which meeting to hold instead.

Detection is keyword-based over the resolution text and title. That is deliberately
conservative — it errs towards flagging, because a false flag costs a click and a false
pass costs a void resolution. The result is stored on the circular either way, so the
decision is auditable rather than merely obstructive.
"""

from __future__ import annotations

import re
from typing import Any

# (matter, statutory reference, phrases that indicate it)
RESTRICTED_MATTERS: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "Making calls on shareholders in respect of money unpaid on their shares",
        "Rule 5 / s.179(3)(a)",
        ("make calls on shareholder", "making calls on shareholder", "call on unpaid", "calls on shares"),
    ),
    (
        "Authorising the buy-back of securities",
        "Rule 5 / s.179(3)(b)",
        ("buy-back", "buyback", "buy back of securities", "repurchase of shares"),
    ),
    (
        "Issuing securities, including debentures, whether in or outside India",
        "Rule 5 / s.179(3)(c)",
        ("issue of debenture", "issuing debenture", "issue securities", "issue of securities", "further issue of shares"),
    ),
    (
        "Borrowing monies",
        "Rule 5 / s.179(3)(d)",
        ("borrow monies", "borrowing of monies", "avail credit facility", "sanction of term loan", "borrowing powers"),
    ),
    (
        "Investing the funds of the company",
        "Rule 5 / s.179(3)(e)",
        ("invest the funds", "investment of funds", "investing the funds of the company"),
    ),
    (
        "Granting loans or giving guarantees or providing security in respect of loans",
        "Rule 5 / s.179(3)(f)",
        ("grant a loan", "granting of loan", "give guarantee", "giving guarantee", "corporate guarantee", "provide security in respect of"),
    ),
    (
        "Approving the financial statements and the Board's Report",
        "Rule 5 / s.179(3)(g)",
        ("financial statement", "balance sheet", "board's report", "boards report", "directors' report", "annual account"),
    ),
    (
        "Diversifying the business of the company",
        "Rule 5 / s.179(3)(h)",
        ("diversify the business", "diversification of business"),
    ),
    (
        "Approving amalgamation, merger or reconstruction",
        "Rule 5 / s.179(3)(i)",
        ("amalgamation", "merger", "demerger", "scheme of arrangement", "reconstruction"),
    ),
    (
        "Taking over another company or acquiring a controlling interest",
        "Rule 5 / s.179(3)(j)",
        ("takeover", "take over another company", "controlling interest", "acquisition of control"),
    ),
    (
        "Approving a prospectus or offer document",
        "Rule 5",
        ("prospectus", "red herring", "offer document", "letter of offer"),
    ),
    (
        "Making political contributions",
        "Rule 5 / s.182",
        ("political contribution", "political part"),
    ),
    (
        "Appointing or removing key managerial personnel",
        "Rule 5 / s.203",
        ("appointment of key managerial", "removal of key managerial", "appoint the managing director", "appointment of managing director", "appointment of whole-time director", "appointment of chief financial officer", "appointment of company secretary"),
    ),
    (
        "Appointing internal or secretarial auditors",
        "Rule 5",
        ("internal auditor", "secretarial auditor", "cost auditor"),
    ),
]


def _normalise(text: str) -> str:
    """Lower-case and collapse whitespace so phrase matching is not defeated by
    line breaks in a pasted resolution."""
    return re.sub(r"\s+", " ", (text or "").lower())


def check(title: str, resolution_text: str, description: str | None = None) -> dict[str, Any]:
    """Evaluate a proposed circular. Returns the stored eligibility result.

    ``eligible`` False means the caller must refuse to circulate.
    """
    haystack = " ".join(_normalise(t) for t in (title, description or "", resolution_text))

    hits: list[dict[str, str]] = []
    for matter, reference, phrases in RESTRICTED_MATTERS:
        for phrase in phrases:
            if phrase in haystack:
                hits.append({"matter": matter, "reference": reference, "matched": phrase})
                break

    if not hits:
        return {
            "eligible": True,
            "blocked_matters": [],
            "message": "No restricted matter detected. This resolution may be passed by circulation.",
        }

    listed = "; ".join(f"{h['matter']} ({h['reference']})" for h in hits)
    return {
        "eligible": False,
        "blocked_matters": hits,
        "message": (
            "This resolution appears to concern a matter the Board may only decide at a meeting: "
            f"{listed}. Passing it by circulation would make the resolution void. "
            "Convene a board meeting instead."
        ),
    }


def override_note(hits: list[dict[str, str]]) -> str:
    """Wording recorded when a user insists the flag is wrong.

    The check is keyword-based and will sometimes be wrong — a resolution can mention
    "merger" in passing without being about one. A professional can proceed, but the
    override is written into the record with what was flagged, so the judgement is
    attributable rather than invisible.
    """
    matters = ", ".join(h["matter"] for h in hits)
    return (
        f"Rule 5 check flagged: {matters}. Overridden on the user's judgement that the "
        f"resolution does not in substance concern a restricted matter."
    )
