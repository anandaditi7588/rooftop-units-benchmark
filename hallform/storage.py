"""Durable record of hall bookings.

Same shape as the trainer registrations: append-only JSONL as the record of
truth, a flat CSV for Excel, and decisions kept in their own file so the
booking the resident signed is never rewritten.

The clash check reads from here, so this is also what stops two residents
being told yes for the same evening.
"""
from __future__ import annotations

import csv
import json
import logging
import threading
from datetime import datetime
from typing import Any, Iterator

from gymform.settings import (
    BOOKING_APPROVALS_JSONL,
    BOOKING_DELETIONS_JSONL,
    BOOKING_PAYMENTS_JSONL,
    BOOKINGS_CSV,
    BOOKINGS_JSONL,
    ensure_dirs,
)
from hallform.models import IST, Booking
from hallform.rules import RULES

logger = logging.getLogger(__name__)

_write_lock = threading.Lock()

DECISIONS = ("approved", "rejected")

CSV_COLUMNS: list[str] = [
    "reference",
    "submitted_at",
    "resident_name",
    "flat_number",
    "mobile",
    "whatsapp",
    "email",
    "venue_name",
    "event_date",
    "start_time",
    "end_time",
    "slot_label",
    "occasion",
    "occasion_detail",
    "expected_persons",
    "charge_inr",
    "security_deposit_inr",
    "declaration_signature",
    "declaration_place",
    "all_rules_acknowledged",
    "payment_upi_reference",
    "payment_reported_at",
    "status",
    "decided_at",
    "removal_reason",
]


def _csv_row(record: dict[str, Any]) -> dict[str, Any]:
    row = {key: record.get(key, "") for key in CSV_COLUMNS}
    acknowledgements = record.get("acknowledgements", {})
    row["all_rules_acknowledged"] = (
        "Yes" if all(acknowledgements.get(rule.key) for rule in RULES) else "No"
    )
    payment = record.get("payment") or {}
    row["payment_upi_reference"] = payment.get("upi_reference", "")
    row["payment_reported_at"] = payment.get("reported_at", "")
    approval = record.get("approval") or {}
    row["status"] = record.get("status", "pending")
    row["decided_at"] = approval.get("decided_at", "")
    removal = record.get("removal") or {}
    row["removal_reason"] = removal.get("reason", "")
    return row


# Column titles for the Google Sheet — see gymform/storage.py.
SHEET_HEADERS: dict[str, str] = {
    "reference": "Reference",
    "submitted_at": "Submitted",
    "resident_name": "Resident",
    "flat_number": "Flat",
    "mobile": "Mobile",
    "whatsapp": "WhatsApp",
    "email": "Email",
    "venue_name": "Amenity",
    "event_date": "Date",
    "start_time": "From",
    "end_time": "To",
    "slot_label": "Duration",
    "occasion": "Occasion",
    "occasion_detail": "Occasion details",
    "expected_persons": "Persons",
    "charge_inr": "Charge (INR)",
    "security_deposit_inr": "Deposit (INR)",
    "declaration_signature": "Signed by",
    "declaration_place": "Place",
    "all_rules_acknowledged": "All rules accepted",
    "payment_upi_reference": "UPI reference",
    "payment_reported_at": "Payment reported",
    "status": "Status",
    "decided_at": "Decided at",
    "removal_reason": "Removed because",
}

SHEET_NAME = "Hall bookings"


def sheet_row(record: dict[str, Any]) -> dict[str, Any]:
    """The Google Sheet view of one booking, keyed on its reference."""
    row = _csv_row(record)
    return {SHEET_HEADERS.get(key, key): row.get(key, "") for key in CSV_COLUMNS}


