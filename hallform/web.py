"""HTTP layer for the amenity (hall / lawn) booking form.

Mounted at ``/hall`` beside the trainer form — see ``portal/web.py`` for the
page that sends a resident to one or the other after they scan the QR code.

The shape deliberately mirrors ``gymform/web.py``: store first, notify second,
never block the event loop, and let the office decide from a signed link in the
email. The one thing that is genuinely new is the clash check, which runs
against stored bookings at submit time so a second resident is told who already
holds the slot instead of finding out on the day.
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

from gymform import payments, sheets, tokens
from gymform.admin_auth import require_admin
from gymform.notify import DeliveryResult
from gymform.rules import SOCIETY_ADDRESS, SOCIETY_NAME
# Imported as a module so the data paths are looked up at call time, which is
# what lets tests redirect them.
from gymform import settings as gym_settings
from gymform.settings import STATIC_DIR, get_settings
from hallform import notify, storage
from hallform.models import (
    BOOKING_HOURS_TEXT,
    describe_clash,
    find_clash,
    format_date,
    format_time,
    parse_booking,
    to_minutes,
)
from hallform.rules import (
    BOOKING_DAY_END,
    BOOKING_DAY_START,
    DECLARATION,
    OCCASIONS,
    RULES,
    SECURITY_DEPOSIT_INR,
    VENUES,
    VENUES_BY_KEY,
)

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

hall_app = FastAPI(
    title=f"{SOCIETY_NAME} — Hall & Amenity Booking",
    docs_url=None,
    redoc_url=None,
)
# Deliberately the trainer form's stylesheet directory: one set of styles for
# both forms means a change to the society's look lands in both places, and a
# resident who has used one form recognises the other.
hall_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="hall-static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

_recent_deliveries: dict[str, list[dict]] = {}
_recent_lock = threading.Lock()
_MAX_RECENT = 50

_last_submit_at: dict[str, float] = {}

# Two residents submitting at the same second must not both clear the clash
# check. Reading the stored bookings and appending the new one happens under
# this lock, which makes the check-then-write a single step.
_booking_lock = threading.Lock()


def _remember_delivery(reference: str, results: list[DeliveryResult]) -> None:
    with _recent_lock:
        _recent_deliveries[reference] = [r.as_dict() for r in results]
        while len(_recent_deliveries) > _MAX_RECENT:
            _recent_deliveries.pop(next(iter(_recent_deliveries)))


def _base_path(request: Request) -> str:
    return (request.scope.get("root_path") or "").rstrip("/")


def _form_url(request: Request) -> str:
    """Absolute, public URL of this form — what signed email links must use."""
    configured = get_settings().public_url
    root = configured.rstrip("/") if configured else str(request.base_url).rstrip("/")
    return root + _base_path(request)


def _template_context(request: Request, **extra) -> dict:
    context = {
        "base": _base_path(request),
        "society_name": SOCIETY_NAME,
        "society_address": SOCIETY_ADDRESS,
        "document_title": notify.DOCUMENT_TITLE,
        "rules": RULES,
        "venues": VENUES,
        "occasions": OCCASIONS,
        "declaration": DECLARATION,
        "security_deposit_inr": SECURITY_DEPOSIT_INR,
        "booking_day_start": BOOKING_DAY_START,
        "booking_day_end": BOOKING_DAY_END,
        "booking_hours_text": BOOKING_HOURS_TEXT,
        "format_time": format_time,
        "format_date": format_date,
    }
    context.update(extra)
    return context


def _empty_form_values() -> dict:
    return {
        "resident_name": "",
        "flat_number": "",
        "mobile": "",
        "whatsapp": "",
        "email": "",
        "venue_key": "",
        "slot_key": "",
        "event_date": "",
        "start_time": "",
        "occasion": "",
        "occasion_detail": "",
        "expected_persons": "",
        "acknowledgements": {rule.key: False for rule in RULES},
        "declaration_signature": "",
        "declaration_place": "",
        "declaration_agree": False,
    }


def _sync_sheet(reference: str) -> None:
    """Mirror one booking into the society's Google Sheet.

    Best effort by design: the booking is already on disk, and a spreadsheet
    that is briefly out of date is a far smaller problem than a resident seeing
    an error page for a booking that was in fact accepted.
    """
    settings = get_settings()
    if not settings.sheets_enabled:
        return
    record = storage.find_booking(reference)
    if record is None:
        return
    ok, detail = sheets.push_row(
        settings, storage.SHEET_NAME, storage.sheet_row(record)
    )
    if not ok:
        logger.warning("Hall form: sheet sync failed for %s — %s", reference, detail)


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

@hall_app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def form_page(request: Request):
    return templates.TemplateResponse(
        request, "hall_form.html",
        _template_context(request, values=_empty_form_values(), errors={}),
    )


@hall_app.get("/rules", response_class=HTMLResponse)
def rules_page(request: Request):
    return templates.TemplateResponse(
        request, "hall_rules.html", _template_context(request)
    )


@hall_app.get("/calendar", response_class=HTMLResponse)
def calendar_page(request: Request):
    """What is already taken — public, so residents can pick a free date.

    Names and flats only: this is the same information the society would put on
    a notice board, and it is precisely what stops a resident planning around a
    date that is already gone.
    """
    from datetime import datetime

    from hallform.models import IST

    today = datetime.now(IST).date().isoformat()
    upcoming = [
        record for record in storage.load_bookings(newest_first=False)
        if record.get("event_date", "") >= today
        and record.get("status", "pending") in ("pending", "approved")
    ]
    upcoming.sort(key=lambda r: (r.get("event_date", ""), r.get("start_time", "")))
    return templates.TemplateResponse(
        request, "hall_calendar.html",
        _template_context(request, bookings=upcoming),
    )


@hall_app.get("/availability")
def availability(venue_key: str = "", event_date: str = "", slot_key: str = "",
                 start_time: str = ""):
    """Is this venue free then? Answered while the resident is still typing.

    The submit path checks this again under a lock — this endpoint is a
    courtesy, not the gate, because anything a browser is told can be stale by
    the time the form is posted.
    """
    venue = VENUES_BY_KEY.get(venue_key)
    slot = venue.slot(slot_key) if venue else None
    if venue is None or slot is None or not event_date:
        return {"checked": False}

    from hallform.models import DAY_END_MIN, DAY_START_MIN

    if slot.key == "day":
        start, end = DAY_START_MIN, DAY_END_MIN
    else:
        start = to_minutes(start_time)
        if start is None:
            return {"checked": False}
        end = start + slot.hours * 60

    clash = find_clash(
        storage.load_bookings(newest_first=False), venue_key, event_date, start, end
    )
    if clash is None:
        return {"checked": True, "available": True}
    return {"checked": True, "available": False, "message": describe_clash(clash)}


@hall_app.post("/submit")
async def submit(request: Request):
    settings = get_settings()
    form = await request.form()

    if (form.get("website") or "").strip():
        logger.info("Hall form: dropped a submission that filled the honeypot field.")
        raise HTTPException(400, "Submission rejected.")

    client_ip = (request.client.host if request.client else "") or "unknown"
    now = time.monotonic()
    previous = _last_submit_at.get(client_ip)
    if previous is not None and now - previous < settings.submit_cooldown_seconds:
        raise HTTPException(
            429,
            "That looks like a duplicate booking — please wait a few seconds "
            "before submitting again.",
        )

    values = {key: form.get(key) for key in form.keys()}

    # Validate and store as one step. Two residents can hit submit in the same
    # second for the same evening; without the lock both would read the same
    # empty slot, both would pass the clash check, and the society would have
    # promised one hall to two families.
    def _validate_and_store():
        with _booking_lock:
            existing = storage.load_bookings(newest_first=False)
            booking, errors, raw = parse_booking(values, existing)
            if booking is None:
                return None, errors, raw
            storage.save_booking(booking)
            return booking, {}, raw

    booking, errors, raw = await run_in_threadpool(_validate_and_store)
    if booking is None:
        return templates.TemplateResponse(
            request, "hall_form.html",
            _template_context(request, values=raw, errors=errors),
            status_code=409 if "clash" in errors else 400,
        )

    _last_submit_at[client_ip] = now

    results = await run_in_threadpool(
        notify.notify_all, settings, booking, _form_url(request)
    )
    _remember_delivery(booking.reference, results)
    await run_in_threadpool(_sync_sheet, booking.reference)

    return RedirectResponse(
        f"{_base_path(request)}/submitted/{booking.reference}", status_code=303
    )


def _payment_context(settings, record: dict) -> dict:
    if not settings.payments_enabled:
        return {"payment_enabled": False}
    return {
        "payment_enabled": True,
        "upi_id": settings.upi_id,
        "upi_payee_name": settings.upi_payee_name,
        "upi_uri": payments.build_upi_uri(
            upi_id=settings.upi_id,
            payee_name=settings.upi_payee_name,
            amount=record.get("charge_inr", 0),
            reference=record.get("reference", ""),
        ),
    }


@hall_app.get("/submitted/{reference}", response_class=HTMLResponse)
def submitted_page(request: Request, reference: str, paid: int = 0):
    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    with _recent_lock:
        deliveries = _recent_deliveries.get(reference, [])

    whatsapp_link = next(
        (d["link"] for d in deliveries if d["channel"] == "whatsapp" and d["link"]), ""
    )
    settings = get_settings()
    return templates.TemplateResponse(
        request, "hall_submitted.html",
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


# ---------------------------------------------------------------------------
# Decisions from the office email
# ---------------------------------------------------------------------------

@hall_app.get("/decide/{reference}", response_class=HTMLResponse)
def decide_page(request: Request, reference: str, d: str = "", t: str = ""):
    """Confirmation page behind an Approve/Reject link in the office email.

    Deliberately does not act on the GET — mail providers and security scanners
    follow links before a person sees them, so a link that decided on click
    would confirm bookings by itself.
    """
    if not tokens.verify_token(reference, d, t):
        raise HTTPException(403, "This approval link is not valid or has expired.")

    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    return templates.TemplateResponse(
        request, "hall_decide.html",
        _template_context(request, record=record, decision=d, token=t),
    )


@hall_app.post("/decide/{reference}")
async def decide_submit(request: Request, reference: str):
    form = await request.form()
    decision = str(form.get("decision") or "").strip()
    token = str(form.get("token") or "").strip()

    if not tokens.verify_token(reference, decision, token):
        raise HTTPException(403, "This approval link is not valid or has expired.")
    if decision not in storage.DECISIONS:
        raise HTTPException(400, f"Decision must be one of {storage.DECISIONS}.")

    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    note = " ".join(str(form.get("note") or "").split())[:300]
    await run_in_threadpool(storage.record_decision, reference, decision, note)
    await run_in_threadpool(
        notify.notify_decision, get_settings(), record, decision, note
    )
    await run_in_threadpool(_sync_sheet, reference)
    return RedirectResponse(
        f"{_base_path(request)}/decide/{reference}/done?d={decision}", status_code=303
    )


@hall_app.get("/decide/{reference}/done", response_class=HTMLResponse)
def decide_done(request: Request, reference: str, d: str = ""):
    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")
    return templates.TemplateResponse(
        request, "hall_decided.html",
        _template_context(request, record=record, decision=d),
    )


# ---------------------------------------------------------------------------
# Payment (UPI, reported not verified)
# ---------------------------------------------------------------------------

@hall_app.get("/upi-qr/{reference}.png")
def upi_qr(reference: str, size: int = 8):
    settings = get_settings()
    if not settings.payments_enabled:
        raise HTTPException(404, "Payment collection is not switched on.")

    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise HTTPException(501, "QR generation needs the 'qrcode' package.") from exc

    uri = payments.build_upi_uri(
        upi_id=settings.upi_id,
        payee_name=settings.upi_payee_name,
        amount=record.get("charge_inr", 0),
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


@hall_app.post("/payment/{reference}")
async def report_payment(request: Request, reference: str):
    """Record the UPI reference a resident says they paid with.

    The rulebook asks for cash the day before; this is the optional online
    alternative. Either way nothing here proves money moved — UPI gives the
    server no callback — so every surface says "reported", not "paid".
    """
    settings = get_settings()
    if not settings.payments_enabled:
        raise HTTPException(404, "Payment collection is not switched on.")

    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    form = await request.form()
    upi_reference = payments.clean_upi_reference(str(form.get("upi_reference") or ""))
    base = _base_path(request)
    if upi_reference is None:
        return RedirectResponse(
            f"{base}/submitted/{reference}"
            "?payment_error=Enter+the+UPI+reference+number+shown+in+your+payment+app.",
            status_code=303,
        )

    amount = record.get("charge_inr", 0)
    await run_in_threadpool(storage.record_payment, reference, upi_reference, amount)
    await run_in_threadpool(notify.notify_payment, settings, record, upi_reference, amount)
    await run_in_threadpool(_sync_sheet, reference)

    return RedirectResponse(f"{base}/submitted/{reference}?paid=1", status_code=303)


# ---------------------------------------------------------------------------
# QR code
# ---------------------------------------------------------------------------

@hall_app.get("/qr.png")
def qr_png(request: Request, size: int = 10):
    """PNG QR code pointing straight at the booking form."""
    try:
        import qrcode
    except ImportError as exc:  # pragma: no cover - depends on the install
        raise HTTPException(
            501,
            "QR generation needs the 'qrcode' package. Install it with: "
            "pip install 'qrcode[pil]'",
        ) from exc

    qr = qrcode.QRCode(
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=max(4, min(int(size), 20)),
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
        headers={
            "Content-Disposition": 'inline; filename="silicon-bay-hall-booking-qr.png"'
        },
    )


# ---------------------------------------------------------------------------
# Office-only pages
# ---------------------------------------------------------------------------

@hall_app.get("/admin", response_class=HTMLResponse, dependencies=[Depends(require_admin)])
def admin_page(request: Request, show_removed: int = 0):
    records = storage.load_bookings(include_removed=bool(show_removed))
    return templates.TemplateResponse(
        request, "hall_admin.html",
        _template_context(
            request,
            records=records,
            settings=get_settings(),
            show_removed=bool(show_removed),
            removed_count=sum(
                1 for booking in storage.load_bookings(include_removed=True)
                if booking.get("status") == "removed"
            ),
        ),
    )


@hall_app.post("/admin/delete/{reference}", dependencies=[Depends(require_admin)])
async def admin_delete(request: Request, reference: str):
    """Strike a wrong booking off the list. Office only — never the resident.

    A resident who has booked the wrong date must ask the office; there is no
    route on the public side that reaches this, by design. Someone who could
    delete their own booking could also delete somebody else's if they ever got
    hold of a reference, and the reference is printed on a confirmation page.

    The submission itself is not erased — see storage.record_deletion. What
    changes is that the slot is released, the booking leaves the public calendar
    and the office's list, and the Google Sheet row is marked removed with the
    reason.
    """
    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    form = await request.form()
    reason = " ".join(str(form.get("reason") or "").split())[:300]

    await run_in_threadpool(storage.record_deletion, reference, reason, True)
    await run_in_threadpool(_sync_sheet, reference)
    return RedirectResponse(f"{_base_path(request)}/admin", status_code=303)


@hall_app.post("/admin/restore/{reference}", dependencies=[Depends(require_admin)])
async def admin_restore(request: Request, reference: str):
    """Undo a removal — including one made by mistake.

    Restoring puts the booking back exactly as the resident submitted it, so a
    slip of the finger on Remove costs nothing. It can fail: if another resident
    has taken the slot in the meantime, they now hold it, and the office is told
    so rather than the society quietly promising the same hall twice.
    """
    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    start = to_minutes(record.get("start_time", ""))
    end = to_minutes(record.get("end_time", ""))
    if start is not None and end is not None:
        clash = find_clash(
            [b for b in storage.load_bookings(newest_first=False)
             if b.get("reference") != reference],
            record.get("venue_key", ""), record.get("event_date", ""), start, end,
        )
        if clash is not None:
            raise HTTPException(409, describe_clash(clash))

    await run_in_threadpool(storage.record_deletion, reference, "", False)
    await run_in_threadpool(_sync_sheet, reference)
    return RedirectResponse(
        f"{_base_path(request)}/admin#{reference}", status_code=303
    )


@hall_app.post("/admin/decision/{reference}", dependencies=[Depends(require_admin)])
async def admin_decision(request: Request, reference: str):
    record = storage.find_booking(reference)
    if record is None:
        raise HTTPException(404, "Unknown booking reference.")

    form = await request.form()
    decision = str(form.get("decision") or "").strip()
    if decision not in storage.DECISIONS:
        raise HTTPException(400, f"Decision must be one of {storage.DECISIONS}.")
    note = " ".join(str(form.get("note") or "").split())[:300]

    await run_in_threadpool(storage.record_decision, reference, decision, note)
    await run_in_threadpool(
        notify.notify_decision, get_settings(), record, decision, note
    )
    await run_in_threadpool(_sync_sheet, reference)
    return RedirectResponse(f"{_base_path(request)}/admin#{reference}", status_code=303)


@hall_app.get("/admin/bookings.csv", dependencies=[Depends(require_admin)])
def admin_csv():
    gym_settings.ensure_dirs()
    csv_path = gym_settings.BOOKINGS_CSV
    if not csv_path.exists():
        raise HTTPException(404, "No bookings yet.")
    return Response(
        csv_path.read_bytes(),
        media_type="text/csv",
        headers={
            "Content-Disposition": 'attachment; filename="silicon-bay-hall-bookings.csv"'
        },
    )


@hall_app.post("/admin/sheet-sync", dependencies=[Depends(require_admin)])
def admin_sheet_sync():
    """Push every stored booking into the Google Sheet.

    Useful once, after the sheet is first connected, so the history that
    predates it is not lost.
    """
    settings = get_settings()
    if not settings.sheets_enabled:
        raise HTTPException(404, "No Google Sheet is configured.")
    sent, failed = 0, []
    for record in storage.load_bookings(newest_first=False, include_removed=True):
        ok, detail = sheets.push_row(
            settings, storage.SHEET_NAME, storage.sheet_row(record)
        )
        if ok:
            sent += 1
        else:
            failed.append({"reference": record.get("reference"), "detail": detail})
    return {"synced": sent, "failed": failed}


# ---------------------------------------------------------------------------
# Diagnostics
# ---------------------------------------------------------------------------

@hall_app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    settings = get_settings()
    return {
        "status": "ok",
        "form_url": _form_url(request),
        "email_provider": settings.email_provider,
        "whatsapp_provider": settings.whatsapp_provider,
        "sheets_enabled": settings.sheets_enabled,
    }
