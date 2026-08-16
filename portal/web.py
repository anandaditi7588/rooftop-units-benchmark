"""Front door for the society's online forms.

One printed QR code, two forms. Scanning lands here; a resident taps the one
they want and the form opens. Both forms are complete sub-applications mounted
underneath, so each keeps its own pages, its own storage and its own office
review — this only routes.

Back-compatibility matters more than tidiness here: notices are already printed
and emails already sent carrying links that pointed at the trainer form when it
sat at the site root. Those paths are redirected rather than broken.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from gymform.rules import SOCIETY_ADDRESS, SOCIETY_NAME
from gymform.settings import STATIC_DIR, get_settings
from gymform.web import gym_app
from hallform.web import hall_app

logger = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

# Where each form lives under this app.
TRAINER_PATH = "/trainer"
HALL_PATH = "/hall"

portal_app = FastAPI(
    title=f"{SOCIETY_NAME} — Online Forms",
    docs_url=None,
    redoc_url=None,
)
portal_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="portal-static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def _base_path(request: Request) -> str:
    return (request.scope.get("root_path") or "").rstrip("/")


def _public_url(request: Request) -> str:
    """Absolute URL of this chooser — what the printed QR code encodes."""
    configured = get_settings().public_url
    root = configured.rstrip("/") if configured else str(request.base_url).rstrip("/")
    return root + _base_path(request)


def _context(request: Request, **extra) -> dict:
    context = {
        "base": _base_path(request),
        "society_name": SOCIETY_NAME,
        "society_address": SOCIETY_ADDRESS,
        "trainer_path": TRAINER_PATH,
        "hall_path": HALL_PATH,
    }
    context.update(extra)
    return context


# ---------------------------------------------------------------------------
# The chooser
# ---------------------------------------------------------------------------

@portal_app.api_route("/", methods=["GET", "HEAD"], response_class=HTMLResponse)
def choose(request: Request):
    return templates.TemplateResponse(request, "choose.html", _context(request))


@portal_app.get("/poster", response_class=HTMLResponse)
def poster(request: Request):
    """A printable A4 notice with the one QR code, for the gate or the lobby."""
    return templates.TemplateResponse(
        request, "poster.html", _context(request, form_url=_public_url(request))
    )


@portal_app.get("/qr.png")
def qr_png(request: Request, size: int = 10):
    """PNG QR code pointing at the chooser. Print it once, it covers both forms."""
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
    qr.add_data(_public_url(request))
    qr.make(fit=True)

    buffer = io.BytesIO()
    qr.make_image(fill_color="#16233a", back_color="white").save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={"Content-Disposition": 'inline; filename="silicon-bay-forms-qr.png"'},
    )


@portal_app.api_route("/health", methods=["GET", "HEAD"])
def health(request: Request):
    """Public liveness check, and what the uptime pinger hits.

    HEAD is answered as well as GET: on a host that sleeps when idle, the first
    ping of the day lands while the platform is still serving its own "waking
    up" page, which is far larger than the response cap some pingers enforce.
    A HEAD request has no body, so it sidesteps that entirely.
    """
    settings = get_settings()
    return {
        "status": "ok",
        "url": _public_url(request),
        "forms": {
            "trainer": _public_url(request) + TRAINER_PATH,
            "hall": _public_url(request) + HALL_PATH,
        },
        "email_provider": settings.email_provider,
        "whatsapp_provider": settings.whatsapp_provider,
        "sheets_enabled": settings.sheets_enabled,
    }


# ---------------------------------------------------------------------------
# Links printed or emailed before the trainer form moved off the site root
# ---------------------------------------------------------------------------

@portal_app.get("/gym", include_in_schema=False)
@portal_app.get("/rules", include_in_schema=False)
def _legacy_root(request: Request):
    target = TRAINER_PATH + ("/rules" if request.url.path.endswith("/rules") else "")
    return RedirectResponse(f"{_base_path(request)}{target}", status_code=308)


@portal_app.get("/submitted/{reference}", include_in_schema=False)
def _legacy_submitted(request: Request, reference: str):
    return RedirectResponse(
        f"{_base_path(request)}{TRAINER_PATH}/submitted/{reference}", status_code=308
    )


@portal_app.get("/decide/{reference}", include_in_schema=False)
def _legacy_decide(request: Request, reference: str):
    query = f"?{request.url.query}" if request.url.query else ""
    return RedirectResponse(
        f"{_base_path(request)}{TRAINER_PATH}/decide/{reference}{query}", status_code=308
    )


@portal_app.get("/admin", include_in_schema=False)
def _legacy_admin(request: Request):
    return RedirectResponse(f"{_base_path(request)}{TRAINER_PATH}/admin", status_code=308)


# Mounted last: a mount matches every path beneath it, so the redirects above
# would be unreachable if these came first.
portal_app.mount(TRAINER_PATH, gym_app, name="trainer-form")
portal_app.mount(HALL_PATH, hall_app, name="hall-form")
