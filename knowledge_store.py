"""Versioned structured knowledge, independent from document/vector storage."""
from contextlib import contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import sqlite3
from urllib.parse import urlsplit
from uuid import uuid4


SCHEMA_VERSION = 2
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9._-]{0,79}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class KnowledgeError(ValueError):
    """A knowledge operation would violate the publication contract."""


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _identifier(value, label):
    value = str(value or "").strip().lower()
    if not _IDENTIFIER.fullmatch(value):
        raise KnowledgeError(f"Invalid {label} identifier.")
    return value


def _text(value, label, *, maximum=240):
    value = str(value or "").strip()
    if not value or len(value) > maximum or any(ord(character) < 32 for character in value):
        raise KnowledgeError(f"Invalid {label}.")
    return value


def _https_url(value, label):
    value = str(value or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise KnowledgeError(f"{label} must be an HTTPS URL without embedded credentials.")
    return value


SCHEMA = """
CREATE TABLE spaces (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    vector_collection TEXT NOT NULL UNIQUE,
    default_ingestion_profile TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE sources (
    id TEXT PRIMARY KEY,
    space_id TEXT NOT NULL REFERENCES spaces(id) ON DELETE RESTRICT,
    kind TEXT NOT NULL,
    name TEXT NOT NULL,
    canonical_url TEXT NOT NULL,
    license_name TEXT,
    license_url TEXT,
    published_snapshot_id TEXT REFERENCES snapshots(id) ON DELETE RESTRICT,
    enabled INTEGER NOT NULL DEFAULT 1 CHECK (enabled IN (0, 1)),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX sources_space ON sources(space_id, id);

CREATE TABLE snapshots (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    status TEXT NOT NULL CHECK (status IN ('staged', 'published', 'superseded', 'failed')),
    fetched_at TEXT NOT NULL,
    source_updated_at TEXT,
    etag TEXT,
    last_modified TEXT,
    original_path TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    published_at TEXT
);
CREATE INDEX snapshots_source_status ON snapshots(source_id, status, created_at);
CREATE UNIQUE INDEX snapshots_active_digest ON snapshots(source_id, sha256)
    WHERE status IN ('staged', 'published', 'superseded');

CREATE TABLE sync_runs (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
    snapshot_id TEXT REFERENCES snapshots(id) ON DELETE RESTRICT,
    status TEXT NOT NULL CHECK (status IN ('running', 'published', 'unchanged', 'failed', 'cancelled')),
    started_at TEXT NOT NULL,
    finished_at TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}',
    error_code TEXT,
    error_message TEXT
);
CREATE INDEX sync_runs_source_time ON sync_runs(source_id, started_at DESC);
CREATE UNIQUE INDEX sync_runs_one_running ON sync_runs(source_id) WHERE status = 'running';

CREATE TABLE vulnerabilities (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    cve_id TEXT NOT NULL,
    published_at TEXT,
    modified_at TEXT,
    status TEXT NOT NULL,
    description TEXT NOT NULL,
    cvss_base REAL,
    cvss_version TEXT,
    cvss_vector TEXT,
    cvss_origin TEXT,
    origin TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, cve_id)
);
CREATE INDEX vulnerabilities_cve ON vulnerabilities(cve_id);

CREATE TABLE vulnerability_weaknesses (
    snapshot_id TEXT NOT NULL,
    cve_id TEXT NOT NULL,
    cwe_id TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, cve_id, cwe_id, origin),
    FOREIGN KEY(snapshot_id, cve_id)
        REFERENCES vulnerabilities(snapshot_id, cve_id) ON DELETE CASCADE
);
CREATE INDEX vulnerability_weaknesses_cwe ON vulnerability_weaknesses(cwe_id, cve_id);

CREATE TABLE vulnerability_references (
    snapshot_id TEXT NOT NULL,
    cve_id TEXT NOT NULL,
    url TEXT NOT NULL,
    tags_json TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, cve_id, url, origin),
    FOREIGN KEY(snapshot_id, cve_id)
        REFERENCES vulnerabilities(snapshot_id, cve_id) ON DELETE CASCADE
);

CREATE TABLE kev (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    cve_id TEXT NOT NULL,
    date_added TEXT NOT NULL,
    vendor TEXT NOT NULL,
    vendor_key TEXT NOT NULL,
    product TEXT NOT NULL,
    product_key TEXT NOT NULL,
    known_ransomware_use TEXT,
    ransomware_known INTEGER CHECK (ransomware_known IN (0, 1) OR ransomware_known IS NULL),
    required_action TEXT NOT NULL,
    due_date TEXT,
    notes TEXT,
    origin TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, cve_id)
);
CREATE INDEX kev_cve ON kev(cve_id);
CREATE INDEX kev_vendor_product ON kev(vendor_key, product_key);

CREATE TABLE epss (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    cve_id TEXT NOT NULL,
    score REAL NOT NULL CHECK (score >= 0 AND score <= 1),
    percentile REAL NOT NULL CHECK (percentile >= 0 AND percentile <= 1),
    score_date TEXT NOT NULL,
    origin TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, cve_id)
);
CREATE INDEX epss_cve ON epss(cve_id);

CREATE TABLE affected (
    snapshot_id TEXT NOT NULL REFERENCES snapshots(id) ON DELETE CASCADE,
    record_id TEXT NOT NULL,
    cve_id TEXT NOT NULL,
    advisory_id TEXT,
    ecosystem TEXT NOT NULL,
    package_name TEXT NOT NULL,
    range_type TEXT,
    events_json TEXT NOT NULL DEFAULT '[]',
    versions_json TEXT NOT NULL DEFAULT '[]',
    origin TEXT NOT NULL,
    PRIMARY KEY(snapshot_id, record_id)
);
CREATE INDEX affected_cve ON affected(cve_id, ecosystem, package_name);

CREATE VIEW current_vulnerabilities AS
    SELECT records.* FROM vulnerabilities records
    JOIN sources ON sources.published_snapshot_id = records.snapshot_id;
CREATE VIEW current_vulnerability_weaknesses AS
    SELECT records.* FROM vulnerability_weaknesses records
    JOIN sources ON sources.published_snapshot_id = records.snapshot_id;
CREATE VIEW current_vulnerability_references AS
    SELECT records.* FROM vulnerability_references records
    JOIN sources ON sources.published_snapshot_id = records.snapshot_id;
CREATE VIEW current_kev AS
    SELECT records.* FROM kev records
    JOIN sources ON sources.published_snapshot_id = records.snapshot_id;
CREATE VIEW current_epss AS
    SELECT records.* FROM epss records
    JOIN sources ON sources.published_snapshot_id = records.snapshot_id;
CREATE VIEW current_affected AS
    SELECT records.* FROM affected records
    JOIN sources ON sources.published_snapshot_id = records.snapshot_id;
"""


class KnowledgeStore:
    """Structured records with staged snapshots and atomic per-source publication."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self._ensure_schema()

    @contextmanager
    def connect(self, *, write=False):
        target = str(self.path) if write else self.path.as_uri() + "?mode=ro"
        db = sqlite3.connect(target, uri=not write, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        if write:
            db.execute("PRAGMA synchronous=FULL")
        else:
            db.execute("PRAGMA query_only=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _ensure_schema(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect(write=True) as db:
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if version == SCHEMA_VERSION:
                return
            if version == 1:
                self._migrate_one_to_two(db)
                return
            if version != 0:
                raise KnowledgeError(f"Unsupported knowledge database format: {version}.")
            existing = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
            if existing:
                raise KnowledgeError("Refusing to adopt an unversioned non-empty database.")
            db.execute("PRAGMA journal_mode=WAL")
            db.executescript(SCHEMA)
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")

    @staticmethod
    def _migrate_one_to_two(db):
        """Allow retrying failed content while retaining active digest uniqueness."""
        db.commit()
        db.execute("PRAGMA foreign_keys=OFF")
        try:
            db.execute("BEGIN IMMEDIATE")
            db.execute("""
                CREATE TABLE snapshots_v2 (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
                    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
                    status TEXT NOT NULL CHECK (status IN ('staged', 'published', 'superseded', 'failed')),
                    fetched_at TEXT NOT NULL,
                    source_updated_at TEXT,
                    etag TEXT,
                    last_modified TEXT,
                    original_path TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    published_at TEXT
                )
            """)
            db.execute("INSERT INTO snapshots_v2 SELECT * FROM snapshots")
            db.execute("DROP TABLE snapshots")
            db.execute("ALTER TABLE snapshots_v2 RENAME TO snapshots")
            db.execute("CREATE INDEX snapshots_source_status ON snapshots(source_id,status,created_at)")
            db.execute("""CREATE UNIQUE INDEX snapshots_active_digest
                          ON snapshots(source_id,sha256)
                          WHERE status IN ('staged','published','superseded')""")
            violations = db.execute("PRAGMA foreign_key_check").fetchall()
            if violations:
                raise KnowledgeError("Knowledge database migration failed its foreign-key check.")
            db.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.execute("PRAGMA foreign_keys=ON")

    def upsert_space(self, identifier, name, *, vector_collection=None,
                     default_ingestion_profile="structured"):
        identifier = _identifier(identifier, "space")
        vector_collection = _identifier(vector_collection or identifier, "vector collection")
        name = _text(name, "space name", maximum=100)
        profile = _identifier(default_ingestion_profile, "ingestion profile")
        now = utc_now()
        with self.connect(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                """INSERT INTO spaces VALUES (?,?,?,?,?,?)
                   ON CONFLICT(id) DO UPDATE SET name=excluded.name,
                       vector_collection=excluded.vector_collection,
                       default_ingestion_profile=excluded.default_ingestion_profile,
                       updated_at=excluded.updated_at""",
                (identifier, name, vector_collection, profile, now, now),
            )
        return identifier

    def upsert_source(self, identifier, space_id, kind, name, canonical_url, *,
                      license_name=None, license_url=None, enabled=True):
        identifier = _identifier(identifier, "source")
        space_id = _identifier(space_id, "space")
        kind = _identifier(kind, "source kind")
        name = _text(name, "source name", maximum=160)
        canonical_url = _https_url(canonical_url, "Canonical source URL")
        if license_name is not None:
            license_name = _text(license_name, "license name", maximum=160)
        if license_url is not None:
            license_url = _https_url(license_url, "License URL")
        now = utc_now()
        try:
            with self.connect(write=True) as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    """INSERT INTO sources
                       (id,space_id,kind,name,canonical_url,license_name,license_url,
                        published_snapshot_id,enabled,created_at,updated_at)
                       VALUES (?,?,?,?,?,?,?,NULL,?,?,?)
                       ON CONFLICT(id) DO UPDATE SET space_id=excluded.space_id,
                           kind=excluded.kind,name=excluded.name,
                           canonical_url=excluded.canonical_url,
                           license_name=excluded.license_name,
                           license_url=excluded.license_url,
                           enabled=excluded.enabled,updated_at=excluded.updated_at""",
                    (identifier, space_id, kind, name, canonical_url, license_name,
                     license_url, int(bool(enabled)), now, now),
                )
        except sqlite3.IntegrityError as error:
            raise KnowledgeError("The source refers to an unavailable space or duplicate identity.") from error
        return identifier

    def start_sync(self, source_id, *, run_id=None):
        source_id = _identifier(source_id, "source")
        run_id = _identifier(run_id or uuid4().hex, "sync run")
        try:
            with self.connect(write=True) as db:
                db.execute("BEGIN IMMEDIATE")
                if db.execute("SELECT 1 FROM sources WHERE id=? AND enabled=1", (source_id,)).fetchone() is None:
                    raise KnowledgeError("The source is unavailable or disabled.")
                db.execute(
                    "INSERT INTO sync_runs VALUES (?,?,NULL,'running',?,NULL,'{}',NULL,NULL)",
                    (run_id, source_id, utc_now()),
                )
        except sqlite3.IntegrityError as error:
            raise KnowledgeError("A synchronization is already running for this source.") from error
        return run_id

    def recover_abandoned(self, source_id):
        """Fail orphaned runs; caller must first own the source's operating-system lock."""
        source_id = _identifier(source_id, "source")
        now = utc_now()
        with self.connect(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute(
                "SELECT id,snapshot_id FROM sync_runs WHERE source_id=? AND status='running'",
                (source_id,),
            ).fetchall()
            snapshots = [(row["snapshot_id"],) for row in rows if row["snapshot_id"]]
            if snapshots:
                db.executemany(
                    "UPDATE snapshots SET status='failed' WHERE id=? AND status='staged'", snapshots
                )
            db.execute(
                """UPDATE sync_runs SET status='failed',finished_at=?,error_code='abandoned',
                       error_message='The previous synchronization process ended before completion.'
                   WHERE source_id=? AND status='running'""",
                (now, source_id),
            )
        return len(rows)

    def stage_snapshot(self, run_id, sha256, original_path, *, snapshot_id=None,
                       fetched_at=None, source_updated_at=None, etag=None,
                       last_modified=None, metadata=None):
        run_id = _identifier(run_id, "sync run")
        snapshot_id = _identifier(snapshot_id or uuid4().hex, "snapshot")
        sha256 = str(sha256 or "").strip().lower()
        if not _SHA256.fullmatch(sha256):
            raise KnowledgeError("Invalid SHA-256 digest.")
        original_path = _text(original_path, "snapshot path", maximum=1000)
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True,
                                   separators=(",", ":"))
        try:
            with self.connect(write=True) as db:
                db.execute("BEGIN IMMEDIATE")
                run = db.execute(
                    "SELECT source_id,snapshot_id,status FROM sync_runs WHERE id=?", (run_id,)
                ).fetchone()
                if run is None or run["status"] != "running":
                    raise KnowledgeError("The synchronization is not running.")
                if run["snapshot_id"] is not None:
                    raise KnowledgeError("The synchronization already has a staged snapshot.")
                db.execute(
                    """INSERT INTO snapshots
                       (id,source_id,sha256,status,fetched_at,source_updated_at,etag,
                        last_modified,original_path,metadata_json,created_at,published_at)
                       VALUES (?,?,?,'staged',?,?,?,?,?,?,?,NULL)""",
                    (snapshot_id, run["source_id"], sha256, fetched_at or utc_now(),
                     source_updated_at, etag, last_modified, original_path,
                     metadata_json, utc_now()),
                )
                db.execute("UPDATE sync_runs SET snapshot_id=? WHERE id=?", (snapshot_id, run_id))
        except sqlite3.IntegrityError as error:
            raise KnowledgeError("This source content has already been staged or published.") from error
        return snapshot_id

    @contextmanager
    def write_staged(self, snapshot_id):
        """Write normalized rows only while a snapshot is private and staged."""
        snapshot_id = _identifier(snapshot_id, "snapshot")
        with self.connect(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM snapshots WHERE id=?", (snapshot_id,)).fetchone()
            if row is None or row["status"] != "staged":
                raise KnowledgeError("Only a staged snapshot can receive records.")
            yield db

    def publish(self, run_id, *, stats=None):
        """Atomically replace one source's visible snapshot and finish its run."""
        run_id = _identifier(run_id, "sync run")
        now = utc_now()
        stats_json = json.dumps(stats or {}, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"))
        with self.connect(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute(
                "SELECT source_id,snapshot_id,status FROM sync_runs WHERE id=?", (run_id,)
            ).fetchone()
            if run is None or run["status"] != "running" or run["snapshot_id"] is None:
                raise KnowledgeError("The synchronization has no publishable snapshot.")
            snapshot = db.execute(
                "SELECT source_id,status FROM snapshots WHERE id=?", (run["snapshot_id"],)
            ).fetchone()
            if snapshot is None or snapshot["status"] != "staged" or snapshot["source_id"] != run["source_id"]:
                raise KnowledgeError("The staged snapshot does not belong to this synchronization.")
            source = db.execute(
                "SELECT published_snapshot_id FROM sources WHERE id=?", (run["source_id"],)
            ).fetchone()
            previous = source["published_snapshot_id"]
            if previous:
                db.execute(
                    "UPDATE snapshots SET status='superseded' WHERE id=? AND status='published'",
                    (previous,),
                )
            db.execute(
                "UPDATE snapshots SET status='published',published_at=? WHERE id=?",
                (now, run["snapshot_id"]),
            )
            db.execute(
                "UPDATE sources SET published_snapshot_id=?,updated_at=? WHERE id=?",
                (run["snapshot_id"], now, run["source_id"]),
            )
            db.execute(
                """UPDATE sync_runs SET status='published',finished_at=?,stats_json=?
                   WHERE id=?""",
                (now, stats_json, run_id),
            )
        return run["snapshot_id"]

    def finish_without_change(self, run_id, *, stats=None):
        run_id = _identifier(run_id, "sync run")
        stats_json = json.dumps(stats or {}, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":"))
        with self.connect(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT status,snapshot_id FROM sync_runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] != "running" or run["snapshot_id"] is not None:
                raise KnowledgeError("Only a running synchronization without a snapshot can be unchanged.")
            db.execute(
                "UPDATE sync_runs SET status='unchanged',finished_at=?,stats_json=? WHERE id=?",
                (utc_now(), stats_json, run_id),
            )

    def fail_sync(self, run_id, code, message, *, cancelled=False):
        run_id = _identifier(run_id, "sync run")
        code = _identifier(code, "error code")
        message = _text(message, "error message", maximum=1000)
        status = "cancelled" if cancelled else "failed"
        with self.connect(write=True) as db:
            db.execute("BEGIN IMMEDIATE")
            run = db.execute("SELECT status,snapshot_id FROM sync_runs WHERE id=?", (run_id,)).fetchone()
            if run is None or run["status"] != "running":
                raise KnowledgeError("Only a running synchronization can fail or be cancelled.")
            if run["snapshot_id"]:
                db.execute(
                    "UPDATE snapshots SET status='failed' WHERE id=? AND status='staged'",
                    (run["snapshot_id"],),
                )
            db.execute(
                """UPDATE sync_runs SET status=?,finished_at=?,error_code=?,error_message=?
                   WHERE id=?""",
                (status, utc_now(), code, message, run_id),
            )

    def published_snapshot(self, source_id):
        source_id = _identifier(source_id, "source")
        with self.connect() as db:
            row = db.execute(
                """SELECT snapshots.* FROM sources
                   JOIN snapshots ON snapshots.id=sources.published_snapshot_id
                   WHERE sources.id=?""",
                (source_id,),
            ).fetchone()
        return dict(row) if row else None

    def list_spaces(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM spaces ORDER BY name COLLATE NOCASE,id"
            )]

    def list_sources(self, *, space_id=None):
        parameters = ()
        where = ""
        if space_id is not None:
            space_id = _identifier(space_id, "space")
            where = " WHERE space_id=?"
            parameters = (space_id,)
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM sources" + where + " ORDER BY name COLLATE NOCASE,id", parameters
            )]

    def published_vendors(self):
        """Return the normalized KEV vendor vocabulary without loading record text."""
        with self.connect() as db:
            return [dict(row) for row in db.execute(
                """SELECT vendor_key,MIN(vendor) AS vendor FROM current_kev
                   GROUP BY vendor_key ORDER BY vendor_key"""
            )]
