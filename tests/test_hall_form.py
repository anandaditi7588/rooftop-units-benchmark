"""Tests for the amenity (hall / lawn) booking form.

Run with:  pytest tests/test_hall_form.py

Like the trainer form's tests, these deliberately avoid importing the RTU
benchmarking application, so they stay fast and independent of it.

The clash tests are the point of this file. Everything else here mirrors a
form that already works; a hall promised to two families on the same evening
is the failure the society actually feels.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from hallform.models import IST, describe_clash, find_clash, parse_booking  # noqa: E402
from hallform.rules import RULES, SECURITY_DEPOSIT_INR, VENUES_BY_KEY  # noqa: E402
from hallform.web import hall_app  # noqa: E402


def in_days(days: int) -> str:
    return (datetime.now(IST).date() + timedelta(days=days)).isoformat()


@pytest.fixture()
def paths(tmp_path, monkeypatch):
    """Redirect every file the hall form writes into a throwaway directory.

    Both modules hold their own reference to these names — storage writes
    through its own imports, the web layer reads the CSV through the settings
    module — so both are patched. A miss here silently writes test bookings
    into the repository's real output directory.
    """
    import gymform.settings as settings_module
    import hallform.storage as storage_module

    files = {
        "BOOKINGS_JSONL": tmp_path / "bookings.jsonl",
        "BOOKINGS_CSV": tmp_path / "bookings.csv",
        "BOOKING_PAYMENTS_JSONL": tmp_path / "booking_payments.jsonl",
        "BOOKING_APPROVALS_JSONL": tmp_path / "booking_approvals.jsonl",
        "BOOKING_DELETIONS_JSONL": tmp_path / "booking_deletions.jsonl",
    }
    for module in (settings_module, storage_module):
        for name, path in files.items():
            monkeypatch.setattr(module, name, path, raising=False)
    monkeypatch.setattr(settings_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_module, "ID_PROOF_DIR", tmp_path / "id_proofs")
    monkeypatch.setenv("GYM_SUBMIT_COOLDOWN", "0")
    return files


@pytest.fixture()
def client(paths):
    parent = FastAPI()
    parent.mount("/hall", hall_app)
    return TestClient(parent)


def payload(**overrides) -> dict:
    """A complete, valid booking — the party lawn for four hours next week."""
    data = {
        "resident_name": "Sunita Deshpande",
        "flat_number": "C-702",
        "mobile": "9822011223",
        "whatsapp": "",
        "email": "sunita@example.com",
        "venue_key": "party_lawn",
        "slot_key": "4h",
        "event_date": in_days(7),
        "start_time": "18:00",
        "occasion": "Birthday party",
        "occasion_detail": "60th birthday",
        "expected_persons": "50",
        "declaration_signature": "Sunita Deshpande",
        "declaration_place": "Pune",
        "declaration_agree": "1",
    }
    data.update({f"ack_{rule.key}": "1" for rule in RULES})
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Rules encoded from the source document
# ---------------------------------------------------------------------------

def test_all_eleven_rules_are_present():
    assert [rule.number for rule in RULES] == list(range(1, 12))
    assert len({rule.key for rule in RULES}) == 11


@pytest.mark.parametrize(
    "venue_key,slot_key,charge",
    [
        ("conference_hall", "4h", 2_500),
        ("conference_hall", "8h", 3_500),
        ("party_lawn", "4h", 3_000),
        ("party_lawn", "8h", 4_000),
        ("community_hall", "day", 2_000),
    ],
)
def test_charges_match_the_document(venue_key, slot_key, charge):
    assert VENUES_BY_KEY[venue_key].slot(slot_key).charge_inr == charge


@pytest.mark.parametrize(
    "venue_key,max_persons",
    [("conference_hall", 75), ("party_lawn", 75), ("community_hall", 30)],
)
def test_capacities_match_the_document(venue_key, max_persons):
    assert VENUES_BY_KEY[venue_key].max_persons == max_persons


def test_security_deposit_is_five_thousand():
    assert SECURITY_DEPOSIT_INR == 5_000


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_a_complete_booking_validates():
    booking, errors, _ = parse_booking(payload())
    assert errors == {}
    assert booking is not None
    assert booking.charge_inr == 3_000
    assert booking.end_time == "22:00"
    assert booking.reference.startswith("SB-HB-")


def test_end_time_is_derived_from_the_slot_length():
    booking, _, _ = parse_booking(payload(slot_key="8h", start_time="09:00"))
    assert booking.end_time == "17:00"


def test_a_full_day_booking_takes_the_whole_permitted_window():
    booking, errors, _ = parse_booking(
        payload(venue_key="community_hall", slot_key="day",
                start_time="", expected_persons="25")
    )
    assert errors == {}
    assert (booking.start_time, booking.end_time) == ("08:30", "22:00")


def test_a_booking_may_not_run_past_ten_at_night():
    _, errors, _ = parse_booking(payload(start_time="19:00"))
    assert "start_time" in errors
    assert "6:00 pm" in errors["start_time"]


def test_a_booking_may_not_start_before_half_past_eight():
    _, errors, _ = parse_booking(payload(start_time="07:00"))
    assert "start_time" in errors


def test_today_is_too_late_because_charges_are_collected_the_day_before():
    _, errors, _ = parse_booking(payload(event_date=in_days(0)))
    assert "day before" in errors["event_date"]


def test_a_past_date_is_refused():
    _, errors, _ = parse_booking(payload(event_date=in_days(-1)))
    assert "passed" in errors["event_date"]


def test_capacity_is_enforced_per_venue():
    _, errors, _ = parse_booking(
        payload(venue_key="community_hall", slot_key="day", expected_persons="40")
    )
    assert "maximum of 30" in errors["expected_persons"]

    booking, errors, _ = parse_booking(
        payload(venue_key="community_hall", slot_key="day", expected_persons="30")
    )
    assert errors == {}
    assert booking is not None


def test_every_rule_must_be_acknowledged():
    data = payload()
    del data["ack_cleaning"]
    _, errors, _ = parse_booking(data)
    assert "rule 5" in errors["acknowledgements"]


def test_the_declaration_must_be_signed_and_accepted():
    _, errors, _ = parse_booking(payload(declaration_agree="", declaration_signature=""))
    assert "declaration_agree" in errors
    assert "declaration_signature" in errors


def test_mobile_numbers_are_normalised():
    booking, errors, _ = parse_booking(payload(mobile="+91 98220 11223"))
    assert errors == {}
    assert booking.mobile == "9822011223"


def test_whatsapp_falls_back_to_the_mobile_number():
    booking, _, _ = parse_booking(payload(whatsapp=""))
    assert booking.whatsapp == booking.mobile


# ---------------------------------------------------------------------------
# Double booking — the reason this form exists
# ---------------------------------------------------------------------------

def _stored(**overrides) -> dict:
    record = {
        "reference": "SB-HB-20260801-AAAAA",
        "resident_name": "Rahul Patil",
        "flat_number": "A-304",
        "venue_key": "party_lawn",
        "venue_name": "Party Lawn (At the swimming pool)",
        "event_date": in_days(7),
        "start_time": "18:00",
        "end_time": "22:00",
        "status": "approved",
    }
    record.update(overrides)
    return record


def test_an_overlapping_booking_is_refused_and_names_the_holder():
    _, errors, _ = parse_booking(payload(), existing=[_stored()])
    assert "clash" in errors
    assert "Rahul Patil" in errors["clash"]
    assert "A-304" in errors["clash"]


def test_a_pending_booking_still_holds_the_slot():
    """The first resident asked first; the office deciding must not cost them it."""
    _, errors, _ = parse_booking(payload(), existing=[_stored(status="pending")])
    assert "already requested" in errors["clash"]


def test_a_rejected_booking_releases_the_slot():
    booking, errors, _ = parse_booking(payload(), existing=[_stored(status="rejected")])
    assert errors == {}
    assert booking is not None


def test_a_different_venue_at_the_same_time_is_fine():
    booking, errors, _ = parse_booking(
        payload(venue_key="conference_hall"), existing=[_stored()]
    )
    assert errors == {}
    assert booking is not None


def test_a_different_date_is_fine():
    booking, errors, _ = parse_booking(
        payload(event_date=in_days(8)), existing=[_stored()]
    )
    assert errors == {}
    assert booking is not None


def test_back_to_back_bookings_do_not_clash():
    """A function ending at 6 pm and one starting at 6 pm share only an instant.

    Half-open overlap is how a hall actually turns over; treating the boundary
    as a clash would lose the society an entire evening slot.
    """
    existing = [_stored(start_time="09:00", end_time="13:00")]
    booking, errors, _ = parse_booking(payload(start_time="13:00"), existing=existing)
    assert errors == {}
    assert booking is not None


def test_an_overlap_of_one_minute_is_still_a_clash():
    existing = [_stored(start_time="09:00", end_time="13:00")]
    _, errors, _ = parse_booking(payload(start_time="12:59"), existing=existing)
    assert "clash" in errors


def test_a_full_day_booking_blocks_every_slot_that_day():
    existing = [_stored(
        venue_key="community_hall", venue_name="Community Hall (A & B Buildings)",
        start_time="08:30", end_time="22:00",
    )]
    _, errors, _ = parse_booking(
        payload(venue_key="community_hall", slot_key="day", expected_persons="20"),
        existing=existing,
    )
    assert "clash" in errors


def test_find_clash_ignores_records_with_unreadable_times():
    existing = [_stored(start_time="", end_time="nonsense")]
    assert find_clash(existing, "party_lawn", in_days(7), 1080, 1320) is None


def test_describe_clash_reads_as_a_sentence_a_resident_understands():
    message = describe_clash(_stored())
    assert message.startswith("Party Lawn")
    assert "already booked by Rahul Patil (Flat A-304)" in message
    assert "choose another time or date" in message


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def test_the_form_page_renders(client):
    response = client.get("/hall/")
    assert response.status_code == 200
    assert "Book a society amenity" in response.text
    assert "Party Lawn" in response.text


def test_the_rules_page_lists_every_rule(client):
    response = client.get("/hall/rules")
    assert response.status_code == 200
    for rule in RULES:
        assert rule.title in response.text


def test_submitting_stores_the_booking_and_confirms(client, paths):
    response = client.post("/hall/submit", data=payload())
    assert response.status_code == 200
    assert "Booking requested" in response.text or "Thank you" in response.text

    lines = paths["BOOKINGS_JSONL"].read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["resident_name"] == "Sunita Deshpande"
    assert record["charge_inr"] == 3_000
    assert record["end_time"] == "22:00"


def test_a_second_booking_for_the_same_slot_is_refused_over_http(client):
    first = client.post("/hall/submit", data=payload())
    assert first.status_code == 200

    second = client.post(
        "/hall/submit",
        data=payload(resident_name="Amit Joshi", flat_number="D-101",
                     email="amit@example.com", declaration_signature="Amit Joshi"),
    )
    assert second.status_code == 409
    assert "Sunita Deshpande" in second.text
    assert "C-702" in second.text


def test_the_availability_check_answers_before_the_form_is_posted(client):
    free = client.get("/hall/availability", params={
        "venue_key": "party_lawn", "slot_key": "4h",
        "event_date": in_days(7), "start_time": "18:00",
    })
    assert free.json() == {"checked": True, "available": True}

    client.post("/hall/submit", data=payload())

    taken = client.get("/hall/availability", params={
        "venue_key": "party_lawn", "slot_key": "4h",
        "event_date": in_days(7), "start_time": "18:00",
    })
    body = taken.json()
    assert body["available"] is False
    assert "Sunita Deshpande" in body["message"]


def test_the_availability_check_says_nothing_when_it_cannot_answer(client):
    assert client.get("/hall/availability").json() == {"checked": False}


def test_the_calendar_shows_who_holds_which_date(client):
    client.post("/hall/submit", data=payload())
    response = client.get("/hall/calendar")
    assert response.status_code == 200
    assert "Sunita Deshpande" in response.text
    assert "C-702" in response.text


def test_the_honeypot_field_rejects_bots(client):
    response = client.post("/hall/submit", data=payload(website="http://spam.example"))
    assert response.status_code == 400


def test_an_incomplete_booking_comes_back_with_the_values_filled_in(client):
    response = client.post("/hall/submit", data=payload(resident_name=""))
    assert response.status_code == 400
    assert "Please enter your full name." in response.text
    # The rest of what they typed survives the round trip.
    assert "C-702" in response.text


def test_the_qr_code_is_a_png(client):
    response = client.get("/hall/qr.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_health_answers_head_for_the_uptime_pinger(client):
    assert client.head("/hall/health").status_code == 200


def test_the_office_pages_refuse_without_a_configured_login(client, monkeypatch):
    for name in ("GYM_ADMIN_USERNAME", "GYM_ADMIN_PASSWORD",
                 "RTU_AUTH_USERNAME", "RTU_AUTH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    assert client.get("/hall/admin").status_code == 503


def test_the_office_can_confirm_a_booking(client, monkeypatch):
    monkeypatch.setenv("GYM_ADMIN_USERNAME", "office")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    client.post("/hall/submit", data=payload())
    import hallform.storage as storage_module
    reference = storage_module.load_bookings()[0]["reference"]

    response = client.post(
        f"/hall/admin/decision/{reference}",
        data={"decision": "approved", "note": "Key with the security desk"},
        auth=("office", "secret"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert storage_module.find_booking(reference)["status"] == "approved"


def test_rejecting_a_booking_frees_the_slot_for_the_next_resident(client, monkeypatch):
    monkeypatch.setenv("GYM_ADMIN_USERNAME", "office")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    client.post("/hall/submit", data=payload())
    import hallform.storage as storage_module
    reference = storage_module.load_bookings()[0]["reference"]
    client.post(
        f"/hall/admin/decision/{reference}",
        data={"decision": "rejected"},
        auth=("office", "secret"),
        follow_redirects=False,
    )

    second = client.post(
        "/hall/submit",
        data=payload(resident_name="Amit Joshi", flat_number="D-101",
                     email="amit@example.com", declaration_signature="Amit Joshi"),
    )
    assert second.status_code == 200


# ---------------------------------------------------------------------------
# Decisions from the office email
# ---------------------------------------------------------------------------

def test_an_approval_link_does_not_decide_when_a_mail_scanner_follows_it(
    client, monkeypatch
):
    """Security scanners fetch links in email before a person sees them."""
    monkeypatch.setenv("GYM_DECISION_SECRET", "test-secret")
    from gymform import tokens

    client.post("/hall/submit", data=payload())
    import hallform.storage as storage_module
    reference = storage_module.load_bookings()[0]["reference"]

    token = tokens.make_token(reference, "approved")
    response = client.get(f"/hall/decide/{reference}?d=approved&t={token}")
    assert response.status_code == 200
    assert storage_module.find_booking(reference)["status"] == "pending"

    response = client.post(
        f"/hall/decide/{reference}",
        data={"decision": "approved", "token": token},
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert storage_module.find_booking(reference)["status"] == "approved"


def test_an_edited_approval_link_is_refused(client, monkeypatch):
    monkeypatch.setenv("GYM_DECISION_SECRET", "test-secret")
    from gymform import tokens

    client.post("/hall/submit", data=payload())
    import hallform.storage as storage_module
    reference = storage_module.load_bookings()[0]["reference"]

    # A token for "rejected" must not work as one for "approved".
    token = tokens.make_token(reference, "rejected")
    assert client.get(
        f"/hall/decide/{reference}?d=approved&t={token}"
    ).status_code == 403


# ---------------------------------------------------------------------------
# Google Sheet archive
# ---------------------------------------------------------------------------

def test_the_sheet_row_covers_every_column_the_office_reads(client):
    import hallform.storage as storage_module

    client.post("/hall/submit", data=payload())
    record = storage_module.load_bookings()[0]
    row = storage_module.sheet_row(record)

    assert row["Resident"] == "Sunita Deshpande"
    assert row["Flat"] == "C-702"
    assert row["Charge (INR)"] == 3_000
    assert row["Status"] == "pending"
    assert row["All rules accepted"] == "Yes"


def test_a_booking_is_pushed_to_the_sheet_on_submit(client, monkeypatch):
    pushed = []
    import hallform.web as web_module

    monkeypatch.setenv("GYM_SHEETS_WEBHOOK_URL", "https://script.example/exec")
    monkeypatch.setattr(
        web_module.sheets, "push_row",
        lambda settings, sheet, row, key="reference": (
            pushed.append((sheet, row)) or (True, "ok")
        ),
    )

    client.post("/hall/submit", data=payload())
    assert len(pushed) == 1
    sheet, row = pushed[0]
    assert sheet == "Hall bookings"
    assert row["Resident"] == "Sunita Deshpande"


def test_a_failing_sheet_never_costs_the_society_a_booking(client, monkeypatch):
    """The sheet is the archive, not the record of truth."""
    import hallform.web as web_module

    monkeypatch.setenv("GYM_SHEETS_WEBHOOK_URL", "https://script.example/exec")
    monkeypatch.setattr(
        web_module.sheets, "push_row",
        lambda *args, **kwargs: (False, "Google Sheet returned HTTP 500"),
    )

    response = client.post("/hall/submit", data=payload())
    assert response.status_code == 200

    import hallform.storage as storage_module
    assert len(storage_module.load_bookings()) == 1


# ---------------------------------------------------------------------------
# Removing a wrong booking — office only
# ---------------------------------------------------------------------------

@pytest.fixture()
def office(monkeypatch):
    monkeypatch.setenv("GYM_ADMIN_USERNAME", "office")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")
    return ("office", "secret")


def _book(client, **overrides) -> str:
    response = client.post("/hall/submit", data=payload(**overrides))
    assert response.status_code == 200, response.status_code
    import hallform.storage as storage_module
    return storage_module.load_bookings()[0]["reference"]


def test_a_resident_cannot_remove_a_booking(client, office):
    """The whole point: removal is the office's, and nobody reaches it without
    the office login — not even the resident who made the booking."""
    reference = _book(client)

    response = client.post(
        f"/hall/delete/{reference}", data={"reason": "changed my mind"}
    )
    assert response.status_code == 404          # no such public route

    response = client.post(
        f"/hall/admin/delete/{reference}", data={"reason": "changed my mind"}
    )
    assert response.status_code == 401          # office login required

    import hallform.storage as storage_module
    assert storage_module.find_booking(reference)["status"] == "pending"


def test_removal_needs_a_login_even_when_none_is_configured(client, monkeypatch):
    """Fails closed, like every other office page."""
    for name in ("GYM_ADMIN_USERNAME", "GYM_ADMIN_PASSWORD",
                 "RTU_AUTH_USERNAME", "RTU_AUTH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)
    reference = _book(client)
    assert client.post(
        f"/hall/admin/delete/{reference}", data={"reason": "x"}
    ).status_code == 503


def test_the_office_can_remove_a_wrong_booking(client, office):
    reference = _book(client)

    response = client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Duplicate entry"},
        auth=office,
        follow_redirects=False,
    )
    assert response.status_code == 303

    import hallform.storage as storage_module
    assert storage_module.load_bookings() == []          # gone from the list
    record = storage_module.find_booking(reference)      # but still on record
    assert record["status"] == "removed"
    assert record["removal"]["reason"] == "Duplicate entry"


def test_removing_a_booking_frees_the_slot(client, office):
    reference = _book(client)
    client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Wrong date"}, auth=office, follow_redirects=False,
    )

    second = client.post(
        "/hall/submit",
        data=payload(resident_name="Amit Joshi", flat_number="D-101",
                     email="amit@example.com", declaration_signature="Amit Joshi"),
    )
    assert second.status_code == 200


def test_a_removed_booking_leaves_the_public_calendar(client, office):
    reference = _book(client)
    assert "Sunita Deshpande" in client.get("/hall/calendar").text

    client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Test entry"}, auth=office, follow_redirects=False,
    )
    assert "Sunita Deshpande" not in client.get("/hall/calendar").text


def test_the_resident_is_told_when_their_booking_is_removed(client, office):
    reference = _book(client)
    client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Hall needed for a society function"},
        auth=office, follow_redirects=False,
    )
    page = client.get(f"/hall/submitted/{reference}")
    assert page.status_code == 200
    assert "removed by the society office" in page.text
    assert "Hall needed for a society function" in page.text


def test_nothing_is_erased_from_the_signed_record(client, office, paths):
    """The society must always be able to answer who removed what, and why."""
    reference = _book(client)
    before = paths["BOOKINGS_JSONL"].read_text(encoding="utf-8")

    client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Duplicate"}, auth=office, follow_redirects=False,
    )

    assert paths["BOOKINGS_JSONL"].read_text(encoding="utf-8") == before
    entry = json.loads(
        paths["BOOKING_DELETIONS_JSONL"].read_text(encoding="utf-8").strip()
    )
    assert entry["reference"] == reference
    assert entry["removed"] is True
    assert entry["reason"] == "Duplicate"
    assert entry["recorded_at"]


def test_the_office_can_undo_a_removal(client, office):
    reference = _book(client)
    client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Mistake"}, auth=office, follow_redirects=False,
    )

    response = client.post(
        f"/hall/admin/restore/{reference}", auth=office, follow_redirects=False
    )
    assert response.status_code == 303

    import hallform.storage as storage_module
    assert storage_module.find_booking(reference)["status"] == "pending"
    assert len(storage_module.load_bookings()) == 1
    assert "Sunita Deshpande" in client.get("/hall/calendar").text


def test_restoring_is_refused_when_someone_else_took_the_slot(client, office):
    """Freeing the slot means somebody may take it. Undo must not double-book."""
    first = _book(client)
    client.post(
        f"/hall/admin/delete/{first}",
        data={"reason": "Wrong date"}, auth=office, follow_redirects=False,
    )
    client.post(
        "/hall/submit",
        data=payload(resident_name="Amit Joshi", flat_number="D-101",
                     email="amit@example.com", declaration_signature="Amit Joshi"),
    )

    response = client.post(
        f"/hall/admin/restore/{first}", auth=office, follow_redirects=False
    )
    assert response.status_code == 409
    assert "Amit Joshi" in response.json()["detail"]

    import hallform.storage as storage_module
    assert storage_module.find_booking(first)["status"] == "removed"


def test_removed_bookings_are_hidden_from_the_office_list_by_default(client, office):
    reference = _book(client)
    client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Duplicate"}, auth=office, follow_redirects=False,
    )

    hidden = client.get("/hall/admin", auth=office)
    assert reference not in hidden.text
    assert "Show 1 removed" in hidden.text

    shown = client.get("/hall/admin?show_removed=1", auth=office)
    assert reference in shown.text
    assert "Duplicate" in shown.text
    assert "Restore this booking" in shown.text


def test_a_removal_reaches_the_google_sheet(client, office, monkeypatch):
    pushed = []
    import hallform.web as web_module

    monkeypatch.setenv("GYM_SHEETS_WEBHOOK_URL", "https://script.example/exec")
    monkeypatch.setattr(
        web_module.sheets, "push_row",
        lambda settings, sheet, row, key="reference": (
            pushed.append(row) or (True, "ok")
        ),
    )

    reference = _book(client)
    client.post(
        f"/hall/admin/delete/{reference}",
        data={"reason": "Duplicate entry"}, auth=office, follow_redirects=False,
    )

    assert pushed[-1]["Status"] == "removed"
    assert pushed[-1]["Removed because"] == "Duplicate entry"
    # Same reference each time, so the sheet updates one row rather than
    # collecting a second line for the same booking.
    assert pushed[-1]["Reference"] == pushed[0]["Reference"] == reference
