"""Notification channels: email to the society office, WhatsApp when available.

Design rule: **notification never fails a submission.** Every sender returns a
:class:`DeliveryResult` instead of raising, the web layer records those results
alongside the stored submission, and the trainer always sees a success page —
the registration is already safely on disk by then.

WhatsApp has three modes, chosen automatically by what is configured:
  * ``twilio`` — Twilio's WhatsApp API (easiest to get running; the sandbox
    works in minutes).
  * ``meta``   — WhatsApp Cloud API, direct from Meta.
  * ``link``   — nothing configured: the office instead receives a one-tap
    ``wa.me`` link in the notification email, and the same link is shown on
    the success page. No API account needed, but the tap is manual.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import requests

from gymform.models import Submission, format_time
from gymform.rules import (
    DOCUMENT_TITLE,
    OPERATING_WINDOWS,
    RULES,
    RULES_BY_KEY,
    SOCIETY_ADDRESS,
    SOCIETY_NAME,
)
from gymform.settings import GymFormSettings

logger = logging.getLogger(__name__)


@dataclass
class DeliveryResult:
    channel: str          # "email" | "email_trainer" | "whatsapp"
    ok: bool
    detail: str = ""
    # For the 'link' WhatsApp mode: the wa.me URL a human can tap.
    link: str = ""

    def as_dict(self) -> dict:
        return {
            "channel": self.channel,
            "ok": self.ok,
            "detail": self.detail,
            "link": self.link,
        }


# ---------------------------------------------------------------------------
# Message composition
# ---------------------------------------------------------------------------

def _client_lines(submission: Submission) -> list[str]:
    return [
        f"{i}. {c.name} — Flat {c.flat_number} — {c.slot_label}"
        for i, c in enumerate(submission.clients, start=1)
    ]


def build_summary_text(submission: Submission) -> str:
    """Plain-text summary, used for the email body and as the WhatsApp message."""
    lines = [
        f"New personal trainer registration — {SOCIETY_NAME}",
        "",
        f"Reference : {submission.reference}",
        f"Submitted : {submission.submitted_at_label}",
        "",
        "TRAINER",
        f"Name      : {submission.trainer_name}",
        f"Mobile    : +91 {submission.mobile}",
        f"WhatsApp  : +91 {submission.whatsapp}",
        f"Email     : {submission.email}",
        f"ID proof  : {submission.id_type} — {submission.id_number}"
        + (f" (file attached: {submission.id_proof_filename})"
           if submission.id_proof_filename else " (no file uploaded)"),
        f"Address   : {submission.address}",
    ]
    if submission.emergency_contact_name or submission.emergency_contact_mobile:
        lines.append(
            f"Emergency : {submission.emergency_contact_name} "
            f"+91 {submission.emergency_contact_mobile}".strip()
        )
    lines += [
        "",
        f"CLIENTS ({submission.client_count})",
        *_client_lines(submission),
        "",
        f"MONTHLY AMENITY FEE: INR {submission.monthly_fee:,}",
    ]
    if submission.has_outside_hours_slot:
        lines += [
            "",
            "NOTE: one or more slots fall outside gym hours "
            f"({_operating_hours_text()}). The trainer has confirmed the office "
            "and security team were informed.",
        ]
        if submission.outside_hours_note:
            lines.append(f"Trainer's note: {submission.outside_hours_note}")
    if submission.committee_approval_reference:
        lines += [
            "",
            "Committee approval quoted for more than four concurrent clients: "
            f"{submission.committee_approval_reference}",
        ]
    lines += [
        "",
        f"All {len(RULES)} rules of the '{DOCUMENT_TITLE}' were acknowledged.",
        f"Signed: {submission.declaration_signature}"
        + (f", {submission.declaration_place}" if submission.declaration_place else ""),
    ]
    return "\n".join(lines)


def _operating_hours_text() -> str:
    return " and ".join(
        f"{format_time(w.start)}–{format_time(w.end)}" for w in OPERATING_WINDOWS
    )


def build_whatsapp_text(submission: Submission) -> str:
    """A shorter message — WhatsApp is read on a phone, at a glance.

    Deliberately narrower than the email: it carries no ID *number* and no
    home address. A WhatsApp notification travels through a third-party
    provider and lands in a chat that gets forwarded, screenshotted and
    backed up, so it says who registered and what they owe, and leaves the
    identity documents to the email and the office review page.
    """
    clients = "\n".join(
        f"• {c.name} (Flat {c.flat_number}) — {c.slot_label}" for c in submission.clients
    )
    parts = [
        f"*New gym trainer registration — {SOCIETY_NAME}*",
        f"Ref: {submission.reference}",
        "",
        f"*{submission.trainer_name}*",
        f"📞 +91 {submission.mobile}",
        f"🆔 {submission.id_type} submitted"
        + (" with a copy of the document" if submission.id_proof_filename else ""),
        "",
        f"*Clients ({submission.client_count})*",
        clients,
        "",
        f"*Monthly amenity fee:* ₹{submission.monthly_fee:,}",
    ]
    if submission.has_outside_hours_slot:
        parts.append("⚠️ Has a slot outside gym hours (office & security informed).")
    if submission.committee_approval_reference:
        parts.append(f"⚠️ Committee approval quoted: {submission.committee_approval_reference}")
    parts.append(f"\nAll {len(RULES)} rules acknowledged on {submission.submitted_at_label}.")
    parts.append("Full details, ID proof and address are in the office email.")
    return "\n".join(parts)


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def build_summary_html(submission: Submission, whatsapp_link: str = "") -> str:
    """HTML email body — the office reads this on a phone most of the time."""
    def row(label: str, value: str) -> str:
        return (
            '<tr>'
            f'<td style="padding:6px 12px 6px 0;color:#5b6b80;white-space:nowrap;vertical-align:top">{_html_escape(label)}</td>'
            f'<td style="padding:6px 0;color:#16233a"><strong>{_html_escape(value)}</strong></td>'
            '</tr>'
        )

    client_rows = "".join(
        '<tr>'
        f'<td style="padding:8px 10px;border-bottom:1px solid #e6ecf3">{i}</td>'
        f'<td style="padding:8px 10px;border-bottom:1px solid #e6ecf3">{_html_escape(c.name)}</td>'
        f'<td style="padding:8px 10px;border-bottom:1px solid #e6ecf3">{_html_escape(c.flat_number)}</td>'
        f'<td style="padding:8px 10px;border-bottom:1px solid #e6ecf3">{_html_escape(c.slot_label)}</td>'
        '</tr>'
        for i, c in enumerate(submission.clients, start=1)
    )

    warnings = ""
    if submission.has_outside_hours_slot:
        note = (
            f'<br><em>Trainer\'s note: {_html_escape(submission.outside_hours_note)}</em>'
            if submission.outside_hours_note else ""
        )
        warnings += (
            '<p style="margin:12px 0;padding:12px 14px;background:#fff6e5;border-left:4px solid #e0a800;border-radius:6px">'
            f'<strong>Outside gym hours.</strong> One or more slots fall outside {_operating_hours_text()}. '
            f'The trainer confirmed the office and security team were informed.{note}</p>'
        )
    if submission.committee_approval_reference:
        warnings += (
            '<p style="margin:12px 0;padding:12px 14px;background:#fff6e5;border-left:4px solid #e0a800;border-radius:6px">'
            '<strong>More than four concurrent clients.</strong> Committee approval quoted: '
            f'{_html_escape(submission.committee_approval_reference)}</p>'
        )

    whatsapp_block = ""
    if whatsapp_link:
        whatsapp_block = (
            '<p style="margin:18px 0 0">'
            f'<a href="{_html_escape(whatsapp_link)}" '
            'style="display:inline-block;background:#25D366;color:#fff;text-decoration:none;'
            'padding:11px 18px;border-radius:8px;font-weight:600">Forward this to WhatsApp</a>'
            '</p>'
        )

    ack_list = "".join(
        f'<li style="margin:2px 0">Rule {rule.number} — {_html_escape(rule.title)}</li>'
        for rule in RULES
        if submission.acknowledgements.get(rule.key)
    )

    id_proof = f"{submission.id_type} — {submission.id_number}"
    if submission.id_proof_filename:
        id_proof += f" (file attached: {submission.id_proof_filename})"

    emergency = ""
    if submission.emergency_contact_name or submission.emergency_contact_mobile:
        emergency = row(
            "Emergency contact",
            f"{submission.emergency_contact_name} +91 {submission.emergency_contact_mobile}".strip(),
        )

    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f4f7fb;padding:20px">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 2px 14px rgba(20,40,70,.08)">
    <div style="background:#16233a;color:#fff;padding:22px 24px">
      <div style="font-size:13px;letter-spacing:.08em;text-transform:uppercase;opacity:.75">{_html_escape(SOCIETY_NAME)}</div>
      <div style="font-size:21px;font-weight:700;margin-top:4px">New personal trainer registration</div>
      <div style="font-size:13px;opacity:.8;margin-top:6px">Reference {_html_escape(submission.reference)} · {_html_escape(submission.submitted_at_label)}</div>
    </div>
    <div style="padding:22px 24px">
      {warnings}
      <h3 style="margin:0 0 8px;font-size:15px;color:#16233a">Trainer</h3>
      <table style="border-collapse:collapse;font-size:14px;width:100%">
        {row("Name", submission.trainer_name)}
        {row("Mobile", f"+91 {submission.mobile}")}
        {row("WhatsApp", f"+91 {submission.whatsapp}")}
        {row("Email", submission.email)}
        {row("ID proof", id_proof)}
        {row("Address", submission.address)}
        {emergency}
      </table>

      <h3 style="margin:22px 0 8px;font-size:15px;color:#16233a">Client list ({submission.client_count})</h3>
      <table style="border-collapse:collapse;font-size:14px;width:100%">
        <tr style="background:#eef3f9;text-align:left">
          <th style="padding:8px 10px">#</th>
          <th style="padding:8px 10px">Client name</th>
          <th style="padding:8px 10px">Flat</th>
          <th style="padding:8px 10px">Training slot</th>
        </tr>
        {client_rows}
      </table>

      <p style="margin:18px 0 0;padding:14px 16px;background:#eaf7f1;border-radius:10px;font-size:15px;color:#0f5c42">
        Monthly amenity usage fee: <strong>₹{submission.monthly_fee:,}</strong>
        <span style="color:#4d7a68"> (for {submission.client_count} client{"s" if submission.client_count != 1 else ""})</span>
      </p>

      <h3 style="margin:22px 0 8px;font-size:15px;color:#16233a">Rules acknowledged</h3>
      <ul style="margin:0;padding-left:20px;font-size:13px;color:#5b6b80">{ack_list}</ul>
      <p style="margin:16px 0 0;font-size:14px;color:#16233a">
        Signed by <strong>{_html_escape(submission.declaration_signature)}</strong>
        {(", " + _html_escape(submission.declaration_place)) if submission.declaration_place else ""}
      </p>
      {whatsapp_block}
    </div>
    <div style="padding:14px 24px;background:#f4f7fb;font-size:12px;color:#7b8a9c">
      {_html_escape(SOCIETY_NAME)}, {_html_escape(SOCIETY_ADDRESS)} · sent automatically by the gym registration form
    </div>
  </div>
</div>"""


