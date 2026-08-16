"""Silicon Bay Society — amenity (hall / lawn) booking form.

A second QR-scannable form alongside the trainer registration, built from the
society's "Rule and Regulation — For personal use of Society's amenities by
Resident" document.

It reuses ``gymform``'s infrastructure rather than copying it: the same
settings snapshot, the same email and WhatsApp senders, the same HMAC-signed
approve/reject links and the same Google Sheet archive. What is specific to
bookings lives here — the venues and charges, the booking model, the
double-booking check, and the pages.
"""
from __future__ import annotations

from hallform.web import hall_app

__all__ = ["hall_app"]
