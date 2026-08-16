"""HTTP layer for the trainer registration form.

Mounted as a standalone sub-application (see ``app.py``) so that the public
form is reachable without the parent application's HTTP Basic gate — a
trainer scanning a QR code at the gym door cannot be expected to hold the
engineering tool's password.
"""
from __future__ import annotations

import io
import logging
import threading
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gymform import notify, payments, storage
from gymform.admin_auth import require_admin
from gymform.models import (
    APPROVAL_MODES,
    DAYS,
    OPERATING_HOURS_TEXT,
    ClientEntry,
    Submission,
    format_time,
    parse_submission,
)
from gymform.rules import (
    DECLARATION,
    DOCUMENT_TITLE,
    FEE_SLABS,
    ID_PROOF_TYPES,
    MAX_CLIENTS_PER_SESSION,
    OPERATING_WINDOWS,
    RULES,
    SOCIETY_ADDRESS,
    SOCIETY_NAME,
    monthly_fee_for,
)
# Imported as a module, not as names: the CSV path is looked up at call time so
# tests (and any redirected data directory) are honoured rather than frozen in
# at import.
from gymform import settings as gym_settings
from gymform.settings import STATIC_DIR, TEMPLATES_DIR, ensure_dirs, get_settings

logger = logging.getLogger(__name__)

gym_app = FastAPI(
    title=f"{SOCIETY_NAME} — Gym Registration for Personal Trainers",
    docs_url=None,
    redoc_url=None,
)
gym_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="gym-static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

ALLOWED_ID_PROOF_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".heic"}

# Delivery outcomes for the most recent submissions, so the success page can
# tell the trainer whether the office was actually reached. Small, capped, and
# deliberately in-memory: it is a UI nicety, never the system of record.
_recent_deliveries: dict[str, list[dict]] = {}
_recent_lock = threading.Lock()
_MAX_RECENT = 50

# Per-IP submit throttle (rule of thumb, not security): stops a stuck phone
# from double-posting the same registration.
_last_submit_at: dict[str, float] = {}


def _remember_delivery(reference: str, results: list[notify.DeliveryResult]) -> None:
    with _recent_lock:
        _recent_deliveries[reference] = [r.as_dict() for r in results]
        while len(_recent_deliveries) > _MAX_RECENT:
            _recent_deliveries.pop(next(iter(_recent_deliveries)))


def _base_path(request: Request) -> str:
    """URL prefix this sub-app is served under, e.g. '/gym'.

    Empty when the form runs standalone at the site root (see
    ``gymform/standalone.py``), which is exactly what the templates want for
    building links.
    """
    return (request.scope.get("root_path") or "").rstrip("/")


def _form_url(request: Request) -> str:
    """Absolute, public URL of the form — what the QR code encodes."""
    configured = get_settings().public_url
    if configured:
        return configured.rstrip("/")
    return str(request.base_url).rstrip("/") + _base_path(request)


def _template_context(request: Request, **extra) -> dict:
    context = {
        "base": _base_path(request),
        "society_name": SOCIETY_NAME,
        "society_address": SOCIETY_ADDRESS,
        "document_title": DOCUMENT_TITLE,
        "rules": RULES,
        "fee_slabs": FEE_SLABS,
        "id_proof_types": ID_PROOF_TYPES,
        "days": DAYS,
        "approval_modes": APPROVAL_MODES,
        "operating_windows": OPERATING_WINDOWS,
        "operating_hours_text": OPERATING_HOURS_TEXT,
        "max_clients_per_session": MAX_CLIENTS_PER_SESSION,
        "declaration": DECLARATION,
        "format_time": format_time,
    }
    context.update(extra)
    return context


def _empty_form_values() -> dict:
    return {
        "trainer_name": "",
        "mobile": "",
        "whatsapp": "",
        "email": "",
        "id_type": "",
        "id_number": "",
        "address": "",
        "emergency_contact_name": "",
        "emergency_contact_mobile": "",
        "clients": [ClientEntry().as_dict()],
        "outside_hours_informed": False,
        "outside_hours_approved_by": "",
        "outside_hours_approval_mode": "",
        "outside_hours_note": "",
        "committee_approval_reference": "",
        "acknowledgements": {rule.key: False for rule in RULES},
        "declaration_signature": "",
        "declaration_place": "",
        "declaration_agree": False,
    }


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@gym_app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def form_page(request: Request):
    return templates.TemplateResponse(
        request, "form.html",
        _template_context(request, values=_empty_form_values(), errors={}),
    )


