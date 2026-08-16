"""The page a QR scan lands on: pick a form, then fill it.

The society has two online forms — personal trainer registration and amenity
booking — but only one notice board and one code worth printing. This package
is the thin front door: a chooser at ``/``, the two forms mounted beneath it,
and the QR/poster pages that point at the chooser rather than at either form.
"""
from __future__ import annotations

from portal.web import portal_app

__all__ = ["portal_app"]
