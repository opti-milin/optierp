"""Drive every Secretarial submodule through a full lifecycle, Phases 0-5.

``seed_secretarial_scenario`` leaves the module *populated*: one row in each register,
a draft meeting, two circulars. That is enough to prove a screen renders and not much
else. Every interesting state in this module is a state a record reaches by being worked
on — minutes with real book numbers, a circular carried by actual consents, a certificate
cancelled because shares moved — and none of those exist in a freshly seeded tenant.

This script does the working. It is the second half of the demo seed and assumes the
first has run:

    python -m scripts.seed_secretarial_demo
    python -m scripts.seed_secretarial_scenario
    python -m scripts.seed_secretarial_deep

Idempotent, and safe to re-run: everything is keyed on a title, a folio or a counterparty
name, and an object already past the state this script would put it in is left alone.

What it leaves behind, per submodule:

* **Meetings** - a board meeting, an EGM and an AGM taken notice -> circulated -> held
  (with attendance and a quorum verdict) -> minutes drafted -> minutes signed with
  consecutive book and page numbers. General meetings share one book, so the AGM follows
  the EGM at entry 2. The Q2 board meeting stays a draft, so both ends of the machine are
  on screen.
* **Circulations** - the board papers sent with per-director tracked links, one recipient
  left `pending`, one `viewed`, one `acknowledged`.
* **Circular resolutions** - the eligible one circulated, carried by real consent
  responses, and placed on a board agenda for ratification. The Rule-5 blocked one is
  left blocked, because that is the demo.
* **CTCs** - one issued off the signed minutes, and a correction that supersedes it with
  a stated reason, which is the only way a CTC is ever changed.
* **Documents / files** - notice, minutes and attendance rendered from the one agenda; a
  challan attached as evidence.
* **Filings** - one with a complete evidence chain (resolution -> document -> SRN ->
  challan) and one deliberately missing its challan, so "on what authority?" has
  something to report.
* **Facts** - three financial years, so the figures have history.
* **Capital (Phase 5)** - certificates tiling the issued capital with distinctive
  numbers, an SH-4 transfer taken through board approval to posting with the certificate
  cancelled and reissued over the same range, a rights issue allotted with certificates
  cut, an ESOP grant, and a declared dividend that passed the s.123 test.
* **s.186** - a register with an exempt wholly-owned-subsidiary loan, entries inside the
  ceiling, one refused for breaching it, and the special resolution that then allows it.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import os
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--database-url",
        default=os.environ.get("SEED_DATABASE_URL")
        or os.environ.get("MIGRATIONS_DATABASE_URL")
        or os.environ.get("DATABASE_URL"),
    )
    return parser.parse_args()


ARGS = _parse_args()
if ARGS.database_url:
    os.environ["DATABASE_URL"] = ARGS.database_url

from sqlalchemy import select  # noqa: E402

from app.core.database import async_session_factory, set_company_context  # noqa: E402
from app.core.exceptions import ValidationError  # noqa: E402
from app.core.security import CurrentUser  # noqa: E402
from app.models.accounts import ShareTransfer, ShareType, Shareholder  # noqa: E402
from app.models.core import Company, User  # noqa: E402
from app.models.secretarial import (  # noqa: E402
    SecretarialAppointment,
    SecretarialCapitalEvent,
    SecretarialCirculation,
    SecretarialCircular,
    SecretarialCtc,
    SecretarialEntity,
    SecretarialFile,
    SecretarialFiling,
    SecretarialMeeting,
    SecretarialMember,
    SecretarialPerson,
    SecretarialS186Entry,
    SecretarialShareCertificate,
    SecretarialShareTransferDetail,
)
from app.schemas.secretarial import FileUploadIn  # noqa: E402
from app.services.secretarial import capital as capital_service  # noqa: E402
from app.services.secretarial import circular as circular_service  # noqa: E402
from app.services.secretarial import circulation as circulation_service  # noqa: E402
from app.services.secretarial import ctc as ctc_service  # noqa: E402
from app.services.secretarial import files as file_service  # noqa: E402
from app.services.secretarial import filings as filing_service  # noqa: E402
from app.services.secretarial import financial_facts as facts_service  # noqa: E402
from app.services.secretarial import meeting as meeting_service  # noqa: E402
from app.services.secretarial import s186 as s186_service  # noqa: E402

# The year just closed: what the calendar's annual filings and the final dividend are
# *for*. The compliance seed uses the same label.
FY = "2025-26"
# The year we are actually in on the demo's clock. The s.186 ceiling is judged against
# the year a loan is made in, and every seeded loan is dated inside it, so this is the
# year those screens open on. Getting these two confused is how a demo ends up showing
# a register of entries under a ceiling that says "cannot be judged".
CURRENT_FY = "2026-27"
FACE_VALUE = Decimal("10")


def _actor(user: User, company_id) -> CurrentUser:
    return CurrentUser(
        {
            "sub": str(user.id),
            "email": user.email,
            "company_id": str(company_id),
            "roles": ["System Manager", "Company Secretary"],
        }
    )


async def _company(db, name: str) -> Company:
    row = await db.scalar(select(Company).where(Company.company_name == name))
    if row is None:
        raise SystemExit(f"Company '{name}' not found - run scripts.seed_showcase first")
    return row


async def _entity_of(db, company: Company, *, name: str | None = None) -> SecretarialEntity:
    stmt = select(SecretarialEntity).where(SecretarialEntity.company_id == company.id)
    stmt = (
        stmt.where(SecretarialEntity.entity_name == name)
        if name
        else stmt.where(SecretarialEntity.linked_company_id == company.id)
    )
    row = await db.scalar(stmt)
    if row is None:
        raise SystemExit(
            f"Secretarial entity {name or company.company_name!r} not found - "
            "run scripts.seed_secretarial_scenario first"
        )
    return row


async def _person(db, company_id, full_name: str) -> SecretarialPerson:
    row = await db.scalar(
        select(SecretarialPerson).where(
            SecretarialPerson.company_id == company_id, SecretarialPerson.full_name == full_name
        )
    )
    if row is None:
        raise SystemExit(f"Person {full_name!r} not found - run scripts.seed_secretarial_scenario")
    return row


async def _member(db, entity_id, folio: str) -> SecretarialMember:
    row = await db.scalar(
        select(SecretarialMember).where(
            SecretarialMember.entity_id == entity_id, SecretarialMember.folio_no == folio
        )
    )
    if row is None:
        raise SystemExit(f"Member folio {folio} not found - run scripts.seed_secretarial_scenario")
    return row


# --- Meetings ---------------------------------------------------------------------


async def _run_meeting(
    db,
    actor,
    entity: SecretarialEntity,
    *,
    title: str,
    meeting_type: str,
    held_at: datetime,
    notice_on: date,
    chair: SecretarialPerson,
    attendance: list[tuple[SecretarialPerson, str]],
    agenda: list[dict],
    venue: str,
    circulate_to: list[SecretarialPerson] | None = None,
) -> SecretarialMeeting:
    """Take one meeting the whole way: notice -> circulated -> held -> minutes signed.

    Written as a resume rather than a create-or-skip. Each step is guarded on the
    meeting's current status, so a meeting left half-driven by an interrupted run is
    picked up where it stopped instead of being returned mid-flight to a caller that
    then asks it for a date it does not have yet. A meeting already at
    ``minutes_signed`` passes straight through, so the minutes-book counter is never
    burned twice for the same meeting.
    """
    meeting = await db.scalar(
        select(SecretarialMeeting).where(
            SecretarialMeeting.entity_id == entity.id, SecretarialMeeting.title == title
        )
    )
    fresh = meeting is None
    if meeting is None:
        meeting = await meeting_service.create_meeting(
            db,
            actor,
            entity_id=entity.id,
            meeting_type=meeting_type,
            scheduled_at=held_at,
            venue=venue,
            title=title,
            mode="physical",
            chairperson_id=chair.id,
            fy=FY,
        )
    if not await meeting_service.get_agenda(db, meeting.id, actor.company_id):
        await meeting_service.set_agenda(db, meeting.id, agenda, actor)

    if meeting.status == "draft":
        # Notice first: the SS-1 gate on `scheduled` refuses a short notice, and passing
        # the despatch date is how a back-dated meeting is caught rather than waved through.
        meeting = await meeting_service.transition(
            db, meeting.id, "scheduled", actor, on_date=notice_on
        )
        await meeting_service.generate_pack(db, meeting.id, actor, fragments=["notice"])

    if meeting.status == "scheduled":
        # Circulating the papers is itself the `scheduled -> circulated` transition.
        await circulation_service.circulate_meeting_papers(
            db,
            meeting.id,
            actor,
            subject=f"Notice and agenda - {title}",
            message="Papers for the meeting are attached. Please acknowledge receipt.",
            person_ids=[p.id for p in circulate_to] if circulate_to else None,
            send_email=False,
        )
        meeting = await meeting_service.get_meeting(db, meeting.id, actor.company_id)

    if meeting.status == "circulated":
        await meeting_service.set_attendance(
            db,
            meeting.id,
            [
                {
                    "person_id": str(person.id),
                    "status": status,
                    "is_chairperson": person.id == chair.id,
                }
                for person, status in attendance
            ],
            actor,
        )
        meeting = await meeting_service.transition(
            db, meeting.id, "held", actor, on_date=held_at.date()
        )

    if meeting.status == "held":
        await meeting_service.generate_pack(
            db, meeting.id, actor, fragments=["minutes_narration", "attendance"]
        )
        meeting = await meeting_service.transition(
            db, meeting.id, "minutes_draft", actor, on_date=held_at.date()
        )

    if meeting.status == "minutes_draft":
        meeting = await meeting_service.transition(
            db, meeting.id, "minutes_signed", actor, on_date=held_at.date(), pages=2
        )

    if fresh or meeting.status == "minutes_signed":
        print(
            f"  meeting: {title} - {meeting.status}, entry {meeting.minutes_entry_no}, "
            f"pages {meeting.minutes_page_from}-{meeting.minutes_page_to}, "
            f"quorum {'met' if meeting.quorum_met else 'NOT met'}"
        )
    return meeting


async def _vary_circulation(db, actor, meeting: SecretarialMeeting) -> None:
    """Leave the recipients at three different states.

    A circulation where everybody is `pending` shows nothing; the whole value of tracked
    despatch is being able to see who has and has not looked at the papers.
    """
    circulation = await db.scalar(
        select(SecretarialCirculation)
        .where(SecretarialCirculation.meeting_id == meeting.id)
        .order_by(SecretarialCirculation.creation)
    )
    if circulation is None:
        return
    rows = await circulation_service.recipients(db, circulation.id, actor.company_id)
    if any(r.status != "pending" for r, _ in rows):
        return
    for index, (recipient, _name) in enumerate(rows):
        if index == 0:
            await circulation_service.acknowledge(db, recipient.id)
        elif index == 1:
            await circulation_service.mark_viewed(db, recipient.id)
    await db.commit()
    settled = await circulation_service.recipients(db, circulation.id, actor.company_id)
    summary = ", ".join(f"{name.split()[0]} {r.status}" for r, name in settled)
    print(f"  circulation: {len(settled)} recipients - {summary}")


# --- Circular resolutions ---------------------------------------------------------


async def _carry_circular(db, actor, entity: SecretarialEntity, directors) -> None:
    """Circulate the eligible resolution, collect consents, and put it up for noting."""
    circular = await db.scalar(
        select(SecretarialCircular).where(
            SecretarialCircular.entity_id == entity.id,
            SecretarialCircular.title == "Engagement of interior consultant",
        )
    )
    if circular is None or circular.status != "draft":
        return

    await circular_service.circulate(
        db,
        circular.id,
        actor,
        person_ids=[d.id for d in directors],
        expires_at=datetime.now(UTC).replace(microsecond=0),
    )
    # The expiry above is immediate only as a value; responses are recorded straight
    # away below, which is what the real flow does over a day or two.
    circular.expires_at = None
    await db.commit()

    for director in directors:
        await circular_service.record_response(
            db,
            circular_id=circular.id,
            person_id=director.id,
            status="consented",
            comments="Agreed - the fee is within the budget approved in April.",
        )

    refreshed = await db.get(SecretarialCircular, circular.id)
    print(f"  circular: 'Engagement of interior consultant' -> {refreshed.status}")

    if refreshed.status == "passed":
        _, ratifying = await circular_service.ratify(db, circular.id, actor)
        print(f"  circular ratification placed on: {ratifying.title or ratifying.meeting_type}")


# --- CTCs -------------------------------------------------------------------------


async def _issue_ctcs(db, actor, entity: SecretarialEntity, meeting: SecretarialMeeting) -> None:
    """Issue a certified copy, then correct it the only way a CTC can be corrected."""
    if await db.scalar(select(SecretarialCtc.id).where(SecretarialCtc.entity_id == entity.id)):
        return

    prefill = await ctc_service.prefill(
        db, actor.company_id, passage_mode="board", meeting_id=meeting.id
    )
    signatories = [{"name": "Anita Desai", "designation": "Company Secretary", "membership_no": "ACS 41122"}]

    first = await ctc_service.issue(
        db,
        actor,
        entity_id=entity.id,
        passage_mode="board",
        resolution_text=prefill["resolution_text"] or "RESOLVED THAT the accounts be approved.",
        passed_on=prefill.get("passed_on"),
        meeting_id=meeting.id,
        place="Mumbai",
        issued_to="HDFC Bank Ltd, Andheri branch",
        purpose="Opening of a current account",
        signatories=signatories,
    )
    await db.commit()

    await ctc_service.issue(
        db,
        actor,
        entity_id=entity.id,
        passage_mode="board",
        resolution_text=first.resolution_text,
        passed_on=first.passed_on,
        meeting_id=meeting.id,
        place="Mumbai",
        issued_to="HDFC Bank Ltd, Andheri branch",
        purpose="Opening of a current account",
        signatories=signatories,
        supersedes_id=first.id,
        superseded_reason=(
            "The bank branch was recorded as Andheri West; the account is at the Andheri "
            "East branch. Reissued with the correct addressee."
        ),
    )
    await db.commit()
    print("  CTCs: one issued, one superseding it with a stated reason")


# --- Evidence: files and filings ---------------------------------------------------


async def _challan(db, actor, entity: SecretarialEntity) -> SecretarialFile:
    existing = await db.scalar(
        select(SecretarialFile).where(
            SecretarialFile.entity_id == entity.id,
            SecretarialFile.file_name == "mca-challan-F12345678.txt",
        )
    )
    if existing:
        return existing
    body = (
        "MINISTRY OF CORPORATE AFFAIRS\r\n"
        "Service Request Number (SRN): F12345678\r\n"
        "Form: DIR-12   Fee paid: INR 600.00\r\n"
        "Placeholder challan for the demo tenant - not a real MCA receipt.\r\n"
    )
    row = await file_service.upload(
        db,
        FileUploadIn(
            file_name="mca-challan-F12345678.txt",
            content_base64=base64.b64encode(body.encode()).decode(),
            content_type="text/plain",
            entity_id=entity.id,
            category="challan",
            description="Fee challan for the DIR-12 filing.",
        ),
        actor,
    )
    await db.commit()
    return row


async def _complete_the_chain(db, actor, entity: SecretarialEntity, meeting: SecretarialMeeting) -> None:
    """Give one filing an unbroken chain and one a visible gap.

    A demo where every chain is complete never shows what the feature is for. The
    MGT-14 below is filed with no challan attached, so "on what authority?" has
    something honest to report.
    """
    challan = await _challan(db, actor, entity)

    dir12 = await db.scalar(
        select(SecretarialFiling).where(
            SecretarialFiling.entity_id == entity.id, SecretarialFiling.form_code == "DIR-12"
        )
    )
    if dir12 is not None and dir12.challan_file_id is None:
        dir12.challan_file_id = challan.id
        dir12.meeting_id = dir12.meeting_id or meeting.id
        await db.commit()

    if not await db.scalar(
        select(SecretarialFiling.id).where(
            SecretarialFiling.entity_id == entity.id, SecretarialFiling.form_code == "MGT-14"
        )
    ):
        await filing_service.record(
            db,
            actor,
            entity_id=entity.id,
            form_code="MGT-14",
            fy=FY,
            srn="F87654321",
            filed_on=date(2026, 5, 28),
            status="filed",
            filing_fee=Decimal("600"),
            meeting_id=meeting.id,
            filed_by="Anita Desai",
            notes="Filed. The challan has not been attached yet - the chain will say so.",
        )
        await db.commit()
    print("  filings: DIR-12 chain complete (challan attached); MGT-14 missing its challan")


# --- Facts ------------------------------------------------------------------------


async def _fact_history(db, actor, entity: SecretarialEntity) -> None:
    """Three years of figures, so thresholds have a trend behind them."""
    years = {
        "2023-24": dict(
            turnover=28_400_000, net_profit=3_100_000, net_worth=14_200_000,
            paid_up_capital=1_000_000, free_reserves=12_400_000, securities_premium=1_500_000,
            borrowings=18_000_000, deposits=0,
        ),
        "2024-25": dict(
            turnover=35_900_000, net_profit=4_800_000, net_worth=17_800_000,
            paid_up_capital=1_000_000, free_reserves=15_900_000, securities_premium=1_500_000,
            borrowings=21_500_000, deposits=0,
        ),
        FY: dict(
            turnover=42_000_000, net_profit=6_200_000, net_worth=21_000_000,
            paid_up_capital=1_000_000, free_reserves=18_500_000, securities_premium=1_500_000,
            borrowings=25_000_000, deposits=0,
        ),
        # The year in progress. Without it the s.186 screen opens on a year it has no
        # figures for and can only say "cannot be judged".
        CURRENT_FY: dict(
            turnover=19_600_000, net_profit=2_900_000, net_worth=23_900_000,
            paid_up_capital=1_200_000, free_reserves=18_500_000, securities_premium=1_500_000,
            borrowings=24_000_000, deposits=0,
        ),
    }
    for fy, values in years.items():
        await facts_service.set_manual_facts(
            db,
            entity.id,
            fy,
            {
                **values,
                "notes": "Demo figures. Entered by hand so the thresholds and the s.186 "
                "ceiling have something definite to work against.",
            },
            actor,
        )
    print(f"  facts: {', '.join(years)}")


# --- Capital (Phase 5) ------------------------------------------------------------


async def _accounts_cap_table(db, actor, mango: Company, priya, rahul) -> dict[str, Shareholder]:
    """Put the founders in the accounts cap table and link them to the register.

    This is what makes the Secretarial cap table read `source: ledger` rather than
    `register` - the shares live in one place, and the certificates are the statutory
    record of the same shares rather than a second opinion about them.
    """
    share_type = await db.scalar(
        select(ShareType).where(
            ShareType.company_id == mango.id, ShareType.share_type_name == "Equity"
        )
    )
    if share_type is None:
        share_type = ShareType(
            company_id=mango.id, share_type_name="Equity", currency="INR",
            par_value=FACE_VALUE, owner=actor.id, modified_by=actor.id,
        )
        db.add(share_type)
        await db.flush()

    holders: dict[str, Shareholder] = {}
    for member, opening in ((priya, 60_000), (rahul, 40_000)):
        holder = await db.scalar(
            select(Shareholder).where(
                Shareholder.company_id == mango.id,
                Shareholder.shareholder_name == member.member_name,
            )
        )
        if holder is None:
            holder = Shareholder(
                company_id=mango.id, shareholder_name=member.member_name,
                folio_no=member.folio_no, owner=actor.id, modified_by=actor.id,
            )
            db.add(holder)
            await db.flush()
            db.add(
                ShareTransfer(
                    company_id=mango.id,
                    name=f"SHT-DEMO-{member.folio_no}",
                    transfer_type="Issue",
                    to_shareholder_id=holder.id,
                    share_type_id=share_type.id,
                    no_of_shares=opening,
                    rate=FACE_VALUE,
                    amount=FACE_VALUE * opening,
                    transfer_date=date(2019, 6, 12),
                    status="Submitted",
                    docstatus=1,
                    remarks="Subscription to the memorandum.",
                    owner=actor.id,
                    modified_by=actor.id,
                )
            )
        if member.shareholder_id != holder.id:
            member.shareholder_id = holder.id
        holders[member.folio_no] = holder
    await db.commit()
    return holders


async def _event(
    db, actor, entity: SecretarialEntity, event_type: str, payload: dict
) -> SecretarialCapitalEvent:
    """Fetch-or-create a capital event and carry it to `approved`.

    Keyed on (entity, event type) because the demo has one of each. Advancing by state
    rather than skipping on existence means a run interrupted between create and approve
    finishes the job next time instead of leaving a permanent draft.
    """
    event = await db.scalar(
        select(SecretarialCapitalEvent).where(
            SecretarialCapitalEvent.entity_id == entity.id,
            SecretarialCapitalEvent.event_type == event_type,
        )
    )
    if event is None:
        event = await capital_service.create_event(
            db, actor, {"entity_id": entity.id, "event_type": event_type, **payload}
        )
        await db.commit()
    if event.status == "draft":
        event = await capital_service.approve_event(db, actor, event.id)
        await db.commit()
    return event


async def _seed_capital(db, actor, entity: SecretarialEntity, mango: Company, board: SecretarialMeeting) -> None:
    priya = await _member(db, entity.id, "M-001")
    rahul = await _member(db, entity.id, "M-002")
    await _accounts_cap_table(db, actor, mango, priya, rahul)

    issued = await db.scalar(
        select(SecretarialShareCertificate.id).where(
            SecretarialShareCertificate.entity_id == entity.id
        )
    )
    if issued is None:
        # Priya's 60,000 is split across two certificates on purpose. A transfer
        # surrenders a whole certificate, so a holding kept on one giant certificate
        # cannot move a slice of itself without being split first - which is exactly
        # how it works on paper, and worth having on screen.
        for member, lots in ((priya, (55_000, 5_000)), (rahul, (40_000,))):
            for lot in lots:
                await capital_service.issue_certificate(
                    db,
                    actor,
                    entity_id=entity.id,
                    member_id=member.id,
                    no_of_shares=Decimal(lot),
                    share_class="Equity",
                    face_value=FACE_VALUE,
                    amount_paid_up=FACE_VALUE,
                    issued_on=date(2019, 6, 12),
                    notes="Issued on subscription to the memorandum.",
                )
        await db.commit()
        print("  certificates: 3 issued, distinctive numbers 1-100000 with no gaps")

    # --- SH-4: 5,000 shares, board-approved, posted, certificate cancelled/reissued
    transfer = await db.scalar(
        select(SecretarialShareTransferDetail).where(
            SecretarialShareTransferDetail.entity_id == entity.id,
            SecretarialShareTransferDetail.instrument_no == 1,
        )
    )
    # Driven by state, not by existence: an interrupted run leaves a draft instrument
    # behind, and skipping it because "a transfer already exists" would leave the demo
    # permanently stuck one step short of the interesting part.
    if transfer is None:
        transfer = await capital_service.create_transfer(
            db,
            actor,
            entity_id=entity.id,
            transferor_member_id=priya.id,
            transferee_member_id=rahul.id,
            no_of_shares=Decimal(5_000),
            face_value=FACE_VALUE,
            consideration=Decimal(750_000),
            stamp_duty=Decimal("1875"),  # 0.25% under Art. 62(a)
            executed_on=date(2026, 5, 2),
            lodged_on=date(2026, 5, 6),
            notes="Founder rebalancing agreed in the shareholders' agreement.",
        )
        await db.commit()

    if transfer.status == "draft":
        transfer = await capital_service.approve_transfer(
            db, actor, transfer.id, board_meeting_id=board.id, approved_on=board.held_at.date()
        )
        await db.commit()

    if transfer.status == "board_approved":
        surrender = await db.scalar(
            select(SecretarialShareCertificate).where(
                SecretarialShareCertificate.entity_id == entity.id,
                SecretarialShareCertificate.member_id == priya.id,
                SecretarialShareCertificate.no_of_shares == Decimal(5_000),
                SecretarialShareCertificate.status == "issued",
            )
        )
        transfer = await capital_service.post_transfer(
            db,
            actor,
            transfer.id,
            posted_on=date(2026, 5, 14),
            surrender_certificate_id=surrender.id if surrender else None,
        )
        await db.commit()
        print(
            f"  transfer: SH-4/{transfer.instrument_no} posted - distinctive "
            f"{transfer.distinctive_from}-{transfer.distinctive_to} moved to Rahul Mehta, "
            "old certificate cancelled and reissued"
        )

    # --- Rights issue, allotted, certificates cut in the same step
    right = await db.scalar(
        select(SecretarialCapitalEvent).where(
            SecretarialCapitalEvent.entity_id == entity.id,
            SecretarialCapitalEvent.event_type == "right_issue",
        )
    )
    if right is None:
        right = await capital_service.create_event(
            db,
            actor,
            {
                "entity_id": entity.id,
                "event_type": "right_issue",
                "title": "Rights issue - 20,000 equity shares at Rs. 25",
                "fy": FY,
                "board_meeting_id": board.id,
                "share_class": "Equity",
                "shares_offered": Decimal(20_000),
                "face_value": FACE_VALUE,
                "price_per_share": Decimal(25),
                "premium_per_share": Decimal(15),
                "offer_on": date(2026, 6, 1),
                "record_on": date(2026, 5, 29),
                "closes_on": date(2026, 6, 21),
                "paid_up_before": Decimal(1_000_000),
                "authorised_capital": Decimal(2_500_000),
                "allottees": [
                    {"member_id": str(priya.id), "name": priya.member_name, "shares": 11_000, "amount": 275_000},
                    {"member_id": str(rahul.id), "name": rahul.member_name, "shares": 9_000, "amount": 225_000},
                ],
                "total_amount": Decimal(500_000),
                "notes": "Offered to existing members in proportion, with a right of renunciation.",
            },
        )
        await db.commit()

    if right.status == "draft":
        right = await capital_service.approve_event(db, actor, right.id)
        await db.commit()
    if right.status == "approved":
        right, certs = await capital_service.allot_event(
            db, actor, right.id, allotted_on=date(2026, 6, 24)
        )
        await db.commit()
        print(f"  rights issue: allotted, {len(certs)} certificates cut")

    # --- ESOP grant: approved but not allotted, because options are not shares yet
    esop = await _event(
        db,
        actor,
        entity,
        "esop_grant",
        {
            "title": "Mango ESOP 2026 - first grant",
            "fy": FY,
            "board_meeting_id": board.id,
            "share_class": "Equity",
            "shares_offered": Decimal(6_000),
            "face_value": FACE_VALUE,
            "price_per_share": Decimal(25),
            "offer_on": date(2026, 5, 14),
            "allottees": [
                {"name": "Sandeep Rao (Head of Engineering)", "shares": 3_500},
                {"name": "Meera Iyer (Head of Sales)", "shares": 2_500},
            ],
            "notes": "Options vest over three years; nothing is allotted until they are exercised.",
        },
    )
    if esop.status == "approved":
        print("  ESOP: 6,000 options granted, approved and correctly not allotted")

    # --- Dividend, checked against s.123 at approval
    dividend = await _event(
        db,
        actor,
        entity,
        "dividend",
        {
            "title": f"Final dividend for FY {FY}",
            "fy": FY,
            "board_meeting_id": board.id,
            "dividend_per_share": Decimal(5),
            "total_amount": Decimal(500_000),
            "record_on": date(2026, 7, 31),
            "notes": "Rs. 5 per equity share on 1,00,000 shares.",
        },
    )
    check = dividend.solvency_check or {}
    print(
        f"  dividend: s.123 check {check.get('verdict')} - distributable "
        f"{check.get('distributable')} against a proposed 500000"
    )


# --- s.186 -------------------------------------------------------------------------


async def _seed_s186(db, actor, entity: SecretarialEntity, egm: SecretarialMeeting) -> None:
    """Build the register, hit the ceiling, and then lift it the lawful way.

    The refused entry is the point of the exercise. A register that only ever contains
    entries that fit tells you nothing about whether the guard works.

    The rollback after that refusal expires every ORM object in the session, so the ids
    this function needs are taken as plain values first and the entity is re-fetched
    afterwards. Reading ``entity.id`` across the rollback would trigger a lazy refresh at
    a point where the session has nothing to refresh from.
    """
    entity_id = entity.id
    egm_id = egm.id
    egm_held_on = egm.held_at.date()
    # Every entry below is dated inside the year in progress, so that is the year whose
    # ceiling governs them. `create_entry` derives the same label from `made_on` when the
    # fy is left off, which is why it is omitted rather than passed.
    fy = CURRENT_FY

    async def _have(party: str) -> bool:
        return bool(
            await db.scalar(
                select(SecretarialS186Entry.id).where(
                    SecretarialS186Entry.entity_id == entity_id,
                    SecretarialS186Entry.party_name == party,
                )
            )
        )

    if await _have("Aurora Renewables Ltd"):
        return  # the whole arc has already run

    status = await s186_service.limit_status(db, entity, fy, refresh=True)
    await db.commit()
    print(
        f"  s.186 ceiling: {status['effective_limit']} "
        f"(60% test {status['limit_sixty_pct']}, 100% test {status['limit_hundred_pct']})"
    )

    entries = [
        dict(
            entry_type="loan", party_name="Mango Retail Pvt Ltd",
            party_cin="U52100MH2021PTC360002", party_relation="wholly_owned_subsidiary",
            amount=Decimal(12_000_000), rate_of_interest=Decimal("9.5"),
            purpose="Working capital for the retail arm.", made_on=date(2026, 4, 18),
        ),
        dict(
            entry_type="investment", party_name="Bharat Components Ltd",
            amount=Decimal(8_500_000), purpose="Strategic equity stake in a key supplier.",
            made_on=date(2026, 5, 20),
        ),
        dict(
            entry_type="guarantee", party_name="Sunbeam Logistics Pvt Ltd",
            amount=Decimal(4_500_000),
            purpose="Corporate guarantee for the logistics partner's term loan.",
            made_on=date(2026, 6, 9),
        ),
    ]
    added = 0
    for payload in entries:
        if await _have(payload["party_name"]):
            continue
        await s186_service.create_entry(
            db, actor, {"entity_id": entity_id, **payload}
        )
        added += 1
    await db.commit()
    print(f"  s.186 register: {added} entries added (the subsidiary loan is exempt under s.186(11))")

    # Now one that breaches the ceiling, to show the guard biting.
    breach = dict(
        entity_id=entity_id, entry_type="investment",
        party_name="Aurora Renewables Ltd", amount=Decimal(9_000_000),
        purpose="Minority stake in a solar developer.", made_on=date(2026, 7, 14),
    )
    try:
        await s186_service.create_entry(db, actor, dict(breach))
        print("  s.186: WARNING - the over-limit entry was accepted; the guard is not biting")
    except ValidationError as exc:
        await db.rollback()
        print(f"  s.186 guard refused the 90,00,000 investment: {exc.args[0][:110]}...")

    # The lawful way through: a special resolution of the members under s.186(3).
    await s186_service.set_limit_overrides(
        db,
        actor,
        entity_id,
        {
            "fy": fy,
            "special_resolution_meeting_id": egm_id,
            "special_resolution_on": egm_held_on,
            "notes": "Ceiling lifted by special resolution passed at the EGM.",
        },
    )
    await db.commit()
    await s186_service.create_entry(db, actor, dict(breach))
    await db.commit()
    print("  s.186: special resolution linked; the same entry is then accepted")


# --- The practice side --------------------------------------------------------------


async def _second_director(db, actor, entity: SecretarialEntity, firm: Company) -> SecretarialPerson:
    """Sunrise Textiles' co-director, created on demand."""
    from app.schemas.secretarial import AppointmentCreate, PersonCreate
    from app.services.secretarial import persons as person_service

    person = await db.scalar(
        select(SecretarialPerson).where(
            SecretarialPerson.company_id == firm.id, SecretarialPerson.full_name == "Nitin Shah"
        )
    )
    if person is None:
        person = await person_service.create_person(
            db,
            PersonCreate(
                full_name="Nitin Shah",
                din="05559876",
                email="nitin@sunrisetextiles.in",
                occupation="Director",
                nationality="Indian",
                kyc_status="verified",
            ),
            actor,
        )
        await db.commit()

    if not await db.scalar(
        select(SecretarialAppointment.id).where(
            SecretarialAppointment.entity_id == entity.id,
            SecretarialAppointment.person_id == person.id,
            SecretarialAppointment.ceased_on.is_(None),
        )
    ):
        await person_service.create_appointment(
            db,
            AppointmentCreate(
                entity_id=entity.id,
                person_id=person.id,
                role_type="director",
                designation="Director",
                appointed_on=date(2018, 2, 14),
                is_signing=True,
            ),
            actor,
        )
        await db.commit()
    return person


