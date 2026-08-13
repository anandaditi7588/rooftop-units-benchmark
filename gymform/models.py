"""Form data model and validation.

The validator returns *field-keyed* errors rather than raising, so the form
can be re-rendered with the trainer's answers intact and a message pinned to
each offending field — a trainer filling this in on a phone at the gym gate
should never lose fifteen minutes of typing to one bad digit.
"""
from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from gymform.rules import (
    ID_PROOF_TYPES,
    MAX_CLIENTS_PER_SESSION,
    OPERATING_WINDOWS,
    RULES,
    monthly_fee_for,
)

# India Standard Time. The society, the trainers and the office are all here,
# so timestamps are stamped in IST regardless of where the server runs.
IST = timezone(timedelta(hours=5, minutes=30), name="IST")

DAYS: tuple[tuple[str, str], ...] = (
    ("mon", "Mon"),
    ("tue", "Tue"),
    ("wed", "Wed"),
    ("thu", "Thu"),
    ("fri", "Fri"),
    ("sat", "Sat"),
    ("sun", "Sun"),
)
DAY_CODES: tuple[str, ...] = tuple(code for code, _ in DAYS)
DAY_LABELS: dict[str, str] = dict(DAYS)

# How a trainer can have obtained permission for an out-of-hours slot. Free
# text would make these unsearchable for the office, so the common routes are
# offered as choices with "Other" as the escape hatch.
APPROVAL_MODES: tuple[str, ...] = (
    "In person at the society office",
    "Phone call",
    "WhatsApp message",
    "Email",
    "Letter / entry in the society register",
    "Other",
)

MOBILE_RE = re.compile(r"^[6-9]\d{9}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")
AADHAAR_RE = re.compile(r"^\d{12}$")
PAN_RE = re.compile(r"^[A-Z]{5}\d{4}[A-Z]$")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def normalise_mobile(raw: str) -> str:
    """Strip spaces, dashes and any +91 / 0 prefix down to 10 bare digits."""
    digits = re.sub(r"\D", "", raw or "")
    if len(digits) > 10:
        if digits.startswith("91"):
            digits = digits[2:]
        elif digits.startswith("0"):
            digits = digits.lstrip("0")
    return digits[-10:] if len(digits) > 10 else digits


def to_minutes(hhmm: str) -> int | None:
    """'06:30' -> 390. Returns None when the value isn't a valid time."""
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", (hhmm or "").strip())
    if not match:
        return None
    hours, minutes = int(match.group(1)), int(match.group(2))
    if not (0 <= hours <= 23 and 0 <= minutes <= 59):
        return None
    return hours * 60 + minutes


def format_time(hhmm: str) -> str:
    """'06:30' -> '6:30 am', for human-readable summaries."""
    minutes = to_minutes(hhmm)
    if minutes is None:
        return hhmm or ""
    hour, minute = divmod(minutes, 60)
    suffix = "am" if hour < 12 else "pm"
    display_hour = hour % 12 or 12
    return f"{display_hour}:{minute:02d} {suffix}"


def within_operating_hours(start: int, end: int) -> bool:
    """True when [start, end] fits entirely inside one gym operating window."""
    for window in OPERATING_WINDOWS:
        w_start, w_end = to_minutes(window.start), to_minutes(window.end)
        if w_start is not None and w_end is not None and start >= w_start and end <= w_end:
            return True
    return False


OPERATING_HOURS_TEXT = " and ".join(
    f"{format_time(w.start)} to {format_time(w.end)}" for w in OPERATING_WINDOWS
)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class ClientEntry:
    """One row of the trainer's client list (rule 1)."""
    name: str = ""
    flat_number: str = ""
    days: list[str] = field(default_factory=list)
    start_time: str = ""
    end_time: str = ""

    @property
    def days_label(self) -> str:
        ordered = [DAY_LABELS[d] for d in DAY_CODES if d in self.days]
        return ", ".join(ordered)

    @property
    def slot_label(self) -> str:
        """The 'Training Slot' column the rules document asks for."""
        times = f"{format_time(self.start_time)} – {format_time(self.end_time)}"
        return f"{self.days_label} · {times}" if self.days_label else times

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "flat_number": self.flat_number,
            "days": list(self.days),
            "start_time": self.start_time,
            "end_time": self.end_time,
            "slot_label": self.slot_label,
        }