@gym_app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    return templates.TemplateResponse(request, "rules.html", _template_context(request))


@gym_app.post("/submit")
async def submit(request: Request):
    settings = get_settings()
    form = await request.form()

    # Honeypot: a field hidden from people, irresistible to bots.
    if (form.get("website") or "").strip():
        logger.info("Gym form: dropped a submission that filled the honeypot field.")
        raise HTTPException(400, "Submission rejected.")

    client_ip = (request.client.host if request.client else "") or "unknown"
    now = time.monotonic()
    previous = _last_submit_at.get(client_ip)
    if previous is not None and now - previous < settings.submit_cooldown_seconds:
        raise HTTPException(
            429,
            "That looks like a duplicate submission — please wait a few seconds "
            "before submitting again.",
        )

    # Multi-value fields (the client rows) need getlist, not dict access.
    values: dict[str, object] = {}
    for key in form.keys():
        items = form.getlist(key)
        values[key] = items if len(items) > 1 else items[0]

    submission, errors, raw = parse_submission(values)
    if submission is None:
        return templates.TemplateResponse(
            request, "form.html",
            _template_context(request, values=raw, errors=errors),
            status_code=400,
        )

    _last_submit_at[client_ip] = now

    # Optional ID proof upload — stored beside the submission and attached to
    # the office's notification email.
    upload = form.get("id_proof")
    if upload is not None and getattr(upload, "filename", ""):
        suffix = Path(upload.filename).suffix.lower()
        if suffix not in ALLOWED_ID_PROOF_SUFFIXES:
            errors["id_proof"] = (
                "Upload the ID proof as a PDF or an image (jpg, png, webp, heic)."
            )
        else:
            content = await upload.read()
            if len(content) > settings.max_id_proof_bytes:
                limit_mb = settings.max_id_proof_bytes / (1024 * 1024)
                errors["id_proof"] = f"That file is larger than {limit_mb:.0f} MB."
            else:
                stored = await run_in_threadpool(
                    storage.save_id_proof, submission.reference, upload.filename, content
                )
                submission.id_proof_filename = Path(upload.filename).name
                submission.id_proof_path = str(stored)

    if errors:
        return templates.TemplateResponse(
            request, "form.html",
            _template_context(request, values=raw, errors=errors),
            status_code=400,
        )

    # Store first: a notification failure must never lose a registration.
    #
    # Both of these are blocking calls — file writes, then smtplib and an HTTPS
    # request that can each sit for their full timeout when a mail server is
    # slow. This handler is `async`, so running them inline would block the
    # event loop for the whole send, freezing *every* other request on the
    # process, health checks included. A host that health-checks its instances
    # (Render does) then reads that as a dead service and restarts it — which
    # is exactly how a successful submission ends up on a 502 page a moment
    # later. Offloading to the threadpool keeps the loop free while still
    # letting the confirmation page report real delivery results.
    await run_in_threadpool(storage.save_submission, submission)
    results = await run_in_threadpool(notify.notify_all, settings, submission)
    _remember_delivery(submission.reference, results)

    return RedirectResponse(
        f"{_base_path(request)}/submitted/{submission.reference}", status_code=303
    )


def _find_submission(reference: str) -> dict | None:
    return next(
        (r for r in storage.load_submissions() if r.get("reference") == reference), None
    )


def _payment_context(settings, record: dict) -> dict:
    """Everything the confirmation page needs to offer a UPI payment."""
    if not settings.payments_enabled:
        return {"payment_enabled": False}
    return {
        "payment_enabled": True,
        "upi_id": settings.upi_id,
        "upi_payee_name": settings.upi_payee_name,
        "upi_uri": payments.build_upi_uri(
            upi_id=settings.upi_id,
            payee_name=settings.upi_payee_name,
            amount=record.get("monthly_fee_inr", 0),
            reference=record.get("reference", ""),
        ),
    }


