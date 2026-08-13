"""Run the trainer registration form on its own.

The form is normally mounted at ``/gym`` on the main application, but it has
no dependency on the rest of this repository — so it can also be deployed by
itself, on its own host, with:

    uvicorn gymform.standalone:app --host 0.0.0.0 --port 8000

or simply:

    python -m gymform.standalone

Here the form sits at the site root, so the QR code encodes ``https://host/``
rather than ``https://host/gym``.
"""
from __future__ import annotations

import os

from gymform.web import gym_app

app = gym_app


def main() -> None:
    import uvicorn

    uvicorn.run(
        "gymform.standalone:app",
        host=os.getenv("GYM_HOST", "0.0.0.0"),
        port=int(os.getenv("PORT") or os.getenv("GYM_PORT") or 8000),
    )


if __name__ == "__main__":
    main()