def build_trainer_copy_text(submission: Submission) -> str:
    """Plain-text twin of the trainer's confirmation.

    It has to say the same thing as the HTML version: a text-only mail client
    must not show the trainer an email addressed to the office.
    """
    lines = [
        f"Dear {submission.trainer_name},",
        "",
        f"Your request to train at the {SOCIETY_NAME} gym has been received on "
        f"{submission.submitted_at_label} and forwarded to the society office.",
        "Please wait for the office to approve your registration before starting "
        "any session.",
        "",
        f"Reference: {submission.reference}",
        f"Clients listed: {submission.client_count}",
        f"Monthly amenity usage fee: INR {submission.monthly_fee:,}",
        "",
        "YOUR CLIENT LIST",
        *_client_lines(submission),
        "",
        "REMINDERS",
        f"- Carry your {submission.id_type} whenever you are inside the society premises.",
        "- Sign the security register with your in-time and out-time on every visit.",
        f"- Gym hours are {_operating_hours_text()}.",
        "- You may train at most four clients at a time.",
        "- Trainers may use the gym only, not any other society amenity.",
        "",
        f"You acknowledged all {len(RULES)} rules of the '{DOCUMENT_TITLE}'.",
        "",
        f"{SOCIETY_NAME}, {SOCIETY_ADDRESS}",
    ]
    return "\n".join(lines)


