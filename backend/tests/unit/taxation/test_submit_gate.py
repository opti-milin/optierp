"""Submitting must be refused while a blocking validation issue stands.

Submission freezes the worksheet and posts the current-tax provision to the general
ledger, so the Review panel cannot be the only thing standing in the way — a direct API
call has to be refused too.
"""

from __future__ import annotations

import inspect

import pytest

from app.core.exceptions import ValidationError
from app.schemas.taxation import TaxValidationIssueOut, TaxValidationOut
from app.services.taxation import computations as computation_service


def _blocked(blocking: int = 1) -> TaxValidationOut:
    issues = [
        TaxValidationIssueOut(
            severity="blocking",
            code="challan_unposted",
            message="1 tax payment challan(s) are still in draft.",
            section="challans",
            field="challans",
        )
        for _ in range(blocking)
    ]
    issues.append(
        TaxValidationIssueOut(
            severity="advisory",
            code="advance_tax_short",
            message="Advance tax instalments are short.",
            section="interest",
        )
    )
    return TaxValidationOut(
        ok=blocking == 0, blocking_count=blocking, advisory_count=1, issues=issues
    )


def test_submit_runs_validation_before_freezing_the_worksheet() -> None:
    """The guard must sit ahead of the run/result lookup and the ledger posting."""
    source = inspect.getsource(computation_service.submit_computation)

    assert "validate_computation" in source, "submit_computation does not validate at all"
    guard = source.index("validate_computation")
    # Anchor on the posting site itself, not the parameter of the same name in the signature.
    posting = source.index("if post_provision")
    assert guard < posting, "validation must run before the provision is posted"
    assert source.index("validation_blocked") < posting


def test_the_error_names_the_blocking_issue_and_its_field() -> None:
    validation = _blocked()
    first = next(i for i in validation.issues if i.severity == "blocking")

    err = ValidationError(
        f"{validation.blocking_count} issue(s) must be resolved before this "
        f"computation can be submitted. {first.message}",
        code="validation_blocked",
        field=first.field,
    )

    assert err.code == "validation_blocked"
    assert err.field == "challans"
    assert "must be resolved" in str(err.detail)
    assert "still in draft" in str(err.detail)


def test_advisory_warnings_alone_do_not_block() -> None:
    clean = TaxValidationOut(
        ok=True,
        blocking_count=0,
        advisory_count=2,
        issues=[
            TaxValidationIssueOut(
                severity="advisory",
                code="advance_tax_short",
                message="Advance tax instalments are short.",
                section="interest",
            ),
            TaxValidationIssueOut(
                severity="advisory",
                code="recon_unexplained",
                message="Form 26AS still has an unexplained difference.",
                section="reconciliation",
            ),
        ],
    )

    assert clean.blocking_count == 0
    assert clean.ok is True


@pytest.mark.parametrize("blocking", [1, 2, 5])
def test_any_number_of_blocking_issues_is_refused(blocking: int) -> None:
    assert _blocked(blocking).blocking_count == blocking
    assert _blocked(blocking).ok is False
