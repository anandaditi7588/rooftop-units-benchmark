"""Tests for the trainer registration form.

Run with:  pytest tests/test_gym_form.py

Only the form's own dependencies are needed (fastapi, jinja2, python-multipart,
qrcode, requests) — these tests deliberately do not import the RTU benchmarking
application, so they stay fast and keep working if that pipeline changes.
"""
from __future__ import annotations

import email
import os
import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gymform import gym_app  # noqa: E402
from gymform.models import parse_submission  # noqa: E402
from gymform.rules import RULES, monthly_fee_for  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    """A test client whose submissions land in a throwaway directory."""
    import gymform.settings as settings_module
    import gymform.storage as storage_module

    # Both modules hold their own reference to these paths — storage writes
    # through its names, the web layer reads the CSV through the settings
    # module — so redirect them in both places.
    for module in (settings_module, storage_module):
        monkeypatch.setattr(module, "SUBMISSIONS_JSONL", tmp_path / "submissions.jsonl")
        monkeypatch.setattr(module, "SUBMISSIONS_CSV", tmp_path / "submissions.csv")
        monkeypatch.setattr(module, "ID_PROOF_DIR", tmp_path / "id_proofs")
    monkeypatch.setattr(settings_module, "DATA_DIR", tmp_path)
    monkeypatch.setenv("GYM_SUBMIT_COOLDOWN", "0")

    parent = FastAPI()
    parent.mount("/gym", gym_app)
    return TestClient(parent)


def payload(**overrides) -> dict:
    """A complete, valid submission — two clients in staggered morning slots."""
    data = {
        "trainer_name": "Ramesh Kulkarni",
        "mobile": "9876543210",
        "whatsapp": "",
        "email": "ramesh@example.com",
        "id_type": "Aadhar Card",
        "id_number": "123456789012",
        "address": "Flat 3, Sai Residency, Wadgaon Sheri, Pune 411014",
        "client_name": ["Anil Shah", "Meera Rao"],
        "client_flat": ["A-101", "B-1204"],
        "client_start": ["06:30", "07:30"],
        "client_end": ["07:30", "08:30"],
        "client_days_0": ["mon", "wed", "fri"],
        "client_days_1": ["tue", "thu"],
        "declaration_signature": "Ramesh Kulkarni",
        "declaration_place": "Pune",
        "declaration_agree": "1",
    }
    data.update({f"ack_{rule.key}": "1" for rule in RULES})
    data.update(overrides)
    return data


# ---------------------------------------------------------------------------
# Rules encoded from the source document
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "clients,expected_fee",
    [(0, 0), (1, 1_000), (4, 1_000), (5, 2_000), (9, 2_000), (10, 3_000), (25, 3_000)],
)
def test_fee_slabs_match_the_rulebook(clients, expected_fee):
    assert monthly_fee_for(clients) == expected_fee


def test_all_fifteen_rules_are_present():
    assert [rule.number for rule in RULES] == list(range(1, 16))
    assert len({rule.key for rule in RULES}) == 15


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

def test_form_renders_with_mount_prefix(client):
    response = client.get("/gym/")
    assert response.status_code == 200
    assert 'action="/gym/submit"' in response.text
    assert "/gym/static/gym.css" in response.text


@pytest.mark.parametrize("path", ["/gym/rules", "/gym/poster", "/gym/health",
                                  "/gym/static/gym.css", "/gym/static/gym.js"])
def test_public_pages_are_reachable_without_a_password(client, path):
    assert client.get(path).status_code == 200


def test_qr_code_is_a_png_of_the_form_url(client):
    response = client.get("/gym/qr.png")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content.startswith(b"\x89PNG")


def test_health_does_not_leak_the_office_contact_details(client):
    body = client.get("/gym/health").json()
    assert body["status"] == "ok"
    assert "notify_email" not in body
    assert "notify_whatsapp" not in body


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_empty_submission_reports_every_missing_section(client):
    response = client.post("/gym/submit", data={})
    assert response.status_code == 400
    for message in ("Please enter your full name",
                    "Add at least one client",
                    "Please tick the acknowledgement",
                    "Type your full name"):
        assert message in response.text