@dataclass
class Submission:
    """A validated trainer registration."""
    reference: str
    submitted_at: datetime

    trainer_name: str = ""
    mobile: str = ""
    whatsapp: str = ""
    email: str = ""
    id_type: str = ""
    id_number: str = ""
    address: str = ""
    emergency_contact_name: str = ""
    emergency_contact_mobile: str = ""

    clients: list[ClientEntry] = field(default_factory=list)

    outside_hours_informed: bool = False
    outside_hours_approved_by: str = ""
    outside_hours_approval_mode: str = ""
    outside_hours_note: str = ""
    committee_approval_reference: str = ""

    acknowledgements: dict[str, bool] = field(default_factory=dict)
    declaration_signature: str = ""
    declaration_place: str = ""

    id_proof_filename: str = ""
    id_proof_path: str = ""

    @property
    def client_count(self) -> int:
        return len(self.clients)

    @property
    def monthly_fee(self) -> int:
        return monthly_fee_for(self.client_count)

    @property
    def submitted_at_label(self) -> str:
        return self.submitted_at.strftime("%d %b %Y, %I:%M %p IST")

    @property
    def has_outside_hours_slot(self) -> bool:
        for client in self.clients:
            start, end = to_minutes(client.start_time), to_minutes(client.end_time)
            if start is not None and end is not None and not within_operating_hours(start, end):
                return True
        return False

    def as_dict(self) -> dict:
        return {
            "reference": self.reference,
            "submitted_at": self.submitted_at.isoformat(),
            "trainer_name": self.trainer_name,
            "mobile": self.mobile,
            "whatsapp": self.whatsapp,
            "email": self.email,
            "id_type": self.id_type,
            "id_number": self.id_number,
            "address": self.address,
            "emergency_contact_name": self.emergency_contact_name,
            "emergency_contact_mobile": self.emergency_contact_mobile,
            "clients": [c.as_dict() for c in self.clients],
            "client_count": self.client_count,
            "monthly_fee_inr": self.monthly_fee,
            "outside_hours_informed": self.outside_hours_informed,
            "outside_hours_approved_by": self.outside_hours_approved_by,
            "outside_hours_approval_mode": self.outside_hours_approval_mode,
            "outside_hours_note": self.outside_hours_note,
            "committee_approval_reference": self.committee_approval_reference,
            "acknowledgements": dict(self.acknowledgements),
            "declaration_signature": self.declaration_signature,
            "declaration_place": self.declaration_place,
            "id_proof_filename": self.id_proof_filename,
            "id_proof_path": self.id_proof_path,
        }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _max_concurrent_clients(clients: list[ClientEntry]) -> int:
    """Largest number of clients scheduled at the same moment on any one day.

    A sweep over start/end events per weekday: rules 4 and 14 cap a trainer at
    four clients *at a time*, not four clients in total.
    """
    busiest = 0
    for day in DAY_CODES:
        events: list[tuple[int, int]] = []
        for client in clients:
            if day not in client.days:
                continue
            start, end = to_minutes(client.start_time), to_minutes(client.end_time)
            if start is None or end is None or end <= start:
                continue
            events.append((start, 1))
            events.append((end, -1))
        # Ends before starts at the same minute: a session that finishes at
        # 07:00 does not overlap one that begins at 07:00.
        events.sort(key=lambda item: (item[0], item[1]))
        running = 0
        for _, delta in events:
            running += delta
            busiest = max(busiest, running)
    return busiest


def _validate_id_number(id_type: str, id_number: str) -> str | None:
    """Returns an error message, or None when the number looks right."""
    value = id_number.upper().replace(" ", "").replace("-", "")
    if id_type == "Aadhar Card" and not AADHAAR_RE.fullmatch(value):
        return "An Aadhar number is 12 digits."
    if id_type == "PAN Card" and not PAN_RE.fullmatch(value):
        return "A PAN is 10 characters, like ABCDE1234F."
    if id_type == "Driving License" and len(value) < 8:
        return "Please enter the full driving licence number."
    return None


