"""Booking notifications — office email, resident copy, WhatsApp, decisions.

Everything actually *sent* here goes through ``gymform.notify``: the same
provider selection, the same "a delivery failure never loses a booking" rule,
and the same :class:`DeliveryResult` the pages already know how to render.
Only the wording is specific to hall bookings.
"""
from __future__ import annotations

import logging

from gymform.notify import (
    DeliveryResult,
    html_escape,
    send_email,
    send_whatsapp,
    whatsapp_link_for,
)
from gymform.rules import SOCIETY_ADDRESS, SOCIETY_NAME
from gymform.settings import GymFormSettings
from hallform.models import Booking
from hallform.rules import BOOKING_DAY_END, BOOKING_DAY_START, SECURITY_DEPOSIT_INR

logger = logging.getLogger(__name__)

DOCUMENT_TITLE = (
    "Rule and Regulation — For personal use of Society's amenities by Resident"
)


# ---------------------------------------------------------------------------
# Message bodies
# ---------------------------------------------------------------------------

def build_summary_text(booking: Booking) -> str:
    lines = [
        f"{SOCIETY_NAME} — amenity booking request",
        "=" * 52,
        f"Reference    : {booking.reference}",
        f"Submitted    : {booking.submitted_at_label}",
        "",
        "RESIDENT",
        f"  Name       : {booking.resident_name}",
        f"  Flat       : {booking.flat_number}",
        f"  Mobile     : +91 {booking.mobile}",
        f"  WhatsApp   : +91 {booking.whatsapp}",
        f"  Email      : {booking.email}",
        "",
        "BOOKING",
        f"  Amenity    : {booking.venue_name}",
        f"  Date       : {booking.when_label}",
        f"  Duration   : {booking.slot_label}",
        f"  Occasion   : {booking.occasion}"
        + (f" — {booking.occasion_detail}" if booking.occasion_detail else ""),
        f"  Persons    : {booking.expected_persons}",
        "",
        "MONEY",
        f"  Charge     : INR {booking.charge_inr:,} — cash, collected one day "
        "before the function",
        f"  Deposit    : INR {booking.security_deposit_inr:,} refundable, by cheque",
        "",
        f"Declaration signed by: {booking.declaration_signature}"
        + (f" at {booking.declaration_place}" if booking.declaration_place else ""),
        "All society rules acknowledged: "
        + ("yes" if all(booking.acknowledgements.values()) else "NO — please check"),
        "",
        "The slot is held for this resident until the office approves or rejects "
        "this request. Another resident asking for the same amenity at the same "
        "time is told it is already taken.",
    ]
    return "\n".join(lines)


def build_whatsapp_text(booking: Booking) -> str:
    """Short enough to read on a lock screen; no ID or address details."""
    return "\n".join([
        f"*{SOCIETY_NAME} — new amenity booking*",
        "",
        f"{booking.resident_name} (Flat {booking.flat_number})",
        f"{booking.venue_name}",
        f"{booking.when_label}",
        f"{booking.occasion} · {booking.expected_persons} persons",
        f"Charge: ₹{booking.charge_inr:,} (cash, day before)",
        f"Deposit: ₹{booking.security_deposit_inr:,} by cheque",
        "",
        f"Ref: {booking.reference}",
        "Approve or reject from the email.",
    ])


def build_decision_buttons(base_url: str, reference: str) -> str:
    """Approve / Reject buttons for the office email.

    Same signed-link scheme as the trainer form — the HMAC binds one decision
    to one booking, and the link only opens a confirmation page, because mail
    scanners follow links before a person ever sees them.
    """
    from gymform import tokens

    if not base_url or not tokens.signing_available():
        return ""

    base = base_url.rstrip("/")
    approve = (f"{base}/decide/{reference}?d=approved"
               f"&t={tokens.make_token(reference, 'approved')}")
    reject = (f"{base}/decide/{reference}?d=rejected"
              f"&t={tokens.make_token(reference, 'rejected')}")
    button = ("display:inline-block;padding:13px 26px;border-radius:8px;"
              "text-decoration:none;font-weight:600;font-size:15px")
    return (
        '<div style="margin:22px 0 6px;padding:18px;background:#f7fafd;'
        'border:1px solid #e6ecf3;border-radius:12px;text-align:center">'
        '<div style="font-size:14px;color:#5b6b80;margin-bottom:14px">'
        'Approving confirms the amenity for this resident and blocks the slot '
        'for everyone else. Rejecting releases it.'
        '</div>'
        f'<a href="{approve}" style="{button};background:#0f8a5f;color:#fff;'
        'margin:0 6px 8px">Approve booking</a>'
        f'<a href="{reject}" style="{button};background:#fff;color:#b3261e;'
        'border:1px solid #b3261e;margin:0 6px 8px">Reject</a>'
        '<div style="font-size:12px;color:#7b8a9c;margin-top:12px">'
        'You will be asked to confirm — nothing changes when you click.'
        '</div></div>'
    )