def save_booking(booking: Booking) -> dict[str, Any]:
    ensure_dirs()
    record = booking.as_dict()

    with _write_lock:
        with open(BOOKINGS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        write_header = not BOOKINGS_CSV.exists() or BOOKINGS_CSV.stat().st_size == 0
        with open(BOOKINGS_CSV, "a", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(_csv_row(record))

    logger.info(
        "Hall form: stored booking %s — %s for %s",
        booking.reference, booking.venue_name, booking.when_label,
    )
    return record


def _append(path, entry: dict[str, Any]) -> dict[str, Any]:
    ensure_dirs()
    with _write_lock:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _latest_by_reference(path) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    if not path.exists():
        return latest
    with open(path, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("reference"):
                latest[entry["reference"]] = entry
    return latest


def record_payment(reference: str, upi_reference: str, amount_inr: int) -> dict[str, Any]:
    """A resident's claim to have paid — reported, never verified here."""
    entry = {
        "reference": reference,
        "upi_reference": upi_reference,
        "amount_inr": amount_inr,
        "reported_at": datetime.now(IST).isoformat(),
    }
    logger.info("Hall form: payment reported for %s (UPI ref %s)", reference, upi_reference)
    return _append(BOOKING_PAYMENTS_JSONL, entry)


def record_decision(reference: str, decision: str, note: str = "") -> dict[str, Any]:
    """The office confirming or refusing a booking.

    Until this says approved, the slot is held but not confirmed — and a
    rejection releases it for the next resident who asks.
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}, got {decision!r}")
    entry = {
        "reference": reference,
        "decision": decision,
        "note": note,
        "decided_at": datetime.now(IST).isoformat(),
    }
    logger.info("Hall form: %s %s", reference, decision)
    return _append(BOOKING_APPROVALS_JSONL, entry)


def record_deletion(reference: str, reason: str = "", removed: bool = True) -> dict[str, Any]:
    """The office striking a wrong booking off the list — or putting it back.

    Nothing is erased. The resident's original submission stays exactly as they
    signed it and this is written alongside, so the society can always answer
    "who removed that booking, when, and why" — which is the whole reason a
    booking system is trusted with the hall in the first place.

    What changes is what the booking *does*: a removed booking stops holding
    its slot, drops off the public calendar, and drops out of the office's list
    unless they ask to see removed ones. Setting ``removed=False`` undoes it,
    which is what makes the button safe to press.
    """
    entry = {
        "reference": reference,
        "removed": bool(removed),
        "reason": reason,
        "recorded_at": datetime.now(IST).isoformat(),
    }
    logger.info(
        "Hall form: %s %s%s",
        reference,
        "removed by the office" if removed else "restored by the office",
        f" — {reason}" if reason else "",
    )
    return _append(BOOKING_DELETIONS_JSONL, entry)


def iter_bookings() -> Iterator[dict[str, Any]]:
    if not BOOKINGS_JSONL.exists():
        return
    with open(BOOKINGS_JSONL, "r", encoding="utf-8") as fh:
        for number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning("Hall form: skipping unreadable booking on line %d", number)


def load_bookings(
    newest_first: bool = True, include_removed: bool = False
) -> list[dict[str, Any]]:
    """Every booking, each carrying its payment, decision and removal.

    Removed bookings are left out by default. That default is what frees the
    slot: the clash check reads this list, so a booking the office has struck
    off simply stops existing as far as the next resident is concerned.
    """
    payments = _latest_by_reference(BOOKING_PAYMENTS_JSONL)
    decisions = _latest_by_reference(BOOKING_APPROVALS_JSONL)
    removals = _latest_by_reference(BOOKING_DELETIONS_JSONL)
    records = []
    for record in iter_bookings():
        reference = record.get("reference", "")
        removal = removals.get(reference)
        record["removal"] = removal if (removal or {}).get("removed") else None
        if record["removal"] and not include_removed:
            continue
        record["payment"] = payments.get(reference)
        record["approval"] = decisions.get(reference)
        record["status"] = (
            "removed" if record["removal"]
            else (record["approval"] or {}).get("decision", "pending")
        )
        records.append(record)
    if newest_first:
        records.reverse()
    return records


def find_booking(
    reference: str, include_removed: bool = True
) -> dict[str, Any] | None:
    """One booking by reference.

    Removed ones are included by default: the office still has to be able to
    open, review and restore what it struck off.
    """
    return next(
        (
            b for b in load_bookings(
                newest_first=False, include_removed=include_removed
            )
            if b.get("reference") == reference
        ),
        None,
    )
