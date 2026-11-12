"""Private datasets, recoverable latest results and coherent temporary snapshots."""
from contextlib import closing, contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import logging
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile

import rag
from index_lock import index_writer
from retrieval_evaluation import LabError, corpus_identity

LOGGER = logging.getLogger('rag.laboratory')


class LaboratoryStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.path = self.root / 'lab.sqlite3'
        self.datasets = self.root / 'datasets'
        self.snapshots = self.root / 'snapshots'
        self.cleanup_marker = self.root / 'cleanup-pending'

    @contextmanager
    def connect(self):
        self.root.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                db.execute('''CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, created_at TEXT NOT NULL,
                    status TEXT NOT NULL, job_id TEXT, request TEXT NOT NULL,
                    report TEXT NOT NULL, error TEXT)''')
                yield db
        finally:
            db.close()

    def create(self, name, content, request):
        digest = hashlib.sha256(content.encode('utf-8')).hexdigest()
        self.datasets.mkdir(parents=True, exist_ok=True)
        target = self.datasets / f'{digest}.jsonl'
        if target.exists():
            if target.read_bytes() != content.encode('utf-8'):
                raise LabError('dataset_changed')
        else:
            pending = target.with_suffix('.pending')
            pending.write_bytes(content.encode('utf-8'))
            pending.replace(target)
        identifier = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()
        initial = {'configuration': request['configuration'], 'dataset_sha256': digest,
                   'summary': {'completed': 0, 'total': request['questions'], 'annotated': 0,
                               'found': 0, 'without_evidence': 0, 'evidence_recall': None},
                   'queries': [], 'generation_evaluated': False}
        with self.connect() as db:
            db.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,NULL)',
                (identifier, name, now, 'queued', None,
                 json.dumps({**request, 'dataset_sha256': digest}, ensure_ascii=False),
                 json.dumps(initial, ensure_ascii=False)))
        return identifier

    def dataset(self, digest):
        if not re.fullmatch(r'[a-f0-9]{64}', digest):
            raise LabError('dataset_changed')
        content = (self.datasets / f'{digest}.jsonl').read_bytes()
        if hashlib.sha256(content).hexdigest() != digest:
            raise LabError('dataset_changed')
        return content.decode('utf-8')

    def get(self, identifier):
        with self.connect() as db:
            row = db.execute('SELECT * FROM runs WHERE id=?', (identifier,)).fetchone()
        if row is None:
            return None
        result = dict(row)
        for field in ('request', 'report', 'error'):
            result[field] = json.loads(result[field]) if result[field] else None
        return result

    def latest(self):
        with self.connect() as db:
            row = db.execute('SELECT id FROM runs ORDER BY created_at DESC LIMIT 1').fetchone()
        return self.get(row['id']) if row else None

    def save_report(self, identifier, report):
        with self.connect() as db:
            db.execute('UPDATE runs SET report=? WHERE id=?',
                (json.dumps(report, ensure_ascii=False, allow_nan=False), identifier))

    def fail(self, identifier, error):
        with self.connect() as db:
            db.execute('UPDATE runs SET error=? WHERE id=?', (json.dumps(error), identifier))

    def mark_cleanup_pending(self, identifier):
        run = self.get(identifier)
        if run is not None:
            self.save_report(identifier, {**run['report'], 'cleanup_pending': True})

    def observe(self, job):
        if job.get('kind') != 'evaluation' or not job.get('lab_run_id'):
            return
        with self.connect() as db:
            db.execute('UPDATE runs SET status=?, job_id=?, error=COALESCE(error,?) WHERE id=?',
                (job['status'], job['id'], json.dumps(job['error_info']) if job.get('error_info') else None,
                 job['lab_run_id']))
            if job['status'] not in {'queued', 'running'}:
                # Technical reload recovery only; phase 1 has no history browser.
                db.execute("""DELETE FROM runs WHERE id IN (SELECT id FROM runs
                    WHERE status NOT IN ('queued','running') ORDER BY created_at DESC LIMIT -1 OFFSET 5)""")
        if job['status'] not in {'queued', 'running'}:
            self.prune_datasets()

    def prune_datasets(self):
        with self.connect() as db:
            retained = {json.loads(row[0])['dataset_sha256'] for row in db.execute('SELECT request FROM runs')}
        if self.datasets.exists():
            for path in self.datasets.glob('*.jsonl'):
                if re.fullmatch(r'[a-f0-9]{64}', path.stem) and path.stem not in retained and not path.is_symlink():
                    path.unlink()

    def discard_unsubmitted(self, identifier):
        with self.connect() as db:
            db.execute("DELETE FROM runs WHERE id=? AND status='queued' AND job_id IS NULL", (identifier,))

    def recover(self):
        """Startup only, before workers exist; preserve results and remove our roots."""
        if self.path.exists():
            with self.connect() as db:
                db.execute("UPDATE runs SET status='interrupted' WHERE status IN ('queued','running')")
            self.prune_datasets()
        remaining = False
        if self.snapshots.exists():
            root = self.snapshots.resolve()
            for path in self.snapshots.glob('run-*'):
                if path.is_dir() and not path.is_symlink() and path.resolve().parent == root:
                    try:
                        shutil.rmtree(path)
                    except OSError:
                        remaining = True
                        LOGGER.warning('Temporary index cleanup still pending; measurements are preserved.')
        if remaining:
            try:
                self.cleanup_marker.touch()
            except OSError:
                LOGGER.warning('Could not persist the deferred-cleanup warning.')
        else:
            self.cleanup_marker.unlink(missing_ok=True)
            if self.path.exists():
                with self.connect() as db:
                    for row in db.execute('SELECT id,report FROM runs').fetchall():
                        report = json.loads(row['report'])
                        if report.get('cleanup_pending'):
                            report['cleanup_pending'] = False
                            db.execute('UPDATE runs SET report=? WHERE id=?', (json.dumps(report), row['id']))

    @contextmanager
    def snapshot(self, config, expected, cancelled, cleanup_pending=None):
        """Read committed WAL data via SQLite backup; never create FTS on the source."""
        from vector_store import VectorStore
        self.snapshots.mkdir(parents=True, exist_ok=True)
        copied_path = None
        temporary = tempfile.TemporaryDirectory(prefix='run-', dir=self.snapshots, ignore_cleanup_errors=True)
        folder = temporary.name
        try:
            frozen = replace(config, db=Path(folder))
            try:
                def check(*_):
                    if cancelled():
                        raise InterruptedError('Experimento cancelado.')
                with index_writer(config):
                    check()
                    state = rag.load_state(config)
                    if corpus_identity(config, state)['fingerprint'] != expected:
                        raise LabError('changed')
                    source = config.db / f'{config.collection}-vectors.sqlite3'
                    needed = sum(path.stat().st_size for path in (source, Path(str(source) + '-wal')) if path.is_file())
                    if shutil.disk_usage(self.root).free < needed + max(2 * 1024**2, needed // 10):
                        raise LabError('disk')
                    copied_path = frozen.db / source.name
                    with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as original:
                        with closing(sqlite3.connect(copied_path)) as copied:
                            original.backup(copied, pages=256, progress=check)
                    rag.save_state(frozen, state)
                yield frozen
            finally:
                if copied_path is not None:
                    VectorStore.clear_cache(copied_path)
        finally:
            temporary.cleanup()
            if Path(folder).exists():
                LOGGER.warning('Temporary index cleanup deferred until startup; measurements are preserved.')
                try:
                    self.cleanup_marker.touch()
                    if cleanup_pending:
                        cleanup_pending()
                except Exception:
                    LOGGER.exception('Could not persist the deferred-cleanup warning.')
