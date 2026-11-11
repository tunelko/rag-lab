"""Shared download, raw preservation and publication mechanics."""
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import unicodedata
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, ProxyHandler, Request
from uuid import uuid4

from knowledge_store import KnowledgeError


class ConnectorError(RuntimeError):
    pass


@dataclass(frozen=True)
class Download:
    content: bytes
    url: str
    final_url: str
    etag: str | None = None
    last_modified: str | None = None
    not_modified: bool = False


@dataclass(frozen=True)
class SyncResult:
    source: str
    status: str
    snapshot_id: str | None
    records: int
    sha256: str | None


class _HTTPSRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, message, headers, new_url):
        if urlsplit(new_url).scheme != "https":
            raise ConnectorError("A source attempted to redirect outside HTTPS.")
        return super().redirect_request(request, fp, code, message, headers, new_url)


class HTTPClient:
    """Small HTTPS-only client that ignores ambient proxy configuration."""

    def __init__(self, *, timeout=30):
        self.timeout = timeout
        self.opener = build_opener(ProxyHandler({}), _HTTPSRedirects())

    def get(self, url, *, headers=None, max_bytes=32 * 1024 * 1024):
        if urlsplit(url).scheme != "https":
            raise ConnectorError("Knowledge sources must use HTTPS.")
        request = Request(url, headers={
            "Accept": "application/json,text/csv,application/gzip;q=0.9,*/*;q=0.1",
            "User-Agent": "RAG-Local/1.0",
            **(headers or {}),
        })
        try:
            response = self.opener.open(request, timeout=self.timeout)
        except HTTPError as error:
            if error.code == 304:
                return Download(b"", url, error.geturl(), error.headers.get("ETag"),
                                error.headers.get("Last-Modified"), True)
            raise ConnectorError(f"Source returned HTTP {error.code}.") from error
        except (OSError, URLError) as error:
            raise ConnectorError(f"Could not download source: {error}.") from error
        with response:
            final_url = response.geturl()
            if urlsplit(final_url).scheme != "https":
                raise ConnectorError("A source resolved outside HTTPS.")
            declared = response.headers.get("Content-Length")
            if declared and declared.isdigit() and int(declared) > max_bytes:
                raise ConnectorError("Source response exceeds the configured size limit.")
            chunks, total = [], 0
            while True:
                chunk = response.read(min(1024 * 1024, max_bytes + 1 - total))
                if not chunk:
                    break
                chunks.append(chunk)
                total += len(chunk)
                if total > max_bytes:
                    raise ConnectorError("Source response exceeds the configured size limit.")
            return Download(b"".join(chunks), url, final_url, response.headers.get("ETag"),
                            response.headers.get("Last-Modified"))


@contextmanager
def source_writer(root, source_id):
    """Crash-released per-source writer lock shared by all synchronizer processes."""
    directory = Path(root).resolve() / "locks"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{source_id}.lock"
    with path.open("a+b") as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            raise ConnectorError(f"Another process is synchronizing {source_id}.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def normalized_key(value):
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return " ".join(value.split())


def canonical_cve(value):
    value = str(value or "").strip().upper()
    if not re.fullmatch(r"CVE-\d{4}-\d{4,}", value):
        raise ConnectorError(f"Invalid CVE identifier: {value or '<empty>'}.")
    return value


def iso_date(value, label, *, optional=False):
    value = str(value or "").strip()
    if not value and optional:
        return None
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError as error:
        raise ConnectorError(f"{label} must use YYYY-MM-DD.") from error
    return value


def iso_timestamp(value, label, *, optional=False):
    value = str(value or "").strip()
    if not value and optional:
        return None
    if len(value) < 10:
        raise ConnectorError(f"{label} must start with an ISO-8601 date.")
    iso_date(value[:10], label)
    return value


def json_object(content, source):
    try:
        value = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ConnectorError(f"{source} did not return valid UTF-8 JSON.") from error
    if not isinstance(value, dict):
        raise ConnectorError(f"{source} did not return a JSON object.")
    return value


def conditional_headers(store, source_id):
    snapshot = store.published_snapshot(source_id)
    if not snapshot:
        return {}
    headers = {}
    if snapshot.get("etag"):
        headers["If-None-Match"] = snapshot["etag"]
    if snapshot.get("last_modified"):
        headers["If-Modified-Since"] = snapshot["last_modified"]
    return headers


def fail_run(store, run_id, error):
    """Make a connector failure terminal even when an exception contains control text."""
    message = " ".join(str(error).split())[:900] or type(error).__name__
    try:
        store.fail_sync(run_id, "sync-error", message)
    except KnowledgeError:
        pass


def preserve_raw(root, source_id, filename, content):
    """Store immutable source bytes under their digest, returning path and digest."""
    digest = hashlib.sha256(content).hexdigest()
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(filename).name)[:160] or "source.bin"
    target = Path(root).resolve() / source_id / digest / safe_name
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
            raise ConnectorError("An immutable raw source file has changed on disk.")
        return target, digest
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("xb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, target)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
    return target, digest


class SingleFileConnector:
    """Template for one downloaded object becoming one published snapshot."""

    source_id = None
    space_id = "vulnerability-intelligence"
    source_kind = None
    source_name = None
    source_url = None
    license_name = None
    license_url = None
    filename = "source.bin"
    max_bytes = 32 * 1024 * 1024

    def register(self, store):
        store.upsert_space(
            self.space_id, "Vulnerability intelligence",
            vector_collection=self.space_id, default_ingestion_profile="structured",
        )
        store.upsert_source(
            self.source_id, self.space_id, self.source_kind, self.source_name,
            self.source_url, license_name=self.license_name, license_url=self.license_url,
        )

    def parse(self, content):
        raise NotImplementedError

    def insert(self, db, snapshot_id, parsed):
        raise NotImplementedError

    def metadata(self, parsed):
        return {}

    def sync(self, store, raw_root, *, client=None):
        client = client or HTTPClient()
        self.register(store)
        with source_writer(Path(raw_root).resolve().parent, self.source_id):
            store.recover_abandoned(self.source_id)
            return self._sync_locked(store, raw_root, client)

    def _sync_locked(self, store, raw_root, client):
        run = store.start_sync(self.source_id)
        try:
            downloaded = client.get(
                self.source_url, headers=conditional_headers(store, self.source_id),
                max_bytes=self.max_bytes,
            )
            if downloaded.not_modified:
                store.finish_without_change(run, stats={"reason": "http-304"})
                return SyncResult(self.source_id, "unchanged", None, 0, None)
            parsed = self.parse(downloaded.content)
            raw_path, digest = preserve_raw(raw_root, self.source_id, self.filename,
                                            downloaded.content)
            current = store.published_snapshot(self.source_id)
            if current and current["sha256"] == digest:
                store.finish_without_change(run, stats={"reason": "same-sha256"})
                return SyncResult(self.source_id, "unchanged", current["id"], 0, digest)
            snapshot = store.stage_snapshot(
                run, digest, str(raw_path), fetched_at=utc_now(), etag=downloaded.etag,
                last_modified=downloaded.last_modified, metadata=self.metadata(parsed),
            )
            with store.write_staged(snapshot) as db:
                records = self.insert(db, snapshot, parsed)
            store.publish(run, stats={"records": records})
            return SyncResult(self.source_id, "published", snapshot, records, digest)
        except Exception as error:
            fail_run(store, run, error)
            if isinstance(error, ConnectorError):
                raise
            raise ConnectorError(f"Could not synchronize {self.source_name}: {error}.") from error
