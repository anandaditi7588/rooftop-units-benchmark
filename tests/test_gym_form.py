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
        monkeypatch.setattr(module, "PAYMENTS_JSONL", tmp_path / "payments.jsonl")
        monkeypatch.setattr(module, "APPROVALS_JSONL", tmp_path / "approvals.jsonl")
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
        monkeypatch.setattr(module, "PAYMENTS_JSONL", tmp_path / "payments.jsonl")
        monkeypatch.setattr(module, "APPROVALS_JSONL", tmp_path / "approvals.jsonl")
        monkeypatch.setattr(module, "ID_PROOF_DIR", tmp_path / "id_proofs")
    monkeypatch.setattr(settings_module, "DATA_DIR", tmp_path)
    monkeypatch.setenv("GYM_SUBMIT_COOLDOWN", "0")

    loop_thread = threading.current_thread()
    ran_on: dict[str, threading.Thread] = {}

    def record_thread(settings, submission, base_url=""):
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


def test_office_can_fire_a_test_alert_from_the_browser(client, monkeypatch):
    """The office checks alerts from a phone, not a terminal."""
    monkeypatch.setenv("GYM_ADMIN_USERNAME", "office")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    page = client.get("/gym/admin", auth=("office", "secret"))
    assert page.status_code == 200
    assert "Send a test alert" in page.text
    assert "/gym/admin/test-notification" in page.text

    calls: list[dict] = []

    class Reply:
        ok = True
        status_code = 200
        text = "Message queued."

    monkeypatch.setattr("gymform.notify.requests.get",
                        lambda url, params=None, **k: (calls.append(params), Reply())[1])
    monkeypatch.setenv("GYM_CALLMEBOT_APIKEY", "123456")

    response = client.post("/gym/admin/test-notification", auth=("office", "secret"))
    assert response.status_code == 200
    results = response.json()["results"]
    whatsapp = next(r for r in results if r["channel"] == "whatsapp")
    assert whatsapp["ok"] is True
    assert calls[0]["phone"] == "+917588610829"
    assert "Test Trainer" in calls[0]["text"]


def test_test_alert_stays_behind_the_office_password(client, monkeypatch):
    monkeypatch.delenv("GYM_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("GYM_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("RTU_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("RTU_AUTH_PASSWORD", raising=False)
    assert client.post("/gym/admin/test-notification").status_code == 503


# ---------------------------------------------------------------------------
# Whapi.Cloud WhatsApp provider
# ---------------------------------------------------------------------------

def test_whapi_sends_the_registration(client, monkeypatch):
    calls: list[dict] = []

    class Reply:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"sent": True, "message": {"id": "abc"}}

    def fake_post(url, json=None, headers=None, timeout=None, **kwargs):
        calls.append({"url": url, "json": json, "headers": headers})
        return Reply()

    monkeypatch.setattr("gymform.notify.requests.post", fake_post)
    monkeypatch.setenv("GYM_WHAPI_TOKEN", "whapi-test-token")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    assert response.status_code == 303

    assert calls[0]["url"] == "https://gate.whapi.cloud/messages/text"
    assert calls[0]["headers"]["Authorization"] == "Bearer whapi-test-token"
    assert calls[0]["json"]["to"] == "917588610829"
    assert "Ramesh Kulkarni" in calls[0]["json"]["body"]
    # The identity document still must not travel over WhatsApp.
    assert "123456789012" not in calls[0]["json"]["body"]

    assert "Sent" in client.get(response.headers["location"]).text


def test_whapi_accepting_but_not_sending_is_reported(client, monkeypatch):
    """A 200 with sent=false means the linked WhatsApp channel is not ready."""
    class Reply:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"sent": False, "error": {"message": "channel not ready"}}

    monkeypatch.setattr("gymform.notify.requests.post", lambda *a, **k: Reply())
    monkeypatch.setenv("GYM_WHAPI_TOKEN", "whapi-test-token")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    page = client.get(response.headers["location"])
    assert "Not sent" in page.text
    assert "channel is linked" in page.text