def test_bad_mobile_and_id_number_are_rejected(client):
    response = client.post("/gym/submit", data=payload(mobile="12345", id_number="99"))
    assert response.status_code == 400
    assert "10-digit Indian mobile" in response.text
    assert "Aadhar number is 12 digits" in response.text


def test_pan_format_is_checked():
    _, errors, _ = parse_submission(
        payload(id_type="PAN Card", id_number="NOTAPAN123")
    )
    assert "id_number" in errors


def test_answers_survive_a_validation_failure(client):
    """A trainer must never lose their typing to one bad digit."""
    response = client.post("/gym/submit", data=payload(mobile="12345"))
    assert response.status_code == 400
    assert "Ramesh Kulkarni" in response.text
    assert "Anil Shah" in response.text
    assert "B-1204" in response.text


def test_missing_one_acknowledgement_blocks_the_submission(client):
    data = payload()
    del data[f"ack_{RULES[6].key}"]
    response = client.post("/gym/submit", data=data)
    assert response.status_code == 400
    assert "rule 7" in response.text


def test_slot_outside_gym_hours_needs_a_confirmation(client):
    """Rule 2 — the gym runs 6-11am and 4-9pm."""
    data = payload(client_start=["05:00", "07:30"], client_end=["06:00", "08:30"])
    response = client.post("/gym/submit", data=data)
    assert response.status_code == 400
    assert "outside gym hours" in response.text

    data["outside_hours_informed"] = "1"
    data["outside_hours_approved_by"] = "Mr Patil, Society Secretary"
    data["outside_hours_approval_mode"] = "Phone call"
    assert client.post("/gym/submit", data=data,
                       follow_redirects=False).status_code == 303


def test_midday_slots_submit_once_approval_is_given(client):
    """The reported failure: several clients trained between 11am and 4pm.

    Every one of those slots sits in the gap between the two operating
    windows, so all of them are flagged. Confirming approval must clear the
    lot in one go, not once per client.
    """
    data = payload()
    for key in ("client_name", "client_flat", "client_start", "client_end"):
        data.pop(key)
    data.update({
        "client_name": ["Client One", "Client Two", "Client Three"],
        "client_flat": ["A-101", "B-202", "C-303"],
        "client_start": ["11:30", "12:30", "14:00"],
        "client_end": ["12:30", "13:30", "15:00"],
        "outside_hours_informed": "1",
        "outside_hours_approved_by": "Mr Patil, Society Secretary",
        "outside_hours_approval_mode": "In person at the society office",
    })
    for i in range(3):
        data[f"client_days_{i}"] = ["mon", "wed"]

    response = client.post("/gym/submit", data=data, follow_redirects=False)
    assert response.status_code == 303, response.text[:600]
    page = client.get(response.headers["location"])
    assert "Mr Patil" in page.text


def test_claiming_approval_requires_naming_who_gave_it(client):
    """A tick with nobody's name behind it cannot be checked later."""
    data = payload(client_start=["11:30", "12:30"], client_end=["12:30", "13:30"])
    data["outside_hours_informed"] = "1"

    response = client.post("/gym/submit", data=data)
    assert response.status_code == 400
    assert "who approved" in response.text
    assert "how you took the approval" in response.text

    data["outside_hours_approved_by"] = "Mr Patil, Society Secretary"
    response = client.post("/gym/submit", data=data)
    assert response.status_code == 400, "still missing how it was taken"

    data["outside_hours_approval_mode"] = "WhatsApp message"
    assert client.post("/gym/submit", data=data,
                       follow_redirects=False).status_code == 303


