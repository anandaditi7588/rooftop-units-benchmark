"""Booking model, validation and the double-booking check.

The clash check is the part that earns its keep: two residents planning the
same evening must not both be told yes. It runs against stored bookings at
submit time and names whoever holds the slot, so the second resident learns
immediately rather than on the day.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from hallform.rules import (
    BOOKING_DAY_END,
    BOOKING_DAY_START,
    OCCASIONS,
    RULES,
    SECURITY_DEPOSIT_INR,
    VENUES_BY_KEY,
    Venue,
)

IST = timezone(timedelta(hours=5, minutes=30), name="IST")

MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# A booking that holds the slot against other residents. A rejected booking
# releases it; a pending one still holds it, because the first resident asked
# first and should not lose their slot while the office is deciding.
BLOCKING_STATUSES = ("pending", "approved")


def to_minutes(hhmm: str) -> int | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (hhmm or "").strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def minutes_to_label(total: int) -> str:
    hour, minute = divmod(total, 60)
    suffix = "am" if hour < 12 else "pm"
    return f"{hour % 12 or 12}:{minute:02d} {suffix}"


def format_time(hhmm: str) -> str:
    minutes = to_minutes(hhmm)
    return minutes_to_label(minutes) if minutes is not None else (hhmm or "")


def format_date(iso: str) -> str:
    try:
        return datetime.strptime(iso, "%Y-%m-%d").strftime("%d %b %Y")
    except (ValueError, TypeError):
        return iso or ""


DAY_START_MIN = to_minutes(BOOKING_DAY_START) or 510
DAY_END_MIN = to_minutes(BOOKING_DAY_END) or 1320
BOOKING_HOURS_TEXT = f"{minutes_to_label(DAY_START_MIN)} to {minutes_to_label(DAY_END_MIN)}"


@dataclass
class Booking:
    reference: str
    submitted_at: datetime

    resident_name: str = ""
    flat_number: str = ""
    mobile: str = ""
    whatsapp: str = ""
    email: str = ""

    venue_key: str = ""
    slot_key: str = ""
    event_date: str = ""      # ISO, YYYY-MM-DD
    start_time: str = ""      # HH:MM
    end_time: str = ""        # HH:MM, derived from the slot length

    occasion: str = ""
    occasion_detail: str = ""
    expected_persons: int = 0

    acknowledgements: dict[str, bool] = field(default_factory=dict)
    declaration_signature: str = ""
    declaration_place: str = ""

    @property
    def venue(self) -> Venue | None:
        return VENUES_BY_KEY.get(self.venue_key)

    @property
    def venue_name(self) -> str:
        venue = self.venue
        return f"{venue.name} ({venue.location})" if venue else self.venue_key

    @property
    def slot_label(self) -> str:
        venue = self.venue
        slot = venue.slot(self.slot_key) if venue else None
        return slot.label if slot else self.slot_key

    @property
    def charge_inr(self) -> int:
        venue = self.venue
        slot = venue.slot(self.slot_key) if venue else None
        return slot.charge_inr if slot else 0

    @property
    def security_deposit_inr(self) -> int:
        return SECURITY_DEPOSIT_INR

    @property
    def when_label(self) -> str:
        return (
            f"{format_date(self.event_date)}, "
            f"{format_time(self.start_time)} – {format_time(self.end_time)}"
        )

    @property
    def submitted_at_label(self) -> str:
        return self.submitted_at.strftime("%d %b %Y, %I:%M %p IST")

    def as_dict(self) -> dict:
        return {
            "reference": self.reference,
            "submitted_at": self.submitted_at.isoformat(),
            "resident_name": self.resident_name,
            "flat_number": self.flat_number,
            "mobile": self.mobile,
            "whatsapp": self.whatsapp,
            "email": self.email,
            "venue_key": self.venue_key,
            "venue_name": self.venue_name,
            "slot_key": self.slot_key,
            "slot_label": self.slot_label,
            "event_date": self.event_date,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "when_label": self.when_label,
            "occasion": self.occasion,
            "occasion_detail": self.occasion_detail,
            "expected_persons": self.expected_persons,
            "charge_inr": self.charge_inr,
            "security_deposit_inr": self.security_deposit_inr,
            "acknowledgements": dict(self.acknowledgements),
            "declaration_signature": self.declaration_signature,
            "declaration_place": self.declaration_place,
        }


# ---------------------------------------------------------------------------
# Double-booking
# ---------------------------------------------------------------------------

def find_clash(
    existing: list[dict], venue_key: str, event_date: str, start: int, end: int
) -> dict | None:
    """The first booking that already holds this venue at this time, if any.

    Overlap is half-open: a function ending at 2 pm does not clash with one
    starting at 2 pm, which is how a hall actually turns over.
    """
    for record in existing:
        if record.get("venue_key") != venue_key or record.get("event_date") != event_date:
            continue
        if record.get("status", "pending") not in BLOCKING_STATUSES:
            continue
        other_start = to_minutes(record.get("start_time", ""))
        other_end = to_minutes(record.get("end_time", ""))
        if other_start is None or other_end is None:
            continue
        if start < other_end and other_start < end:
            return record
    return None


def describe_clash(record: dict) -> str:
    """What the second resident is told. Names the holder, as asked."""
    who = record.get("resident_name") or "another resident"
    flat = record.get("flat_number")
    holder = f"{who} (Flat {flat})" if flat else who
    status = record.get("status", "pending")
    state = "already booked" if status == "approved" else "already requested"
    return (
        f"{record.get('venue_name', 'This venue')} is {state} by {holder} on "
        f"{format_date(record.get('event_date', ''))} from "
        f"{format_time(record.get('start_time', ''))} to "
        f"{format_time(record.get('end_time', ''))}. Please choose another time or date."
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _normalise_mobile(raw: str) -> str:
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) > 10:
        if digits.startswith("91"):
            digits = digits[2:]
        elif digits.startswith("0"):
            digits = digits.lstrip("0")
    return digits[-10:] if len(digits) > 10 else digits


def parse_booking(
    form: dict[str, object], existing: list[dict] | None = None
) -> tuple[Booking | None, dict[str, str], dict]:
    """Validate a booking request. Returns (booking, errors, raw)."""
    errors: dict[str, str] = {}
    existing = existing or []

    def text(name: str) -> str:
        value = form.get(name)
        return value.strip() if isinstance(value, str) else ""

    def checked(name: str) -> bool:
        value = form.get(name)
        return isinstance(value, str) and value.lower() not in ("", "0", "false", "off")

    resident_name = text("resident_name")
    flat_number = text("flat_number")
    mobile = _normalise_mobile(text("mobile"))
    whatsapp = _normalise_mobile(text("whatsapp")) or mobile
    email = text("email").lower()
    venue_key = text("venue_key")
    slot_key = text("slot_key")
    event_date = text("event_date")
    start_time = text("start_time")
    occasion = text("occasion")
    occasion_detail = text("occasion_detail")
    persons_raw = text("expected_persons")

    if len(resident_name) < 3:
        errors["resident_name"] = "Please enter your full name."
    if not flat_number:
        errors["flat_number"] = "Enter your flat number."
    if not MOBILE_RE.fullmatch(mobile):
        errors["mobile"] = "Enter a 10-digit Indian mobile number."
    if whatsapp and not MOBILE_RE.fullmatch(whatsapp):
        errors["whatsapp"] = "Enter a 10-digit WhatsApp number, or leave it blank."
    if not EMAIL_RE.fullmatch(email):
        errors["email"] = "Enter a valid email address."

    venue = VENUES_BY_KEY.get(venue_key)
    if venue is None:
        errors["venue_key"] = "Choose which amenity you want to book."
    slot = venue.slot(slot_key) if venue else None
    if venue is not None and slot is None:
        errors["slot_key"] = "Choose how long you need the amenity for."

    if occasion not in OCCASIONS:
        errors["occasion"] = "Choose the occasion."

    try:
        expected_persons = int(persons_raw)
    except ValueError:
        expected_persons = 0
    if expected_persons < 1:
        errors["expected_persons"] = "Enter how many people are expected."
    elif venue is not None and expected_persons > venue.max_persons:
        errors["expected_persons"] = (
            f"The {venue.name} allows a maximum of {venue.max_persons} persons."
        )

    # --- Date -------------------------------------------------------------
    if not DATE_RE.fullmatch(event_date):
        errors["event_date"] = "Choose the date of your function."
    else:
        try:
            chosen = datetime.strptime(event_date, "%Y-%m-%d").date()
        except ValueError:
            errors["event_date"] = "That is not a valid date."
        else:
            today = datetime.now(IST).date()
            if chosen < today:
                errors["event_date"] = "That date has already passed."
            elif chosen == today:
                # The rules require the cash a day before the function.
                errors["event_date"] = (
                    "Please book at least one day ahead — the charges are collected "
                    "the day before the function."
                )

    # --- Time window ------------------------------------------------------
    start = to_minutes(start_time)
    end = None
    if slot is not None:
        if slot.key == "day":
            # Priced per day, so it simply takes the whole permitted window.
            start, end = DAY_START_MIN, DAY_END_MIN
            start_time = BOOKING_DAY_START
        elif start is None:
            errors["start_time"] = "Choose what time your function starts."
        else:
            end = start + slot.hours * 60
            if start < DAY_START_MIN:
                errors["start_time"] = f"Bookings start no earlier than {BOOKING_HOURS_TEXT.split(' to ')[0]}."
            elif end > DAY_END_MIN:
                latest = minutes_to_label(DAY_END_MIN - slot.hours * 60)
                errors["start_time"] = (
                    f"A {slot.label.lower()} booking must start by {latest} so it ends "
                    f"by {minutes_to_label(DAY_END_MIN)}."
                )

    # --- Double-booking ---------------------------------------------------
    clash = None
    if not errors and venue is not None and start is not None and end is not None:
        clash = find_clash(existing, venue_key, event_date, start, end)
        if clash is not None:
            errors["clash"] = describe_clash(clash)

    # --- Rules ------------------------------------------------------------
    acknowledgements = {rule.key: checked(f"ack_{rule.key}") for rule in RULES}
    missing = [rule.number for rule in RULES if not acknowledgements[rule.key]]
    if missing:
        numbers = ", ".join(str(n) for n in missing)
        errors["acknowledgements"] = (
            f"Please tick the acknowledgement for rule{'s' if len(missing) > 1 else ''} {numbers}."
        )

    signature = text("declaration_signature")
    if len(signature) < 3:
        errors["declaration_signature"] = "Type your full name to sign this declaration."
    if not checked("declaration_agree"):
        errors["declaration_agree"] = "Please accept the declaration before submitting."

    raw = {
        "resident_name": resident_name,
        "flat_number": flat_number,
        "mobile": mobile,
        "whatsapp": text("whatsapp"),
        "email": email,
        "venue_key": venue_key,
        "slot_key": slot_key,
        "event_date": event_date,
        "start_time": text("start_time"),
        "occasion": occasion,
        "occasion_detail": occasion_detail,
        "expected_persons": persons_raw,
        "acknowledgements": acknowledgements,
        "declaration_signature": signature,
        "declaration_place": text("declaration_place"),
        "declaration_agree": checked("declaration_agree"),
    }

    if errors:
        return None, errors, raw

    now = datetime.now(IST)
    booking = Booking(
        reference=f"SB-HB-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:5].upper()}",
        submitted_at=now,
        resident_name=resident_name,
        flat_number=flat_number,
        mobile=mobile,
        whatsapp=whatsapp,
        email=email,
        venue_key=venue_key,
        slot_key=slot_key,
        event_date=event_date,
        start_time=start_time,
        end_time=f"{end // 60:02d}:{end % 60:02d}",
        occasion=occasion,
        occasion_detail=occasion_detail,
        expected_persons=expected_persons,
        acknowledgements=acknowledgements,
        declaration_signature=signature,
        declaration_place=text("declaration_place"),
    )
    return booking, {}, raw