def build_trainer_copy_html(submission: Submission) -> str:
    """The trainer's own confirmation: what they agreed to, and what they owe."""
    rule_items = "".join(
        f'<li style="margin:6px 0"><strong>{rule.number}. {_html_escape(rule.title)}</strong><br>'
        f'<span style="color:#5b6b80">{_html_escape(RULES_BY_KEY[rule.key].acknowledgement)}</span></li>'
        for rule in RULES
    )
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f4f7fb;padding:20px">
  <div style="max-width:640px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden;box-shadow:0 2px 14px rgba(20,40,70,.08)">
    <div style="background:#16233a;color:#fff;padding:22px 24px">
      <div style="font-size:21px;font-weight:700">Registration received</div>
      <div style="font-size:13px;opacity:.8;margin-top:6px">Reference {_html_escape(submission.reference)}</div>
    </div>
    <div style="padding:22px 24px;font-size:14px;color:#16233a;line-height:1.6">
      <p>Dear {_html_escape(submission.trainer_name)},</p>
      <p>Your request to train at the {_html_escape(SOCIETY_NAME)} gym has been received on
         {_html_escape(submission.submitted_at_label)} and forwarded to the society office.
         <strong>Please wait for the office to approve your registration before starting any session.</strong></p>
      <p style="padding:14px 16px;background:#eaf7f1;border-radius:10px;color:#0f5c42;margin:16px 0">
        You listed <strong>{submission.client_count} client{"s" if submission.client_count != 1 else ""}</strong>,
        so your monthly amenity usage fee is <strong>₹{submission.monthly_fee:,}</strong>.
      </p>
      <p>Remember to carry your {_html_escape(submission.id_type)} whenever you are inside the
         society premises, and to sign the security register with your in-time and out-time on every visit.</p>
      <h3 style="margin:22px 0 8px;font-size:15px">You agreed to:</h3>
      <ol style="margin:0;padding-left:20px;font-size:13px">{rule_items}</ol>
    </div>
    <div style="padding:14px 24px;background:#f4f7fb;font-size:12px;color:#7b8a9c">
      {_html_escape(SOCIETY_NAME)}, {_html_escape(SOCIETY_ADDRESS)}
    </div>
  </div>
