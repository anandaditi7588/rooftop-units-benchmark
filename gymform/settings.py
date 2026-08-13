"""Environment-driven settings for the trainer registration form.

Nothing here is required for the form to work: with zero configuration the
form still renders, validates and stores submissions on disk. Configuration
only adds delivery channels (email, WhatsApp) on top of that, so a missing
SMTP password can never cost the society a registration.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR: Path = Path(__file__).resolve().parent.parent

# Submissions land here. Under /output, which this repo already keeps out of
# git — trainer phone numbers and ID details must never be committed.
DATA_DIR: Path = BASE_DIR / "output" / "gym_form"
SUBMISSIONS_JSONL: Path = DATA_DIR / "submissions.jsonl"
SUBMISSIONS_CSV: Path = DATA_DIR / "submissions.csv"
ID_PROOF_DIR: Path = DATA_DIR / "id_proofs"

TEMPLATES_DIR: Path = Path(__file__).resolve().parent / "templates"
STATIC_DIR: Path = Path(__file__).resolve().parent / "static"

# Where the form is mounted on the parent application.
MOUNT_PATH = "/gym"


def _env(name: str, default: str = "") -> str:
    return (os.getenv(name) or default).strip()


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    try:
        return int(raw) if raw else default
    except ValueError:
        return default


@dataclass(frozen=True)
class GymFormSettings:
    """A snapshot of the environment, built fresh by :func:`get_settings`.

    Every field uses ``default_factory`` rather than a plain default so the
    environment is read when the snapshot is *created*, not when this module
    is first imported — changing a variable on the host therefore takes
    effect without a code edit, and tests can patch ``os.environ`` freely.
    """

    # --- Who gets told about a new submission ---------------------------
    notify_email: str = field(
        default_factory=lambda: _env("GYM_NOTIFY_EMAIL", "dulange111@gmail.com"))
    notify_whatsapp: str = field(
        default_factory=lambda: _env("GYM_NOTIFY_WHATSAPP", "917588610829"))
    # Send the trainer their own copy of what they submitted.
    send_trainer_copy: bool = field(
        default_factory=lambda: _env("GYM_SEND_TRAINER_COPY", "1") != "0")

    # --- Outgoing email (SMTP) ------------------------------------------
    smtp_host: str = field(
        default_factory=lambda: _env("GYM_SMTP_HOST", "smtp.gmail.com"))
    smtp_port: int = field(default_factory=lambda: _env_int("GYM_SMTP_PORT", 587))
    smtp_user: str = field(default_factory=lambda: _env("GYM_SMTP_USER"))
    smtp_password: str = field(default_factory=lambda: _env("GYM_SMTP_PASSWORD"))
    smtp_from: str = field(default_factory=lambda: _env("GYM_SMTP_FROM"))
    smtp_use_ssl: bool = field(
        default_factory=lambda: _env("GYM_SMTP_USE_SSL", "0") == "1")
    smtp_timeout: int = field(default_factory=lambda: _env_int("GYM_SMTP_TIMEOUT", 20))

    # --- WhatsApp: Twilio -----------------------------------------------
    twilio_account_sid: str = field(
        default_factory=lambda: _env("GYM_TWILIO_ACCOUNT_SID"))
    twilio_auth_token: str = field(
        default_factory=lambda: _env("GYM_TWILIO_AUTH_TOKEN"))
    # Twilio's shared sandbox sender, overridable with your own approved number.
    twilio_from: str = field(
        default_factory=lambda: _env("GYM_TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886"))

    # --- WhatsApp: Meta (WhatsApp Cloud API) -----------------------------
    meta_phone_number_id: str = field(
        default_factory=lambda: _env("GYM_META_PHONE_NUMBER_ID"))
    meta_access_token: str = field(
        default_factory=lambda: _env("GYM_META_ACCESS_TOKEN"))
    meta_api_version: str = field(
        default_factory=lambda: _env("GYM_META_API_VERSION", "v21.0"))

    # --- Misc -------------------------------------------------------------
    # Absolute URL the QR code should point at, e.g.
    # https://silicon-bay.onrender.com/gym. Falls back to RENDER_EXTERNAL_URL,
    # which Render sets automatically on every web service — so a Render
    # deployment encodes the right URL with nothing to configure. Failing both,
    # it is derived from the incoming request.
    public_url: str = field(
        default_factory=lambda: _env("GYM_PUBLIC_URL") or _env("RENDER_EXTERNAL_URL"))
    request_timeout: int = field(
        default_factory=lambda: _env_int("GYM_HTTP_TIMEOUT", 15))
    # Minimum seconds between two submissions from the same IP address.
    submit_cooldown_seconds: int = field(
        default_factory=lambda: _env_int("GYM_SUBMIT_COOLDOWN", 20))
    max_id_proof_bytes: int = field(
        default_factory=lambda: _env_int("GYM_MAX_ID_PROOF_BYTES", 5 * 1024 * 1024))

    @property
    def email_configured(self) -> bool:
        return bool(self.smtp_host and self.smtp_user and self.smtp_password)

    @property
    def email_sender(self) -> str:
        return self.smtp_from or self.smtp_user

    @property
    def whatsapp_provider(self) -> str:
        """Which WhatsApp backend will be used: 'twilio', 'meta' or 'link'.

        'link' means no API credentials are configured, so the office gets a
        one-tap wa.me link in the notification email instead of an automatic
        WhatsApp message.
        """
        if self.twilio_account_sid and self.twilio_auth_token:
            return "twilio"
        if self.meta_phone_number_id and self.meta_access_token:
            return "meta"
        return "link"


def get_settings() -> GymFormSettings:
    """Build a settings snapshot from the current environment."""
    return GymFormSettings()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    ID_PROOF_DIR.mkdir(parents=True, exist_ok=True)