def test_whapi_bad_token_is_explained(client, monkeypatch):
    class Reply:
        ok = False
        status_code = 401
        text = '{"error":"unauthorized"}'

    monkeypatch.setattr("gymform.notify.requests.post", lambda *a, **k: Reply())
    monkeypatch.setenv("GYM_WHAPI_TOKEN", "wrong")

    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    page = client.get(response.headers["location"])
    assert "GYM_WHAPI_TOKEN" in page.text


def test_whapi_is_chosen_over_callmebot(monkeypatch):
    from gymform.settings import get_settings

    monkeypatch.setenv("GYM_CALLMEBOT_APIKEY", "123456")
    assert get_settings().whatsapp_provider == "callmebot"
    monkeypatch.setenv("GYM_WHAPI_TOKEN", "whapi-test-token")
    assert get_settings().whatsapp_provider == "whapi"
    assert get_settings().whatsapp_provider_label == "Whapi.Cloud"


# ---------------------------------------------------------------------------
# Amenity fee collection over UPI
# ---------------------------------------------------------------------------

def _submit_and_get_reference(client) -> str:
    response = client.post("/gym/submit", data=payload(), follow_redirects=False)
    assert response.status_code == 303
    return response.headers["location"].rsplit("/", 1)[-1]


def test_payment_section_is_off_until_a_upi_id_is_set(client, monkeypatch):
    monkeypatch.delenv("GYM_UPI_ID", raising=False)
    reference = _submit_and_get_reference(client)
    page = client.get(f"/gym/submitted/{reference}")
    assert "Pay the monthly amenity fee" not in page.text
    assert client.get(f"/gym/upi-qr/{reference}.png").status_code == 404
    assert client.post(f"/gym/payment/{reference}",
                       data={"upi_reference": "412345678901"}).status_code == 404


def test_upi_link_carries_payee_amount_and_reference(client, monkeypatch):
    monkeypatch.setenv("GYM_UPI_ID", "siliconbay@okhdfcbank")
    monkeypatch.setenv("GYM_UPI_PAYEE_NAME", "Silicon Bay Society")

    reference = _submit_and_get_reference(client)
    page = client.get(f"/gym/submitted/{reference}")
    assert page.status_code == 200
    assert "upi://pay?" in page.text
    assert "siliconbay%40okhdfcbank" in page.text     # pa=, URL-encoded
    assert "am=1000.00" in page.text                  # two clients -> first slab
    assert "cu=INR" in page.text
    assert reference in page.text

    qr = client.get(f"/gym/upi-qr/{reference}.png")
    assert qr.status_code == 200
    assert qr.content.startswith(b"\x89PNG")


def test_upi_amount_follows_the_fee_slab(monkeypatch):
    from gymform.payments import build_upi_uri

    uri = build_upi_uri(upi_id="a@b", payee_name="Soc", amount=2000, reference="SB-PT-1")
    assert "am=2000.00" in uri
    assert "pa=a%40b" in uri


def test_reporting_a_payment_records_and_notifies(client, monkeypatch, tmp_path):
    posts: list[dict] = []

    class Reply:
        ok = True
        status_code = 201
        text = "{}"

    monkeypatch.setattr("gymform.notify.requests.post",
                        lambda url, json=None, **k: (posts.append(json), Reply())[1])
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("GYM_UPI_ID", "siliconbay@okhdfcbank")

    reference = _submit_and_get_reference(client)
    posts.clear()   # drop the registration emails

    response = client.post(f"/gym/payment/{reference}",
                           data={"upi_reference": "412345678901"},
                           follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].endswith("?paid=1")

    page = client.get(f"/gym/submitted/{reference}")
    assert "Payment reported" in page.text
    assert "412345678901" in page.text
    # The pay button is gone once a payment is on record.
    assert "I have paid — record it" not in page.text

    assert (tmp_path / "payments.jsonl").exists()

    office = posts[0]["textContent"]
    assert "412345678901" in office
    assert "not a confirmed receipt" in office