def test_approval_details_reach_the_office(client, monkeypatch):
    posts: list[dict] = []

    class Reply:
        ok = True
        status_code = 201
        text = "{}"

    monkeypatch.setattr("gymform.notify.requests.post",
                        lambda url, json=None, **k: (posts.append(json), Reply())[1])
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")

    data = payload(client_start=["11:30", "12:30"], client_end=["12:30", "13:30"])
    data.update({
        "outside_hours_informed": "1",
        "outside_hours_approved_by": "Mr Patil, Society Secretary",
        "outside_hours_approval_mode": "Phone call",
        "outside_hours_note": "Approved on 12 Aug for the monsoon months",
    })
    assert client.post("/gym/submit", data=data,
                       follow_redirects=False).status_code == 303

    office = posts[0]["textContent"]
    assert "Approval taken from: Mr Patil, Society Secretary" in office
    assert "How it was taken: Phone call" in office
    assert "monsoon months" in office


def test_more_than_four_clients_at_once_needs_committee_approval(client):
    """Rules 4 and 14 — four clients at a time, not four in total."""
    data = payload()
    for key in ("client_name", "client_flat", "client_start", "client_end"):
        data.pop(key)
    data.update({
        "client_name": [f"Client {i}" for i in range(5)],
        "client_flat": [f"A-{i}" for i in range(5)],
        "client_start": ["07:00"] * 5,
        "client_end": ["08:00"] * 5,
    })
    for i in range(5):
        data[f"client_days_{i}"] = ["mon"]
    data.pop("client_days_1", None)
    for i in range(5):
        data[f"client_days_{i}"] = ["mon"]

    response = client.post("/gym/submit", data=data)
    assert response.status_code == 400
    assert "limit is 4" in response.text

    data["committee_approval_reference"] = "SBMC/2026/17"
    assert client.post("/gym/submit", data=data,
                       follow_redirects=False).status_code == 303


def test_five_clients_in_staggered_slots_are_fine(client):
    """Five clients is only a problem when they overlap."""
    data = payload()
    for key in ("client_name", "client_flat", "client_start", "client_end"):
        data.pop(key)
    data.update({
        "client_name": [f"Client {i}" for i in range(5)],
        "client_flat": [f"A-{i}" for i in range(5)],
        "client_start": ["06:00", "07:00", "08:00", "16:00", "17:00"],
        "client_end": ["07:00", "08:00", "09:00", "17:00", "18:00"],
    })
    for i in range(5):
        data[f"client_days_{i}"] = ["mon"]

    response = client.post("/gym/submit", data=data, follow_redirects=False)
    assert response.status_code == 303
    # Five clients moves the trainer into the second fee slab.
    assert "2,000" in client.get(response.headers["location"]).text


def test_honeypot_submission_is_dropped(client):
    assert client.post("/gym/submit", data=payload(website="http://spam")).status_code == 400


# ---------------------------------------------------------------------------
# Happy path, storage and uploads
# ---------------------------------------------------------------------------

