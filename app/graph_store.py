"""Versioned, domain-independent evidence and proposed relationships in SQLite."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import re
from pathlib import Path
import sqlite3
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator


def identifier(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=False).encode()).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def literal_span(source, quote):
    """Match unchanged words across PDF whitespace; return original source bytes."""
    start = source.find(quote)
    if start >= 0:
        return start, start + len(quote)
    wanted = re.findall(r'\S+', quote)
    words = list(re.finditer(r'\S+', source))
    if wanted:
        for offset in range(len(words) - len(wanted) + 1):
            if [word.group() for word in words[offset:offset + len(wanted)]] == wanted:
                return words[offset].start(), words[offset + len(wanted) - 1].end()
    raise ValueError('La cita no coincide con las palabras del pasaje original.')


class Entity(BaseModel):
    model_config = {'extra': 'forbid'}
    key: str = Field(min_length=1, max_length=80)
    label: str = Field(min_length=1, max_length=200)
    kind: str = Field(min_length=1, max_length=100)


class Assertion(BaseModel):
    model_config = {'extra': 'forbid'}
    subject: str = Field(min_length=1, max_length=80)
    predicate: str = Field(min_length=1, max_length=100)
    object: str = Field(min_length=1, max_length=80)
    quote: str = Field(min_length=8, max_length=2000)
    basis: Literal['explicit', 'inferred']
    polarity: Literal['positive', 'negative'] = 'positive'
    qualifiers: dict[str, Annotated[str, Field(max_length=300)]] = Field(default_factory=dict, max_length=10)


class Extraction(BaseModel):
    model_config = {'extra': 'forbid'}
    entities: list[Entity] = Field(max_length=20)
    assertions: list[Assertion] = Field(max_length=12)

    @model_validator(mode='after')
    def references(self):
        keys = {entity.key for entity in self.entities}
        if len(keys) != len(self.entities) or any(row.subject not in keys or row.object not in keys for row in self.assertions):
            raise ValueError('Las relaciones deben referenciar entidades únicas del mismo lote.')
        return self


class VerificationRequired(ValueError):
    pass


class GraphStore:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def connection(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        try:
            db.execute('PRAGMA foreign_keys=ON')
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS graph_meta(key TEXT PRIMARY KEY,value TEXT);
                INSERT OR IGNORE INTO graph_meta VALUES('schema','1');
                CREATE TABLE IF NOT EXISTS graph_units(id TEXT PRIMARY KEY,file TEXT,version TEXT,page INTEGER,
                    ordinal INTEGER,text TEXT,kind TEXT,metadata TEXT,active INTEGER NOT NULL,published_run TEXT);
                CREATE INDEX IF NOT EXISTS graph_file ON graph_units(file,active);
                CREATE TABLE IF NOT EXISTS graph_runs(id TEXT PRIMARY KEY,status TEXT,config TEXT,created TEXT,error TEXT);
                CREATE TABLE IF NOT EXISTS graph_batches(run TEXT REFERENCES graph_runs(id),unit TEXT REFERENCES graph_units(id),
                    status TEXT,result TEXT,PRIMARY KEY(run,unit));
                CREATE TABLE IF NOT EXISTS graph_entities(id TEXT PRIMARY KEY,unit TEXT REFERENCES graph_units(id),
                    local_key TEXT,label TEXT,kind TEXT,run TEXT REFERENCES graph_runs(id));
                CREATE TABLE IF NOT EXISTS graph_assertions(id TEXT PRIMARY KEY,unit TEXT REFERENCES graph_units(id),
                    subject TEXT REFERENCES graph_entities(id),predicate TEXT,object TEXT REFERENCES graph_entities(id),
                    quote TEXT,start INTEGER,end INTEGER,basis TEXT,polarity TEXT,qualifiers TEXT,
                    review TEXT NOT NULL,run TEXT REFERENCES graph_runs(id));
                CREATE INDEX IF NOT EXISTS graph_assertion_unit ON graph_assertions(unit);
                CREATE TABLE IF NOT EXISTS graph_reviews(id INTEGER PRIMARY KEY,assertion TEXT REFERENCES graph_assertions(id),
                    previous TEXT,current TEXT,created TEXT);
                CREATE TABLE IF NOT EXISTS graph_verifications(id INTEGER PRIMARY KEY,
                    assertion TEXT REFERENCES graph_assertions(id),status TEXT,note TEXT,created TEXT);
            ''')
            if db.execute("SELECT value FROM graph_meta WHERE key='schema'").fetchone()[0] != '1':
                raise ValueError('Versión del grafo no compatible.')
            with db:
                yield db
        finally:
            db.close()

    def publish_units(self, units, files):
        """Publish a prepared selection atomically; prior evidence stays immutable."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for file in files:
                db.execute('UPDATE graph_units SET active=0 WHERE file=?', (file,))
            for unit in units:
                db.execute('INSERT OR IGNORE INTO graph_units(id,file,version,page,ordinal,text,kind,metadata,active) VALUES(?,?,?,?,?,?,?,?,1)', (
                    unit['id'], unit['file'], unit['version'], unit['page'], unit['ordinal'],
                    unit['text'], unit['kind'], json.dumps(unit.get('metadata', {}), ensure_ascii=False)))
                db.execute('UPDATE graph_units SET active=1 WHERE id=?', (unit['id'],))

    def units(self, files=None):
        with self.connection() as db:
            rows = db.execute('SELECT * FROM graph_units WHERE active=1 ORDER BY file,ordinal').fetchall()
        return [dict(row) for row in rows if files is None or row['file'] in files]

    def start_run(self, config, units):
        run = identifier(now(), config, units)
        with self.connection() as db:
            db.execute('INSERT INTO graph_runs VALUES(?,?,?,?,?)', (run, 'queued', json.dumps(config), now(), None))
            db.executemany('INSERT INTO graph_batches VALUES(?,?,?,?)', [(run, unit, 'pending', None) for unit in units])
        return run

    def run(self, run):
        with self.connection() as db:
            row = db.execute('SELECT * FROM graph_runs WHERE id=?', (run,)).fetchone()
            if row is None:
                raise ValueError('Modelización no disponible.')
            result = dict(row)
            result['config'] = json.loads(result['config'])
            result['batches'] = [dict(row) for row in db.execute('SELECT unit,status FROM graph_batches WHERE run=?', (run,))]
            return result

    def state(self, run, status, error=None):
        with self.connection() as db:
            db.execute('UPDATE graph_runs SET status=?,error=? WHERE id=?', (status, error, run))

    def complete(self, run):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT COUNT(*) FROM graph_batches WHERE run=? AND status!='done'", (run,)).fetchone()[0]:
                raise ValueError('Hay unidades pendientes; el grafo anterior se conserva.')
            if db.execute('SELECT COUNT(*) FROM graph_batches b JOIN graph_units u ON u.id=b.unit WHERE b.run=? AND u.active=0', (run,)).fetchone()[0]:
                raise ValueError('Una fuente ha cambiado durante la modelización.')
            db.execute('UPDATE graph_units SET published_run=? WHERE id IN (SELECT unit FROM graph_batches WHERE run=?)', (run, run))
            db.execute("UPDATE graph_runs SET status='done',error=NULL WHERE id=?", (run,))

    def cached_units(self, config):
        with self.connection() as db:
            rows = db.execute("SELECT b.unit,r.config FROM graph_batches b JOIN graph_runs r ON r.id=b.run JOIN graph_units u ON u.id=b.unit WHERE r.status='done' AND b.status='done' AND u.published_run=r.id").fetchall()
        return {row['unit'] for row in rows if json.loads(row['config']).get('signature') == config['signature']}

    def recover(self):
        if self.path.is_file():
            with self.connection() as db:
                db.execute("UPDATE graph_runs SET status='interrupted' WHERE status IN ('queued','running')")

    def publish_extraction(self, run, unit_id, extraction, metrics):
        extraction = Extraction.model_validate(extraction)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            unit = db.execute('SELECT text,active FROM graph_units WHERE id=?', (unit_id,)).fetchone()
            batch = db.execute('SELECT status FROM graph_batches WHERE run=? AND unit=?', (run, unit_id)).fetchone()
            if unit is None or not unit['active'] or batch is None:
                raise ValueError('Unidad de evidencia fuera del trabajo o retirada.')
            if batch['status'] == 'done':
                return
            spans = []
            for assertion in extraction.assertions:
                start, end = literal_span(unit['text'], assertion.quote)
                assertion.quote = unit['text'][start:end]
                spans.append((start, end))
            entities = {row.key: identifier(unit_id, run, row.key) for row in extraction.entities}
            for row in extraction.entities:
                db.execute('INSERT INTO graph_entities VALUES(?,?,?,?,?,?)',
                    (entities[row.key], unit_id, row.key, row.label, row.kind, run))
            for number, (row, span) in enumerate(zip(extraction.assertions, spans)):
                db.execute('INSERT INTO graph_assertions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    identifier(run, unit_id, number), unit_id, entities[row.subject], row.predicate, entities[row.object],
                    row.quote, *span, row.basis, row.polarity, json.dumps(row.qualifiers), 'proposed', run))
            db.execute('UPDATE graph_batches SET status=?,result=? WHERE run=? AND unit=?',
                ('done', json.dumps({'metrics': metrics}), run, unit_id))

    def snapshot(self, limit=100):
        with self.connection() as db:
            counts = dict(db.execute('SELECT COUNT(DISTINCT file) AS documents,COUNT(*) AS units FROM graph_units WHERE active=1').fetchone())
            counts['modelled_units'] = db.execute('SELECT COUNT(*) FROM graph_units WHERE active=1 AND published_run IS NOT NULL').fetchone()[0]
            counts['assertions'] = db.execute('SELECT COUNT(*) FROM graph_assertions a JOIN graph_units u ON u.id=a.unit WHERE u.active=1 AND a.run=u.published_run').fetchone()[0]
            rows = db.execute('''SELECT a.*,u.file,u.page,u.version,s.label AS subject_label,o.label AS object_label,
                COALESCE(v.status,'pending') AS verification,v.note AS verification_note
                FROM graph_assertions a JOIN graph_units u ON u.id=a.unit
                JOIN graph_entities s ON s.id=a.subject JOIN graph_entities o ON o.id=a.object
                LEFT JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE u.active=1 AND a.run=u.published_run ORDER BY a.rowid DESC LIMIT ?''', (limit,)).fetchall()
            runs = [dict(row) for row in db.execute('SELECT id,status,created,error FROM graph_runs ORDER BY created DESC LIMIT 20')]
            latest = db.execute('SELECT config FROM graph_runs ORDER BY created DESC LIMIT 1').fetchone()
            return {'schema': 1, **counts, 'rows': [dict(row) for row in rows], 'runs': runs,
                    'extraction_settings': {key: value for key, value in (json.loads(latest[0]).items() if latest else []) if key in {'provider', 'model'}},
                    'limit': limit, 'truncated': counts['assertions'] > limit}

    def review(self, assertion, state):
        if state not in {'proposed', 'accepted', 'rejected'}:
            raise ValueError('Estado de revisión no válido.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            previous = db.execute('SELECT review,unit,run FROM graph_assertions WHERE id=?', (assertion,)).fetchone()
            if previous is None:
                raise ValueError('Relación no disponible.')
            if state == 'accepted':
                unit = db.execute('SELECT active,published_run FROM graph_units WHERE id=?', (previous['unit'],)).fetchone()
                if unit is None or not unit['active'] or unit['published_run'] != previous['run']:
                    raise VerificationRequired('La relación no pertenece a la versión publicada actual.')
                verdict = db.execute('SELECT status FROM graph_verifications WHERE assertion=? ORDER BY id DESC LIMIT 1', (assertion,)).fetchone()
                if not verdict or verdict[0] != 'supported':
                    raise VerificationRequired('Contrasta primero el significado de la relación con su fuente. La cita literal no basta.')
            db.execute('INSERT INTO graph_reviews(assertion,previous,current,created) VALUES(?,?,?,?)',
                       (assertion, previous[0], state, now()))
            db.execute('UPDATE graph_assertions SET review=? WHERE id=?', (state, assertion))

    def verify(self, assertion, status, note):
        if status not in {'supported', 'unsupported', 'uncertain'} or not 20 <= len(note.strip()) <= 2000:
            raise ValueError('Indica un resultado y una justificación de al menos 20 caracteres.')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('''SELECT a.*,u.text,u.active,u.published_run FROM graph_assertions a
                JOIN graph_units u ON u.id=a.unit WHERE a.id=?''', (assertion,)).fetchone()
            if row is None or not row['active'] or row['published_run'] != row['run']:
                raise ValueError('La relación no pertenece a la versión publicada actual.')
            if row['text'][row['start']:row['end']] != row['quote']:
                raise ValueError('La cita no coincide con su evidencia; no se puede validar.')
            db.execute('INSERT INTO graph_verifications(assertion,status,note,created) VALUES(?,?,?,?)', (assertion, status, note.strip(), now()))
            if status != 'supported' and row['review'] == 'accepted':
                updated = 'rejected' if status == 'unsupported' else 'proposed'
                db.execute('INSERT INTO graph_reviews(assertion,previous,current,created) VALUES(?,?,?,?)', (assertion, row['review'], updated, now()))
                db.execute('UPDATE graph_assertions SET review=? WHERE id=?', (updated, assertion))

    def evidence(self, unit):
        with self.connection() as db:
            row = db.execute('SELECT * FROM graph_units WHERE id=?', (unit,)).fetchone()
            if row is None:
                raise ValueError('Evidencia no disponible.')
            return dict(row)
