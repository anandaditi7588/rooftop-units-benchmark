"""Run the society's online forms on their own.

This is the deployment entry point (see ``render.yaml``). It serves the
chooser at the site root, with the two forms mounted beneath it:

    /            the page a QR scan lands on
    /hall/       amenity booking
    /trainer/    personal trainer registration
    /health      liveness, for the uptime pinger

Start it with:

    uvicorn gymform.standalone:app --host 0.0.0.0 --port 8000

or simply:

    python -m gymform.standalone

The module keeps its name because that is the path already configured on the
running service; what it serves grew from one form to two.
"""
from __future__ import annotations

import os

from portal.web import portal_app

app = portal_app


def main() -> None:
    import uvicorn

    uvicorn.run(
        "gymform.standalone:app",
        host=os.getenv("GYM_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT") or os.getenv("GYM_PORT") or 8000),
    )


if __name__ == "__main__":
    main()
