"""Tests for the chooser page a QR scan lands on.

Run with:  pytest tests/test_portal.py

Two things matter here. One: a resident who scans the code gets a choice and
can reach either form. Two: the links already printed on notices and already
sitting in the society's inbox — which pointed at the trainer form when it was
at the site root — still work.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from portal.web import portal_app  # noqa: E402


@pytest.fixture()
def client(tmp_path, monkeypatch):
    import gymform.settings as settings_module

    monkeypatch.setattr(settings_module, "DATA_DIR", tmp_path)
    monkeypatch.setattr(settings_module, "ID_PROOF_DIR", tmp_path / "id_proofs")
    monkeypatch.delenv("GYM_PUBLIC_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    return TestClient(portal_app)


def test_the_landing_page_offers_both_forms(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "Book a hall or lawn" in response.text
    assert "Register as a personal trainer" in response.text
    assert 'href="/hall/"' in response.text
    assert 'href="/trainer/"' in response.text


def test_both_forms_open_from_the_chooser(client):
    assert "Book a society amenity" in client.get("/hall/").text
    assert "Personal Trainer Registration" in client.get("/trainer/").text


def test_the_landing_page_answers_head(client):
    """Uptime pingers send HEAD; FastAPI does not add it for a GET route."""
    assert client.head("/").status_code == 200
    assert client.head("/health").status_code == 200


def test_health_names_both_forms(client):
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["forms"]["hall"].endswith("/hall")
    assert body["forms"]["trainer"].endswith("/trainer")


def test_the_qr_code_encodes_the_chooser_not_either_form(client, monkeypatch):
    monkeypatch.setenv("GYM_PUBLIC_URL", "https://silicon-bay.example")
    assert client.get("/health").json()["url"] == "https://silicon-bay.example"

    response = client.get("/qr.png")
    assert response.status_code == 200
    assert response.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_the_poster_carries_the_one_code_for_both_forms(client):
    response = client.get("/poster")
    assert response.status_code == 200
    assert "Book a hall or lawn" in response.text
    assert "Register as a personal trainer" in response.text


@pytest.mark.parametrize(
    "old,new",
    [
        ("/gym", "/trainer"),
        ("/rules", "/trainer/rules"),
        ("/admin", "/trainer/admin"),
        ("/submitted/SB-PT-1", "/trainer/submitted/SB-PT-1"),
    ],
)
def test_links_printed_before_the_move_still_work(client, old, new):
    """Notices are already on walls and links already in the office's inbox."""
    response = client.get(old, follow_redirects=False)
    assert response.status_code == 308
    assert response.headers["location"] == new


def test_an_old_decision_link_keeps_its_signature_through_the_redirect(client):
    response = client.get(
        "/decide/SB-PT-1?d=approved&t=abc123", follow_redirects=False
    )
    assert response.status_code == 308
    assert response.headers["location"] == "/trainer/decide/SB-PT-1?d=approved&t=abc123"


def test_the_decision_links_in_email_point_at_the_form_not_the_chooser(monkeypatch):
    """The signed Approve/Reject links must survive the forms moving.

    A link built from the public URL alone would land on the chooser and 404 —
    which the office would read as "the approval link is broken".
    """
    monkeypatch.setenv("GYM_PUBLIC_URL", "https://silicon-bay.example")
    monkeypatch.setenv("GYM_DECISION_SECRET", "test-secret")

    from gymform.web import _form_url as trainer_form_url
    from hallform.web import _form_url as hall_form_url

    class FakeRequest:
        def __init__(self, root_path):
            self.scope = {"root_path": root_path}
            self.base_url = "https://silicon-bay.example/"

    assert trainer_form_url(FakeRequest("/trainer")) == (
        "https://silicon-bay.example/trainer"
    )
    assert hall_form_url(FakeRequest("/hall")) == "https://silicon-bay.example/hall"
