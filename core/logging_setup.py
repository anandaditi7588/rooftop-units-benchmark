"""Application-wide logging configuration.

Writes to /logs/app.log (rotating) and to the console. Every module gets its
logger via ``logging.getLogger(__name__)`` after :func:`configure_logging`
has run once at process start (called from ``app.py``).
"""
from __future__ import annotations

import logging
import sys
from logging.handlers import RotatingFileHandler

from core.config import LOGS_DIR

_CONFIGURED = False


def configure_logging(level: int = logging.INFO) -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_file = LOGS_DIR / "app.log"
    formatter = logging.Formatter(
        fmt="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = RotatingFileHandler(
        log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(file_handler)
    root.addHandler(console_handler)

    # Quiet down noisy third-party loggers unless something goes wrong.
    for noisy in ("urllib3", "httpx", "PIL", "pdfminer", "fontTools"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True


def get_run_logger(job_id: str) -> logging.Logger:
    """A per-job logger that ALSO writes to logs/jobs/<job_id>.log.

    This gives each benchmarking run its own audit trail (downloaded files,
    search URLs, matched/missing parameters, errors, timings) as required by
    the spec, without cluttering the main app.log.
    """
    job_log_dir = LOGS_DIR / "jobs"
    job_log_dir.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(f"job.{job_id}")
    if logger.handlers:
        return logger  # already set up for this job id

    handler = logging.FileHandler(job_log_dir / f"{job_id}.log", encoding="utf-8")
    handler.setFormatter(
        logging.Formatter(
            fmt="%(asctime)s | %(levelname)-8s | %(message)s", datefmt="%H:%M:%S"
        )
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = True  # also goes to app.log via root handlers
    return logger