def test_valid_submission_is_stored_and_confirmed(client, tmp_path):
    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    assert response.status_code == 303

    page = client.get(response.headers["location"])
    assert page.status_code == 200
    assert "Thank you, Ramesh Kulkarni" in page.text
    assert "1,000" in page.text          # two clients -> first slab

    stored = (tmp_path / "submissions.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(stored) == 1
    assert "Ramesh Kulkarni" in stored[0]
    assert (tmp_path / "submissions.csv").exists()


def test_submission_survives_notification_failure(client):
    """No SMTP is configured in tests, yet the registration must still land."""
    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    assert response.status_code == 303
    page = client.get(response.headers["location"])
    assert "Not sent" in page.text          # honest about delivery
    assert "wa.me" in page.text             # manual fallback offered


def test_id_proof_upload_is_stored_and_typed(client):
    files = {"id_proof": ("aadhar.png", b"\x89PNG\r\n\x1a\n" + b"0" * 64, "image/png")}
    response = client.post("/gym/submit", data=payload(), files=files,
                           follow_redirects=False)
    assert response.status_code == 303
    assert "aadhar.png" in client.get(response.headers["location"]).text

    bad = {"id_proof": ("payload.exe", b"MZ" * 8, "application/octet-stream")}
    rejected = client.post("/gym/submit", data=payload(), files=bad)
    assert rejected.status_code == 400
    assert "PDF or an image" in rejected.text


# ---------------------------------------------------------------------------
# Office-only pages
# ---------------------------------------------------------------------------

def test_review_pages_fail_closed_without_credentials(client, monkeypatch):
    """Submissions hold ID numbers and addresses — never serve them by default."""
    monkeypatch.delenv("GYM_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("GYM_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("RTU_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("RTU_AUTH_PASSWORD", raising=False)

    for path in ("/gym/admin", "/gym/admin/submissions.csv", "/gym/admin/diagnostics"):
        assert client.get(path).status_code == 503


def test_review_pages_need_the_right_password(client, monkeypatch):
    monkeypatch.setenv("GYM_ADMIN_USERNAME", "office")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    client.post("/gym/submit", data=payload(), follow_redirects=False)

    assert client.get("/gym/admin").status_code == 401
    assert client.get("/gym/admin", auth=("office", "wrong")).status_code == 401

    page = client.get("/gym/admin", auth=("office", "secret"))
    assert page.status_code == 200
    assert "Ramesh Kulkarni" in page.text

    csv_export = client.get("/gym/admin/submissions.csv", auth=("office", "secret"))
    assert csv_export.status_code == 200
    assert "Ramesh Kulkarni" in csv_export.text


# ---------------------------------------------------------------------------
# Notification content
# ---------------------------------------------------------------------------

def test_notifications_are_addressed_correctly(client, monkeypatch):
    """The office gets the full summary; the trainer gets their own wording."""
    sent: list[tuple[str, bytes]] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, *args, **kwargs):
            pass

        def login(self, *args, **kwargs):
            pass

        def send_message(self, message):
            sent.append((message["To"], message.as_bytes()))

    monkeypatch.setattr("gymform.notify.smtplib.SMTP", FakeSMTP)
    monkeypatch.setenv("GYM_SMTP_USER", "office@example.com")
    monkeypatch.setenv("GYM_SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("GYM_NOTIFY_EMAIL", "office@example.com")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    assert response.status_code == 303

    assert [to for to, _ in sent] == ["office@example.com", "ramesh@example.com"]

    def part(raw: bytes, content_type: str) -> str:
        message = email.message_from_bytes(raw)
        for piece in message.walk():
            if piece.get_content_type() == content_type:
                return piece.get_payload(decode=True).decode()
        return ""

    office_text = part(sent[0][1], "text/plain")
    assert "New personal trainer registration" in office_text
    assert "Anil Shah — Flat A-101" in office_text
    assert "INR 1,000" in office_text
    assert "wa.me" in part(sent[0][1], "text/html")

    trainer_text = part(sent[1][1], "text/plain")
    assert trainer_text.startswith("Dear Ramesh Kulkarni")
    assert "New personal trainer registration" not in trainer_text


def test_brevo_sends_over_https_when_its_key_is_set(client, monkeypatch):
    """The route that works on hosts blocking outbound SMTP."""
    posts: list[dict] = []

    class Reply:
        ok = True
        status_code = 201
        text = '{"messageId":"<abc@brevo>"}'

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        posts.append({"url": url, "json": json, "headers": headers})
        return Reply()

    monkeypatch.setattr("gymform.notify.requests.post", fake_post)
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("GYM_NOTIFY_EMAIL", "office@example.com")
    monkeypatch.setenv("GYM_EMAIL_FROM", "office@example.com")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    assert response.status_code == 303

    assert len(posts) == 2, "office copy and trainer copy"
    assert all("api.brevo.com" in p["url"] for p in posts)
    assert posts[0]["headers"]["api-key"] == "xkeysib-test"
    assert posts[0]["json"]["to"] == [{"email": "office@example.com"}]
    assert posts[1]["json"]["to"] == [{"email": "ramesh@example.com"}]
    assert "Anil Shah" in posts[0]["json"]["textContent"]
    assert posts[0]["json"]["sender"]["email"] == "office@example.com"

    assert "Sent" in client.get(response.headers["location"]).text


def test_brevo_is_preferred_over_smtp(monkeypatch):
    """Brevo works where SMTP is blocked, so it wins when both are set."""
    monkeypatch.setenv("GYM_SMTP_USER", "someone@gmail.com")
    monkeypatch.setenv("GYM_SMTP_PASSWORD", "app-password")
    from gymform.settings import get_settings

    assert get_settings().email_provider == "smtp"
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")
    assert get_settings().email_provider == "brevo"


def test_brevo_rejecting_an_unverified_sender_is_explained(client, monkeypatch):
    class Reply:
        ok = False
        status_code = 400
        text = '{"code":"invalid_parameter","message":"sender is not valid"}'

    monkeypatch.setattr("gymform.notify.requests.post", lambda *a, **k: Reply())
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    page = client.get(response.headers["location"])
    assert "Not sent" in page.text
    assert "must be verified in Brevo" in page.text


def test_a_blocked_smtp_port_explains_itself(client, monkeypatch):
    """Free hosting blocks outbound SMTP; a bare errno sends people
    off checking their password for an hour instead."""
    def refuse(*args, **kwargs):
        raise OSError(101, "Network is unreachable")

    monkeypatch.setattr("gymform.notify.smtplib.SMTP", refuse)
    monkeypatch.setenv("GYM_SMTP_USER", "someone@gmail.com")
    monkeypatch.setenv("GYM_SMTP_PASSWORD", "app-password")
    monkeypatch.delenv("GYM_BREVO_API_KEY", raising=False)

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    page = client.get(response.headers["location"])
    assert "Not sent" in page.text
    assert "block outbound" in page.text
    assert "GYM_BREVO_API_KEY" in page.text


def test_whatsapp_text_withholds_the_identity_document(client, monkeypatch):
    """WhatsApp travels through a third party and lands in a forwardable chat."""
    from gymform.models import IST, ClientEntry, Submission
    from datetime import datetime

    submission = Submission(
        reference="SB-PT-TEST-1", submitted_at=datetime.now(IST),
        trainer_name="Ramesh Kulkarni", mobile="9876543210",
        email="ramesh@example.com", id_type="Aadhar Card",
        id_number="123456789012",
        address="Flat 3, Sai Residency, Wadgaon Sheri, Pune 411014",
        clients=[ClientEntry("Anil Shah", "A-101", ["mon"], "07:00", "08:00")],
    )
    from gymform.notify import build_summary_text, build_whatsapp_text

    whatsapp = build_whatsapp_text(submission)
    assert "123456789012" not in whatsapp
    assert "Sai Residency" not in whatsapp
    # ...but the office still learns who registered and what they owe.
    assert "Ramesh Kulkarni" in whatsapp
    assert "9876543210" in whatsapp
    assert "Aadhar Card" in whatsapp
    assert "1,000" in whatsapp

    # The email is the channel that carries the full record.
    email_text = build_summary_text(submission)
    assert "123456789012" in email_text and "Sai Residency" in email_text


def test_callmebot_is_used_when_its_free_key_is_set(client, monkeypatch):
    calls: list[dict] = []

    class Reply:
        ok = True
        status_code = 200
        text = "Message queued. You will receive it in a few seconds."

    def fake_get(url, params=None, timeout=None):
        calls.append({"url": url, "params": params})
        return Reply()

    monkeypatch.setattr("gymform.notify.requests.get", fake_get)
    monkeypatch.setenv("GYM_CALLMEBOT_APIKEY", "123456")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    assert response.status_code == 303

    assert len(calls) == 1
    assert "callmebot.com" in calls[0]["url"]
    assert calls[0]["params"]["apikey"] == "123456"
    assert calls[0]["params"]["phone"] == "+917588610829"
    assert "Ramesh Kulkarni" in calls[0]["params"]["text"]

    assert "Sent" in client.get(response.headers["location"]).text


def test_callmebot_html_error_is_not_reported_as_delivered(client, monkeypatch):
    """It answers 200 with an error page for a bad key — that is a failure."""
    class Reply:
        ok = True
        status_code = 200
        text = "<html>ERROR: APIKey is invalid</html>"

    monkeypatch.setattr("gymform.notify.requests.get",
                        lambda *a, **k: Reply())
    monkeypatch.setenv("GYM_CALLMEBOT_APIKEY", "wrong")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    page = client.get(response.headers["location"])
    assert "Not sent" in page.text
    assert "CallMeBot rejected" in page.text


async def test_sending_never_runs_on_the_event_loop_thread(tmp_path, monkeypatch):
    """Regression: a submission must not block the server while it sends.

    Sending is blocking I/O — smtplib, then an HTTPS call — and each can sit
    for its full timeout when a mail server is slow. The submit handler is
    `async`, so calling it inline pins the event loop for the whole send and
    every other request stalls behind it, health checks included. A host that
    health-checks its instances (Render does) reads that as a dead service and
    restarts it, which turns a successful submission into a 502 on the
    confirmation page — exactly the failure seen in production.

    The invariant is asserted by thread rather than by elapsed time: the send
    must happen off the loop's own thread. Timing is not reliable here, because
    in-process the health request can finish before the submission task even
    reaches the send, so a stopwatch reports success against the bug.
    """
    import threading

    import httpx

    import gymform.settings as settings_module
    import gymform.storage as storage_module

    for module in (settings_module, storage_module):
        monkeypatch.setattr(module, "SUBMISSIONS_JSONL", tmp_path / "submissions.jsonl")
        monkeypatch.setattr(module, "SUBMISSIONS_CSV", tmp_path / "submissions.csv")
        monkeypatch.setattr(module, "ID_PROOF_DIR", tmp_path / "id_proofs")
    monkeypatch.setattr(settings_module, "DATA_DIR", tmp_path)
    monkeypatch.setenv("GYM_SUBMIT_COOLDOWN", "0")

    loop_thread = threading.current_thread()
    ran_on: dict[str, threading.Thread] = {}

    def record_thread(settings, submission):
        ran_on["notify"] = threading.current_thread()
        return []

    monkeypatch.setattr("gymform.web.notify.notify_all", record_thread)

    parent = FastAPI()
    parent.mount("/gym", gym_app)

    transport = httpx.ASGITransport(app=parent)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http:
        response = await http.post("/gym/submit", data=payload())

    assert response.status_code == 303
    assert "notify" in ran_on, "notifications were never attempted"
    assert ran_on["notify"] is not loop_thread, (
        "notifications ran on the event loop thread — a slow mail server will "
        "freeze every other request and get the service restarted"
    )


def test_office_email_carries_the_id_proof_attachment(client, monkeypatch):
    sent: list[bytes] = []

    class FakeSMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def starttls(self, *args, **kwargs):
            pass

        def login(self, *args, **kwargs):
            pass

        def send_message(self, message):
            sent.append(message.as_bytes())

    monkeypatch.setattr("gymform.notify.smtplib.SMTP", FakeSMTP)
    monkeypatch.setenv("GYM_SMTP_USER", "office@example.com")
    monkeypatch.setenv("GYM_SMTP_PASSWORD", "app-password")
    monkeypatch.setenv("GYM_SEND_TRAINER_COPY", "0")

    files = {"id_proof": ("pan.pdf", b"%PDF-1.4 sample", "application/pdf")}
    client.post("/gym/submit", data=payload(), files=files, follow_redirects=False)

    assert len(sent) == 1
    names = [
        piece.get_filename()
        for piece in email.message_from_bytes(sent[0]).walk()
        if piece.get_filename()
    ]
    assert names == ["pan.pdf"]
