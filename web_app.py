"""Loopback-only web UI for the local RAG lab. No models are loaded on startup."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import secrets
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
from jobs_runtime import JobFailure, JobRuntime

ALLOWED = {".pdf", ".txt", ".md", ".markdown"}
MAX_FILE = 20 * 1024**2


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=6000)
    model: str | None = Field(default=None, max_length=300)
    k: int = Field(default=4, ge=1, le=12)
    search_only: bool = False


class IndexRequest(BaseModel):
    file: str | None = None


class RagService(JobRuntime):
    def __init__(self, config=None, documents=None):
        self.config = config or rag.Config()
        self.documents = (documents or rag.ROOT / "data" / "documents").resolve()
        self.documents.mkdir(parents=True, exist_ok=True)
        self.embeddings = None
        super().__init__()

    def get_embeddings(self, download=False):
        if self.embeddings is None:
            self.embeddings = rag.make_embeddings(self.config, allow_download=download)
        return self.embeddings

    def prepare(self, job_id):
        self.update(job_id, phase="Preparando embeddings en CPU · primera descarga si hace falta", stage="prepare", progress=None)
        try:
            self.get_embeddings(download=True)
        except Exception as exc:
            raise JobFailure("EMBEDDINGS_NOT_READY", "El modelo de búsqueda no está preparado.", str(exc)) from exc
        return {"message": "Modelo de búsqueda preparado en CPU."}

    def index(self, job_id, only_file=None):
        documents = self.list_documents()
        if only_file:
            documents = [document for document in documents if document["name"] == only_file]
        if not documents:
            raise ValueError("Añade documentos PDF, TXT o Markdown antes de indexar.")
        rag.load_state(self.config)
        self.update(job_id, phase="Cargando embeddings en CPU")
        self.update(job_id, stage="chunking", stage_progress=0, progress=0)
        try:
            embeddings = self.get_embeddings(download=True)
        except Exception as exc:
            raise JobFailure("EMBEDDINGS_NOT_READY", "El modelo de búsqueda no está preparado.", str(exc)) from exc
        finished = set()
        outcomes = {}

        def observe(**event):
            document = event["document"]
            stage, percent = event["stage"], event["stage_progress"]
            part = percent / 2 if stage == "chunking" else 50 + percent / 2
            if event.get("skipped") or event.get("complete") or stage == "error":
                record = {"name": document, "chunks": event.get("chunks", 0), "skipped": event.get("skipped", False)}
                if stage == "error":
                    record["error_info"] = {"code": "UNEXPECTED", "message": "Error inesperado al procesar el archivo.", "detail": event.get("error", "")}
                elif not record["chunks"]:
                    record["error_info"] = {"code": "NO_TEXT", "message": "No se ha podido extraer texto: parece un PDF escaneado.", "detail": "El documento no contiene texto extraíble. No hay OCR instalado."}
                outcomes[document] = record
                finished.add(document)
                overall = 100 * len(finished) / len(documents)
            else:
                overall = 100 * (len(finished) + part / 100) / len(documents)
            self.update(job_id, stage=stage, stage_progress=percent, progress=min(overall, 100),
                phase=("Fragmentando" if stage == "chunking" else "Indexando") + f" · {document}",
                current_document=document, chunks=event.get("chunks", 0), documents=list(outcomes.values()),
                cancellable=stage != "indexing")

        summary = rag.ingest(self.config, self.documents, embeddings,
            paths=[self.documents / document["name"] for document in documents], observer=observe,
            cancelled=lambda: self.cancelled(job_id))
        self.update(job_id, summary=summary, documents=list(outcomes.values()))
        failures = [item for item in outcomes.values() if item.get("error_info")]
        if failures:
            first = failures[0]["error_info"]
            raise JobFailure(first["code"], first["message"], first["detail"])
        return {"summary": summary, "documents": list(outcomes.values()), "message": "Índice actualizado"}

    def list_documents(self):
        state = rag.load_state(self.config)
        jobs = self.list_jobs()["jobs"]
        items = []
        for path in sorted(self.documents.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED or not path.resolve().is_relative_to(self.documents):
                continue
            indexed = state["files"].get(str(path.resolve()), {})
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            name = path.relative_to(self.documents).as_posix()
            ready = indexed.get("sha256") == digest and bool(indexed.get("ids"))
            related = [job for job in jobs if job.get("document_name") == name or job.get("current_document") == path.name]
            latest = related[-1] if related else {}
            error = latest.get("error_info") if latest.get("status") == "error" else None
            if indexed.get("sha256") == digest and not indexed.get("ids"):
                error = {"code": "NO_TEXT", "message": "Necesita OCR" if path.suffix.lower() == ".pdf" else "Sin texto extraíble", "detail": "No se han obtenido fragmentos."}
            processing = latest.get("status") in {"queued", "running"}
            items.append({"name": name, "bytes": path.stat().st_size, "indexed": ready,
                "chunks": len(indexed.get("ids", [])), "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "document_id": hashlib.sha256(name.encode()).hexdigest()[:24],
                "status": "processing" if processing else ("error" if error else ("indexed" if ready else "pending")),
                "stage": latest.get("stage"), "progress": latest.get("stage_progress"), "error_info": error})
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
                "active_job": self.active, "ollama": self.ollama_status(),
                "activity": self.activity[-6:], "jobs": self.list_jobs(active=True)["jobs"]}

    def query(self, job_id, query):
        started = time.perf_counter()
        if not (self.config.db / f"{self.config.collection}-manifest.json").exists():
            raise ValueError("Todavía no hay un índice. Añade e indexa documentos en Administración.")
        self.update(job_id, phase="Recuperando fragmentos relevantes en CPU")
        try:
            embeddings = self.get_embeddings()
        except Exception as exc:
            raise JobFailure("EMBEDDINGS_NOT_READY", "El modelo de búsqueda no está preparado.", str(exc)) from exc
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
            raise JobFailure("OLLAMA_UNAVAILABLE", "Ollama no responde.", available.get("error", "Inicia ollama serve."))
        if model not in available["models"]:
            raise ValueError("Selecciona un modelo local instalado en Ollama.")
        self.update(job_id, phase="Generando respuesta con Ollama", model=model)

        def on_chunk(piece):
            self.check_cancel(job_id)
            with self.lock:
                self.jobs[job_id]["answer"] += piece

        try:
            answer, metrics = rag.generate_ollama(messages, model, on_chunk=on_chunk, cancelled=lambda: self.cancelled(job_id))
        except InterruptedError:
            raise
        except OSError as exc:
            raise JobFailure("OLLAMA_UNAVAILABLE", "Ollama no responde.", str(exc)) from exc
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
    @app.get("/biblioteca")
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

    @app.get("/api/jobs")
    def jobs(active: int = 0):
        return service.list_jobs(active=bool(active))

    @app.delete("/api/jobs/{job_id}")
    def cancel(job_id: str):
        return service.cancel(job_id)

    @app.post("/api/query", status_code=202)
    def query(body: Query):
        if not body.question.strip():
            raise HTTPException(422, "Escribe una pregunta.")
        if not (service.config.db / f"{service.config.collection}-manifest.json").exists():
            raise HTTPException(409, "Añade e indexa documentos en Administración antes de consultar.")
        return service.start("query", lambda job_id: service.query(job_id, body),
                             question=body.question, search_only=body.search_only)

    @app.post("/api/admin/prepare", status_code=202)
    def prepare():
        return service.start("prepare", service.prepare)

    @app.post("/api/admin/index", status_code=202)
    def index(body: IndexRequest | None = None):
        if not service.list_documents():
            raise HTTPException(409, "Añade documentos antes de indexar.")
        name = body.file if body else None
        if name:
            path = (service.documents / name).resolve()
            if not path.is_relative_to(service.documents) or not path.is_file() or path.suffix.lower() not in ALLOWED:
                raise HTTPException(404, "Documento no disponible.")
        return service.start("index", lambda job_id: service.index(job_id, name), document_name=name)

    @app.post("/api/admin/upload")
    async def upload(files: Annotated[list[UploadFile], File()], auto_index: Annotated[bool, Form()] = True,
                     batch_id: Annotated[str, Form()] = ""):
        if not 1 <= len(files) <= 20:
            raise HTTPException(422, "Selecciona entre 1 y 20 documentos.")
        # Validate all names before saving any file. Existing documents are never overwritten.
        for file in files:
            name = file.filename or ""
            if Path(name).suffix.lower() not in ALLOWED:
                return JSONResponse({"detail": "Formato no compatible.", "error_info": {"code": "UNSUPPORTED_TYPE", "message": f"Formato no compatible ({Path(name).suffix}).", "detail": "PDF, TXT o Markdown."}}, status_code=422)
            if Path(name).name != name or ".." in name or any(char in name for char in '\\/:*?"<>|') or name.endswith((".", " ")):
                return JSONResponse({"detail": "Nombre de archivo no válido.", "error_info": {"code": "INVALID_NAME", "message": "El nombre contiene caracteres o rutas no válidos.", "detail": name}}, status_code=422)
        saved = []
        total = 0
        payloads = []
        for file in files:
            content = await file.read(MAX_FILE + 1)
            await file.close()
            total += len(content)
            if len(content) > MAX_FILE or total > 100 * 1024**2:
                return JSONResponse({"detail": "Límite: 20 MB por documento y 100 MB por lote.", "error_info": {"code": "FILE_TOO_LARGE", "message": "Supera el límite de 20 MB por archivo.", "detail": file.filename}}, status_code=413)
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
        result = {"saved": saved, "document_id": hashlib.sha256(saved[0].encode()).hexdigest()[:24]}
        if auto_index:
            name = saved[0] if len(saved) == 1 else None
            result.update(service.start("index", lambda job_id: service.index(job_id, name),
                document_name=name, document_id=result["document_id"], batch_id=batch_id[:100],
                bytes=len(payloads[0][1]) if len(payloads) == 1 else sum(len(item[1]) for item in payloads)))
        return result

    @app.get("/api/source")
    def source(file: str):
        path = (service.documents / file).resolve()
        if not path.is_relative_to(service.documents) or not path.is_file() or path.suffix.lower() not in ALLOWED:
            raise HTTPException(404, "Documento no disponible.")
        return FileResponse(path, filename=path.name, media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    return app


app = create_app()
