"""
HTTP Basic authentication gate for the whole app.

Off by default (local development stays frictionless — every earlier run in
this project worked with no login). It switches on automatically the moment
both ``RTU_AUTH_USERNAME`` and ``RTU_AUTH_PASSWORD`` are set in the
environment, which is exactly what a deployment (Render, Railway, etc.)
should do before the app is reachable from the public internet — this tool
has no per-user accounts, so a single shared credential is the appropriate
amount of protection for "don't let a random stranger who finds the URL
start scraping jobs or read your uploaded files."
"""
from __future__ import annotations

import os
import secrets

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials

_security = HTTPBasic(auto_error=False)


def require_auth(credentials: HTTPBasicCredentials | None = Depends(_security)) -> None:
    expected_user = os.getenv("RTU_AUTH_USERNAME")
    expected_pass = os.getenv("RTU_AUTH_PASSWORD")

    if not expected_user or not expected_pass:
        return  # Auth disabled: no credentials configured (local/dev default).

    valid = credentials is not None and (
        secrets.compare_digest(credentials.username, expected_user)
        and secrets.compare_digest(credentials.password, expected_pass)
    )
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Basic"},
        )