def test_a_junk_payment_reference_is_rejected(client, monkeypatch):
    monkeypatch.setenv("GYM_UPI_ID", "siliconbay@okhdfcbank")
    reference = _submit_and_get_reference(client)

    response = client.post(f"/gym/payment/{reference}",
                           data={"upi_reference": "??"}, follow_redirects=False)
    assert response.status_code == 303
    assert "payment_error" in response.headers["location"]

    page = client.get(response.headers["location"])
    assert "Enter the UPI reference number" in page.text
    assert "Payment reported" not in page.text


def test_payment_endpoints_reject_an_unknown_reference(client, monkeypatch):
    monkeypatch.setenv("GYM_UPI_ID", "siliconbay@okhdfcbank")
    assert client.get("/gym/upi-qr/SB-PT-NOPE.png").status_code == 404
    assert client.post("/gym/payment/SB-PT-NOPE",
                       data={"upi_reference": "412345678901"}).status_code == 404


def test_office_page_shows_payment_status(client, monkeypatch):
    monkeypatch.setenv("GYM_UPI_ID", "siliconbay@okhdfcbank")
    monkeypatch.setenv("GYM_ADMIN_USERNAME", "office")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    reference = _submit_and_get_reference(client)

    page = client.get("/gym/admin", auth=("office", "secret"))
    assert "Not reported" in page.text

    client.post(f"/gym/payment/{reference}", data={"upi_reference": "412345678901"},
                follow_redirects=False)

    page = client.get("/gym/admin", auth=("office", "secret"))
    assert "Reported paid" in page.text
    assert "412345678901" in page.text
    assert "match it against the bank statement" in page.text

    csv_export = client.get("/gym/admin/submissions.csv", auth=("office", "secret"))
    assert "payment_upi_reference" in csv_export.text


# ---------------------------------------------------------------------------
# Office approval — the society's actual gate
# ---------------------------------------------------------------------------

def _office(monkeypatch):
    monkeypatch.setenv("GYM_ADMIN_USERNAME", "office")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")
    return ("office", "secret")


def test_a_new_registration_starts_pending(client, monkeypatch):
    auth = _office(monkeypatch)
    reference = _submit_and_get_reference(client)

    page = client.get(f"/gym/submitted/{reference}")
    assert "Awaiting office approval" in page.text
    assert "do not start training until then" in page.text

    office = client.get("/gym/admin", auth=auth)
    assert "Awaiting your approval" in office.text


def test_office_can_approve_and_the_trainer_is_told(client, monkeypatch):
    auth = _office(monkeypatch)
    posts: list[dict] = []

    class Reply:
        ok = True
        status_code = 201
        text = "{}"

    monkeypatch.setattr("gymform.notify.requests.post",
                        lambda url, json=None, **k: (posts.append(json), Reply())[1])
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")

    reference = _submit_and_get_reference(client)
    posts.clear()

    response = client.post(f"/gym/admin/decision/{reference}",
                           data={"decision": "approved", "note": "Fee received 13 Aug"},
                           auth=auth, follow_redirects=False)
    assert response.status_code == 303

    page = client.get(f"/gym/submitted/{reference}")
    assert "Approved by the society office" in page.text
    assert "Fee received 13 Aug" in page.text

    office = client.get("/gym/admin", auth=auth)
    assert "Approved" in office.text

    # The trainer is the one waiting on this, so they get the email.
    assert posts[0]["to"] == [{"email": "ramesh@example.com"}]
    assert "approved" in posts[0]["subject"].lower()
    assert "sign the security register" in posts[0]["textContent"]


def test_office_can_reject(client, monkeypatch):
    auth = _office(monkeypatch)
    reference = _submit_and_get_reference(client)

    client.post(f"/gym/admin/decision/{reference}",
                data={"decision": "rejected", "note": "Fee not received"},
                auth=auth, follow_redirects=False)

    page = client.get(f"/gym/submitted/{reference}")
    assert "Not approved yet" in page.text
    assert "Fee not received" in page.text


def test_a_later_decision_supersedes_the_earlier_one(client, monkeypatch):
    """The office may reject, then approve once the fee lands."""
    auth = _office(monkeypatch)
    reference = _submit_and_get_reference(client)

    client.post(f"/gym/admin/decision/{reference}", data={"decision": "rejected"},
                auth=auth, follow_redirects=False)
    client.post(f"/gym/admin/decision/{reference}",
                data={"decision": "approved", "note": "Paid later"},
                auth=auth, follow_redirects=False)

    page = client.get(f"/gym/submitted/{reference}")
    assert "Approved by the society office" in page.text
    assert "Not approved yet" not in page.text


