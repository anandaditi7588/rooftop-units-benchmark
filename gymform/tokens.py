"""Signed links that let the office decide straight from the email.

The office should not have to find a password to approve a trainer, so the
notification email carries Approve and Reject links. Those links are public
URLs, which means the only thing standing between them and a stranger is the
signature — so it is an HMAC over the exact registration and the exact
decision, compared in constant time. A link that approves SB-PT-1 cannot be
edited into one that approves SB-PT-2, or flipped from reject to approve.

The secret is ``GYM_DECISION_SECRET`` when set, otherwise the office password
(which every deployment already has). Deriving it from an existing secret
means one less thing to configure, and rotating the office password
invalidates outstanding links — which is the behaviour you want when a
password is rotated because someone left.
"""
from __future__ import annotations

import hashlib
import hmac
import os


def _secret() -> str:
    return (
        os.getenv("GYM_DECISION_SECRET")
        or os.getenv("GYM_ADMIN_PASSWORD")
        or os.getenv("RTU_AUTH_PASSWORD")
        or ""
    )


def signing_available() -> bool:
    """False when nothing can be signed, so links must be left out entirely."""
    return bool(_secret())


def make_token(reference: str, decision: str) -> str:
    """A short HMAC binding one decision to one registration."""
    message = f"{reference}:{decision}".encode("utf-8")
    digest = hmac.new(_secret().encode("utf-8"), message, hashlib.sha256)
    # 20 hex characters is 80 bits — far beyond guessing, and short enough to
    # keep the URL readable if someone forwards it in a chat.
    return digest.hexdigest()[:20]


def verify_token(reference: str, decision: str, token: str) -> bool:
    if not signing_available() or not token:
        return False
    return hmac.compare_digest(make_token(reference, decision), token)
