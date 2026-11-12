"""Loopback-only web UI for the local RAG lab. No models are loaded on startup."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hashlib
import base64
import ipaddress
import json
import logging
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
from model_routing import select_model, select_external_model, asks_for_vision
import retrieval_pipeline
from server_console import ConsoleBuffer, ConsoleCapture
from hardware_monitor import HardwareMonitor
from file_fingerprints import FileFingerprints
from library_queries import (Intent, query_intent, catalog_answer, global_retrieve, summary_scope, tokens,
                             has_summaries, set_question, topic_query)
from global_answers import GlobalAnswer
from statistics_store import StatisticsStore, RETENTION
from source_locations import pdf_page_label
from localization import text as localized
from query_images import normalize_query_images
from server_control import validate_host
from library_topics import TopicStore, TopicError
from index_lock import IndexBusyError, index_writer
import library_reset
from ollama_http import open_ollama
from credential_store import CredentialError, CredentialStore, CredentialStoreLocked, PROVIDERS as CREDENTIAL_PROVIDERS
import generation_providers
import provider_vision
import provider_pricing

ALLOWED = {".pdf", ".txt", ".md", ".markdown", ".zip", *multimodal.IMAGES}
SHEET_CHARS = 480   # room one summary sheet needs in a whole-set answer: its opening block
MAX_FILE = 20 * 1024**2


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=3000)


class Query(BaseModel):
    question: str = Field(min_length=1, max_length=6000)
    language: Literal["es", "en"] = "es"
    images: list[Annotated[str, Field(max_length=4*1024**2+100)]] = Field(default_factory=list, max_length=4)
    model: str | None = Field(default=None, max_length=300)
    k: int = Field(default=4, ge=1, le=12)
    search_only: bool = False
    history: list[ChatMessage] = Field(default_factory=list, max_length=6)
    topic_id: str | None = Field(default=None, max_length=64)


class IndexRequest(BaseModel):
    file: str | None = None


class TopicRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    parent_id: str | None = Field(default=None, max_length=64)


class AssignmentRequest(BaseModel):
    names: list[Annotated[str, Field(min_length=1, max_length=1000)]] = Field(min_length=1, max_length=500)
    topic_id: str | None = Field(default=None, max_length=64)


class CredentialRequest(BaseModel):
    api_key: str = Field(min_length=8, max_length=8192)


class UnlockRequest(BaseModel):
    passphrase: str = Field(min_length=12, max_length=1024)


class EmptyLibraryRequest(BaseModel):
    confirm: Literal["empty-library"]
    keep_captions: bool = False


class RagService(JobRuntime):
    def __init__(self, config=None, documents=None):
        self.config = config or rag.Config()
        self.documents = (documents or rag.ROOT / "data" / "documents").resolve()
        self.documents.mkdir(parents=True, exist_ok=True)
        self.embeddings = None
        self.settings = SettingsStore(self.config.db.parent / "settings.json")
        self.credentials = CredentialStore(self.config.db.parent / "credentials.sqlite3")
        self.visual_root = self.config.db.parent / "visual"
        self.capabilities_cache = {}
        self.console = ConsoleBuffer()
        self.statistics_archive = StatisticsStore(self.config.db.parent / 'statistics.sqlite3')
        self.hardware = HardwareMonitor(archive=self.statistics_archive)
        self.fingerprints = FileFingerprints()
        self.topics = TopicStore(self.config.db / f'{self.config.collection}-topics.sqlite3')
        super().__init__()

    def record_job(self, job):
        self.statistics_archive.save_job(job)

    def snapshot(self, job_id):
        try:
            return super().snapshot(job_id)
        except HTTPException as error:
            if error.status_code!=404:raise
            archived=self.statistics_archive.index_job(job_id)
            if archived:return archived
            raise

    def close(self):
        super().close()
        self.hardware.stop()
        self.statistics_archive.close()
        self.credentials.close()

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

    def index(self, job_id, only_file=None, *, files=None, preferences=None):
        documents = self.list_documents()
        if only_file:
            documents = [document for document in documents if document["name"] == only_file]
        if files is not None:
            documents=[document for document in documents if document['name'] in files]
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

        settings = preferences or self.settings.get()

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

        try:
            summary = rag.ingest(self.config, self.documents, embeddings,
                paths=[self.documents / document["name"] for document in documents], observer=observe,
                cancelled=lambda: self.cancelled(job_id), loader=loader, extensions=ALLOWED,
                policy_for=lambda path: multimodal.policy(settings) if path.suffix.lower() in multimodal.EXTENSIONS else None)
        except IndexBusyError as error:
            raise JobFailure('INDEX_BUSY',str(error)) from error
        self.update(job_id, summary=summary, documents=list(outcomes.values()))
        failures = [item for item in outcomes.values() if item.get("error_info")]
        if failures:
            first = failures[0]["error_info"]
            raise JobFailure(first["code"], first["message"], first["detail"])
        return {"summary": summary, "documents": list(outcomes.values()), "message": "Índice actualizado"}

    def empty_library(self, keep_captions=False):
        """Move the whole library to a dated backup folder and start from an empty one."""
        # The job lock is held throughout, so no upload, index or query can start meanwhile.
        with self.lock:
            if self.closed:
                raise HTTPException(503, "El servidor se está cerrando.")
            if any(job["status"] in {"queued", "running"} for job in self.jobs.values()):
                raise HTTPException(409, "Hay tareas en curso o en cola. Espera a que terminen o cancélalas antes de vaciar la biblioteca.")
            manifest = self.config.db / f"{self.config.collection}-manifest.json"
            # Counted from disk: this must also work when the manifest belongs to another configuration.
            names = [path for path in self.documents.rglob("*") if path.is_file() and path.suffix.lower() in ALLOWED]
            if not names and not manifest.exists():
                raise HTTPException(409, "La biblioteca ya está vacía.")
            try:
                chunks = sum(len(item.get("ids", [])) for item in json.loads(manifest.read_text(encoding="utf-8")).get("files", {}).values())
            except (OSError, ValueError, AttributeError):
                chunks = 0
            try:
                with index_writer(self.config):
                    result = library_reset.empty_library(self.config, self.documents, self.visual_root,
                        self.config.db.parent / "library-backups", keep_captions=keep_captions)
            except IndexBusyError as error:
                raise HTTPException(409, str(error)) from error
            except library_reset.LibraryBusyError as error:
                raise HTTPException(409, f"No se ha vaciado nada: hay un archivo en uso ({error}). Cierra los documentos abiertos y vuelve a intentarlo.") from error
            finally:
                # Also after a failed attempt: a concurrent reader may have seen a half-moved state.
                from vector_store import VectorStore
                VectorStore.clear_cache(self.config.db / f"{self.config.collection}-vectors.sqlite3")
                retrieval_pipeline.clear_catalogs()
                self.fingerprints.clear()
                self._embedding_dimensions = None
                self.topics = TopicStore(self.config.db / f"{self.config.collection}-topics.sqlite3")
            path = result["path"]
            logging.getLogger("rag.jobs").info("Biblioteca vaciada · %d documentos y %d fragmentos movidos a %s", len(names), chunks, path.name)
            return {"documents": len(names), "chunks": chunks, "kept_captions": keep_captions,
                    "backup": path.relative_to(rag.ROOT).as_posix() if path.is_relative_to(rag.ROOT) else str(path)}

    def list_documents(self):
        state = rag.load_state(self.config)
        settings = self.settings.get()
        jobs = self.list_jobs()["jobs"]
        items = []
        assignments = self.topics.assignments()
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
                "topic_id": assignments.get(name),
                "needs_reindex": bool(indexed.get('ids')) and indexed.get('sha256') == digest and not rag.same_parser_policy(indexed.get('parser_policy'), expected_policy),
                "chunks": len(indexed.get("ids", [])), "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(),
                "document_id": hashlib.sha256(name.encode()).hexdigest()[:24],
                "status": "processing" if processing else ("error" if error else ("indexed" if ready else "pending")),
                "stage": latest.get("stage"), "progress": latest.get("stage_progress"), "error_info": error})
        return items

    def ollama_status(self):
        base = os.getenv("RAG_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        try:
            with open_ollama(base + "/api/tags", timeout=3) as response:
                models = json.load(response).get("models", [])
            with open_ollama(base + "/api/ps", timeout=3) as response:
                loaded = json.load(response).get("models", [])
            names = [item["name"] for item in models if not item["name"].lower().endswith(":cloud")
                     and not item.get("remote_host") and not item.get("remote_model")]
            default = os.getenv("RAG_OLLAMA_MODEL", "gemma-4-12b-redteam-blackice:latest")
            return {"connected": True, "models": names, "default_model": default if default in names else (names[0] if names else ""),
                    "loaded": [{"name": item.get("name"), "vram_bytes": item.get("size_vram", 0)} for item in loaded]}
        except (OSError, ValueError) as exc:
            return {"connected": False, "models": [], "default_model": "", "loaded": [], "error": str(exc)}

    def status(self):
        state = rag.load_state(self.config)
        dimensions=getattr(self,'_embedding_dimensions',None)
        if dimensions is None and any(item.get('ids') for item in state['files'].values()):
            try:
                vectors=rag.open_store(self.config,None).get(limit=1,include=['embeddings']).get('embeddings')
                if vectors is not None and len(vectors):
                    dimensions=self._embedding_dimensions=len(vectors[0])
            except (OSError,RuntimeError,ValueError):
                pass
        documents = self.list_documents()
        chunks = sum(len(file.get("ids", [])) for file in state["files"].values())
        return {"documents": documents, "document_count": len(documents), "chunk_count": chunks,
                "library": self.topics.snapshot(documents),
                "server": {"bind_host": getattr(self,'bind_host','127.0.0.1'),
                           "access": 'loopback' if getattr(self,'bind_host','127.0.0.1').startswith('127.') else 'lan'},
                "collection": self.config.collection, "embedding_model": self.config.embedding_model,
                "embedding_device": self.config.embedding_device, "embedding_loaded": self.embeddings is not None,
                "chunk_size": self.config.chunk_size, "chunk_overlap": self.config.chunk_overlap,
                "embedding_dimensions": dimensions,
                "reranker": retrieval_pipeline.reranker_status(),
                "visual_count": sum(len(item.get('visuals',[])) for item in state['files'].values()),
                "last_index_at": self.statistics_archive.last_index_time(),
                "active_job": self.active, "ollama": self.ollama_status(),
                "activity": self.activity[-6:], "jobs": self.list_jobs(active=True)["jobs"]}

    def model_capabilities(self, model):
        cached = self.capabilities_cache.get(model)
        if cached and time.monotonic() - cached[0] < 60:
            return cached[1]
        base = os.getenv("RAG_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
        request = urllib.request.Request(base + "/api/show", data=json.dumps({"model": model}).encode(), headers={"Content-Type": "application/json"})
        try:
            with open_ollama(request, timeout=5) as response:
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
                "connected": available["connected"], "providers": generation_providers.provider_info(),
                "credentials": self.credentials.status()}

    def statistics(self, seconds=RETENTION, page=1, per_page=50, ingestion_page=1, query_id=None):
        files = rag.load_state(self.config)["files"]
        label='1 día' if seconds==86400 else f'{seconds//86400} días'
        return {"scope": f'Histórico local en disco · últimos {label}. Ejecución y espera en cola por separado.',
                **self.statistics_archive.queries(seconds,page=page,per_page=per_page,query_id=query_id),
                'ingestions':self.statistics_archive.ingestions(seconds,page=ingestion_page,per_page=per_page),
                "visuals": sum(len(file.get("visuals", [])) for file in files.values()),
                "diagnostics": {"fingerprints": self.fingerprints.snapshot()}}

    def query_in_batches(self, job_id, query, sheets, capacity, scope_label, preferences, provider_key):
        """Answer a question about every item from its summary sheets, a group at a time, and join the rows."""
        count = -(-len(sheets) // capacity)
        size = -(-len(sheets) // count)      # groups of similar size rather than one small last group
        header, rows, sources, metrics, seconds = [], [], [], {}, 0.0
        for number in range(count):
            self.check_cancel(job_id)
            group = sheets[number * size:(number + 1) * size]
            part = self.query(job_id, query, frozenset(item['name'] for item in group), scope_label,
                              preferences=preferences, provider_key=provider_key)
            offset = len(sources)
            lines = [line for line in part['answer'].splitlines() if line.startswith('|')]
            header = header or lines[:2]
            # Citation numbers restart in every group: shift them so they point at the joined source list.
            rows += [re.sub(r'\[(\d+)\]', lambda match: f'[{int(match[1]) + offset}]', line) for line in lines[2:]]
            for source in part['sources']:
                sources.append({**source, 'citation': source['citation'] + offset})
            metrics = part['metrics']
            seconds += metrics.get('generation_wall_seconds', 0)
            self.update(job_id, answer='\n'.join([*header, *rows]), sources=sources,
                        phase=f'Leyendo fichas · grupo {number + 1}/{count}')
        answer = '\n'.join([*header, *rows]) if rows else part['answer']
        return {'answer': answer, 'sources': sources,
                'metrics': {**metrics, 'scope_documents': len(sheets), 'documents_indexed': len(sheets), 'documents_retrieved': len(sources),
                            'answer_documents': len(rows), 'answer_batches': count, 'generation_wall_seconds': round(seconds, 6)}}

    def query(self, job_id, query, document_names=None, scope_label=None, *, preferences=None, provider_key=None):
        started = time.perf_counter()
        documents = self.list_documents()
        if document_names is not None:
            documents = [item for item in documents if item['name'] in document_names]
        scope_metrics = {'topic_id': query.topic_id, 'scope_label': scope_label, 'scope_documents': len(documents)}
        intent = query_intent(query.question)
        if intent.mode == 'focused' and has_summaries(documents) and set_question(query.question):
            # "Is there any…", "which…", "how many…": with one summary sheet per item in scope, the
            # question is about the set and every item must be looked at, not the eight best fragments.
            intent = Intent('global', topic_query(query.question))
        if intent.mode == 'global' and has_summaries(documents):
            sheets = summary_scope(documents)
            # Each sheet needs room for its whole opening block. More sheets than fit in one answer
            # are read in several passes and their rows joined, instead of cutting every sheet short.
            capacity = max(1, self.config.context_chars // SHEET_CHARS)
            if len(sheets) > capacity:
                return self.query_in_batches(job_id, query, sheets, capacity, scope_label, preferences, provider_key)
        if intent.mode.startswith('catalog_'):
            self.update(job_id, phase='Consultando el registro real de la biblioteca')
            answer, metrics = catalog_answer(documents, intent.mode, language=query.language)
            return {'answer': answer, 'sources': [], 'metrics': {**metrics, **scope_metrics}}
        if not any(item.get('indexed') for item in documents):
            return {'answer': localized('answer.empty', query.language), 'sources': [], 'metrics': scope_metrics}
        if not (self.config.db / f"{self.config.collection}-manifest.json").exists():
            raise ValueError("Todavía no hay un índice. Añade e indexa documentos en Administración.")
        self.update(job_id, phase="Recuperando fragmentos relevantes en CPU")
        stage_started = time.perf_counter()
        try:
            embeddings = self.get_embeddings()
        except Exception as exc:
            raise JobFailure("EMBEDDINGS_NOT_READY", "El modelo de búsqueda no está preparado.", str(exc)) from exc
        timings = {"embedding_setup_seconds": round(time.perf_counter() - stage_started, 6), **scope_metrics}
        stage_started = time.perf_counter()
        if intent.mode == 'global':
            # A question about every item is answered from its summary sheets when the scope has them:
            # one sheet per tender, instead of every PDF of every tender.
            hits, scope = global_retrieve(self.config, summary_scope(documents), embeddings, query.question,
                cancelled=lambda: self.cancelled(job_id),
                observer=lambda done, total: self.update(job_id, phase=f'Revisando documentos · {done}/{total}'))
        else:
            hits, retrieval = retrieval_pipeline.retrieve(
                self.config, query.question.strip(), embeddings, query.k,
                files=frozenset(item['name'] for item in documents if item.get('indexed')),
                visual=bool(query.images) or asks_for_vision(query.question, query.history),
                reranker=retrieval_pipeline.configured_reranker(),
                rerank_window=retrieval_pipeline.rerank_window())
            scope = {'retrieval_mode': 'focused', **retrieval_pipeline.trace_metrics(retrieval)}
        timings.update(scope)
        timings["vector_search_seconds"] = round(time.perf_counter() - stage_started, 6)
        if not hits:
            return {"answer": localized('answer.empty',query.language), "sources": [], "metrics": timings}
        stage_started = time.perf_counter()
        messages, sources = rag.build_messages(query.question.strip(), hits, self.config.context_chars, language=query.language)
        if intent.mode == 'global':
            represented = len({source['file'] for source in sources})
            if represented != scope['documents_indexed']:
                raise ValueError('El contexto no incluye todos los documentos. Se ha detenido la respuesta global para evitar omisiones.')
            messages[0]['content'] += (
                f' Esta consulta global aporta evidencia de {represented} documentos distintos. '
                'Responde para EXACTAMENTE cada documento, sin agrupar ni siquiera versiones en distintos idiomas. '
                f'Redacta también las celdas en {"inglés" if query.language == "en" else "español"}, traduciendo las fuentes y conservando nombres técnicos. '
                'Solo indica el nombre de una entidad si aparece en esa fuente; si no, conserva el nombre del archivo. '
                'No transfieras el nombre ni las especificaciones de un documento a otro. '
                'Para documentos sin el dato solicitado en sus fragmentos, indícalo expresamente. '
                'Conserva el nivel de detalle de las fuentes: no inventes modelos concretos a partir de familias o generaciones. '
                'Las fuentes actuales prevalecen sobre respuestas anteriores.')
        # Previous dialogue helps follow-up questions without replacing retrieved evidence.
        if query.history and intent.mode != 'global':
            history, remaining = [], 6000
            for message in reversed(query.history):
                if remaining <= 0:
                    break
                content = message.content[-remaining:]
                history.append({"role": message.role, "content": content})
                remaining -= len(content)
            messages[1:1] = reversed(history)
        manifest = rag.load_state(self.config)
        settings = preferences or self.settings.get()
        images = {}
        for source, (document, _) in zip(sources, hits):
            source["text"] = document.page_content
            if "chunk" in document.metadata:
                source["chunk"] = int(document.metadata["chunk"]) + 1
            path = Path(source.pop("source")).resolve()
            source["url"] = "/api/source?file=" + quote(path.relative_to(self.documents).as_posix(), safe="") if path.is_relative_to(self.documents) else None
            if source["url"] and path.suffix.lower() == ".pdf":
                source["page_label"] = pdf_page_label(path, source["page"])
                if source["page"]:
                    source["url"] += f"#page={source['page']}"
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
                if asset not in images and len(images) < settings.max_images-len(query.images):
                    images[asset] = source["citation"]
        retrieval_seconds = round(time.perf_counter() - started, 2)
        timings["source_preparation_seconds"] = round(time.perf_counter() - stage_started, 6)
        self.update(job_id, sources=sources, metrics={"retrieval_seconds": retrieval_seconds, **timings})
        if query.search_only:
            return {"answer": localized('answer.search_only',query.language), "sources": sources,
                    "metrics": {"retrieval_seconds": retrieval_seconds, **timings, "routing_mode": "search_only", "routing_reason": localized('answer.search_only_reason',query.language)}}
        stage_started = time.perf_counter()
        provider = settings.response_provider
        external_api_key = None
        visual_mode = "descriptions" if any(document.metadata.get("asset_id") for document, _ in hits[:len(sources)]) else "text"
        if provider == "ollama":
            available = self.ollama_status()
            if not available["connected"]:
                raise JobFailure("OLLAMA_UNAVAILABLE", "Ollama no responde.", available.get("error", "Inicia ollama serve."))
            route = select_model(settings, query.question, hits[:len(sources)], bool(images), available,
                                 self.model_capabilities, requested=query.model, history=query.history, input_images=bool(query.images))
            model = route.model
            if route.images:
                messages[-1]["images"] = [*query.images, *(base64.b64encode((self.visual_root / f"{asset}.png").read_bytes()).decode() for asset in images)]
                labels=[f'imagen {i}: aportada por el usuario como parte de la pregunta' for i in range(1,len(query.images)+1)]
                labels += [f'imagen {i}: fuente [{citation}]' for i,citation in enumerate(images.values(),len(query.images)+1)]
                messages[-1]["content"] += "\n\nIMÁGENES ADJUNTAS (en este orden): " + "; ".join(labels)
                messages[0]["content"] += " Inspecciona también las imágenes adjuntas; sus descripciones automáticas pueden contener errores."
                visual_mode = "images"
            routing = {"model": model, "provider": "ollama", "cloud": False,
                       "routing_mode": route.mode, "routing_reason": route.reason}
            phase = "Generando respuesta con Ollama"
        else:
            try:
                external_api_key = provider_key if provider_key is not None else self.credentials.get(provider)
                profile = generation_providers.PROVIDERS[provider]
                route = select_external_model(settings, query.question, hits[:len(sources)], bool(images),
                    history=query.history, input_images=bool(query.images))
                model = route.model
                if route.images:
                    generation_providers.require_vision_model(provider, model, external_api_key)
                    limit = min(settings.max_images, provider_vision.image_limit(provider, model))
                    if len(query.images) > limit:
                        raise ValueError(f'El modelo visual admite como máximo {limit} imágenes por consulta.')
                    selected_images = list(images.items())[:limit-len(query.images)]
                    messages[-1]['images'] = [*query.images, *(base64.b64encode((self.visual_root / f'{asset}.png').read_bytes()).decode() for asset, _ in selected_images)]
                    labels = [f'imagen {i}: aportada por el usuario' for i in range(1, len(query.images)+1)]
                    labels += [f'imagen {i}: fuente [{citation}]' for i, (_, citation) in enumerate(selected_images, len(query.images)+1)]
                    messages[-1]['content'] += '\n\nIMÁGENES ADJUNTAS (en este orden): ' + '; '.join(labels)
                    messages[0]['content'] += ' Inspecciona las imágenes adjuntas; sus descripciones automáticas pueden contener errores.'
                    visual_mode = 'images'
            except (CredentialError, KeyError) as exc:
                raise JobFailure("PROVIDER_CREDENTIALS", "El proveedor externo no está preparado.", str(exc)) from exc
            except (ValueError, generation_providers.ProviderError) as exc:
                raise JobFailure('PROVIDER_VISION', 'Revisa los modelos del proveedor en Configuración.', str(exc)) from exc
            routing = {"model": model, "provider": provider, "provider_label": profile.label, "cloud": True,
                       "routing_mode": route.mode, "routing_reason": route.reason}
            phase = f"Generando respuesta con {profile.label}"
        timings["routing_seconds"] = round(time.perf_counter() - stage_started, 6)
        self.update(job_id, phase=phase, model=model,
                    metrics={"retrieval_seconds": retrieval_seconds, **timings, **routing, 'images_used':len(messages[-1].get('images',[])), 'visual_mode':visual_mode})

        structured = GlobalAnswer(sources,cpu_guard={'cpu','support'}.issubset(tokens(intent.topic)),language=query.language) if intent.mode == 'global' else None
        if structured:
            messages[-1]['content'] += structured.instruction()

        def on_chunk(piece):
            self.check_cancel(job_id)
            if structured:
                partial = structured.feed(piece)
                self.update(job_id, answer=partial, phase=f'Generando tabla · {len(structured.rows)}/{len(sources)} documentos')
                return
            with self.lock:
                self.jobs[job_id]["answer"] += piece

        stage_started = time.perf_counter()
        try:
            generation_options = {
                'context_length': int(os.getenv('RAG_OLLAMA_GLOBAL_CONTEXT', '8192')),
                'max_output_tokens': int(os.getenv('RAG_GLOBAL_MAX_NEW_TOKENS', '2048')),
                'response_format': structured.schema,
            } if intent.mode == 'global' else {}
            if provider == 'ollama' and intent.mode != 'global' and 'thinking' in self.model_capabilities(model):
                # Reasoning shares num_predict with the final answer. A small text
                # budget can end before message.content, even with think=False.
                generation_options['max_output_tokens'] = max(
                    int(os.getenv('RAG_MAX_NEW_TOKENS', '512')),
                    int(os.getenv('RAG_REASONING_MAX_NEW_TOKENS', '2048')))
            if provider == 'ollama':
                answer, metrics = rag.generate_ollama(messages, model, on_chunk=on_chunk,
                    cancelled=lambda: self.cancelled(job_id), **generation_options)
            else:
                answer, metrics = generation_providers.generate(messages, provider, model, external_api_key,
                    on_chunk=on_chunk, cancelled=lambda: self.cancelled(job_id), **generation_options)
            if structured:
                answer = structured.finish(answer)
                metrics.update(answer_documents=len(structured.rows), answer_coverage_complete=True)
                metrics['answer_grounding_fallbacks']=len(structured.grounding_fallbacks)
        except InterruptedError:
            raise
        except generation_providers.ProviderError as exc:
            raise JobFailure("PROVIDER_UNAVAILABLE", "El proveedor externo no pudo generar la respuesta.", str(exc)) from exc
        except OSError as exc:
            code = "OLLAMA_UNAVAILABLE" if provider == "ollama" else "PROVIDER_UNAVAILABLE"
            message = "Ollama no responde." if provider == "ollama" else "El proveedor externo no responde."
            raise JobFailure(code, message, str(exc)) from exc
        if not answer.strip():
            self.update(job_id, metrics={**metrics, **routing, **timings})
            raise JobFailure('EMPTY_RESPONSE', 'El modelo terminó sin devolver una respuesta visible.',
                f"Modelo: {model}. Tokens generados: {metrics.get('output_tokens', 'desconocido')}. "
                f"Motivo de fin: {metrics.get('done_reason', 'desconocido')}. "
                'Un modelo de razonamiento puede agotar el límite antes de responder. Revisa RAG_MAX_NEW_TOKENS.')
        timings["generation_wall_seconds"] = round(time.perf_counter() - stage_started, 6)
        return {"answer": answer, "sources": sources, "metrics": {**metrics, **routing, **timings, "retrieval_seconds": retrieval_seconds,
                "visual_mode": visual_mode, "images_used": len(messages[-1].get('images',[])), "language": query.language}}


def create_app(service=None):
    background_monitor = service is None
    service = service or RagService()
    token = secrets.token_urlsafe(32)

    def require_local_credentials(request: Request, detail="Las credenciales solo pueden administrarse desde el propio equipo que ejecuta RAG Local."):
        host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = False
        same_host = host == getattr(service, "bind_host", None)
        if not local and not same_host:
            raise HTTPException(403, detail)

    @asynccontextmanager
    async def lifespan(app):
        service.statistics_archive.mark_interrupted()
        if background_monitor:
            service.hardware.start()
        with ConsoleCapture(service.console):
            try:
                yield
            finally:
                service.close()

    app = FastAPI(title="RAG Local", docs_url=None, redoc_url=None, lifespan=lifespan)
    bind_host=validate_host(os.getenv('RAG_WEB_HOST','127.0.0.1'))
    service.bind_host=bind_host
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "testserver",bind_host])
    app.state.service = service

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin and (urlsplit(origin).scheme not in {'http','https'} or urlsplit(origin).netloc != request.headers.get("host")):
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
    def telemetry(history_seconds: int = QueryParameter(default=900, ge=60, le=RETENTION)):
        return service.hardware.snapshot(history_seconds)

    @app.get("/api/settings")
    def settings():
        return service.preferences()

    @app.put("/api/settings")
    def save_settings(body: Preferences):
        available = service.ollama_status()
        if body.response_provider == "ollama" and body.response_model and available["connected"] and body.response_model not in available["models"]:
            raise HTTPException(422, "El modelo de respuesta no está instalado en Ollama.")
        if body.multimodal and (body.vision_model not in available["models"] or "vision" not in service.model_capabilities(body.vision_model)):
            raise HTTPException(422, "Selecciona un modelo visual local instalado antes de activar el modo multimodal.")
        if body.response_provider == 'ollama' and body.query_vision_model and (body.query_vision_model not in available['models'] or 'vision' not in service.model_capabilities(body.query_vision_model)):
            raise HTTPException(422, 'Selecciona un modelo visual local instalado para las respuestas con imágenes.')
        if body.response_provider != 'ollama' and body.external_vision_model:
            try:
                generation_providers.require_vision_model(body.response_provider, body.external_vision_model,
                    service.credentials.get(body.response_provider))
            except (CredentialError, generation_providers.ProviderError) as exc:
                raise HTTPException(422, str(exc)) from exc
        return {"settings": service.settings.save(body).model_dump()}

    @app.put("/api/credentials/{provider}")
    def save_credential(provider: str, body: CredentialRequest, request: Request):
        require_local_credentials(request)
        if provider not in CREDENTIAL_PROVIDERS:
            raise HTTPException(404, "Proveedor no disponible.")
        try:
            return {"credentials": service.credentials.set(provider, body.api_key)}
        except CredentialStoreLocked as exc:
            raise HTTPException(409, str(exc)) from exc
        except CredentialError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.delete("/api/credentials/{provider}")
    def delete_credential(provider: str, request: Request):
        require_local_credentials(request)
        if provider not in CREDENTIAL_PROVIDERS:
            raise HTTPException(404, "Proveedor no disponible.")
        return {"credentials": service.credentials.delete(provider)}

    @app.post("/api/credentials/unlock")
    def unlock_credentials(body: UnlockRequest, request: Request):
        require_local_credentials(request)
        try:
            return {"credentials": service.credentials.unlock(body.passphrase)}
        except CredentialError as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.post("/api/credentials/lock")
    def lock_credentials(request: Request):
        require_local_credentials(request)
        service.credentials.lock_store()
        return {"credentials": service.credentials.status()}

    @app.get("/api/providers/{provider}/models")
    def provider_models(provider: str, request: Request):
        require_local_credentials(request)
        if provider not in CREDENTIAL_PROVIDERS:
            raise HTTPException(404, "Proveedor no disponible.")
        try:
            api_key = service.credentials.get(provider)
            models = generation_providers.list_models(provider, api_key)
        except CredentialStoreLocked as exc:
            raise HTTPException(409, str(exc)) from exc
        except CredentialError as exc:
            raise HTTPException(409, str(exc)) from exc
        except generation_providers.ProviderError as exc:
            raise HTTPException(502, str(exc)) from exc
        return {"provider": provider, "models": models,
                "vision_models": [name for name in models if provider_vision.image_limit(provider, name)],
                "image_limits": {name: provider_vision.image_limit(provider, name) for name in models if provider_vision.image_limit(provider, name)}}

    @app.get("/api/providers/{provider}/pricing")
    def provider_prices(provider: str, request: Request):
        require_local_credentials(request)
        if provider not in CREDENTIAL_PROVIDERS:
            raise HTTPException(404, "Proveedor no disponible.")
        try:
            return provider_pricing.fetch_pricing(provider)
        except provider_pricing.PricingError as exc:
            raise HTTPException(502, str(exc)) from exc

    @app.get("/api/statistics")
    def statistics(history_seconds: int = QueryParameter(default=RETENTION, ge=86400, le=RETENTION),
                   page: int = QueryParameter(default=1, ge=1), per_page: int = QueryParameter(default=50, ge=1, le=200),
                   ingestion_page: int = QueryParameter(default=1, ge=1),
                   query_id: str | None = QueryParameter(default=None, max_length=80)):
        return service.statistics(history_seconds,page,per_page,ingestion_page,query_id=query_id)

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
        try:
            documents, scope_label = service.topics.scope(body.topic_id, service.list_documents())
        except TopicError as error:
            raise HTTPException(404, str(error)) from error
        # Freeze membership when accepted: moving files while queued cannot broaden a query.
        document_names = frozenset(item['name'] for item in documents)
        if not (service.config.db / f"{service.config.collection}-manifest.json").exists() and not query_intent(body.question).mode.startswith('catalog_'):
            raise HTTPException(409, "Añade e indexa documentos en Administración antes de consultar.")
        settings = service.settings.get()
        if "k" not in body.model_fields_set:
            body.k = settings.k
        if "search_only" not in body.model_fields_set:
            body.search_only = settings.search_only
        if body.images:
            if body.search_only or query_intent(body.question).mode.startswith('catalog_'):
                raise HTTPException(422,'Las imágenes adjuntas necesitan una consulta con generación, no solo recuperación o inventario.')
            try:
                body.images=normalize_query_images(body.images,settings.max_images)
            except ValueError as error:
                raise HTTPException(422,str(error)) from error
        provider_key = None
        generates = not body.search_only and not query_intent(body.question).mode.startswith('catalog_')
        if generates and settings.response_provider != 'ollama':
            if not settings.external_model.strip() and not (body.images and settings.external_vision_model):
                raise HTTPException(422, 'Escribe el identificador exacto del modelo externo en Configuración.')
            try:
                provider_key = service.credentials.get(settings.response_provider)
                if body.images:
                    generation_providers.require_vision_model(settings.response_provider, settings.external_vision_model, provider_key)
                    limit = provider_vision.image_limit(settings.response_provider, settings.external_vision_model)
                    if len(body.images) > limit:
                        raise HTTPException(422, f'El modelo visual admite como máximo {limit} imágenes por consulta.')
            except CredentialError as error:
                raise HTTPException(409, str(error)) from error
            except generation_providers.ProviderError as error:
                raise HTTPException(422, str(error)) from error
        return service.start("query", lambda job_id: service.query(job_id, body, document_names, scope_label,
                             preferences=settings, provider_key=provider_key),
                             question=body.question, search_only=body.search_only, k=body.k, language=body.language,
                             image_count=len(body.images), topic_id=body.topic_id, scope_label=scope_label)

    @app.get('/api/library')
    def library():
        return service.topics.snapshot(service.list_documents())

    @app.post('/api/admin/topics', status_code=201)
    def create_topic(body: TopicRequest):
        try:
            return {'id': service.topics.create(body.name, body.parent_id)}
        except TopicError as error:
            raise HTTPException(409, str(error)) from error

    @app.patch('/api/admin/topics/{topic_id}')
    def rename_topic(topic_id: str, body: TopicRequest):
        if body.parent_id is not None:
            raise HTTPException(422, 'Para organizar documentos, usa la acción Mover.')
        try:
            service.topics.rename(topic_id, body.name)
            return {'ok': True}
        except TopicError as error:
            raise HTTPException(409, str(error)) from error

    @app.delete('/api/admin/topics/{topic_id}')
    def remove_topic(topic_id: str):
        try:
            service.topics.remove(topic_id,existing_names={item['name'] for item in service.list_documents()})
            return {'ok': True}
        except TopicError as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/admin/library/assign')
    def assign_documents(body: AssignmentRequest):
        available = {item['name'] for item in service.list_documents()}
        if not set(body.names) <= available:
            raise HTTPException(404, 'Algún documento ya no está disponible. Actualiza la biblioteca.')
        try:
            service.topics.assign(body.names, body.topic_id)
            return {'ok': True, 'assigned': len(set(body.names))}
        except TopicError as error:
            raise HTTPException(409, str(error)) from error

    @app.post('/api/admin/library/empty')
    def empty_library(body: EmptyLibraryRequest, request: Request):
        require_local_credentials(request, "La biblioteca solo puede vaciarse desde el propio equipo que ejecuta RAG Local.")
        return service.empty_library(keep_captions=body.keep_captions)

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
        preferences=service.settings.get()
        return service.start("index", lambda job_id: service.index(job_id, name,preferences=preferences), document_name=name)

    @app.post("/api/admin/upload")
    async def upload(files: Annotated[list[UploadFile], File()], auto_index: Annotated[bool, Form()] = True,
                     batch_id: Annotated[str, Form()] = "", topic_id: Annotated[str, Form(max_length=64)] = ""):
        if not 1 <= len(files) <= 20:
            raise HTTPException(422, "Selecciona entre 1 y 20 documentos.")
        if topic_id:
            try:
                service.topics.scope(topic_id, [])
            except TopicError as error:
                raise HTTPException(422, str(error)) from error
            if topic_id == 'unassigned':
                raise HTTPException(422, 'Selecciona una categoría o subcategoría como destino.')
        # Validate all names before saving any file. Existing documents are never overwritten.
        for file in files:
            name = file.filename or ""
            if Path(name).suffix.lower() not in ALLOWED:
                return JSONResponse({"detail": "Formato no compatible.", "error_info": {"code": "UNSUPPORTED_TYPE", "message": f"Formato no compatible ({Path(name).suffix}).", "detail": "PDF, TXT o Markdown."}}, status_code=422)
            reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
            if Path(name).name != name or ".." in name or any(ord(char) < 32 or char in '\\/:*?"<>|' for char in name) or name.endswith((".", " ")) or name.split('.')[0].upper() in reserved:
                return JSONResponse({"detail": "Nombre de archivo no válido.", "error_info": {"code": "INVALID_NAME", "message": "El nombre contiene caracteres o rutas no válidos.", "detail": name}}, status_code=422)
        preferences = service.settings.get()
        saved = []
        created = []
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
                    created.append(target.name)
                    break
                except FileExistsError:
                    if target.is_file() and hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(content).digest():
                        saved.append(target.name)
                        break
                    continue
            else:
                raise HTTPException(409, "Demasiados documentos con el mismo nombre.")
        if topic_id:
            try:
                # Re-uploading identical content keeps the existing organization.
                service.topics.assign(created, topic_id)
            except TopicError as error:
                raise HTTPException(409, str(error)) from error
        result = {"saved": saved, "document_id": hashlib.sha256(saved[0].encode()).hexdigest()[:24]}
        if auto_index:
            name = saved[0] if len(saved) == 1 else None
            result.update(service.start("index", lambda job_id: service.index(job_id, name,files=frozenset(saved),preferences=preferences),
                document_name=name, document_id=result["document_id"], batch_id=batch_id[:100],
                bytes=len(payloads[0][1]) if len(payloads) == 1 else sum(len(item[1]) for item in payloads)))
        return result

    @app.get("/api/source")
    def source(file: str):
        path = (service.documents / file).resolve()
        if not path.is_relative_to(service.documents) or not path.is_file() or path.suffix.lower() not in ALLOWED:
            raise HTTPException(404, "Documento no disponible.")
        return FileResponse(path, filename=path.name,
                            content_disposition_type="inline" if path.suffix.lower() == ".pdf" else "attachment",
                            media_type=mimetypes.guess_type(path.name)[0] or "application/octet-stream")

    return app


app = create_app()