def test_only_the_office_can_decide(client, monkeypatch):
    monkeypatch.delenv("GYM_ADMIN_USERNAME", raising=False)
    monkeypatch.delenv("GYM_ADMIN_PASSWORD", raising=False)
    monkeypatch.delenv("RTU_AUTH_USERNAME", raising=False)
    monkeypatch.delenv("RTU_AUTH_PASSWORD", raising=False)
    reference = _submit_and_get_reference(client)

    # Fails closed with no admin login configured...
    assert client.post(f"/gym/admin/decision/{reference}",
                       data={"decision": "approved"}).status_code == 503

    # ...and needs the right password once one is.
    auth = _office(monkeypatch)
    assert client.post(f"/gym/admin/decision/{reference}",
                       data={"decision": "approved"},
                       auth=("office", "wrong")).status_code == 401
    assert client.post(f"/gym/admin/decision/{reference}", data={"decision": "approved"},
                       auth=auth, follow_redirects=False).status_code == 303


def test_a_bogus_decision_is_refused(client, monkeypatch):
    auth = _office(monkeypatch)
    reference = _submit_and_get_reference(client)
    assert client.post(f"/gym/admin/decision/{reference}",
                       data={"decision": "maybe"}, auth=auth).status_code == 400
    assert client.post("/gym/admin/decision/SB-PT-NOPE",
                       data={"decision": "approved"}, auth=auth).status_code == 404


def test_status_reaches_the_csv(client, monkeypatch):
    auth = _office(monkeypatch)
    reference = _submit_and_get_reference(client)
    client.post(f"/gym/admin/decision/{reference}", data={"decision": "approved"},
                auth=auth, follow_redirects=False)

    csv_export = client.get("/gym/admin/submissions.csv", auth=auth)
    assert "status" in csv_export.text


def test_poster_warns_about_the_cold_start(client):
    """Free hosting sleeps when idle; a trainer should not read that as broken."""
    page = client.get("/gym/poster")
    assert "up to a minute" in page.text
    assert "not broken" in page.text
    # And it sets the expectation that approval, not submission, grants entry.
    assert "approve before your first session" in page.text


@pytest.mark.parametrize("path", ["/gym/health", "/gym/"])
def test_head_requests_are_answered(client, path):
    """Uptime pingers use HEAD, and the host's own check does too.

    A 405 here is what made the free-tier keep-warm ping report failure: the
    first ping of the day arrives while the platform is still serving its
    "waking up" page, which is larger than some pingers will accept. HEAD has
    no body, so it sidesteps the size cap — but only if it is not rejected.
    """
    response = client.head(path)
    assert response.status_code == 200
    assert response.content == b""


def test_health_response_is_small(client):
    """It is polled every few minutes; there is no reason for it to be big."""
    assert len(client.get("/gym/health").content) < 512


# ---------------------------------------------------------------------------
# Deciding straight from the office email
# ---------------------------------------------------------------------------

def _decide_links(client, monkeypatch, reference=None):
    """The Approve/Reject URLs the office email would carry."""
    from gymform import tokens
    return {
        d: f"/gym/decide/{reference}?d={d}&t={tokens.make_token(reference, d)}"
        for d in ("approved", "rejected")
    }


def test_office_email_carries_approve_and_reject_buttons(client, monkeypatch):
    posts: list[dict] = []

    class Reply:
        ok = True
        status_code = 201
        text = "{}"

    monkeypatch.setattr("gymform.notify.requests.post",
                        lambda url, json=None, **k: (posts.append(json), Reply())[1])
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    reference = _submit_and_get_reference(client)
    office_html = posts[0]["htmlContent"]

    assert f"/decide/{reference}?d=approved" in office_html
    assert f"/decide/{reference}?d=rejected" in office_html
    assert "nothing changes when you click" in office_html.lower()


