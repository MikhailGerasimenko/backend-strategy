from __future__ import annotations

import threading
import traceback
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable


class JobStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class JobCancelled(Exception):
    """Работа остановлена пользователем."""

    is_cancellation = True


@dataclass
class Job:
    id: str
    kind: str
    status: JobStatus = JobStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str | None = None
    docx_path: str | None = None
    json_path: str | None = None
    cancel_event: threading.Event = field(default_factory=threading.Event)

    def append_log(self, message: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {message}"
        self.logs.append(line)

    def request_cancel(self) -> bool:
        """Помечает джоб на отмену. True, если запрос принят."""
        if self.status in (JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED):
            return False
        self.cancel_event.set()
        self.append_log("Получен запрос на остановку…")
        return True

    def is_cancel_requested(self) -> bool:
        return self.cancel_event.is_set()

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "status": self.status.value,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
            "logs": self.logs,
            "result": self.result,
            "error": self.error,
            "docx_path": self.docx_path,
            "json_path": self.json_path,
            "docx_filename": (
                self.result.get("docx_filename") if self.result else None
            ),
            "json_filename": (
                self.result.get("json_filename") if self.result else None
            ),
            "cancel_requested": self.is_cancel_requested(),
        }


def _is_cancellation(exc: BaseException) -> bool:
    if isinstance(exc, JobCancelled):
        return True
    return bool(getattr(exc, "is_cancellation", False))


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        self._worker_lock = threading.Lock()

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> Job | None:
        job = self.get(job_id)
        if not job:
            return None
        job.request_cancel()
        return job

    def submit(self, kind: str, target: Callable[[Job], None]) -> Job:
        job = Job(id=str(uuid.uuid4()), kind=kind)
        with self._lock:
            self._jobs[job.id] = job

        def runner() -> None:
            with self._worker_lock:
                job.status = JobStatus.RUNNING
                try:
                    target(job)
                    if job.is_cancel_requested():
                        job.status = JobStatus.CANCELLED
                        job.error = job.error or "Генерация остановлена пользователем"
                        job.append_log("Остановлено.")
                    else:
                        job.status = JobStatus.COMPLETED
                except Exception as exc:  # noqa: BLE001
                    if _is_cancellation(exc) or job.is_cancel_requested():
                        job.status = JobStatus.CANCELLED
                        job.error = str(exc) or "Генерация остановлена пользователем"
                        job.append_log(f"Остановлено: {job.error}")
                    else:
                        job.status = JobStatus.FAILED
                        job.error = str(exc)
                        job.append_log(f"Ошибка: {exc}")
                        job.append_log(traceback.format_exc())
                finally:
                    job.finished_at = datetime.now().isoformat(timespec="seconds")

        thread = threading.Thread(target=runner, daemon=True)
        thread.start()
        return job


job_manager = JobManager()