def _shell(title: str, reference: str, inner: str, colour: str = "#16233a") -> str:
    return f"""\
<div style="font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;background:#f4f7fb;padding:20px">
  <div style="max-width:620px;margin:0 auto;background:#fff;border-radius:14px;overflow:hidden">
    <div style="background:{colour};color:#fff;padding:20px 24px">
      <div style="font-size:19px;font-weight:700">{title}</div>
      <div style="font-size:13px;opacity:.8;margin-top:4px">{html_escape(reference)}</div>
    </div>
    <div style="padding:20px 24px;font-size:14px;color:#16233a;line-height:1.6">
      {inner}
    </div>
    <div style="padding:14px 24px;background:#f4f7fb;font-size:12px;color:#7b8a9c">
      {html_escape(SOCIETY_NAME)}, {html_escape(SOCIETY_ADDRESS)}
    </div>
  </div>
</div>"""


def _row(label: str, value: str) -> str:
    return (
        '<tr>'
        f'<td style="padding:6px 12px 6px 0;color:#5b6b80;white-space:nowrap;'
        f'vertical-align:top">{html_escape(label)}</td>'
        f'<td style="padding:6px 0;color:#16233a"><strong>{html_escape(value)}'
        '</strong></td></tr>'
    )


def build_summary_html(
    booking: Booking, whatsapp_link: str = "", base_url: str = ""
) -> str:
    occasion = booking.occasion + (
        f" — {booking.occasion_detail}" if booking.occasion_detail else ""
    )
    details = "".join([
        _row("Resident", booking.resident_name),
        _row("Flat", booking.flat_number),
        _row("Mobile", f"+91 {booking.mobile}"),
        _row("Email", booking.email),
    ])
    slot = "".join([
        _row("Amenity", booking.venue_name),
        _row("Date & time", booking.when_label),
        _row("Duration", booking.slot_label),
        _row("Occasion", occasion),
        _row("Persons expected", str(booking.expected_persons)),
    ])
    whatsapp_block = (
        f'<p style="text-align:center;margin:18px 0 0">'
        f'<a href="{whatsapp_link}" style="display:inline-block;padding:12px 22px;'
        'background:#25D366;color:#fff;border-radius:8px;text-decoration:none;'
        'font-weight:600">Forward this on WhatsApp</a></p>'
        if whatsapp_link else ""
    )
    inner = f"""
      <p>A resident has requested an amenity. The slot is <strong>held</strong>
         for them until you decide.</p>
      <h3 style="font-size:14px;margin:18px 0 6px;color:#5b6b80;text-transform:uppercase;letter-spacing:.04em">Booking</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px">{slot}</table>
      <h3 style="font-size:14px;margin:18px 0 6px;color:#5b6b80;text-transform:uppercase;letter-spacing:.04em">Resident</h3>
      <table style="width:100%;border-collapse:collapse;font-size:14px">{details}</table>
      <div style="margin:18px 0;padding:14px 16px;background:#eef3f9;border-radius:10px">
        <div>Charge: <strong>₹{booking.charge_inr:,}</strong> — cash, collected
             one day before the function, to the cultural fund.</div>
        <div style="margin-top:6px">Security deposit:
             <strong>₹{booking.security_deposit_inr:,}</strong> refundable, by cheque.</div>
      </div>
      <p style="font-size:13px;color:#5b6b80">
        Declaration signed by <strong>{html_escape(booking.declaration_signature)}</strong>.
        All {len(booking.acknowledgements)} society rules acknowledged.
      </p>
      {build_decision_buttons(base_url, booking.reference)}
      {whatsapp_block}
    """
    return _shell("New amenity booking request", booking.reference, inner)


def build_resident_copy_text(booking: Booking) -> str:
    return "\n".join([
        f"Dear {booking.resident_name},",
        "",
        f"Your booking request has reached the {SOCIETY_NAME} office. The slot is "
        "held for you while the office confirms it — you will get an email and a "
        "WhatsApp message the moment it is approved.",
        "",
        f"Reference : {booking.reference}",
        f"Amenity   : {booking.venue_name}",
        f"Date      : {booking.when_label}",
        f"Occasion  : {booking.occasion}",
        f"Persons   : {booking.expected_persons}",
        "",
        "PLEASE REMEMBER",
        f"  * Charge of INR {booking.charge_inr:,} is collected in cash one day "
        "before the function.",
        f"  * A refundable security deposit of INR {SECURITY_DEPOSIT_INR:,} is "
        "payable by cheque.",
        "  * Chairs, tables and guest parking are your responsibility.",
        "  * Clean the amenity after use, or the cleaning cost is deducted from "
        "the deposit.",
        "  * Switch off lights and fans when the function is over.",
        f"  * Bookings run {BOOKING_DAY_START} to {BOOKING_DAY_END} at the latest.",
        "",
        "Please do not treat this as confirmed until the office approves it.",
        "",
        f"{SOCIETY_NAME}, {SOCIETY_ADDRESS}",
    ])


