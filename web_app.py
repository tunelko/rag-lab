"""Loopback-only web UI for the local RAG lab. No models are loaded on startup."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import base64
import json
import mimetypes
import os
from pathlib import Path
import secrets
import re
import time
from typing import Annotated, Literal
from urllib.parse import quote, urlsplit
import urllib.request

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile, Query as QueryParameter
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

import rag
from jobs_runtime import JobFailure, JobRuntime
from settings_store import SettingsStore, Preferences
import multimodal
from model_routing import select_model
from server_console import ConsoleBuffer, ConsoleCapture
from hardware_monitor import HardwareMonitor
from file_fingerprints import FileFingerprints

ALLOWED = {".pdf", ".txt", ".md", ".markdown", ".zip", *multimodal.IMAGES}
MAX_FILE = 20 * 1024**2


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=3000)


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=6000)
    model: str | None = Field(default=None, max_length=300)
    k: int = Field(default=4, ge=1, le=12)
    search_only: bool = False
    history: list[ChatMessage] = Field(default_factory=list, max_length=6)


class IndexRequest(BaseModel):
    file: str | None = None


class RagService(JobRuntime):
    def __init__(self, config=None, documents=None):
        self.config = config or rag.Config()
        self.documents = (documents or rag.ROOT / "data" / "documents").resolve()
        self.documents.mkdir(parents=True, exist_ok=True)
        self.embeddings = None
        self.settings = SettingsStore(self.config.db.parent / "settings.json")
        self.visual_root = self.config.db.parent / "visual"
        self.capabilities_cache = {}
        self.console = ConsoleBuffer()
        self.hardware = HardwareMonitor()
        self.fingerprints = FileFingerprints()
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
        self.update(job_id, document_names=[document["name"] for document in documents])
        self.update(job_id, phase="Cargando embeddings en CPU")
        self.update(job_id, stage="chunking", stage_progress=0, progress=0)
        try:
            embeddings = self.get_embeddings(download=True)
        except Exception as exc:
            self.update(job_id, documents=[{"name": document["name"], "chunks": 0,
                "error_info": {"code": "EMBEDDINGS_NOT_READY", "message": "El modelo de búsqueda no está preparado.", "detail": str(exc)}} for document in documents])
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
                    record["error_info"] = {"code": event.get("code", "UNEXPECTED"), "message": event.get("error") if event.get("code") == "VISUAL_IMPORT" else "No se ha podido procesar el archivo.", "detail": event.get("error", "")}
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

        settings = self.settings.get()

        def describe(data, model):
            self.check_cancel(job_id)
            if "vision" not in self.model_capabilities(model):
                raise multimodal.VisualError("Selecciona un modelo visual instalado en Configuración.")
            self.update(job_id, phase="Describiendo imágenes en local con Ollama", cancellable=True)
            return multimodal.describe_image(data, model, os.getenv("RAG_OLLAMA_URL", "http://127.0.0.1:11434"), cancelled=lambda: self.cancelled(job_id))

        reader = multimodal.VisualReader(self.visual_root, settings, describe, lambda: self.cancelled(job_id))

        def loader(path):
            if path.suffix.lower() in multimodal.EXTENSIONS:
                if settings.multimodal:
                    return reader.load(path)
                if path.suffix.lower() != ".pdf":
                    raise multimodal.VisualError("Activa el procesamiento multimodal en Configuración para indexar imágenes o exportaciones MinerU.")
            return rag.read_documents(path)

        summary = rag.ingest(self.config, self.documents, embeddings,
            paths=[self.documents / document["name"] for document in documents], observer=observe,
            cancelled=lambda: self.cancelled(job_id), loader=loader, extensions=ALLOWED,
            policy_for=lambda path: multimodal.policy(settings) if path.suffix.lower() in multimodal.EXTENSIONS else None)
        self.update(job_id, summary=summary, documents=list(outcomes.values()))
        failures = [item for item in outcomes.values() if item.get("error_info")]
        if failures:
            first = failures[0]["error_info"]
            raise JobFailure(first["code"], first["message"], first["detail"])
        return {"summary": summary, "documents": list(outcomes.values()), "message": "Índice actualizado"}

    def list_documents(self):
        state = rag.load_state(self.config)
        settings = self.settings.get()
        jobs = self.list_jobs()["jobs"]
        items = []
        for path in sorted(self.documents.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in ALLOWED or not path.resolve().is_relative_to(self.documents):
                continue
            indexed = state["files"].get(str(path.resolve()), {})
            digest = self.fingerprints.digest(path)
            name = path.relative_to(self.documents).as_posix()
            expected_policy = multimodal.policy(settings) if path.suffix.lower() in multimodal.EXTENSIONS else None
            ready = indexed.get("sha256") == digest and bool(indexed.get("ids")) and rag.same_parser_policy(indexed.get("parser_policy"), expected_policy)
            related = [job for job in jobs if job.get("document_name") == name or job.get("current_document") == path.name or name in job.get("document_names", [])]
            latest = related[-1] if related else {}
            outcome = next((item for item in latest.get("documents", []) if item.get("name") in {name, path.name}), None)
            error = (outcome.get("error_info") if outcome else latest.get("error_info")) if latest.get("status") == "error" else None
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

    def model_capabilities(self, model):
        cached = self.capabilities_cache.get(model)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        base = os.getenv("RAG_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        request = urllib.request.Request(base + "/api/show", data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                capabilities = json.load(response).get("capabilities", [])
        except (OSError, ValueError):
            capabilities = []
        self.capabilities_cache[model] = (time.monotonic(), capabilities)
        return capabilities

    def preferences(self):
        value = self.settings.get()
        available = self.ollama_status()
        if not value.response_model:
            value.response_model = available["default_model"]
        return {"settings": value.model_dump(), "models": [{"name": name, "vision": "vision" in self.model_capabilities(name)} for name in available["models"]],
                "connected": available["connected"]}

    def statistics(self):
        jobs = [job for job in self.list_jobs()["jobs"] if job["kind"] == "query"]
        done = [job for job in jobs if job["status"] == "done"]
        files = rag.load_state(self.config)["files"]
        return {"scope": "Consultas recientes en memoria (hasta 200 tareas). El tiempo de ejecución y la espera en cola se muestran por separado.",
                "queries": len(jobs), "completed": len(done), "errors": sum(job["status"] == "error" for job in jobs),
                "output_tokens": sum(job.get("metrics", {}).get("output_tokens", 0) for job in done),
                "visuals": sum(len(file.get("visuals", [])) for file in files.values()),
                "diagnostics": {"fingerprints": self.fingerprints.snapshot()},
                "recent": [{"question": job.get("question", ""), "status": job["status"], "created_at": job["created_at"],
                            "elapsed_seconds": job.get("elapsed_seconds"), "queue_seconds": job.get("queue_seconds"),
                            "metrics": job.get("metrics", {})} for job in reversed(jobs[-30:])]}

    def query(self, job_id, query):
        started = time.perf_counter()
        if not (self.config.db / f"{self.config.collection}-manifest.json").exists():
            raise ValueError("Todavía no hay un índice. Añade e indexa documentos en Administración.")
        self.update(job_id, phase="Recuperando fragmentos relevantes en CPU")
        stage_started = time.perf_counter()
        try:
            embeddings = self.get_embeddings()
        except Exception as exc:
            raise JobFailure("EMBEDDINGS_NOT_READY", "El modelo de búsqueda no está preparado.", str(exc)) from exc
        timings = {"embedding_setup_seconds": round(time.perf_counter() - stage_started, 6)}
        stage_started = time.perf_counter()
        hits = rag.retrieve(self.config, query.question.strip(), embeddings, query.k)
        timings["vector_search_seconds"] = round(time.perf_counter() - stage_started, 6)
        if not hits:
            return {"answer": "No hay fragmentos disponibles para responder. Revisa la biblioteca.", "sources": [], "metrics": timings}
        stage_started = time.perf_counter()
        messages, sources = rag.build_messages(query.question.strip(), hits, self.config.context_chars)
        # Previous dialogue helps follow-up questions without replacing retrieved evidence.
        if query.history:
            history, remaining = [], 6000
            for message in reversed(query.history):
                if remaining <= 0:
                    break
                content = message.content[-remaining:]
                history.append({"role": message.role, "content": content})
                remaining -= len(content)
            messages[1:1] = reversed(history)
        manifest = rag.load_state(self.config)
        settings = self.settings.get()
        images = {}
        for source, (document, _) in zip(sources, hits):
            source["text"] = document.page_content
            if "chunk" in document.metadata:
                source["chunk"] = int(document.metadata["chunk"]) + 1
            path = Path(source.pop("source")).resolve()
            source["url"] = "/api/source?file=" + quote(path.relative_to(self.documents).as_posix(), safe="") if path.is_relative_to(self.documents) else None
            source["item_type"] = document.metadata.get("item_type", "text")
            visuals = manifest["files"].get(str(path), {}).get("visuals", [])
            source["figures"] = []
            # Prefer the hit's own figure, then figures from that same page.
            visuals = sorted(visuals, key=lambda value: value.get("asset_id") != document.metadata.get("asset_id"))
            for visual in visuals:
                asset = visual.get("asset_id", "")
                if visual.get("page") != source["page"] or not re.fullmatch(r"[a-f0-9]{64}", asset):
                    continue
                if not (self.visual_root / f"{asset}.png").is_file():
                    continue
                source["figures"].append({"url": f"/api/figures/{asset}", "page": visual["page"], "kind": visual.get("item_type", "figure")})
                if asset not in images and len(images) < settings.max_images:
                    images[asset] = source["citation"]
        retrieval_seconds = round(time.perf_counter() - started, 2)
        timings["source_preparation_seconds"] = round(time.perf_counter() - stage_started, 6)
        self.update(job_id, sources=sources, metrics={"retrieval_seconds": retrieval_seconds, **timings})
        if query.search_only:
            return {"answer": "Fragmentos recuperados. No se ha solicitado una respuesta al modelo.", "sources": sources,
                    "metrics": {"retrieval_seconds": retrieval_seconds, **timings, "routing_mode": "search_only", "routing_reason": "Búsqueda sin generación."}}
        stage_started = time.perf_counter()
        available = self.ollama_status()
        if not available["connected"]:
            raise JobFailure("OLLAMA_UNAVAILABLE", "Ollama no responde.", available.get("error", "Inicia ollama serve."))
        route = select_model(settings, query.question, hits[:len(sources)], bool(images), available,
                             self.model_capabilities, requested=query.model, history=query.history)
        model = route.model
        visual_mode = "descriptions" if any(document.metadata.get("asset_id") for document, _ in hits[:len(sources)]) else "text"
        if route.images:
            messages[-1]["images"] = [base64.b64encode((self.visual_root / f"{asset}.png").read_bytes()).decode() for asset in images]
            messages[-1]["content"] += "\n\nIMÁGENES ADJUNTAS (en este orden): " + "; ".join(f"imagen {i}: fuente [{citation}]" for i, citation in enumerate(images.values(), 1))
            messages[0]["content"] += " Inspecciona también las imágenes adjuntas; sus descripciones automáticas pueden contener errores."
            visual_mode = "images"
        routing = {"model": model, "routing_mode": route.mode, "routing_reason": route.reason}
        timings["routing_seconds"] = round(time.perf_counter() - stage_started, 6)
        self.update(job_id, phase="Generando respuesta con Ollama", model=model,
                    metrics={"retrieval_seconds": retrieval_seconds, **timings, **routing})

        def on_chunk(piece):
            self.check_cancel(job_id)
            with self.lock:
                self.jobs[job_id]["answer"] += piece

        stage_started = time.perf_counter()
        try:
            answer, metrics = rag.generate_ollama(messages, model, on_chunk=on_chunk, cancelled=lambda: self.cancelled(job_id))
        except InterruptedError:
            raise
        except OSError as exc:
            raise JobFailure("OLLAMA_UNAVAILABLE", "Ollama no responde.", str(exc)) from exc
        if not answer.strip():
            raise ValueError("El modelo no devolvió texto visible. Prueba otro modelo o aumenta RAG_MAX_NEW_TOKENS en .env.")
        timings["generation_wall_seconds"] = round(time.perf_counter() - stage_started, 6)
        return {"answer": answer, "sources": sources, "metrics": {**metrics, **routing, **timings, "retrieval_seconds": retrieval_seconds,
                "visual_mode": visual_mode, "images_used": len(images) if visual_mode == "images" else 0}}


def create_app(service=None):
    service = service or RagService()
    token = secrets.token_urlsafe(32)

    @asynccontextmanager
    async def lifespan(app):
        with ConsoleCapture(service.console):
            try:
                yield
            finally:
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
    @app.get("/configuracion")
    @app.get("/estadisticas")
    @app.get("/consola")
    def page():
        return FileResponse(rag.ROOT / "dist" / "index.html")

    @app.get("/api/session")
    def session():
        return {"token": token}

    @app.get("/api/console")
    def console(after: int = QueryParameter(default=0, ge=0), limit: int = QueryParameter(default=200, ge=1, le=500)):
        return service.console.snapshot(after, limit)

    @app.get("/api/telemetry")
    def telemetry():
        return service.hardware.sample()

    @app.get("/api/settings")
    def settings():
        return service.preferences()

    @app.put("/api/settings")
    def save_settings(body: Preferences):
        available = service.ollama_status()
        if body.response_model and available["connected"] and body.response_model not in available["models"]:
            raise HTTPException(422, "El modelo de respuesta no está instalado en Ollama.")
        if body.multimodal and (body.vision_model not in available["models"] or "vision" not in service.model_capabilities(body.vision_model)):
            raise HTTPException(422, "Selecciona un modelo visual local instalado antes de activar el modo multimodal.")
        return {"settings": service.settings.save(body).model_dump()}

    @app.get("/api/statistics")
    def statistics():
        return service.statistics()

    @app.get("/api/figures/{asset}")
    def figure(asset: str):
        if not re.fullmatch(r"[a-f0-9]{64}", asset):
            raise HTTPException(404, "Figura no disponible.")
        path = (service.visual_root / f"{asset}.png").resolve()
        if not path.is_relative_to(service.visual_root.resolve()) or not path.is_file():
            raise HTTPException(404, "Figura no disponible.")
        return FileResponse(path, media_type="image/png")

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
        settings = service.settings.get()
        if "k" not in body.model_fields_set:
            body.k = settings.k
        if "search_only" not in body.model_fields_set:
            body.search_only = settings.search_only
        return service.start("query", lambda job_id: service.query(job_id, body),
                             question=body.question, search_only=body.search_only, k=body.k)

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
            reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
            if Path(name).name != name or ".." in name or any(ord(char) < 32 or char in '\\/:*?"<>|' for char in name) or name.endswith((".", " ")) or name.split('.')[0].upper() in reserved:
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