async def _deepen_practice(db, firm_id, admin_id, admin_email: str) -> None:
    """Give the managed clients enough to be worth opening.

    Takes plain ids rather than ORM objects: the caller has just been working in another
    tenant and may have rolled back, and an object loaded under the previous company
    cannot be refreshed here.
    """
    await set_company_context(db, firm_id)
    firm = await db.get(Company, firm_id)
    admin = await db.get(User, admin_id) or await db.scalar(
        select(User).where(User.email == admin_email)
    )
    actor = _actor(admin, firm.id)

    textiles = await _entity_of(db, firm, name="Sunrise Textiles Pvt Ltd")
    kavita = await _person(db, firm.id, "Kavita Shah")
    # A private company needs two directors (s.149), and the quorum check measures
    # attendance against the board's total strength — so a one-director demo entity
    # reports "quorum not met" at every meeting for a reason that is an artefact of the
    # seed rather than anything the user did.
    nitin = await _second_director(db, actor, textiles, firm)

    board = await _run_meeting(
        db,
        actor,
        textiles,
        title="Board meeting - approval of accounts FY 2025-26",
        meeting_type="board",
        held_at=datetime(2026, 7, 9, 15, 30, tzinfo=UTC),
        notice_on=date(2026, 6, 29),
        chair=kavita,
        attendance=[(kavita, "present"), (nitin, "present")],
        venue="Registered office, Surat",
        agenda=[
            {"title": "Leave of absence", "body": "None sought."},
            {
                "title": "Approval of the audited financial statements",
                "body": "Consider and approve the accounts for the year ended 31 March 2026.",
                "resolution_text": (
                    "RESOLVED THAT the audited financial statements for the year ended "
                    "31 March 2026, together with the Board's Report, be and are hereby approved."
                ),
            },
            {"title": "Any other business", "body": "With the permission of the Chair."},
        ],
    )

    # A managed client's books are elsewhere, so everything the ceiling needs is typed in.
    limit = await s186_service.limit_status(db, textiles, CURRENT_FY, refresh=True)
    await db.commit()
    if limit["effective_limit"] is None:
        await s186_service.set_limit_overrides(
            db,
            actor,
            textiles.id,
            {
                "fy": CURRENT_FY,
                "paid_up_capital": Decimal(1_000_000),
                "free_reserves": Decimal(4_200_000),
                "securities_premium": Decimal(0),
                "notes": "Typed in - Sunrise is a managed client and its books are not in this tenant.",
            },
        )
        await db.commit()

    if not await db.scalar(
        select(SecretarialS186Entry.id).where(SecretarialS186Entry.entity_id == textiles.id)
    ):
        await s186_service.create_entry(
            db,
            actor,
            {
                "entity_id": textiles.id,
                "entry_type": "loan",
                "party_name": "Surat Weavers Co-operative",
                "amount": Decimal(1_500_000),
                "rate_of_interest": Decimal("11"),
                "purpose": "Advance against a long-term supply arrangement.",
                "made_on": date(2026, 5, 6),
                "board_meeting_id": board.id,
            },
        )
        await db.commit()

    if not await db.scalar(
        select(SecretarialShareCertificate.id).where(
            SecretarialShareCertificate.entity_id == textiles.id
        )
    ):
        for name, folio, shares in (("Kavita Shah", "S-001", 70_000), ("Nitin Shah", "S-002", 30_000)):
            await capital_service.issue_certificate(
                db,
                actor,
                entity_id=textiles.id,
                holder_name=name,
                folio_no=folio,
                no_of_shares=Decimal(shares),
                share_class="Equity",
                face_value=FACE_VALUE,
                issued_on=date(2018, 2, 14),
            )
        await db.commit()

    print("  Sunrise Textiles: held board meeting with signed minutes, s.186 register, certificates")