def test_no_buttons_without_a_signing_secret(client, monkeypatch):
    """Unsigned links would let anyone approve; leave them out instead."""
    posts: list[dict] = []

    class Reply:
        ok = True
        status_code = 201
        text = "{}"

    monkeypatch.setattr("gymform.notify.requests.post",
                        lambda url, json=None, **k: (posts.append(json), Reply())[1])
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")
    for name in ("GYM_DECISION_SECRET", "GYM_ADMIN_PASSWORD", "RTU_AUTH_PASSWORD"):
        monkeypatch.delenv(name, raising=False)

    _submit_and_get_reference(client)
    assert "/decide/" not in posts[0]["htmlContent"]


def test_clicking_the_link_does_not_decide_anything(client, monkeypatch):
    """Mail scanners follow links before a person sees them.

    If the GET decided, a spam filter opening the email would approve the
    trainer. The click may only *show* the decision; a POST commits it.
    """
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")
    reference = _submit_and_get_reference(client)
    links = _decide_links(client, monkeypatch, reference)

    page = client.get(links["approved"])
    assert page.status_code == 200
    assert "Approve" in page.text
    assert "Nothing has changed yet" in page.text

    # Still pending — the visit alone changed nothing.
    assert "Awaiting office approval" in client.get(f"/gym/submitted/{reference}").text


def test_confirming_from_the_email_link_approves_and_tells_the_trainer(client, monkeypatch):
    sent: list[dict] = []

    class Reply:
        ok = True
        status_code = 201
        text = "{}"

    monkeypatch.setattr("gymform.notify.requests.post",
                        lambda url, json=None, **k: (sent.append(json), Reply())[1])
    monkeypatch.setenv("GYM_BREVO_API_KEY", "xkeysib-test")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    reference = _submit_and_get_reference(client)
    from gymform import tokens
    sent.clear()

    response = client.post(
        f"/gym/decide/{reference}",
        data={"decision": "approved", "token": tokens.make_token(reference, "approved"),
              "note": "Fee received 16 Aug"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    done = client.get(response.headers["location"])
    assert "is approved" in done.text

    assert "Approved by the society office" in client.get(
        f"/gym/submitted/{reference}").text

    # The trainer is told, and the note travels with it.
    assert sent[0]["to"] == [{"email": "ramesh@example.com"}]
    assert "Fee received 16 Aug" in sent[0]["textContent"]


def test_a_tampered_link_is_refused(client, monkeypatch):
    """The token binds one decision to one registration."""
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")
    reference = _submit_and_get_reference(client)
    from gymform import tokens

    reject_token = tokens.make_token(reference, "rejected")

    # Reject token re-pointed at approve.
    assert client.get(
        f"/gym/decide/{reference}?d=approved&t={reject_token}").status_code == 403
    assert client.post(
        f"/gym/decide/{reference}",
        data={"decision": "approved", "token": reject_token}).status_code == 403
    # Made-up token.
    assert client.get(
        f"/gym/decide/{reference}?d=approved&t=deadbeefdeadbeefdead").status_code == 403
    # No token at all.
    assert client.get(f"/gym/decide/{reference}?d=approved").status_code == 403

    assert "Awaiting office approval" in client.get(f"/gym/submitted/{reference}").text


def test_the_decision_also_goes_to_the_trainer_on_whatsapp(client, monkeypatch):
    calls: list[dict] = []

    class Reply:
        ok = True
        status_code = 200

        @staticmethod
        def json():
            return {"sent": True}

    monkeypatch.setattr("gymform.notify.requests.post",
                        lambda url, json=None, **k: (calls.append(json), Reply())[1])
    monkeypatch.setenv("GYM_WHAPI_TOKEN", "whapi-test-token")
    monkeypatch.setenv("GYM_ADMIN_PASSWORD", "secret")

    reference = _submit_and_get_reference(client)
    from gymform import tokens
    calls.clear()

    client.post(f"/gym/decide/{reference}",
                data={"decision": "approved",
                      "token": tokens.make_token(reference, "approved")},
                follow_redirects=False)

    # Goes to the trainer's own number, not the office's.
    assert calls[0]["to"] == "919876543210"
    assert "Approved" in calls[0]["body"]
    assert reference in calls[0]["body"]
