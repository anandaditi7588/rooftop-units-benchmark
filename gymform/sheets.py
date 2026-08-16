"""Mirror every confirmed record into a Google Sheet.

The society wants a running history they can open like a spreadsheet, and the
server's own disk is wiped on every redeploy of a free instance — so the sheet
is the durable archive, not a convenience.

Deliberately no Google API client, no service account, no OAuth. The society
creates a Sheet, pastes a short Apps Script, deploys it as a web app, and the
form POSTs JSON to that URL. That means no credentials to store, no library to
install, and the sheet belongs to them from the start. Setup is in
docs/GYM_FORM.md.

Like every other channel here, a failure is reported and never allowed to lose
a submission: the record is already on disk before this is called.
"""
from __future__ import annotations

import logging

import requests

from gymform.settings import GymFormSettings

logger = logging.getLogger(__name__)


def push_row(
    settings: GymFormSettings, sheet: str, row: dict, key: str = "reference"
) -> tuple[bool, str]:
    """Write one record into the society's sheet.

    ``sheet`` names the tab — "Trainers" or "Hall bookings" — so both forms
    share one spreadsheet without colliding.

    ``key`` names the column the script matches on. The same record is written
    again whenever its status changes (a payment reported, an approval given),
    so the script *updates* the row with that reference rather than appending a
    second one. The office wants one line per registration showing where it
    stands, not an audit trail they have to read backwards — the append-only
    JSONL on the server is the audit trail.
    """
    url = settings.sheets_webhook_url
    if not url:
        return False, "No Google Sheet configured (GYM_SHEETS_WEBHOOK_URL)."

    try:
        response = requests.post(
            url,
            json={"sheet": sheet, "key": key, "row": row},
            timeout=settings.request_timeout,
            # Apps Script answers the POST with a redirect to its result page.
            allow_redirects=True,
        )
    except requests.RequestException as exc:
        logger.warning("Sheets: request failed: %s", exc)
        return False, f"Could not reach the Google Sheet: {exc}"

    if response.ok:
        logger.info("Sheets: wrote a row to %s", sheet)
        return True, f"Saved to the '{sheet}' sheet."

    detail = response.text[:200]
    logger.warning("Sheets: returned %s: %s", response.status_code, detail)
    hint = ""
    if response.status_code in (401, 403):
        hint = (
            " The Apps Script deployment must be set to run as you, with access "
            "for 'Anyone'."
        )
    return False, f"Google Sheet returned HTTP {response.status_code}: {detail}{hint}"
