"""Authentication for the submission-review pages.

Submissions contain trainers' mobile numbers, home addresses and government ID
numbers, so — unlike the public form — the review pages **fail closed**: when
no admin credentials are configured they return 503 rather than quietly
serving personal data to anyone who guesses the URL.

Credentials are read from ``GYM_ADMIN_USERNAME`` / ``GYM_ADMIN_PASSWORD``,
falling back to the app-wide ``RTU_AUTH_USERNAME`` / ``RTU_AUTH_PASSWORD`` so
a deployment that already set those does not need a second pair.
"""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_security = HTTPBasic(auto_error=False)


def _configured_credentials() -> tuple[str, str] | None:
    username = (os.getenv("GYM_ADMIN_USERNAME") or os.getenv("RTU_AUTH_USERNAME") or "").strip()
    password = (os.getenv("GYM_ADMIN_PASSWORD") or os.getenv("RTU_AUTH_PASSWORD") or "").strip()
    return (username, password) if username and password else None


def require_admin(
    credentials: HTTPBasicCredentials | None = Depends(_security),
) -> None:
    expected = _configured_credentials()
    if expected is None:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "Submission review is disabled because no admin login is configured. "
            "Set GYM_ADMIN_USERNAME and GYM_ADMIN_PASSWORD (or RTU_AUTH_USERNAME and "
            "RTU_AUTH_PASSWORD) on the server, then reload this page.",
        )

    expected_user, expected_pass = expected
    valid = credentials is not None and (
        secrets.compare_digest(credentials.username, expected_user)
        and secrets.compare_digest(credentials.password, expected_pass)
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": 'Basic realm="Gym form admin"'},
        )
