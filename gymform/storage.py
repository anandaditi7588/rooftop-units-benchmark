"""Durable record of every submission.

Notification channels can fail — an SMTP password expires, a WhatsApp
credential lapses, the network blips. Storage is therefore written *before*
anything is sent, so the society office can always recover a registration
from disk (or the CSV export) even when no message ever arrived.

Two files are kept in step:
  * submissions.jsonl — one full JSON record per line, the complete record.
  * submissions.csv   — a flat, one-row-per-submission view for Excel.
"""
from __future__ import annotations

import csv
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from gymform.models import IST, Submission
from gymform.rules import RULES
from gymform.settings import (
    ID_PROOF_DIR,
    PAYMENTS_JSONL,
    SUBMISSIONS_CSV,
    SUBMISSIONS_JSONL,
    ensure_dirs,
)

logger = logging.getLogger(__name__)

# Appends happen from the request thread; a lock keeps two simultaneous
# submissions from interleaving half-written lines into the same file.
_write_lock = threading.Lock()

CSV_COLUMNS: list[str] = [
    "reference",
    "submitted_at",
    "trainer_name",
    "mobile",
    "whatsapp",
    "email",
    "id_type",
    "id_number",
    "address",
    "emergency_contact_name",
    "emergency_contact_mobile",
    "client_count",
    "monthly_fee_inr",
    "clients",
    "outside_hours_informed",
    "outside_hours_approved_by",
    "outside_hours_approval_mode",
    "outside_hours_note",
    "committee_approval_reference",
    "declaration_signature",
    "declaration_place",
    "id_proof_filename",
    "all_rules_acknowledged",
    "payment_upi_reference",
    "payment_reported_at",
]


def _csv_row(record: dict[str, Any]) -> dict[str, Any]:
    clients = "; ".join(
        f"{c['name']} (Flat {c['flat_number']}) {c['slot_label']}"
        for c in record.get("clients", [])
    )
    acknowledgements = record.get("acknowledgements", {})
    row = {key: record.get(key, "") for key in CSV_COLUMNS}
    row["clients"] = clients
    row["outside_hours_informed"] = "Yes" if record.get("outside_hours_informed") else "No"
    row["all_rules_acknowledged"] = (
        "Yes" if all(acknowledgements.get(rule.key) for rule in RULES) else "No"
    )
    payment = record.get("payment") or {}
    row["payment_upi_reference"] = payment.get("upi_reference", "")
    row["payment_reported_at"] = payment.get("reported_at", "")
    return row


def save_submission(submission: Submission) -> dict[str, Any]:
    """Append the submission to both files. Returns the stored record."""
    ensure_dirs()
    record = submission.as_dict()

    with _write_lock:
        with open(SUBMISSIONS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, ensure_ascii=False) + "\n")

        write_header = not SUBMISSIONS_CSV.exists() or SUBMISSIONS_CSV.stat().st_size == 0
        with open(SUBMISSIONS_CSV, "a", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=CSV_COLUMNS, extrasaction="ignore")
            if write_header:
                writer.writeheader()
            writer.writerow(_csv_row(record))

    logger.info(
        "Gym form: stored submission %s from %s (%d clients)",
        submission.reference, submission.trainer_name, submission.client_count,
    )
    return record


def save_id_proof(reference: str, filename: str, content: bytes) -> Path:
    """Store an uploaded ID proof next to the submissions, named by reference."""
    ensure_dirs()
    suffix = Path(filename).suffix.lower()[:10] or ".bin"
    destination = ID_PROOF_DIR / f"{reference}{suffix}"
    destination.write_bytes(content)
    return destination


def record_payment(reference: str, upi_reference: str, amount_inr: int) -> dict[str, Any]:
    """Note that a trainer says they have paid.

    Written to its own append-only file rather than editing the submission in
    place: the registration record stays exactly as the trainer signed it, and
    a payment claim can never corrupt or truncate it. Reported, not verified —
    only the bank statement settles that.
    """
    ensure_dirs()
    entry = {
        "reference": reference,
        "upi_reference": upi_reference,
        "amount_inr": amount_inr,
        "reported_at": datetime.now(IST).isoformat(),
    }
    with _write_lock:
        with open(PAYMENTS_JSONL, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    logger.info("Gym form: payment reported for %s (UPI ref %s)", reference, upi_reference)
    return entry


def load_payments() -> dict[str, dict[str, Any]]:
    """Latest reported payment per registration reference."""
    payments: dict[str, dict[str, Any]] = {}
    if not PAYMENTS_JSONL.exists():
        return payments
    with open(PAYMENTS_JSONL, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            if entry.get("reference"):
                payments[entry["reference"]] = entry
    return payments


def iter_submissions() -> Iterator[dict[str, Any]]:
    """Yield stored submissions, newest last. Skips any corrupted line."""
    if not SUBMISSIONS_JSONL.exists():
        return
    with open(SUBMISSIONS_JSONL, "r", encoding="utf-8") as fh:
        for line_number, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                logger.warning(
                    "Gym form: skipping unreadable submission on line %d", line_number
                )


def load_submissions(limit: int | None = None) -> list[dict[str, Any]]:
    """Stored submissions, newest first, each with any reported payment."""
    payments = load_payments()
    records = list(iter_submissions())
    for record in records:
        record["payment"] = payments.get(record.get("reference", ""))
    records.reverse()
    return records[:limit] if limit else records