# --- Entry point -------------------------------------------------------------------


async def main() -> None:
    async with async_session_factory() as db:
        mango = await _company(db, "Mango Appliances Demo")
        firm = await _company(db, "OptiReach Demo Pvt Ltd")
        admin = await db.scalar(
            select(User).where(User.email == os.environ.get("ADMIN_EMAIL", "admin@example.com").lower())
        )
        if admin is None:
            raise SystemExit("Admin user not found - run scripts.seed first")
        demo = await db.scalar(select(User).where(User.email == "demo@optireach.in"))

        # Ids, not ORM objects, for anything used after the tenant context moves on: a
        # rollback later in the run expires everything loaded here, and an object first
        # read under one tenant cannot be refreshed under another.
        firm_id = firm.id
        practice_user = demo or admin
        practice_user_id, practice_user_email = practice_user.id, practice_user.email

        actor = _actor(admin, mango.id)
        await set_company_context(db, mango.id)
        entity = await _entity_of(db, mango)

        priya = await _person(db, mango.id, "Priya Sharma")
        rahul = await _person(db, mango.id, "Rahul Mehta")
        anita = await _person(db, mango.id, "Anita Desai")
        directors = [priya, rahul]

        print("Mango Appliances Demo - driving the lifecycles:")
        await _fact_history(db, actor, entity)

        board = await _run_meeting(
            db,
            actor,
            entity,
            title="Board meeting - Q1 FY 2026-27",
            meeting_type="board",
            held_at=datetime(2026, 5, 14, 11, 0, tzinfo=UTC),
            notice_on=date(2026, 5, 4),
            chair=priya,
            attendance=[(priya, "present"), (rahul, "present"), (anita, "present")],
            venue="Registered office, Andheri",
            # The Company Secretary gets the papers too, which also gives the tracked
            # circulation three recipients and so all three of its states on one screen.
            circulate_to=[priya, rahul, anita],
            agenda=[
                {"title": "Leave of absence", "body": "None sought."},
                {
                    "title": "Registration of transfer of shares",
                    "body": "Consider the instrument of transfer in Form SH-4 lodged by Priya Sharma.",
                    "pack_code": "share-transfer-sh4",
                    "resolution_text": (
                        "RESOLVED THAT the transfer of 5,000 equity shares bearing distinctive "
                        "numbers 55001 to 60000 from Priya Sharma to Rahul Mehta be and is hereby "
                        "approved and registered, and that a fresh share certificate be issued to "
                        "the transferee."
                    ),
                },
                {
                    "title": "Rights issue of equity shares",
                    "body": "Consider offering 20,000 equity shares to existing members under s.62(1)(a).",
                    "pack_code": "right-issue",
                    "resolution_text": (
                        "RESOLVED THAT 20,000 equity shares of Rs. 10 each be offered at Rs. 25 per "
                        "share to the existing members in proportion to their holdings."
                    ),
                },
                {"title": "Any other business", "body": "With the permission of the Chair."},
            ],
        )
        await _vary_circulation(db, actor, board)

        egm = await _run_meeting(
            db,
            actor,
            entity,
            title="Extraordinary General Meeting - July 2026",
            meeting_type="egm",
            held_at=datetime(2026, 7, 20, 10, 0, tzinfo=UTC),
            notice_on=date(2026, 6, 26),
            chair=priya,
            attendance=[(priya, "present"), (rahul, "present")],
            venue="Registered office, Andheri",
            agenda=[
                {
                    "title": "Authority under s.186(3)",
                    "body": "Special resolution to make loans and investments beyond the s.186(2) ceiling.",
                    "pack_code": "s186-resolution",
                    "resolution_text": (
                        "RESOLVED AS A SPECIAL RESOLUTION THAT pursuant to Section 186(3) of the "
                        "Companies Act, 2013, consent be and is hereby accorded to the Board to give "
                        "loans, guarantees and securities and to make investments exceeding the "
                        "limits prescribed by Section 186(2), up to an aggregate of Rs. 5,00,00,000."
                    ),
                },
            ],
        )

        await _run_meeting(
            db,
            actor,
            entity,
            title="Annual General Meeting 2026",
            meeting_type="agm",
            held_at=datetime(2026, 8, 8, 11, 0, tzinfo=UTC),
            notice_on=date(2026, 7, 15),
            chair=priya,
            attendance=[(priya, "present"), (rahul, "present"), (anita, "present")],
            venue="Registered office, Andheri",
            agenda=[
                {
                    "title": "Adoption of the financial statements",
                    "body": "Consider and adopt the accounts for the year ended 31 March 2026.",
                    "resolution_text": (
                        "RESOLVED THAT the audited financial statements for the year ended "
                        "31 March 2026, together with the reports of the Board and the Auditors, "
                        "be and are hereby received, considered and adopted."
                    ),
                },
                {
                    "title": "Declaration of dividend",
                    "body": "Declare a final dividend of Rs. 5 per equity share.",
                    "pack_code": "dividend-declaration",
                },
            ],
        )

        await _carry_circular(db, actor, entity, directors)
        await _issue_ctcs(db, actor, entity, board)
        await _complete_the_chain(db, actor, entity, board)
        await _seed_capital(db, actor, entity, mango, board)
        await _seed_s186(db, actor, entity, egm)

        print("\nOptiReach Demo Pvt Ltd (practice):")
        await _deepen_practice(db, firm_id, practice_user_id, practice_user_email)

    print(
        "\nEvery Secretarial submodule now has something at a real state.\n"
        "  /secretarial/meetings     - board, EGM and AGM signed into the book; one draft\n"
        "  /secretarial/circulars    - one carried by consents and ratified, one Rule-5 blocked\n"
        "  /secretarial/ctcs         - an issuance superseded with a reason\n"
        "  /secretarial/filings      - one complete chain, one with a visible gap\n"
        "  /secretarial/capital      - cap table, certificates, a posted SH-4, a rights issue\n"
        "  /secretarial/s186         - a register against a ceiling that a resolution lifted\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