def build_resident_copy_html(booking: Booking) -> str:
    inner = f"""
      <p>Dear {html_escape(booking.resident_name)},</p>
      <p>Your booking request has reached the {html_escape(SOCIETY_NAME)} office.
         The slot is <strong>held for you</strong> while the office confirms it.
         You will get an email and a WhatsApp message as soon as it is approved.</p>
      <table style="width:100%;border-collapse:collapse;font-size:14px">
        {_row("Amenity", booking.venue_name)}
        {_row("Date & time", booking.when_label)}
        {_row("Occasion", booking.occasion)}
        {_row("Persons", str(booking.expected_persons))}
        {_row("Charge", f"₹{booking.charge_inr:,}")}
      </table>
      <div style="margin:18px 0;padding:14px 16px;background:#fff6e5;border-left:4px solid #e0a800;border-radius:6px;color:#7a5200">
        <strong>Before your function</strong>
        <ul style="margin:8px 0 0;padding-left:20px">
          <li>₹{booking.charge_inr:,} in cash, one day before.</li>
          <li>₹{SECURITY_DEPOSIT_INR:,} refundable security deposit, by cheque.</li>
          <li>Chairs, tables and guest parking are yours to arrange.</li>
          <li>Clean the amenity afterwards, or the cost comes out of the deposit.</li>
          <li>Switch off the lights and fans when you finish.</li>
        </ul>
      </div>
      <p style="font-size:13px;color:#5b6b80">
        Please do not treat this as confirmed until the office approves it.
      </p>
    """
    return _shell("Booking request received", booking.reference, inner)


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------

def notify_all(
    settings: GymFormSettings, booking: Booking, base_url: str = ""
) -> list[DeliveryResult]:
    """Send every configured notification for a new booking.

    WhatsApp first, so that when no WhatsApp API is configured the resulting
    one-tap wa.me link can be embedded in the office's email.
    """
    results: list[DeliveryResult] = []

    whatsapp_result = send_whatsapp(settings, build_whatsapp_text(booking))
    results.append(whatsapp_result)

    results.append(send_email(
        settings,
        to=settings.notify_email,
        subject=(
            f"[{SOCIETY_NAME}] Amenity booking — {booking.venue_name} on "
            f"{booking.when_label} — {booking.resident_name} — {booking.reference}"
        ),
        text_body=build_summary_text(booking),
        html_body=build_summary_html(
            booking, whatsapp_link=whatsapp_result.link, base_url=base_url
        ),
    ))

    if settings.send_trainer_copy and booking.email:
        results.append(send_email(
            settings,
            to=booking.email,
            subject=(
                f"{SOCIETY_NAME} — booking request received ({booking.reference})"
            ),
            text_body=build_resident_copy_text(booking),
            html_body=build_resident_copy_html(booking),
        ))

    return results


def notify_payment(
    settings: GymFormSettings, record: dict, upi_reference: str, amount_inr: int
) -> list[DeliveryResult]:
    """Tell the office a resident has reported paying the booking charge.

    The rules say cash the day before; UPI is offered as a convenience. Either
    way this is the resident's own claim, never a verified receipt — the office
    still matches it against the society account.
    """
    resident = record.get("resident_name", "A resident")
    reference = record.get("reference", "")
    venue = record.get("venue_name", "an amenity")
    when = record.get("when_label", "")

    text = "\n".join([
        f"{resident} has reported paying the booking charge.",
        "",
        f"Booking      : {reference}",
        f"Amenity      : {venue}",
        f"Date         : {when}",
        f"Amount       : INR {amount_inr:,}",
        f"UPI reference: {upi_reference}",
        f"Flat         : {record.get('flat_number', '')}",
        f"Mobile       : +91 {record.get('mobile', '')}",
        "",
        "This is the resident's own entry, not a confirmed receipt. Match it "
        "against the society account before treating the charge as settled. The "
        f"refundable deposit of INR {SECURITY_DEPOSIT_INR:,} is still due by cheque.",
    ])
    inner = f"""
      <p><strong>{html_escape(resident)}</strong> has reported paying
         <strong>₹{amount_inr:,}</strong> for {html_escape(venue)} on
         {html_escape(when)}.</p>
      <p style="padding:12px 14px;background:#eef3f9;border-radius:10px">
        UPI reference: <strong>{html_escape(upi_reference)}</strong>
      </p>
      <p style="padding:12px 14px;background:#fff6e5;border-left:4px solid #e0a800;border-radius:6px;color:#7a5200">
        The resident's own entry, not a confirmed receipt. Match it against the
        society account first. The refundable deposit of ₹{SECURITY_DEPOSIT_INR:,}
        is still due by cheque.
      </p>
    """
    results = [send_email(
        settings,
        to=settings.notify_email,
        subject=(
            f"[{SOCIETY_NAME}] Booking charge reported — {resident} "
            f"(INR {amount_inr:,}) — {reference}"
        ),
        text_body=text,
        html_body=_shell("Booking charge reported paid", reference, inner),
    )]

    results.append(send_whatsapp(settings, "\n".join([
        f"*Booking charge reported — {SOCIETY_NAME}*",
        f"{resident} reports paying ₹{amount_inr:,}",
        f"{venue} · {when}",
        f"UPI ref: {upi_reference}",
        f"Booking: {reference}",
        "",
        "Not verified — please check the society account.",
    ])))
    return results


