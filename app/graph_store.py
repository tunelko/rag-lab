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
    source_start: int | None = Field(default=None, ge=0)
    source_end: int | None = Field(default=None, ge=0)


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


class Assessment(BaseModel):
    model_config = {'extra': 'forbid'}
    index: int = Field(ge=0, le=11)
    status: Literal['supported', 'unsupported', 'uncertain']
    reason: str = Field(min_length=20, max_length=1500)
    evidence_id: str = Field(min_length=1, max_length=20)


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
                CREATE INDEX IF NOT EXISTS graph_verification_latest ON graph_verifications(assertion,id);
                CREATE INDEX IF NOT EXISTS graph_assertion_review ON graph_assertions(review,run,unit);
                CREATE TABLE IF NOT EXISTS graph_identity_decisions(
                    id TEXT PRIMARY KEY,left_entity TEXT REFERENCES graph_entities(id),
                    right_entity TEXT REFERENCES graph_entities(id),decision TEXT,reason TEXT,
                    verification TEXT,verification_reason TEXT,config TEXT,created TEXT);
                CREATE INDEX IF NOT EXISTS graph_identity_pair ON graph_identity_decisions(left_entity,right_entity);
                CREATE TABLE IF NOT EXISTS graph_tasks(id TEXT PRIMARY KEY,status TEXT,config TEXT,
                    created TEXT,updated TEXT,processed INTEGER NOT NULL DEFAULT 0,
                    elapsed REAL NOT NULL DEFAULT 0,current_run TEXT,error TEXT);
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
            db.execute('CREATE TEMP TABLE allowed_files(file TEXT PRIMARY KEY)')
            if files is not None:
                db.executemany('INSERT OR IGNORE INTO allowed_files VALUES(?)', [(name,) for name in files])
            rows = db.execute('''SELECT * FROM graph_units WHERE active=1
                AND (?=0 OR file IN (SELECT file FROM allowed_files)) ORDER BY file,ordinal''', (int(files is not None),)).fetchall()
        return [dict(row) for row in rows]

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

    def complete(self, run, *, task=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            record = db.execute('SELECT status,config FROM graph_runs WHERE id=?', (run,)).fetchone()
            if record is None:
                raise ValueError('Modelización no disponible.')
            if record['status'] == 'done':
                return
            if db.execute("SELECT COUNT(*) FROM graph_batches WHERE run=? AND status!='done'", (run,)).fetchone()[0]:
                raise ValueError('Hay unidades pendientes; el grafo anterior se conserva.')
            if json.loads(record['config']).get('automatic_assessment') and db.execute('''SELECT 1
                FROM graph_assertions a LEFT JOIN graph_verifications v
                ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE a.run=? AND v.id IS NULL LIMIT 1''', (run,)).fetchone():
                raise ValueError('El contraste está pendiente; la extracción guardada no se publica sin verificar.')
            if db.execute('SELECT COUNT(*) FROM graph_batches b JOIN graph_units u ON u.id=b.unit WHERE b.run=? AND u.active=0', (run,)).fetchone()[0]:
                raise ValueError('Una fuente ha cambiado durante la modelización.')
            db.execute('UPDATE graph_units SET published_run=? WHERE id IN (SELECT unit FROM graph_batches WHERE run=?)', (run, run))
            db.execute("UPDATE graph_runs SET status='done',error=NULL WHERE id=?", (run,))
            if task:
                # Publication and the authorized unit budget commit together.
                db.execute('''UPDATE graph_tasks SET processed=processed+
                    (SELECT COUNT(*) FROM graph_batches WHERE run=?),current_run=?,updated=? WHERE id=?''',
                    (run, run, now(), task))

    def cached_units(self, config):
        with self.connection() as db:
            rows = db.execute('''SELECT DISTINCT b.unit FROM graph_batches b JOIN graph_runs r ON r.id=b.run
                JOIN graph_units u ON u.id=b.unit WHERE r.status='done' AND b.status='done'
                AND u.active=1 AND json_extract(r.config,'$.signature')=?
                AND NOT EXISTS(SELECT 1 FROM graph_assertions a LEFT JOIN graph_verifications v
                    ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                    WHERE a.run=r.id AND a.unit=u.id AND v.id IS NULL)''', (config['signature'],)).fetchall()
        return {row['unit'] for row in rows}

    def checkpoint_inventory(self, signature=None):
        """Small stage records; do not load passage bodies for the source picker."""
        with self.connection() as db:
            units = [dict(row) for row in db.execute(
                'SELECT id,file,version,metadata FROM graph_units WHERE active=1 ORDER BY file,ordinal')]
            batches = [dict(row) for row in db.execute('''SELECT b.unit,b.run,b.status AS batch_status,
                r.status AS run_status,r.created,COUNT(a.id) AS claims,COUNT(v.id) AS assessed,
                COALESCE(SUM(v.status='supported' AND a.review='accepted'),0) AS supported
                FROM graph_batches b JOIN graph_runs r ON r.id=b.run JOIN graph_units u ON u.id=b.unit
                LEFT JOIN graph_assertions a ON a.unit=b.unit AND a.run=b.run
                LEFT JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE u.active=1 AND (? IS NULL OR json_extract(r.config,'$.signature')=?)
                GROUP BY b.unit,b.run ORDER BY r.created DESC''', (signature, signature))]
        return units, batches

    def prepared_units(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute(
                'SELECT id,file,version,metadata FROM graph_units WHERE active=1 ORDER BY file,ordinal')]

    def resumable_run(self, config, files, max_units):
        """Use a previous bounded checkpoint only if its entire scope is selected."""
        with self.connection() as db:
            rows = db.execute('''SELECT r.id,COUNT(*) AS units FROM graph_runs r
                JOIN graph_batches b ON b.run=r.id JOIN graph_units u ON u.id=b.unit
                WHERE r.status IN ('error','interrupted','cancelled') AND json_extract(r.config,'$.signature')=?
                GROUP BY r.id HAVING COUNT(*)<=? AND MIN(u.active)=1 AND SUM(b.status='done')>0
                ORDER BY r.created DESC''', (config['signature'], max_units)).fetchall()
            for row in rows:
                names = {value[0] for value in db.execute('''SELECT u.file FROM graph_batches b
                    JOIN graph_units u ON u.id=b.unit WHERE b.run=?''', (row['id'],))}
                if names <= set(files):
                    return row['id']
        return None

    def reuse_checkpoint(self, run, unit, config):
        """Copy a compatible staged unit without expanding the current file scope.

        Whole-run resume is preferable. This also handles a changed sibling PDF
        or a smaller batch: other still valid units must not be extracted again.
        """
        with self.connection() as db:
            previous = db.execute('''SELECT b.run,b.result FROM graph_batches b JOIN graph_runs r ON r.id=b.run
                JOIN graph_units u ON u.id=b.unit WHERE b.unit=? AND b.run!=? AND b.status='done' AND u.active=1
                AND r.status IN ('done','error','cancelled','interrupted') AND json_extract(r.config,'$.signature')=?
                ORDER BY NOT EXISTS(SELECT 1 FROM graph_assertions a LEFT JOIN graph_verifications v
                    ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                    WHERE a.run=b.run AND a.unit=b.unit AND v.id IS NULL) DESC,r.created DESC LIMIT 1''',
                (unit, run, config['signature'])).fetchone()
            if previous is None:
                return False
            rows = db.execute('''SELECT a.*,s.local_key AS subject_key,o.local_key AS object_key,
                v.status AS verification,v.note AS verification_note
                FROM graph_assertions a JOIN graph_entities s ON s.id=a.subject JOIN graph_entities o ON o.id=a.object
                LEFT JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE a.run=? AND a.unit=? ORDER BY a.rowid''', (previous['run'], unit)).fetchall()
            entities = [{'key': row['local_key'], 'label': row['label'], 'kind': row['kind']} for row in db.execute(
                'SELECT * FROM graph_entities WHERE run=? AND unit=? ORDER BY rowid', (previous['run'], unit))]
            assertions = [{'subject': row['subject_key'], 'object': row['object_key'], 'predicate': row['predicate'],
                'quote': row['quote'], 'basis': row['basis'], 'polarity': row['polarity'],
                'qualifiers': json.loads(row['qualifiers']), 'source_start': row['start'], 'source_end': row['end']} for row in rows]
            assessments = None
            if all(row['verification'] is not None for row in rows):
                assessments = [{'index': index, 'status': row['verification'],
                    'reason': row['verification_note'].removeprefix('Contraste automático del modelo: ')[:1500],
                    'evidence_id': 'saved-checkpoint'} for index, row in enumerate(rows)]
            metrics = {'reused_from_run': previous['run'], 'cache_signature': config['signature']}
        # The regular publication validator rechecks schema, references and every
        # source span; no new semantic verdict is fabricated by this replay.
        self.publish_extraction(run, unit, Extraction(entities=entities, assertions=assertions), metrics, assessments=assessments)
        return True

    def recover(self):
        if self.path.is_file():
            with self.connection() as db:
                db.execute("UPDATE graph_runs SET status='interrupted' WHERE status IN ('queued','running')")
                for row in db.execute("SELECT * FROM graph_tasks WHERE status='running'").fetchall():
                    # Conservatively account for an interrupted in-flight call;
                    # a restart must never silently reset a spent time budget.
                    duration = max(0, (datetime.now(timezone.utc) - datetime.fromisoformat(row['updated'])).total_seconds())
                    db.execute('UPDATE graph_tasks SET elapsed=? WHERE id=?', (
                        min(json.loads(row['config'])['max_seconds'], row['elapsed'] + duration), row['id']))
                db.execute("UPDATE graph_tasks SET status='interrupted' WHERE status IN ('queued','running')")

    def publish_extraction(self, run, unit_id, extraction, metrics, assessments=None):
        extraction = Extraction.model_validate(extraction)
        checked = None if assessments is None else self._assessments(assessments, len(extraction.assertions))
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
                if assertion.source_start is not None or assertion.source_end is not None:
                    start, end = assertion.source_start, assertion.source_end
                    if start is None or end is None or not 0 <= start < end <= len(unit['text']) or unit['text'][start:end] != assertion.quote:
                        raise ValueError('La posición y la cita deben coincidir exactamente con la fuente.')
                else:
                    start, end = literal_span(unit['text'], assertion.quote)
                assertion.quote = unit['text'][start:end]
                spans.append((start, end))
            entities = {row.key: identifier(unit_id, run, row.key) for row in extraction.entities}
            for row in extraction.entities:
                db.execute('INSERT INTO graph_entities VALUES(?,?,?,?,?,?)',
                    (entities[row.key], unit_id, row.key, row.label, row.kind, run))
            for number, (row, span) in enumerate(zip(extraction.assertions, spans)):
                claim_id = identifier(run, unit_id, number)
                assessment = checked[number] if checked is not None else None
                review = ('accepted' if assessment.status == 'supported' else 'rejected') if assessment else 'proposed'
                db.execute('INSERT INTO graph_assertions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)', (
                    claim_id, unit_id, entities[row.subject], row.predicate, entities[row.object],
                    row.quote, *span, row.basis, row.polarity, json.dumps(row.qualifiers), review, run))
                if assessment:
                    db.execute('INSERT INTO graph_verifications(assertion,status,note,created) VALUES(?,?,?,?)',
                        (claim_id, assessment.status, 'Contraste automático del modelo: ' + assessment.reason, now()))
            db.execute('UPDATE graph_batches SET status=?,result=? WHERE run=? AND unit=?',
                ('done', json.dumps({'metrics': metrics}), run, unit_id))

    @staticmethod
    def _assessments(values, count):
        rows = [Assessment.model_validate(row) for row in values]
        by_index = {row.index: row for row in rows}
        if len(by_index) != len(rows) or set(by_index) != set(range(count)):
            raise ValueError('El contraste debe cubrir todas las relaciones exactamente una vez.')
        return by_index

    def batch_for_assessment(self, run, unit):
        with self.connection() as db:
            rows = db.execute('''SELECT a.*,s.local_key AS subject_key,o.local_key AS object_key
                FROM graph_assertions a JOIN graph_entities s ON s.id=a.subject JOIN graph_entities o ON o.id=a.object
                WHERE a.run=? AND a.unit=? ORDER BY a.rowid''', (run, unit)).fetchall()
            needed = any(not db.execute('SELECT 1 FROM graph_verifications WHERE assertion=?', (row['id'],)).fetchone() for row in rows)
            if not needed:
                return None
            entities = [{'key': row['local_key'], 'label': row['label'], 'kind': row['kind']} for row in db.execute(
                'SELECT * FROM graph_entities WHERE run=? AND unit=? ORDER BY rowid', (run, unit))]
            assertions = [{'subject': row['subject_key'], 'object': row['object_key'], 'predicate': row['predicate'],
                'quote': row['quote'], 'basis': row['basis'], 'polarity': row['polarity'],
                'qualifiers': json.loads(row['qualifiers']), 'source_start': row['start'], 'source_end': row['end']} for row in rows]
            return Extraction(entities=entities, assertions=assertions)

    def assess_existing_batch(self, run, unit, assessments, metrics, extraction=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('''SELECT a.*,s.local_key AS subject_key,o.local_key AS object_key
                FROM graph_assertions a JOIN graph_entities s ON s.id=a.subject JOIN graph_entities o ON o.id=a.object
                WHERE a.run=? AND a.unit=? ORDER BY a.rowid''', (run, unit)).fetchall()
            checked = self._assessments(assessments, len(rows))
            if extraction is not None:
                extraction = Extraction.model_validate(extraction)
                source = db.execute('SELECT text FROM graph_units WHERE id=? AND active=1', (unit,)).fetchone()
                if source is None or len(extraction.assertions) != len(rows):
                    raise ValueError('El contraste no corresponde a la extracción guardada.')
                stored_entities = sorted(tuple(row) for row in db.execute(
                    'SELECT local_key,label,kind FROM graph_entities WHERE run=? AND unit=?', (run, unit)))
                if stored_entities != sorted((row.key, row.label, row.kind) for row in extraction.entities):
                    raise ValueError('El contraste no puede cambiar las entidades de la extracción guardada.')
                for row, claim in zip(rows, extraction.assertions):
                    if (claim.subject, claim.predicate, claim.object, claim.basis, claim.polarity, claim.qualifiers) != (
                            row['subject_key'], row['predicate'], row['object_key'], row['basis'], row['polarity'], json.loads(row['qualifiers'])):
                        raise ValueError('El contraste no puede cambiar la relación guardada; solo puede precisar su cita.')
                    start, end = claim.source_start, claim.source_end
                    if start is None or end is None or not 0 <= start < end <= len(source[0]) or source[0][start:end] != claim.quote:
                        raise ValueError('La cita contrastada debe coincidir con el pasaje original.')
                    db.execute('UPDATE graph_assertions SET quote=?,start=?,end=? WHERE id=?', (claim.quote, start, end, row['id']))
            for index, row in enumerate(rows):
                assessment = checked[index]
                db.execute('INSERT INTO graph_verifications(assertion,status,note,created) VALUES(?,?,?,?)',
                    (row['id'], assessment.status, 'Contraste automático del modelo: ' + assessment.reason, now()))
                db.execute('UPDATE graph_assertions SET review=? WHERE id=?',
                    ('accepted' if assessment.status == 'supported' else 'rejected', row['id']))
            previous = db.execute('SELECT result FROM graph_batches WHERE run=? AND unit=?', (run, unit)).fetchone()
            result = json.loads(previous[0]) if previous and previous[0] else {}
            result['automatic_assessment'] = metrics
            db.execute('UPDATE graph_batches SET result=? WHERE run=? AND unit=?', (json.dumps(result), run, unit))

    def snapshot(self, limit=100):
        with self.connection() as db:
            counts = dict(db.execute('SELECT COUNT(DISTINCT file) AS documents,COUNT(*) AS units FROM graph_units WHERE active=1').fetchone())
            counts['modelled_units'] = db.execute('SELECT COUNT(*) FROM graph_units WHERE active=1 AND published_run IS NOT NULL').fetchone()[0]
            counts['assertions'] = db.execute('''SELECT COUNT(*) FROM graph_assertions a JOIN graph_units u ON u.id=a.unit JOIN graph_runs r ON r.id=a.run
                LEFT JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE u.active=1 AND r.status='done' AND (a.run=u.published_run OR (a.review='accepted' AND v.status='supported'))''').fetchone()[0]
            counts['assessment_counts'] = dict(db.execute('''SELECT
                COALESCE(SUM(a.review='accepted' AND v.status='supported'),0) AS accepted,
                COALESCE(SUM(a.review='rejected'),0) AS rejected,
                COALESCE(SUM(a.review!='rejected' AND (a.review!='accepted' OR v.status IS NULL OR v.status!='supported')),0) AS unverified
                FROM graph_assertions a JOIN graph_units u ON u.id=a.unit JOIN graph_runs r ON r.id=a.run
                LEFT JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE u.active=1 AND r.status='done' AND (a.run=u.published_run OR (a.review='accepted' AND v.status='supported'))''').fetchone())
            rows = db.execute('''SELECT a.*,u.file,u.page,u.version,u.ordinal AS passage_number,
                (SELECT COUNT(*) FROM graph_units q WHERE q.file=u.file AND q.active=1) AS passage_total,
                s.label AS subject_label,o.label AS object_label,s.kind AS subject_kind,o.kind AS object_kind,
                COALESCE(v.status,'pending') AS verification,v.note AS verification_note
                FROM graph_assertions a JOIN graph_units u ON u.id=a.unit
                JOIN graph_runs r ON r.id=a.run
                JOIN graph_entities s ON s.id=a.subject JOIN graph_entities o ON o.id=a.object
                LEFT JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE u.active=1 AND r.status='done' AND (a.run=u.published_run OR (a.review='accepted' AND v.status='supported')) ORDER BY a.rowid DESC LIMIT ?''', (limit,)).fetchall()
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
                run = db.execute('SELECT status FROM graph_runs WHERE id=?', (previous['run'],)).fetchone()
                if unit is None or not unit['active'] or run is None or run[0] != 'done':
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
            row = db.execute('''SELECT a.*,u.text,u.active,u.published_run,r.status AS run_status FROM graph_assertions a
                JOIN graph_units u ON u.id=a.unit JOIN graph_runs r ON r.id=a.run WHERE a.id=?''', (assertion,)).fetchone()
            if row is None or not row['active'] or row['run_status'] != 'done':
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

    def accepted_rows(self, files=None):
        """Read only published, currently supported claims, without a UI row limit."""
        if files is not None and not files:
            return []
        with self.connection() as db:
            db.execute('CREATE TEMP TABLE allowed_files(file TEXT PRIMARY KEY)')
            if files is not None:
                db.executemany('INSERT OR IGNORE INTO allowed_files VALUES(?)', [(name,) for name in files])
            rows = db.execute('''SELECT a.*,u.file,u.page,u.version,u.text AS source_text,
                s.label AS subject_label,o.label AS object_label,s.kind AS subject_kind,o.kind AS object_kind
                FROM graph_assertions a JOIN graph_units u ON u.id=a.unit JOIN graph_runs r ON r.id=a.run
                JOIN graph_entities s ON s.id=a.subject JOIN graph_entities o ON o.id=a.object
                JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                WHERE u.active=1 AND u.published_run=a.run AND r.status='done' AND a.review='accepted' AND v.status='supported'
                AND (?=0 OR u.file IN (SELECT file FROM allowed_files)) ORDER BY a.id''', (int(files is not None),)).fetchall()
            return [dict(row) for row in rows]

    def identity_decisions(self):
        with self.connection() as db:
            return [dict(row) for row in db.execute('SELECT * FROM graph_identity_decisions ORDER BY created,id')]

    def publish_identities(self, decisions, config, eligible):
        """Atomic model decisions, never a direct entity merge or an implicit name match."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            for row in decisions:
                left, right = sorted((row['left'], row['right']))
                if left == right or left not in eligible or right not in eligible:
                    raise ValueError('La equivalencia referencia entidades fuera de la selección contrastada.')
                if row['decision'] not in {'same', 'different', 'uncertain'} or row['verification'] not in {'supported', 'unsupported', 'uncertain'}:
                    raise ValueError('Decisión de identidad no válida.')
                active = db.execute('''SELECT COUNT(*) FROM graph_entities e JOIN graph_units u ON u.id=e.unit
                    WHERE e.id IN (?,?) AND u.active=1''', (left, right)).fetchone()[0]
                if active != 2:
                    raise ValueError('La versión de una entidad ha cambiado durante la reconciliación.')
                binding = {**config, 'left_assertion': eligible[left]['assertion'], 'right_assertion': eligible[right]['assertion'],
                           'shared_identifier': row.get('shared_identifier')}
                for claim in (binding['left_assertion'], binding['right_assertion']):
                    supported = db.execute('''SELECT 1 FROM graph_assertions a JOIN graph_runs r ON r.id=a.run
                        JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)
                        WHERE a.id=? AND a.review='accepted' AND v.status='supported' AND r.status='done' ''', (claim,)).fetchone()
                    if not supported:
                        raise ValueError('Una evidencia fue retirada antes de publicar la identidad.')
                db.execute('INSERT OR IGNORE INTO graph_identity_decisions VALUES(?,?,?,?,?,?,?,?,?)', (
                    identifier(left, right, binding), left, right, row['decision'], row['reason'],
                    row['verification'], row['verification_reason'], json.dumps(binding), now()))

    def task_create(self, config):
        task = identifier(now(), config)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if db.execute("SELECT 1 FROM graph_tasks WHERE status IN ('queued','running')").fetchone():
                raise ValueError('Ya hay un procesamiento continuo activo en esta colección.')
            db.execute('INSERT INTO graph_tasks VALUES(?,?,?,?,?,0,0,NULL,NULL)',
                       (task, 'queued', json.dumps(config), now(), now()))
        return task

    def tasks(self, task=None):
        with self.connection() as db:
            if task:
                rows = db.execute('SELECT * FROM graph_tasks WHERE id=? ORDER BY created DESC', (task,)).fetchall()
            else:
                rows = db.execute("SELECT * FROM graph_tasks ORDER BY CASE WHEN status IN ('queued','running') THEN 0 ELSE 1 END,created DESC LIMIT 30").fetchall()
            results = [{**dict(row), 'config': json.loads(row['config'])} for row in rows]
            if task and not results:
                raise ValueError('Procesamiento continuo no disponible.')
            return results[0] if task else results[:30]

    def task_update(self, task, *, status=None, processed=0, elapsed=0, run=None, error=None):
        with self.connection() as db:
            db.execute('''UPDATE graph_tasks SET status=COALESCE(?,status),processed=processed+?,elapsed=elapsed+?,
                current_run=COALESCE(?,current_run),error=COALESCE(?,error),updated=? WHERE id=?''',
                (status, processed, elapsed, run, error, now(), task))
