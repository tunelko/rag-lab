"""Loopback-only web UI for the local RAG lab. No models are loaded on startup."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
import copy
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import secrets
import threading
import time
from typing import Annotated
from urllib.parse import quote, urlsplit
import urllib.request

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import rag

ALLOWED = {".pdf", ".txt", ".md", ".markdown"}
MAX_FILE = 20 * 1024**2


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=6000)
    model: str | None = Field(default=None, max_length=300)
    k: int = Field(default=4, ge=1, le=12)
    search_only: bool = False


class RagService:
    def __init__(self, config=None, documents=None):
        self.config = config or rag.Config()
        self.documents = (documents or rag.ROOT / "data" / "documents").resolve()
        self.documents.mkdir(parents=True, exist_ok=True)
        self.embeddings = None
        self.executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="rag-job")
        self.lock = threading.RLock()
        self.jobs = {}
        self.active = None
        self.closed = False

    def close(self):
        self.closed = True
        self.executor.shutdown(wait=True, cancel_futures=True)

    def snapshot(self, job_id):
        with self.lock:
            if job_id not in self.jobs:
                raise HTTPException(404, "La tarea ya no está disponible.")
            return copy.deepcopy(self.jobs[job_id])

    def update(self, job_id, **changes):
        with self.lock:
            self.jobs[job_id].update(changes)

    def start(self, kind, work):
        with self.lock:
            if self.active:
                raise HTTPException(409, "Hay una tarea en marcha. Espera a que termine.")
            job_id = secrets.token_hex(12)
            self.jobs[job_id] = {"id": job_id, "kind": kind, "status": "queued", "phase": "En cola",
                                 "created_at": utc_now(), "answer": "", "sources": [], "metrics": {}}
            self.active = job_id
            while len(self.jobs) > 30:
                self.jobs.pop(next(iter(self.jobs)))
            self.executor.submit(self._run, job_id, work)
            return {"job_id": job_id}

    def _run(self, job_id, work):
        started = time.perf_counter()
        self.update(job_id, status="running", phase="Preparando")
        try:
            result = work(job_id) or {}
            self.update(job_id, **result, status="done", phase="Completado", elapsed_seconds=round(time.perf_counter() - started, 2))
        except Exception as exc:
            detail = str(exc)
            if hasattr(exc, "read"):
                try:
                    detail += ": " + exc.read().decode("utf-8")[:1000]
                except Exception:
                    pass
            self.update(job_id, status="error", phase="No se pudo completar", error=detail,
                        elapsed_seconds=round(time.perf_counter() - started, 2))
        finally:
            with self.lock:
                self.active = None

    def get_embeddings(self, download=False):
        if self.embeddings is None:
            self.embeddings = rag.make_embeddings(self.config, allow_download=download)
        return self.embeddings

    def prepare(self, job_id):
        self.update(job_id, phase="Preparando embeddings en CPU · primera descarga si hace falta")
        self.get_embeddings(download=True)
        return {"message": "Modelo de búsqueda preparado en CPU."}

    def index(self, job_id):
        if not self.list_documents():
            raise ValueError("Añade documentos PDF, TXT o Markdown antes de indexar.")
        rag.load_state(self.config)
        self.update(job_id, phase="Cargando embeddings en CPU")
        embeddings = self.get_embeddings(download=True)
        self.update(job_id, phase="Leyendo, fragmentando e indexando documentos en CPU")
        summary = rag.ingest(self.config, self.documents, embeddings)
        return {"summary": summary, "message": "Índice actualizado" if not summary["errors"] else "Indexado parcial: revisa los errores en la consola."}

    def list_documents(self):
        state = rag.load_state(self.config)
        items = []
        for path in sorted(self.documents.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED or not path.resolve().is_relative_to(self.documents):
                continue
            indexed = state["files"].get(str(path.resolve()), {})
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            items.append({"name": path.relative_to(self.documents).as_posix(), "bytes": path.stat().st_size,
                          "indexed": indexed.get("sha256") == digest, "chunks": len(indexed.get("ids", []))})
        return items

    def ollama_status(self):
        base = os.getenv("RAG_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        try:
            with urllib.request.urlopen(base + "/api/tags", timeout=3) as response:
                models = json.load(response).get("models", [])
            with urllib.request.urlopen(base + "/api/ps", timeout=3) as response:
                loaded = json.load(response).get("models", [])
            names = [item["name"] for item in models if not item["name"].endswith(":cloud") and not item.get("remote_host")]
            default = os.getenv("RAG_OLLAMA_MODEL", "gemma-4-12b-redteam-blackice:latest")
            return {"connected": True, "models": names, "default_model": default if default in names else (names[0] if names else ""),
                    "loaded": [{"name": item.get("name"), "vram_bytes": item.get("size_vram", 0)} for item in loaded]}
        except (OSError, ValueError) as exc:
            return {"connected": False, "models": [], "default_model": "", "loaded": [], "error": str(exc)}

    def status(self):
        state = rag.load_state(self.config)
        documents = self.list_documents()
        chunks = sum(len(file.get("ids", [])) for file in state["files"].values())
        return {"documents": documents, "document_count": len(documents), "chunk_count": chunks,
                "collection": self.config.collection, "embedding_model": self.config.embedding_model,
                "embedding_device": self.config.embedding_device, "embedding_loaded": self.embeddings is not None,
                "chunk_size": self.config.chunk_size, "chunk_overlap": self.config.chunk_overlap,
                "active_job": self.active, "ollama": self.ollama_status()}

    def query(self, job_id, query):
        started = time.perf_counter()
        if not (self.config.db / f"{self.config.collection}-manifest.json").exists():
            raise ValueError("Todavía no hay un índice. Añade e indexa documentos en Administración.")
        self.update(job_id, phase="Recuperando fragmentos relevantes en CPU")
        embeddings = self.get_embeddings()
        hits = rag.retrieve(self.config, query.question.strip(), embeddings, query.k)
        if not hits:
            return {"answer": "No hay fragmentos disponibles para responder. Revisa la biblioteca.", "sources": []}
        messages, sources = rag.build_messages(query.question.strip(), hits, self.config.context_chars)
        for source, (document, _) in zip(sources, hits):
            source["text"] = document.page_content
            path = Path(source.pop("source")).resolve()
            source["url"] = "/api/source?file=" + quote(path.relative_to(self.documents).as_posix(), safe="") if path.is_relative_to(self.documents) else None
        retrieval_seconds = round(time.perf_counter() - started, 2)
        self.update(job_id, sources=sources, metrics={"retrieval_seconds": retrieval_seconds})
        if query.search_only:
            return {"answer": "Fragmentos recuperados. No se ha solicitado una respuesta al modelo.", "sources": sources,
                    "metrics": {"retrieval_seconds": retrieval_seconds}}
        available = self.ollama_status()
        model = query.model or available["default_model"]
        if not available["connected"]:
            raise ValueError("Ollama no responde. Inicia ollama serve y vuelve a intentarlo.")
        if model not in available["models"]:
            raise ValueError("Selecciona un modelo local instalado en Ollama.")
        self.update(job_id, phase="Generando respuesta con Ollama", model=model)

        def on_chunk(piece):
            with self.lock:
                self.jobs[job_id]["answer"] += piece

        answer, metrics = rag.generate_ollama(messages, model, on_chunk=on_chunk)
        if not answer.strip():
            raise ValueError("El modelo no devolvió texto visible. Prueba otro modelo o aumenta RAG_MAX_NEW_TOKENS en .env.")
        return {"answer": answer, "sources": sources, "metrics": {**metrics, "retrieval_seconds": retrieval_seconds}}


def create_app(service=None):
    service = service or RagService()
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        yield
        service.close()

    app = FastAPI(title="RAG Local", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver"])
    app.state.service = service

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and urlsplit(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "Origen no permitido."}, status_code=403)
            if not secrets.compare_digest(request.headers.get("x-rag-token", ""), token):
                return JSONResponse({"detail": "Recarga esta página antes de continuar."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'self'"
        response.headers["Cache-Control"] = "no-store"
        return response

    app.mount("/assets", StaticFiles(directory=str(rag.ROOT / "dist")), name="assets")

    @app.get("/")
    @app.get("/admin")
    def page():
        return FileResponse(rag.ROOT / "dist" / "index.html")

    @app.get("/api/session")
    def session():
        return {"token": token}

    @app.get("/api/status")
    def status():
        try:
            return service.status()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @app.get("/api/jobs/{job_id}")
    def job(job_id: str):
        return service.snapshot(job_id)

    @app.post("/api/query", status_code=202)
    def query(body: Query):
        if not body.question.strip():
            raise HTTPException(422, "Escribe una pregunta.")
        if not (service.config.db / f"{service.config.collection}-manifest.json").exists():
            raise HTTPException(409, "Añade e indexa documentos en Administración antes de consultar.")
        return service.start("query", lambda job_id: service.query(job_id, body))

    @app.post("/api/admin/prepare", status_code=202)
    def prepare():
        return service.start("prepare", service.prepare)

    @app.post("/api/admin/index", status_code=202)
    def index():
        if not service.list_documents():
            raise HTTPException(409, "Añade documentos antes de indexar.")
        return service.start("index", service.index)

    @app.post("/api/admin/upload")
    async def upload(files: Annotated[list[UploadFile], File()], auto_index: Annotated[bool, Form()] = True):
        if service.active:
            raise HTTPException(409, "Espera a que termine la tarea actual antes de añadir archivos.")
        if not 1 <= len(files) <= 20:
            raise HTTPException(422, "Selecciona entre 1 y 20 documentos.")
        # Validate all names before saving any file. Existing documents are never overwritten.
        for file in files:
            name = file.filename or ""
            if Path(name).name != name or any(char in name for char in '\\/:*?"<>|') or Path(name).suffix.lower() not in ALLOWED:
                raise HTTPException(422, "Solo se admiten nombres de archivo PDF, TXT o Markdown, sin rutas.")
        saved = []
        total = 0
        payloads = []
        for file in files:
            content = await file.read(MAX_FILE + 1)
            await file.close()
            total += len(content)
            if len(content) > MAX_FILE or total > 100 * 1024**2:
                raise HTTPException(413, "Límite: 20 MB por documento y 100 MB por lote.")
            payloads.append((file.filename, content))
        for name, content in payloads:
            original = Path(name)
            for number in range(1, 10000):
                target = service.documents / (name if number == 1 else f"{original.stem}-{number}{original.suffix}")
                try:
                    with target.open("xb") as output:
                        output.write(content)
                    saved.append(target.name)
                    break
                except FileExistsError:
                    if target.is_file() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(content).digest():
                        saved.append(target.name)
                        break
                    continue
            else:
                raise HTTPException(409, "Demasiados documentos con el mismo nombre.")
        result = {"saved": saved}
        if auto_index:
            result.update(service.start("index", service.index))
        return result

    @app.get("/api/source")
    def source(file: str):
        path = (service.documents / file).resolve()
        if not path.is_relative_to(service.documents) or not path.is_file() or path.suffix.lower() not in ALLOWED:
            raise HTTPException(404, "Documento no disponible.")
        return FileResponse(path, filename=path.name, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    return app


app = create_app()