def notify_decision(
    settings: GymFormSettings, record: dict, decision: str, note: str = ""
) -> list[DeliveryResult]:
    """Tell the resident the office has confirmed or refused their booking."""
    resident = record.get("resident_name", "")
    reference = record.get("reference", "")
    venue = record.get("venue_name", "the amenity")
    when = record.get("when_label", "")
    charge = record.get("charge_inr", 0)
    email_to = record.get("email", "")
    approved = decision == "approved"

    if not email_to:
        return [DeliveryResult("email_trainer", False, "No resident email on record.")]

    if approved:
        headline = "Your booking is confirmed"
        body = (
            f"{venue} is reserved for you on {when}. Please pay ₹{charge:,} in cash "
            f"one day before the function and hand over the refundable security "
            f"deposit of ₹{SECURITY_DEPOSIT_INR:,} by cheque. Chairs, tables and "
            "guest parking are yours to arrange, the amenity must be cleaned after "
            "use, and lights and fans switched off when you finish."
        )
    else:
        headline = "Your booking could not be confirmed"
        body = (
            f"{venue} has not been confirmed for {when}. Please contact the society "
            "office — they can tell you which dates are free, and you are welcome "
            "to book another slot."
        )

    note_line = f"\n\nNote from the office: {note}" if note else ""
    text = (
        f"Dear {resident},\n\n{headline}.\n\n{body}{note_line}\n\n"
        f"Reference: {reference}\n\n{SOCIETY_NAME}, {SOCIETY_ADDRESS}"
    )
    note_html = (
        f'<p style="padding:12px 14px;background:#eef3f9;border-radius:10px">'
        f'<strong>Note from the office:</strong> {html_escape(note)}</p>'
        if note else ""
    )
    inner = f"""
      <p>Dear {html_escape(resident)},</p>
      <p>{html_escape(body)}</p>
      {note_html}
    """
    results = [send_email(
        settings,
        to=email_to,
        subject=(
            f"{SOCIETY_NAME} — amenity booking "
            f"{'confirmed' if approved else 'not confirmed'} ({reference})"
        ),
        text_body=text,
        html_body=_shell(
            headline, reference, inner, colour="#0f8a5f" if approved else "#b3261e"
        ),
    )]

    # ...and on WhatsApp, because that is where a resident actually looks.
    resident_whatsapp = record.get("whatsapp") or record.get("mobile") or ""
    if resident_whatsapp:
        status_line = (
            f"✅ *Confirmed* — {venue} is yours on {when}."
            if approved else
            f"❌ *Not confirmed* — {venue} is not available on {when}."
        )
        results.append(send_whatsapp(settings, "\n".join([
            f"*{SOCIETY_NAME} — amenity booking*",
            "",
            f"Hello {resident},",
            status_line,
            f"Reference: {reference}",
            *([f"Note: {note}"] if note else []),
            *([
                "",
                f"Please pay ₹{charge:,} cash one day before, and give the "
                f"₹{SECURITY_DEPOSIT_INR:,} refundable deposit by cheque.",
            ] if approved else [
                "",
                "Please contact the society office for another date.",
            ]),
        ]), to_number=f"91{resident_whatsapp}"))

    return results


__all__ = [
    "DOCUMENT_TITLE",
    "build_decision_buttons",
    "build_resident_copy_html",
    "build_resident_copy_text",
    "build_summary_html",
    "build_summary_text",
    "build_whatsapp_text",
    "notify_all",
    "notify_decision",
    "notify_payment",
    "whatsapp_link_for",
]
