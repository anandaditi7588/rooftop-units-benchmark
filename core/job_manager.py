"""
Background job manager.

The benchmarking run (scrape -> extract -> match -> build Excel/JSON) is
too slow to run inside a request/response cycle, so it runs in the
background while the frontend polls ``GET /api/job-status/{job_id}`` for
progress. This module is a small, dependency-free in-memory job store; for
a multi-process deployment this would be swapped for Redis/Celery, but the
public interface (``create_job``/``update_job``/``get_job``) would not need
to change, which is the point of keeping it isolated here.

Jobs run one at a time on a single dedicated worker thread, not one raw
thread per job. PDF extraction is CPU-bound (regex scanning, pdfplumber
table parsing), and Python's GIL means several of those running
concurrently don't parallelize — they fight over the same core badly enough
to starve the main process's ability to answer *any* HTTP request in a
reasonable time, including a plain page load. A serial queue means only one
CPU-bound benchmark ever runs at once; extra "start benchmark" clicks queue
up and run in order instead of piling onto the GIL simultaneously.

Cancellation: a job nobody's watching anymore shouldn't block every future
run until it happens to finish on its own. Python can't forcibly kill a
running thread safely, so cancellation is cooperative — ``request_cancel``
sets a per-job ``threading.Event``, and the pipeline checks
``is_cancelled(job_id)`` at natural breakpoints (between documents, between
competitors) and stops early when it's set. A queued-but-not-yet-started
job is cancelled immediately since the worker checks before running it.
"""
from __future__ import annotations

import queue
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Optional

from core.schemas import JobStatus


class JobCancelledError(Exception):
    """Raised inside a running job's target once cancellation was
    requested, so the worker can distinguish "stopped on purpose" from
    "crashed" and mark the job status accordingly."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobStatus] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._queue: "queue.Queue[tuple[str, Callable[[], None]]]" = queue.Queue()
        self._active_job_id: Optional[str] = None
        self._worker = threading.Thread(target=self._run_worker, daemon=True, name="benchmark-worker")
        self._worker.start()

    def create_job(self) -> str:
        job_id = uuid.uuid4().hex[:12]
        with self._lock:
            self._jobs[job_id] = JobStatus(
                job_id=job_id, status="queued", progress=0, message="Queued",
                started_at=_now_iso(),
            )
            self._cancel_events[job_id] = threading.Event()
        return job_id

    def update(
        self,
        job_id: str,
        *,
        status: Optional[str] = None,
        progress: Optional[int] = None,
        message: Optional[str] = None,
        stage: Optional[str] = None,
        error: Optional[str] = None,
        stats: Optional[dict] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = max(0, min(100, progress))
            if message is not None:
                job.message = message
            if stage is not None:
                job.stage = stage
            if error is not None:
                job.error = error
            if stats is not None:
                job.stats.update(stats)
            if status in ("completed", "failed", "cancelled"):
                job.finished_at = _now_iso()

    def get(self, job_id: str) -> Optional[JobStatus]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.model_copy(deep=True) if job else None

    def is_cancelled(self, job_id: str) -> bool:
        """Checked by the pipeline at natural breakpoints (between
        documents, between competitors) so a cancelled job actually stops
        instead of just being marked cancelled while still grinding away."""
        event = self._cancel_events.get(job_id)
        return event.is_set() if event else False

    def request_cancel(self, job_id: str) -> bool:
        """Ask a queued or running job to stop. Returns False if the job id
        is unknown or already finished (nothing to cancel)."""
        with self._lock:
            job = self._jobs.get(job_id)
            event = self._cancel_events.get(job_id)
            if job is None or event is None:
                return False
            if job.status in ("completed", "failed", "cancelled"):
                return False
            event.set()
            if job.status == "queued":
                # Not picked up by the worker yet — safe to mark cancelled
                # immediately; the worker checks the event before running it.
                job.status = "cancelled"
                job.message = "Cancelled before it started."
                job.finished_at = _now_iso()
            else:
                job.message = "Cancelling… (finishing the current document first)"
        return True

    def run_async(self, job_id: str, target: Callable[[], None]) -> None:
        """Enqueue ``target`` to run on the single benchmark worker thread,
        marking the job failed on any uncaught exception instead of losing
        it silently. If another job is already running, this one waits its
        turn — its status stays "queued" with a message saying so."""
        with self._lock:
            ahead = self._queue.qsize() + (1 if self._active_job_id else 0)
        if ahead:
            self.update(
                job_id, status="queued",
                message=f"Waiting for {ahead} job(s) ahead of this one to finish…",
            )
        self._queue.put((job_id, target))

    def _run_worker(self) -> None:
        while True:
            job_id, target = self._queue.get()
            if self.is_cancelled(job_id):
                # Cancelled while still queued — request_cancel() already
                # marked it, nothing left to do but skip running it.
                self._queue.task_done()
                continue
            with self._lock:
                self._active_job_id = job_id
            try:
                self.update(job_id, status="running", progress=1, message="Starting…")
                target()
            except JobCancelledError:
                self.update(job_id, status="cancelled", message="Cancelled.")
            except Exception as exc:  # noqa: BLE001 - top-level job guard, intentional
                self.update(job_id, status="failed", error=str(exc), message=f"Failed: {exc}")
            finally:
                with self._lock:
                    self._active_job_id = None
                self._queue.task_done()


job_manager = JobManager()
