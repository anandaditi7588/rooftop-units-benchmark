"""Silicon Bay Society — personal trainer gym registration form.

A self-contained sub-application: a public, QR-scannable online form that
captures everything the society's "Gym Rules for Personal Trainers" document
asks a trainer to submit, records their acknowledgement of each rule, and
notifies the society office by email (and WhatsApp, when a provider is
configured) the moment a trainer submits.

It is deliberately independent of the rest of this repository — it only
borrows the HTTP server it is mounted on. Everything else (templates, static
assets, storage, notifications, settings) lives inside this package.
"""
from __future__ import annotations

from gymform.web import gym_app

__all__ = ["gym_app"]
