"""One background worker that reads uploaded PDFs one after another (OCR is CPU-heavy)."""
from __future__ import annotations

import queue
import threading
import traceback
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class Job:
    id: int
    filename: str
    status: str = "queued"  # queued | running | done | error
    page: int = 0
    total: int = 0
    message: str = ""
    started: datetime = field(default_factory=datetime.now)


class JobManager:
    def __init__(self, inline: bool = False):
        self.jobs: dict[int, Job] = {}
        self._next = 1
        self._inline = inline  # run on the calling thread (tests)
        self._q: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        if not inline:
            threading.Thread(target=self._worker, daemon=True).start()

    def submit(self, filename: str, fn) -> Job:
        """`fn(job)` does the work and updates job.page / job.total."""
        with self._lock:
            job = Job(self._next, filename)
            self._next += 1
            self.jobs[job.id] = job
        if self._inline:
            self._run(job, fn)
        else:
            self._q.put((job, fn))
        return job

    def _run(self, job: Job, fn) -> None:
        job.status = "running"
        try:
            job.message = fn(job) or ""
            job.status = "done"
        except Exception as e:  # noqa: BLE001 - shown to the user, details in the log
            job.status, job.message = "error", f"{type(e).__name__}: {e}"
            traceback.print_exc()

    def _worker(self) -> None:
        while True:
            job, fn = self._q.get()
            self._run(job, fn)

    def recent(self, n: int = 10) -> list[Job]:
        return sorted(self.jobs.values(), key=lambda j: -j.id)[:n]
