"""Background job manager with live event streaming for DubCut Studio.

Each job runs in its own thread. Engine code (vendor modules) emits progress through the
streamlit shim, which we bind to the job's event queue. The server exposes those events as SSE.
"""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import shims.streamlit as st_shim  # the shim package (on sys.path)


@dataclass
class Job:
    id: str
    kind: str
    status: str = "running"  # running | done | error | cancelled
    progress: float = 0.0
    result: Any = None
    error: Optional[str] = None
    created: float = field(default_factory=time.time)
    finished: Optional[float] = None  # set when status becomes terminal (history + ETA)
    cancel: threading.Event = field(default_factory=threading.Event)
    log: List[dict] = field(default_factory=list)
    cancel_cleanup: List[Callable[[], None]] = field(default_factory=list)
    seq: int = 0  # monotonic event counter — SSE subscribers stream by seq cursor


class JobManager:
    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._lock = threading.Lock()

    def get(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    def list(self) -> List[dict]:
        # Newest first, so the UI shows the active/most-recent jobs at the top.
        jobs = sorted(self._jobs.values(), key=lambda j: j.created, reverse=True)
        return [self._summary(j) for j in jobs]

    @staticmethod
    def _summary(j: Job) -> dict:
        return {
            "id": j.id,
            "kind": j.kind,
            "status": j.status,
            "progress": j.progress,
            "error": j.error,
            "created": j.created,
            "finished": j.finished,
        }

    _MAX_JOBS = 80  # keep recent history bounded so memory doesn't grow forever

    def _evict_old(self) -> None:
        if len(self._jobs) <= self._MAX_JOBS:
            return
        # Drop the oldest FINISHED jobs first; never evict a still-running one.
        finished = sorted(
            (j for j in self._jobs.values() if j.status != "running"),
            key=lambda j: j.finished or j.created,
        )
        for j in finished[: len(self._jobs) - self._MAX_JOBS]:
            self._jobs.pop(j.id, None)

    def _push(self, job: Job, event: dict) -> None:
        job.seq += 1
        event = {"seq": job.seq, "ts": time.time(), **event}
        job.log.append(event)
        if len(job.log) > 1000:
            job.log = job.log[-1000:]

    def start(self, kind: str, target: Callable[["JobContext"], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind)
        with self._lock:
            self._jobs[job.id] = job
            self._evict_old()

        def _run() -> None:
            ctx = JobContext(self, job)

            def _sink(level: str, message: str) -> None:
                if level == "progress":
                    self._push(job, {"type": "progress", "message": message})
                else:
                    self._push(job, {"type": "log", "level": level, "message": message})

            st_shim.bind_sink(_sink)
            try:
                result = target(ctx)
                if job.cancel.is_set() or job.status == "cancelled":
                    raise st_shim.StopException()
                job.result = result
                job.status = "done"
                job.progress = 1.0
                self._push(job, {"type": "done", "result": _jsonable(result)})
            except st_shim.StopException:
                job.status = "cancelled"
                if not any(e.get("type") == "cancelled" for e in job.log[-3:]):
                    self._push(job, {"type": "cancelled"})
            except Exception as exc:  # noqa: BLE001
                job.status = "error"
                job.error = str(exc)
                self._push(job, {"type": "error", "message": str(exc),
                                 "trace": traceback.format_exc()})
            finally:
                job.finished = time.time()
                st_shim.bind_sink(None)
                self._push(job, {"type": "end", "status": job.status})

        threading.Thread(target=_run, daemon=True, name=f"job-{kind}-{job.id}").start()
        return job

    def cancel(self, job_id: str) -> Optional[Job]:
        job = self.get(job_id)
        if not job:
            return None
        job.cancel.set()
        # Pipelines may register small, idempotent cleanups (partial audio, worker
        # request files). Run them here, not only when their worker thread wakes up.
        for cleanup in list(job.cancel_cleanup):
            try:
                cleanup()
            except Exception:
                pass
        if job.status == "running":
            job.status = "cancelled"
            job.finished = time.time()
            self._push(job, {"type": "cancelled"})
            self._push(job, {"type": "end", "status": job.status})
        return job


class JobContext:
    """Passed to job targets. Provides progress + log helpers and cancellation."""

    def __init__(self, manager: JobManager, job: Job) -> None:
        self._m = manager
        self.job = job

    @property
    def cancelled(self) -> bool:
        return self.job.cancel.is_set()

    def check_cancel(self) -> None:
        if self.cancelled:
            raise st_shim.StopException()

    def on_cancel(self, cleanup: Callable[[], None]) -> None:
        """Register an idempotent cleanup that also runs on an immediate cancel."""
        self.job.cancel_cleanup.append(cleanup)
        if self.cancelled:
            try:
                cleanup()
            except Exception:
                pass

    def log(self, message: str, level: str = "info") -> None:
        self._m._push(self.job, {"type": "log", "level": level, "message": message})

    def step(self, message: str) -> None:
        # Every phase boundary is a cancellation checkpoint — so "Przerwij" aborts at
        # the next step in any module without each pipeline needing explicit checks.
        self.check_cancel()
        self._m._push(self.job, {"type": "log", "level": "step", "message": message})

    def progress(self, value: float, message: str = "") -> None:
        # Same for progress ticks: pipelines (and vendor engines that report progress
        # through ctx) hit this often, so cancel becomes responsive even mid-step.
        self.check_cancel()
        self.job.progress = max(0.0, min(1.0, value))
        self._m._push(self.job, {"type": "progress", "value": self.job.progress,
                                 "message": message})


def _jsonable(value: Any) -> Any:
    try:
        import json
        json.dumps(value)
        return value
    except Exception:
        return str(value)


manager = JobManager()