</div>"""


def whatsapp_link_for(number: str, message: str) -> str:
    """A wa.me deep link that opens WhatsApp with the message pre-filled."""
    digits = "".join(ch for ch in number if ch.isdigit())
    return f"https://wa.me/{digits}?text={quote(message)}"


# ---------------------------------------------------------------------------
# Email
# ---------------------------------------------------------------------------

def _send_email(
    settings: GymFormSettings,
    *,
    to: str,
    subject: str,
    text_body: str,
    html_body: str,
    attachment: Path | None = None,
    attachment_name: str = "",
) -> DeliveryResult:
    channel = "email" if to == settings.notify_email else "email_trainer"

    if not settings.email_configured:
        return DeliveryResult(
            channel, False,
            "SMTP is not configured (set GYM_SMTP_USER and GYM_SMTP_PASSWORD). "
            "The submission is saved on the server.",
        )

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = f"{SOCIETY_NAME} Gym Form <{settings.email_sender}>"
    message["To"] = to
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")

    if attachment and attachment.exists():
        try:
            data = attachment.read_bytes()
            suffix = attachment.suffix.lower()
            maintype, subtype = (
                ("application", "pdf") if suffix == ".pdf"
                else ("image", suffix.lstrip(".").replace("jpg", "jpeg") or "octet-stream")
            )
            message.add_attachment(
                data, maintype=maintype, subtype=subtype,
                filename=attachment_name or attachment.name,
            )
        except OSError as exc:  # A missing attachment must not lose the email.
            logger.warning("Gym form: could not attach ID proof: %s", exc)

    try:
        context = ssl.create_default_context()
        if settings.smtp_use_ssl:
            with smtplib.SMTP_SSL(
                settings.smtp_host, settings.smtp_port,
                timeout=settings.smtp_timeout, context=context,
            ) as server:
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
        else:
            with smtplib.SMTP(
                settings.smtp_host, settings.smtp_port, timeout=settings.smtp_timeout
            ) as server:
                server.starttls(context=context)
                server.login(settings.smtp_user, settings.smtp_password)
                server.send_message(message)
    except smtplib.SMTPAuthenticationError as exc:
        logger.error("Gym form: SMTP authentication failed: %s", exc)
        return DeliveryResult(
            channel, False,
            "SMTP rejected the login. For Gmail, use a 16-character App Password, "
            "not the account password.",
        )
    except Exception as exc:  # noqa: BLE001 - never let delivery break a submission
        logger.exception("Gym form: could not send email to %s", to)
        return DeliveryResult(channel, False, f"{type(exc).__name__}: {exc}")

    logger.info("Gym form: email sent to %s", to)
    return DeliveryResult(channel, True, f"Sent to {to}")


# ---------------------------------------------------------------------------
# WhatsApp
# ---------------------------------------------------------------------------

def _send_whatsapp(settings: GymFormSettings, message: str) -> DeliveryResult:
    number = "".join(ch for ch in settings.notify_whatsapp if ch.isdigit())
    link = whatsapp_link_for(number, message)

    if not number:
        return DeliveryResult("whatsapp", False, "No WhatsApp number configured.")

    provider = settings.whatsapp_provider
    if provider == "twilio":
        return _send_whatsapp_twilio(settings, number, message, link)
    if provider == "meta":
        return _send_whatsapp_meta(settings, number, message, link)
    if provider == "callmebot":
        return _send_whatsapp_callmebot(settings, number, message, link)

    return DeliveryResult(
        "whatsapp", False,
        "No WhatsApp API configured — a one-tap wa.me link was included in the "
        "notification email instead.",
        link=link,
    )


def _send_whatsapp_twilio(
    settings: GymFormSettings, number: str, message: str, link: str
) -> DeliveryResult:
    url = (
        "https://api.twilio.com/2010-04-01/Accounts/"
        f"{settings.twilio_account_sid}/Messages.json"
    )
    try:
        response = requests.post(
            url,
            data={
                "From": settings.twilio_from,
                "To": f"whatsapp:+{number}",
                "Body": message,
            },
            auth=(settings.twilio_account_sid, settings.twilio_auth_token),
            timeout=settings.request_timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Gym form: Twilio request failed: %s", exc)
        return DeliveryResult("whatsapp", False, f"Twilio request failed: {exc}", link=link)

    if response.status_code in (200, 201):
        logger.info("Gym form: WhatsApp sent via Twilio to +%s", number)
        return DeliveryResult("whatsapp", True, f"Sent via Twilio to +{number}", link=link)

    detail = response.text[:300]
    logger.warning("Gym form: Twilio returned %s: %s", response.status_code, detail)
    return DeliveryResult(
        "whatsapp", False, f"Twilio returned HTTP {response.status_code}: {detail}", link=link
    )


def _send_whatsapp_callmebot(
    settings: GymFormSettings, number: str, message: str, link: str
) -> DeliveryResult:
    """Free relay to one pre-authorised number — no account, no card.

    It answers with an HTML page rather than a status code for some failures,
    so a 200 that contains "ERROR" is treated as a failure rather than
    reported to the office as delivered.
    """
    try:
        response = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": f"+{number}", "text": message,
                    "apikey": settings.callmebot_apikey},
            timeout=settings.request_timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Gym form: CallMeBot request failed: %s", exc)
        return DeliveryResult("whatsapp", False, f"CallMeBot request failed: {exc}", link=link)

    body = response.text.strip()
    if response.ok and "ERROR" not in body.upper():
        logger.info("Gym form: WhatsApp sent via CallMeBot to +%s", number)
        return DeliveryResult("whatsapp", True, f"Sent via CallMeBot to +{number}", link=link)

    logger.warning("Gym form: CallMeBot returned %s: %s", response.status_code, body[:200])
    return DeliveryResult(
        "whatsapp", False,
        f"CallMeBot rejected the message (HTTP {response.status_code}): {body[:200]}. "
        "Check that the API key matches the recipient number, and that the number "
        "has messaged the bot to authorise it.",
        link=link,
    )


def _send_whatsapp_meta(
    settings: GymFormSettings, number: str, message: str, link: str
) -> DeliveryResult:
    url = (
        f"https://graph.facebook.com/{settings.meta_api_version}/"
        f"{settings.meta_phone_number_id}/messages"
    )
    try:
        response = requests.post(
            url,
            json={
                "messaging_product": "whatsapp",
                "to": number,
                "type": "text",
                "text": {"preview_url": False, "body": message},
            },
            headers={"Authorization": f"Bearer {settings.meta_access_token}"},
            timeout=settings.request_timeout,
        )
    except requests.RequestException as exc:
        logger.warning("Gym form: WhatsApp Cloud API request failed: %s", exc)
        return DeliveryResult("whatsapp", False, f"Cloud API request failed: {exc}", link=link)

    if response.ok:
        logger.info("Gym form: WhatsApp sent via Cloud API to +%s", number)
        return DeliveryResult("whatsapp", True, f"Sent via Cloud API to +{number}", link=link)

    detail = response.text[:300]
    logger.warning("Gym form: Cloud API returned %s: %s", response.status_code, detail)
    return DeliveryResult(
        "whatsapp", False,
        f"Cloud API returned HTTP {response.status_code}: {detail}. Note that free-form "
        "texts are only delivered inside a 24-hour customer service window; outside it "
        "an approved message template is required.",
        link=link,
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def notify_all(settings: GymFormSettings, submission: Submission) -> list[DeliveryResult]:
    """Send every configured notification for a submission.

    WhatsApp goes first so that, when no WhatsApp API is configured, the
    resulting one-tap wa.me link can be embedded in the office's email.
    """
    results: list[DeliveryResult] = []

    whatsapp_result = _send_whatsapp(settings, build_whatsapp_text(submission))
    results.append(whatsapp_result)

    attachment = Path(submission.id_proof_path) if submission.id_proof_path else None
    results.append(_send_email(
        settings,
        to=settings.notify_email,
        subject=(
            f"[{SOCIETY_NAME}] Trainer registration — {submission.trainer_name} "
            f"({submission.client_count} client"
            f"{'s' if submission.client_count != 1 else ''}) — {submission.reference}"
        ),
        text_body=build_summary_text(submission),
        html_body=build_summary_html(submission, whatsapp_link=whatsapp_result.link),
        attachment=attachment,
        attachment_name=submission.id_proof_filename,
    ))

    if settings.send_trainer_copy and submission.email:
        results.append(_send_email(
            settings,
            to=submission.email,
            subject=f"{SOCIETY_NAME} — gym registration received ({submission.reference})",
            text_body=build_trainer_copy_text(submission),
            html_body=build_trainer_copy_html(submission),
        ))

    return results
