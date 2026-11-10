"""Additive job observation, structured errors and cooperative cancellation."""
from concurrent.futures import ThreadPoolExecutor
import copy
from datetime import datetime, timezone
import secrets
import threading
import time

from fastapi import HTTPException


def now():
    return datetime.now(timezone.utc).isoformat()


class JobFailure(RuntimeError):
    def __init__(self, code, message, detail=""):
        super().__init__(message)
        self.code, self.message, self.detail = code, message, detail


def error_info(exc):
    if isinstance(exc, JobFailure):
        return {"code": exc.code, "message": exc.message, "detail": exc.detail}
    return {"code": "UNEXPECTED", "message": "Error inesperado al procesar el archivo.", "detail": str(exc)}


class JobRuntime:
    def __init__(self):
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-job")
        self.lock = threading.RLock()
        self.jobs = {}
        self.cancels = {}
        self.active = None
        self.closed = False
        self.activity = []

    def close(self):
        with self.lock:
            self.closed = True
            for event in self.cancels.values():
                event.set()
        self.executor.shutdown(wait=True, cancel_futures=True)

    def snapshot(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, "La tarea ya no está disponible.")
            return copy.deepcopy(self.jobs[job_id])

    def update(self, job_id, **changes):
        with self.lock:
            old = self.jobs[job_id]
            if changes.get("phase") and changes["phase"] != old.get("phase"):
                self.activity.append({"time": now(), "text": changes["phase"], "document": old.get("document_name", "")})
                self.activity = self.activity[-6:]
            old.update(changes)
            old["updated_at"] = now()

    def start(self, kind, work, **metadata):
        with self.lock:
            if self.closed:
                raise HTTPException(503, "El servidor se está cerrando.")
            active_count = sum(job["status"] in {"queued", "running"} for job in self.jobs.values())
            if active_count >= 100:
                raise HTTPException(429, "La cola del servidor está llena. Espera antes de añadir más documentos.")
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = {"id": job_id, "kind": kind, "status": "queued", "state": "queued",
                "phase": "En cola", "stage": "upload" if kind == "index" else kind,
                "progress": 0 if kind == "index" else None, "stage_progress": 0,
                "chunks": 0, "created_at": now(), "updated_at": now(), "answer": "", "sources": [],
                "metrics": {}, "cancel_requested": False, "cancellable": True, **metadata}
            self.cancels[job_id] = threading.Event()
            self.executor.submit(self._run, job_id, work)
            return {"job_id": job_id}

    def cancelled(self, job_id):
        return self.cancels[job_id].is_set()

    def check_cancel(self, job_id):
        if self.cancelled(job_id):
            raise InterruptedError("Trabajo cancelado.")

    def cancel(self, job_id):
        with self.lock:
            job = self.snapshot(job_id)
            if job["status"] not in {"queued", "running"}:
                return job
            if job["stage"] == "indexing" and job["status"] == "running":
                raise HTTPException(409, "La escritura del índice ya ha empezado. Espera a que termine.")
            self.cancels[job_id].set()
            self.update(job_id, cancel_requested=True, phase="Cancelando…", cancellable=False)
            if job["status"] == "queued":
                self.update(job_id, status="cancelled", state="cancelled", phase="Cancelado")
            return self.snapshot(job_id)

    def list_jobs(self, active=False):
        with self.lock:
            jobs = list(self.jobs.values())
            running = [copy.deepcopy(job) for job in jobs if job["status"] in {"queued", "running"}]
            recent = [copy.deepcopy(job) for job in jobs if job["status"] not in {"queued", "running"}][-30:]
            return {"jobs": running if active else copy.deepcopy(jobs), "recent": recent, "active_job": self.active}

    def _run(self, job_id, work):
        started = time.perf_counter()
        with self.lock:
            if self.jobs[job_id]["status"] == "cancelled":
                return
            self.active = job_id
        self.update(job_id, status="running", state="processing", phase="Preparando")
        try:
            self.check_cancel(job_id)
            result = work(job_id) or {}
            self.check_cancel(job_id)
            self.update(job_id, **result, status="done", state="done", phase="Completado", progress=100,
                        stage_progress=100, cancellable=False, elapsed_seconds=round(time.perf_counter() - started, 2))
        except InterruptedError:
            self.update(job_id, status="cancelled", state="cancelled", phase="Cancelado", cancellable=False,
                        elapsed_seconds=round(time.perf_counter() - started, 2))
        except Exception as exc:
            info = error_info(exc)
            self.update(job_id, status="error", state="error", phase="No se pudo completar",
                        error=info["message"], error_info=info, cancellable=False,
                        elapsed_seconds=round(time.perf_counter() - started, 2))
        finally:
            with self.lock:
                self.active = None
                # Keep active jobs and the last 200 terminal records for browser recovery.
                terminal = [key for key, job in self.jobs.items() if job["status"] not in {"queued", "running"}]
                for key in terminal[:-200]:
                    self.jobs.pop(key, None)
                    self.cancels.pop(key, None)