@gym_app.get("/submitted/{reference}", response_class=HTMLResponse)
def submitted_page(request: Request, reference: str, paid: int = 0):
    record = _find_submission(reference)
    if record is None:
        raise HTTPException(404, "Unknown submission reference.")

    with _recent_lock:
        deliveries = _recent_deliveries.get(reference, [])

    whatsapp_link = next(
        (d["link"] for d in deliveries if d["channel"] == "whatsapp" and d["link"]), ""
    )
    settings = get_settings()
    return templates.TemplateResponse(
        request, "submitted.html",
        _template_context(
            request,
            record=record,
            deliveries=deliveries,
            whatsapp_link=whatsapp_link,
            office_email=settings.notify_email,
            payment_just_reported=bool(paid),
            payment_error=request.query_params.get("payment_error", ""),
            **_payment_context(settings, record),
        ),
    )


@gym_app.get("/upi-qr/{reference}.png")
def upi_qr(reference: str, size: int = 8):
    """QR of the UPI intent, for paying from a different device.

    The ``upi://`` button only works on a phone that has a UPI app installed;
    scanning this covers everyone else.
    """
    settings = get_settings()
    if not settings.payments_enabled:
        raise HTTPException(404, "Payment collection is not switched on.")

    record = _find_submission(reference)
    if record is None:
        raise HTTPException(404, "Unknown submission reference.")

    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise HTTPException(501, "QR generation needs the 'qrcode' package.") from exc

    uri = payments.build_upi_uri(
        upi_id=settings.upi_id,
        payee_name=settings.upi_payee_name,
        amount=record.get("monthly_fee_inr", 0),
        reference=reference,
    )
    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=max(4, min(int(size), 16)),
        border=2,
    )
    qr.add_data(uri)
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(fill_color="#16233a", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(buffer, media_type="image/png")


@gym_app.post("/payment/{reference}")
async def report_payment(request: Request, reference: str):
    """Record the UPI reference a trainer says they paid with.

    This is a *claim*, not a verification — UPI gives the server no callback,
    so nothing here proves money moved. It is stored and forwarded to the
    office so someone can match it against the bank statement, and every
    surface that shows it says "reported" rather than "paid".
    """
    settings = get_settings()
    if not settings.payments_enabled:
        raise HTTPException(404, "Payment collection is not switched on.")

    record = _find_submission(reference)
    if record is None:
        raise HTTPException(404, "Unknown submission reference.")

    form = await request.form()
    upi_reference = payments.clean_upi_reference(str(form.get("upi_reference") or ""))
    base = _base_path(request)
    if upi_reference is None:
        return RedirectResponse(
            f"{base}/submitted/{reference}"
            "?payment_error=Enter+the+UPI+reference+number+shown+in+your+payment+app.",
            status_code=303,
        )

    amount = record.get("monthly_fee_inr", 0)
    await run_in_threadpool(storage.record_payment, reference, upi_reference, amount)
    await run_in_threadpool(notify.notify_payment, settings, record, upi_reference, amount)

    return RedirectResponse(f"{base}/submitted/{reference}?paid=1", status_code=303)


# ---------------------------------------------------------------------------
# QR code
# ---------------------------------------------------------------------------

@gym_app.get("/qr.png")
def qr_png(request: Request, size: int = 10):
    """PNG QR code pointing at this form. Print it, stick it on the gym door."""
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise HTTPException(
            501,
            "QR generation needs the 'qrcode' package. Install it with: "
            "pip install 'qrcode[pil]'",
        ) from exc

    box_size = max(4, min(int(size), 20))
    qr = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=box_size,
        border=2,
    )
    qr.add_data(_form_url(request))
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(fill_color="#16233a", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={"Content-Disposition": 'inline; filename="silicon-bay-gym-form-qr.png"'},
    )


@gym_app.get("/poster", response_class=HTMLResponse)
def poster(request: Request):
    """A printable A4 notice with the QR code, for the gym or the gate."""
    return templates.TemplateResponse(
        request, "poster.html",
        _template_context(request, form_url=_form_url(request)),
    )


# ---------------------------------------------------------------------------
# Office-only pages
# ---------------------------------------------------------------------------

@gym_app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_page(request: Request):
    records = storage.load_submissions()
    return templates.TemplateResponse(
        request, "admin.html",
        _template_context(
            request,
            records=records,
            settings=get_settings(),
            monthly_fee_for=monthly_fee_for,
        ),
    )