def parse_submission(form: dict[str, object]) -> tuple[Submission | None, dict[str, str], dict]:
    """Validate raw form values.

    Returns ``(submission, errors, raw)``. When ``errors`` is non-empty
    ``submission`` is None and ``raw`` carries the trainer's answers back to
    the template so nothing they typed is lost.
    """
    errors: dict[str, str] = {}

    def text(name: str) -> str:
        value = form.get(name)
        return value.strip() if isinstance(value, str) else ""

    def checked(name: str) -> bool:
        value = form.get(name)
        return isinstance(value, str) and value.lower() not in ("", "0", "false", "off")

    def text_list(name: str) -> list[str]:
        values = form.get(name)
        if isinstance(values, list):
            return [v.strip() if isinstance(v, str) else "" for v in values]
        return [values.strip()] if isinstance(values, str) else []

    trainer_name = text("trainer_name")
    mobile = normalise_mobile(text("mobile"))
    whatsapp = normalise_mobile(text("whatsapp")) or mobile
    email = text("email").lower()
    id_type = text("id_type")
    id_number = text("id_number").upper().replace(" ", "")
    address = text("address")
    emergency_name = text("emergency_contact_name")
    emergency_mobile = normalise_mobile(text("emergency_contact_mobile"))

    if len(trainer_name) < 3:
        errors["trainer_name"] = "Please enter your full name."
    if not MOBILE_RE.fullmatch(mobile):
        errors["mobile"] = "Enter a 10-digit Indian mobile number."
    if whatsapp and not MOBILE_RE.fullmatch(whatsapp):
        errors["whatsapp"] = "Enter a 10-digit WhatsApp number, or leave it blank."
    if not EMAIL_RE.fullmatch(email):
        errors["email"] = "Enter a valid email address."
    if id_type not in ID_PROOF_TYPES:
        errors["id_type"] = "Choose which government ID you are submitting."
    elif not id_number:
        errors["id_number"] = "Enter your ID number."
    else:
        id_error = _validate_id_number(id_type, id_number)
        if id_error:
            errors["id_number"] = id_error
    if len(address) < 10:
        errors["address"] = "Enter your full residential address."
    if emergency_mobile and not MOBILE_RE.fullmatch(emergency_mobile):
        errors["emergency_contact_mobile"] = "Enter a 10-digit number, or leave it blank."

    # --- Client list (rule 1) -------------------------------------------
    names = text_list("client_name")
    flats = text_list("client_flat")
    starts = text_list("client_start")
    ends = text_list("client_end")
    row_count = max(len(names), len(flats), len(starts), len(ends))

    clients: list[ClientEntry] = []
    row_errors: list[str] = []
    for index in range(row_count):
        name = names[index] if index < len(names) else ""
        flat = flats[index] if index < len(flats) else ""
        start = starts[index] if index < len(starts) else ""
        end = ends[index] if index < len(ends) else ""
        days = [d for d in text_list(f"client_days_{index}") if d in DAY_CODES]

        # A completely blank row is just an unused row, not a mistake.
        if not any((name, flat, start, end)) and not days:
            continue

        entry = ClientEntry(
            name=name, flat_number=flat, days=days, start_time=start, end_time=end
        )
        clients.append(entry)

        position = len(clients)
        start_minutes, end_minutes = to_minutes(start), to_minutes(end)
        if not name:
            row_errors.append(f"Client {position}: enter the client's name.")
        if not flat:
            row_errors.append(f"Client {position}: enter the flat number.")
        if not days:
            row_errors.append(f"Client {position}: pick at least one training day.")
        if start_minutes is None or end_minutes is None:
            row_errors.append(f"Client {position}: enter the training start and end time.")
        elif end_minutes <= start_minutes:
            row_errors.append(f"Client {position}: the end time must be after the start time.")

    if not clients:
        errors["clients"] = "Add at least one client you will be training in Silicon Bay."
    elif row_errors:
        errors["clients"] = " ".join(row_errors)

    # --- Timings (rule 2) -------------------------------------------------
    outside_hours_informed = checked("outside_hours_informed")
    outside_hours_approved_by = text("outside_hours_approved_by")
    outside_hours_approval_mode = text("outside_hours_approval_mode")
    outside_hours_note = text("outside_hours_note")
    outside_rows = [
        f"Client {i + 1} ({c.name or 'unnamed'})"
        for i, c in enumerate(clients)
        if (to_minutes(c.start_time) is not None
            and to_minutes(c.end_time) is not None
            and not within_operating_hours(to_minutes(c.start_time), to_minutes(c.end_time)))
    ]
    if outside_rows:
        if not outside_hours_informed:
            errors["outside_hours_informed"] = (
                f"{', '.join(outside_rows)} falls outside gym hours "
                f"({OPERATING_HOURS_TEXT}). Confirm you have taken approval for it."
            )
        else:
            # Ticking the box is a claim of approval, so it has to be
            # accountable: the office needs to know who granted it and how, or
            # the tick is worth nothing when a dispute comes up later.
            if len(outside_hours_approved_by) < 3:
                errors["outside_hours_approved_by"] = (
                    "Enter the name of the person who approved the out-of-hours slot."
                )
            if outside_hours_approval_mode not in APPROVAL_MODES:
                errors["outside_hours_approval_mode"] = (
                    "Choose how you took the approval."
                )

    # --- Trainee limit (rules 4 and 14) -----------------------------------
    committee_reference = text("committee_approval_reference")
    concurrent = _max_concurrent_clients(clients)
    if concurrent > MAX_CLIENTS_PER_SESSION and not committee_reference:
        errors["committee_approval_reference"] = (
            f"Your schedule has {concurrent} clients training at the same time, but the "
            f"limit is {MAX_CLIENTS_PER_SESSION}. Either stagger the slots, or enter the "
            "society committee's written approval reference."
        )

    # --- Rule acknowledgements --------------------------------------------
    acknowledgements = {rule.key: checked(f"ack_{rule.key}") for rule in RULES}
    missing = [rule.number for rule in RULES if not acknowledgements[rule.key]]
    if missing:
        numbers = ", ".join(str(n) for n in missing)
        errors["acknowledgements"] = (
            f"Please tick the acknowledgement for rule{'s' if len(missing) > 1 else ''} {numbers}."
        )

    # --- Declaration ------------------------------------------------------
    signature = text("declaration_signature")
    if len(signature) < 3:
        errors["declaration_signature"] = "Type your full name to sign this declaration."
    if not checked("declaration_agree"):
        errors["declaration_agree"] = "Please accept the declaration before submitting."

    raw = {
        "trainer_name": trainer_name,
        "mobile": mobile,
        "whatsapp": text("whatsapp"),
        "email": email,
        "id_type": id_type,
        "id_number": id_number,
        "address": address,
        "emergency_contact_name": emergency_name,
        "emergency_contact_mobile": emergency_mobile,
        "clients": [c.as_dict() for c in clients] or [ClientEntry().as_dict()],
        "outside_hours_informed": outside_hours_informed,
        "outside_hours_approved_by": outside_hours_approved_by,
        "outside_hours_approval_mode": outside_hours_approval_mode,
        "outside_hours_note": outside_hours_note,
        "committee_approval_reference": committee_reference,
        "acknowledgements": acknowledgements,
        "declaration_signature": signature,
        "declaration_place": text("declaration_place"),
        "declaration_agree": checked("declaration_agree"),
    }

    if errors:
        return None, errors, raw

    now = datetime.now(IST)
    submission = Submission(
        reference=f"SB-PT-{now.strftime('%Y%m%d')}-{uuid.uuid4().hex[:5].upper()}",
        submitted_at=now,
        trainer_name=trainer_name,
        mobile=mobile,
        whatsapp=whatsapp,
        email=email,
        id_type=id_type,
        id_number=id_number,
        address=address,
        emergency_contact_name=emergency_name,
        emergency_contact_mobile=emergency_mobile,
        clients=clients,
        outside_hours_informed=outside_hours_informed,
        outside_hours_approved_by=outside_hours_approved_by,
        outside_hours_approval_mode=outside_hours_approval_mode,
        outside_hours_note=outside_hours_note,
        committee_approval_reference=committee_reference,
        acknowledgements=acknowledgements,
        declaration_signature=signature,
        declaration_place=text("declaration_place"),
    )
    return submission, {}, raw
