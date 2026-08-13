"""Collecting the monthly amenity fee over UPI.

Deliberately gateway-free. A UPI intent link costs the society nothing, needs
no KYC and no merchant account, and opens PhonePe / Google Pay / Paytm with the
payee, amount and reference already filled in — the trainer only approves.

The trade-off is honest and unavoidable: **UPI tells the server nothing**.
There is no callback, so the form cannot know whether money actually moved. It
therefore asks the trainer for the UPI reference number after paying, tells the
office, and leaves the final tick to whoever reads the bank statement. Anything
that claims more certainty than that would be lying to the office.
"""
from __future__ import annotations

import re
from urllib.parse import quote

# UPI reference numbers (UTR/RRN) are 12 digits from most banks, but apps
# surface all sorts of ids, so this stays permissive and only guards against
# junk and overlong input.
UPI_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 \-/]{5,39}$")

# A UPI ID looks like name@bank.
UPI_ID_RE = re.compile(r"^[A-Za-z0-9.\-_]{2,64}@[A-Za-z][A-Za-z0-9.\-]{1,63}$")


def build_upi_uri(
    *, upi_id: str, payee_name: str, amount: int, reference: str, note: str = ""
) -> str:
    """A ``upi://pay`` intent link.

    Tapping it on a phone opens the UPI app chooser with everything filled in.
    The same string encoded as a QR is what makes this work from a desktop or
    a second device.
    """
    transaction_note = note or f"Gym amenity fee {reference}"
    params = [
        ("pa", upi_id),
        ("pn", payee_name),
        ("am", f"{amount}.00"),
        ("cu", "INR"),
        ("tn", transaction_note),
        # Merchant-side reference, so the payment is traceable to the
        # registration in the bank statement narration where apps pass it on.
        ("tr", reference.replace("-", "")[:35]),
    ]
    query = "&".join(f"{key}={quote(str(value), safe='')}" for key, value in params)
    return f"upi://pay?{query}"


def is_valid_upi_id(value: str) -> bool:
    return bool(UPI_ID_RE.fullmatch((value or "").strip()))


def clean_upi_reference(value: str) -> str | None:
    """Normalise a trainer-entered UPI reference, or None when it is junk."""
    cleaned = " ".join((value or "").split())
    return cleaned if UPI_REFERENCE_RE.fullmatch(cleaned) else None