@gym_app.post("/admin/decision/{reference}", dependencies=[Depends(require_admin)])
async def admin_decision(request: Request, reference: str):
    """Approve or reject a registration.

    This is the society's actual gate. The form cannot see the bank account,
    so it can never know whether the amenity fee arrived — the office can, and
    approves once it has. Security admits approved trainers only, which is
    exactly how the rulebook already words it: entry is permitted when the
    office has registered the trainer, not when a web page said so.
    """
    record = _find_submission(reference)
    if record is None:
        raise HTTPException(404, "Unknown submission reference.")

    form = await request.form()
    decision = str(form.get("decision") or "").strip()
    if decision not in storage.DECISIONS:
        raise HTTPException(400, f"Decision must be one of {storage.DECISIONS}.")
    note = " ".join(str(form.get("note") or "").split())[:300]

    await run_in_threadpool(storage.record_decision, reference, decision, note)
    await run_in_threadpool(
        notify.notify_decision, get_settings(), record, decision, note
    )
    return RedirectResponse(f"{_base_path(request)}/admin#{reference}", status_code=303)


@gym_app.get("/admin/submissions.csv", dependencies=[Depends(require_admin)])
def admin_csv():
    ensure_dirs()
    csv_path = gym_settings.SUBMISSIONS_CSV
    if not csv_path.exists():
        raise HTTPException(404, "No submissions yet.")
    return Response(
        csv_path.read_bytes(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="gym-trainer-submissions.csv"'
        },
    )


@gym_app.get("/admin/id-proof/{reference}", dependencies=[Depends(require_admin)])
def admin_id_proof(reference: str):
    record = next(
        (r for r in storage.load_submissions() if r.get("reference") == reference), None
    )
    if record is None or not record.get("id_proof_path"):
        raise HTTPException(404, "No ID proof stored for that submission.")

    path = Path(record["id_proof_path"])
    if not path.exists():
        raise HTTPException(
            404,
            "The stored ID proof file is gone — on free hosting tiers the disk is "
            "wiped on redeploy. The copy attached to the notification email is the "
            "durable one.",
        )
    media_type = "application/pdf" if path.suffix.lower() == ".pdf" else "image/*"
    return Response(path.read_bytes(), media_type=media_type)


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@gym_app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    """Public liveness check — deliberately free of contact details or counts.

    HEAD is answered as well as GET, because this is what uptime pingers hit.
    On a host that sleeps when idle, the first ping of the day lands while the
    platform is still showing its own "waking up" page — far larger than the
    response cap some pingers enforce, which they report as a failure even
    though the wake-up worked. A HEAD request has no body, so it sidesteps
    that entirely.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "form_url": _form_url(request),
        "email_configured": settings.email_configured,
        "email_provider": settings.email_provider,
        "whatsapp_provider": settings.whatsapp_provider,
    }


@gym_app.get("/admin/diagnostics", dependencies=[Depends(require_admin)])
def diagnostics(request: Request):
    """The same check with the details only the office should see."""
    settings = get_settings()
    return {
        "status": "ok",
        "form_url": _form_url(request),
        "notify_email": settings.notify_email,
        "notify_whatsapp": f"+{settings.notify_whatsapp}",
        "email_configured": settings.email_configured,
        "email_provider": settings.email_provider,
        "email_sender": settings.email_sender,
        "smtp_host": f"{settings.smtp_host}:{settings.smtp_port}",
        "whatsapp_provider": settings.whatsapp_provider,
        "send_trainer_copy": settings.send_trainer_copy,
        "submissions_stored": sum(1 for _ in storage.iter_submissions()),
    }


@gym_app.post("/admin/test-notification", dependencies=[Depends(require_admin)])
def test_notification(request: Request):
    """Send a dummy registration through the real channels.

    The fastest way to answer "did I set the SMTP password correctly?" without
    waiting for a real trainer to walk up to the gym door.
    """
    from datetime import datetime

    from gymform.models import IST

    sample = Submission(
        reference="SB-PT-TEST-00000",
        submitted_at=datetime.now(IST),
        trainer_name="Test Trainer",
        mobile="9876543210",
        whatsapp="9876543210",
        email=get_settings().notify_email,
        id_type="Aadhar Card",
        id_number="123456789012",
        address="Test address, Wadgaon Sheri, Pune",
        clients=[ClientEntry("Test Client", "A-101", ["mon", "wed", "fri"], "07:00", "08:00")],
        acknowledgements={rule.key: True for rule in RULES},
        declaration_signature="Test Trainer",
        declaration_place="Pune",
    )
    results = notify.notify_all(get_settings(), sample)
    return {"results": [r.as_dict() for r in results]}
