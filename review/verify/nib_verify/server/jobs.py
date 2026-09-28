"""Long-running work, and how the interface watches it.

A run takes seconds to minutes, so it cannot happen inside a request. The
registry below starts it on a thread and keeps the last N lines of progress for
the browser to poll.

Two decisions worth stating.

**Polling, not streaming.** Server-sent events would be tidier, but the review
page is served from the same process on loopback and a run is short. Polling has
no reconnection semantics to get wrong, works through every proxy that ever gets
put in front of a dashboard, and can be tested without a live socket.

**A failure is data, not an exception.** Whatever the work raises is caught,
recorded on the job, and handed back to the caller as a state. The interface
never sees a 500 because a run went wrong; it sees a job that says ``failed`` and
a reason. That is the same rule the executor follows one level down.
"""

from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

UTC = timezone.utc

RUNNING = "running"
DONE = "done"
FAILED = "failed"

#: how many progress lines a single job keeps for a viewer that arrives late
MAX_LINES = 500

#: how many finished jobs stay queryable
MAX_JOBS = 50


@dataclass
class Job:
    """One unit of background work."""

    id: str
    kind: str
    label: str
    state: str = RUNNING
    started: datetime = field(default_factory=lambda: datetime.now(UTC))
    finished: datetime | None = None
    lines: list[str] = field(default_factory=list)
    result: dict | None = None
    error: str | None = None
    detail: str | None = None

    @property
    def elapsed_seconds(self) -> float:
        end = self.finished or datetime.now(UTC)
        return (end - self.started).total_seconds()

    def view(self, after: int = 0) -> dict:
        """What a polling client needs: new lines, and the state."""
        after = max(0, min(after, len(self.lines)))
        return {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "state": self.state,
            "started": self.started.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "finished": self.finished.strftime("%Y-%m-%dT%H:%M:%SZ") if self.finished else None,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "lines": self.lines[after:],
            "cursor": len(self.lines),
            "dropped": max(0, len(self.lines) - MAX_LINES),
            "result": self.result,
            "error": self.error,
            "detail": self.detail,
        }


class JobRegistry:
    """The jobs this process has run, newest first."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()

    def submit(
        self,
        kind: str,
        label: str,
        work: Callable[[Callable[[str], None]], dict | None],
        *,
        job_id: str | None = None,
    ) -> Job:
        """Run ``work`` on a thread.  It receives a ``report(line)`` callback."""
        job = Job(id=job_id or uuid.uuid4().hex[:12], kind=kind, label=label)

        with self._lock:
            self._prune_locked()
            self._jobs[job.id] = job

        def report(line: str) -> None:
            with self._lock:
                job.lines.append(str(line))
                if len(job.lines) > MAX_LINES * 2:
                    del job.lines[: len(job.lines) - MAX_LINES]

        def runner() -> None:
            try:
                job.result = work(report) or {}
                job.state = DONE
            except BaseException as exc:  # noqa: BLE001 - a job never crashes us
                job.state = FAILED
                job.error = f"{type(exc).__name__}: {exc}"
                job.detail = traceback.format_exc(limit=6)
                report(f"failed: {job.error}")
            finally:
                with self._lock:
                    job.finished = datetime.now(UTC)

        threading.Thread(target=runner, name=f"nib-verify-{kind}-{job.id}",
                         daemon=True).start()
        return job

    def get(self, job_id: str) -> Job:
        with self._lock:
            job = self._jobs.get(job_id)
        if job is None:
            raise KeyError(job_id)
        return job

    def view(self, job_id: str, after: int = 0) -> dict:
        return self.get(job_id).view(after)

    def list(self) -> list[dict]:
        with self._lock:
            jobs = list(self._jobs.values())
        jobs.sort(key=lambda j: j.started, reverse=True)
        return [
            {k: v for k, v in j.view().items() if k not in ("lines", "detail")}
            for j in jobs
        ]

    def busy(self, kind: str | None = None) -> list[Job]:
        with self._lock:
            jobs = list(self._jobs.values())
        return [j for j in jobs if j.state == RUNNING and (kind is None or j.kind == kind)]

    def wait(self, job_id: str, timeout: float = 60.0) -> Job:
        """Block until a job finishes.  For tests, and for the CLI's sake."""
        import time

        deadline = time.monotonic() + timeout
        job = self.get(job_id)
        while job.state == RUNNING and time.monotonic() < deadline:
            time.sleep(0.01)
        return job

    def _prune_locked(self) -> None:
        finished = [(j.started, jid) for jid, j in self._jobs.items() if j.state != RUNNING]
        if len(self._jobs) <= MAX_JOBS:
            return
        finished.sort()
        for _, jid in finished[: max(0, len(self._jobs) - MAX_JOBS)]:
            self._jobs.pop(jid, None)
