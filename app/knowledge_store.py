"""A read-only query language over documented entity/edge observations."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3
from typing import Annotated, Literal

from pydantic import BaseModel, Field, model_validator
from cti_records import alias
from model_routing import normalize


class KnowledgeQuery(BaseModel):
    model_config = {'extra': 'forbid'}
    operation: Literal['entity', 'neighbors', 'intersection', 'evidence', 'list_names', 'semantic'] = 'semantic'
    entities: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=4)
    source: Literal['mitre', 'brandefense', 'misp', 'all'] = 'mitre'
    target_type: Literal['any', 'intrusion-set', 'attack-pattern', 'malware', 'tool', 'campaign'] = 'any'
    target_types: list[Literal['intrusion-set', 'attack-pattern', 'malware', 'tool', 'campaign']] = Field(default_factory=list, max_length=5)
    targets: list[Annotated[str, Field(min_length=1, max_length=200)]] = Field(default_factory=list, max_length=4)
    direction: Literal['outgoing', 'incoming', 'both'] = 'outgoing'
    relationship: str = Field(default='uses', max_length=80)
    limit: int | None = Field(default=100, ge=0)

    @model_validator(mode='after')
    def check_list(self):
        if self.operation == 'list_names' and (self.target_type == 'any' or self.entities or self.targets or self.target_types):
            raise ValueError('list_names needs one entity type and no relationship filters.')
        return self


_CATALOG_TYPES = {
    'intrusion-set': {'apt', 'apts', 'grupo', 'grupos', 'group', 'groups'},
    'attack-pattern': {'tecnica', 'tecnicas', 'technique', 'techniques'},
    'campaign': {'campana', 'campanas', 'campaign', 'campaigns'},
    'malware': {'malware'}, 'tool': {'herramienta', 'herramientas', 'tool', 'tools'},
}
_LIST_WORDS = set('nombres nombre nombrs names name lista listado listar listame lista enumera enumerate list '
                  'dime dame muestrame show give me los las el la unos unas de del the of a una un por favor '
                  'please documentados documentadas disponibles documented available todos todas all'.split())


def cti_list_candidate(question, scope_label):
    words = set(re.findall(r'\w+', normalize(question)))
    if any(words & labels for labels in _CATALOG_TYPES.values()) or re.search(r'\b[GTSC]\d{4}\b', question, re.I):
        return True
    # A generic request for names may rely on the category to identify the type.
    return (bool(scope_label) and bool(words & {'nombres', 'names', 'nombrs'})
            and not (words - _LIST_WORDS - {'hasta', 'up', 'to'} - {word for word in words if word.isdecimal()}))


def direct_name_list(question):
    """Only complete, unqualified catalogue requests take the model-free path."""
    words = normalize(question)
    if re.search(r'\b(?:apt|group|grupo)\s*[-#]?\s*\d', words):
        return None  # An actor identifier such as "APT 29" is not a result limit.
    numbers = re.findall(r'\b\d+\b', words)
    if len(numbers) > 1 or (numbers and len(numbers[0]) > 7):
        return None
    words = re.sub(r'\b(?:(?:hasta|up to|at most|maximo(?: de)?)\s+)?\d+\b', ' ', words)
    words = set(re.findall(r'\w+', words))
    kinds = [kind for kind, labels in _CATALOG_TYPES.items() if words & labels]
    if len(kinds) != 1 or words - _LIST_WORDS - _CATALOG_TYPES[kinds[0]]:
        return None
    if not (numbers or words & {'nombre', 'nombres', 'nombrs', 'name', 'names', 'lista', 'listado', 'listar',
                                 'listame', 'list', 'enumera', 'enumerate', 'todos', 'todas', 'all', 'dame', 'show'}):
        return None
    return KnowledgeQuery(operation='list_names', target_type=kinds[0], source='all',
                          limit=int(numbers[0]) if numbers else None if words & {'todos', 'todas', 'all'} else 100)


def list_names(db, plan, documents):
    kinds = ('intrusion-set', 'threat-actor') if plan.target_type == 'intrusion-set' else (plan.target_type,)
    sql = f"""SELECT canonical, id, name, json_extract(record,'$.document')
              FROM observations WHERE active=1 AND name<>'' AND kind IN ({','.join('?' for _ in kinds)})"""
    params = list(kinds)
    if plan.source != 'all':
        sql += ' AND origin=?'
        params.append(plan.source)
    # Pick a primary name per documented identity, then return distinct display names.
    # Identical spelling alone never joins two actors in the knowledge graph.
    sql += " ORDER BY CASE origin WHEN 'mitre' THEN 0 WHEN 'brandefense' THEN 1 ELSE 2 END, id"
    identities, names = set(), {}
    for canonical, identifier, name, document in db.execute(sql, params):
        if documents is not None and 'cti/' + document not in documents:
            continue
        name = ' '.join(name.split())
        if canonical in identities or not name:
            continue
        identities.add(canonical)
        names.setdefault(name.casefold(), {'id': identifier, 'name': name, 'document': document})
    rows = [names[key] for key in sorted(names)[:plan.limit]]
    return {'status': 'ok', 'rows': rows, 'total': len(names), 'plan': plan.model_dump()}


class KnowledgeStore:
    def __init__(self, path):
        self.path = Path(path)

    def publish(self, rows, receipt):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        entities = {row['id']: row for row in rows if row['type'] != 'relationship'}
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.executescript('''
                CREATE TABLE IF NOT EXISTS observations(id TEXT PRIMARY KEY, kind TEXT, origin TEXT,
                    canonical TEXT, name TEXT, active INTEGER, record TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS aliases(value TEXT, observation TEXT);
                CREATE INDEX IF NOT EXISTS observation_canonical ON observations(canonical,active);
                CREATE INDEX IF NOT EXISTS alias_value ON aliases(value);
                CREATE TABLE IF NOT EXISTS edges(id TEXT PRIMARY KEY, source TEXT, target TEXT,
                    relationship TEXT, origin TEXT, active INTEGER);
                CREATE INDEX IF NOT EXISTS edge_source ON edges(source,relationship);
                CREATE INDEX IF NOT EXISTS edge_target ON edges(target,relationship);
                CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
            ''')
            db.execute('BEGIN IMMEDIATE')
            for table in ('observations', 'aliases', 'edges'):
                db.execute('DELETE FROM ' + table)
            for row in rows:
                db.execute('INSERT INTO observations VALUES(?,?,?,?,?,?,?)',
                           (row['id'], row['type'], row['source'], row['canonical_id'], row['name'],
                            int(row['active']), json.dumps(row, ensure_ascii=False)))
                if row['type'] == 'relationship':
                    if row.get('resolved'):
                        db.execute('INSERT INTO edges VALUES(?,?,?,?,?,?)',
                                   (row['id'], entities[row['source_ref']]['canonical_id'],
                                    entities[row['target_ref']]['canonical_id'], row['relationship_type'],
                                    row['source'], int(row['active'])))
                else:
                    values = {alias(value) for value in [row['name'], row['id'], row.get('public_id') or '', *row['aliases']] if value}
                    db.executemany('INSERT INTO aliases VALUES(?,?)', ((value, row['id']) for value in values))
            db.execute('INSERT OR REPLACE INTO metadata VALUES(?,?)', ('receipt', json.dumps(receipt)))

    def query(self, plan: KnowledgeQuery, *, documents=None):
        if not self.path.is_file():
            raise ValueError('Sincroniza e indexa primero el dataset CTI desde Biblioteca.')
        with closing(sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True)) as db:
            db.execute('BEGIN')
            if plan.operation == 'list_names':
                return list_names(db, plan, documents)
            if documents is not None:
                raise ValueError('Scoped graph queries require evidence within the selected documents.')
            def read(identifier):
                row = db.execute('SELECT record FROM observations WHERE id=? AND active=1', (identifier,)).fetchone()
                return json.loads(row[0]) if row else None
            def records(canonical):
                return [json.loads(row[0]) for row in db.execute(
                    'SELECT record FROM observations WHERE canonical=? AND active=1 ORDER BY origin,id', (canonical,))]
            def lookup(term):
                value = alias(term)
                origin = ' AND o.origin=?' if plan.source != 'all' else ''
                parameters = [value] + ([plan.source] if plan.source != 'all' else [])
                base = 'SELECT DISTINCT o.record FROM observations o JOIN aliases a ON a.observation=o.id WHERE o.active=1 AND '
                matches = [json.loads(row[0]) for row in db.execute(base + 'a.value=?' + origin, parameters)]
                if not matches and len(value) >= 4:
                    # A shortened name (e.g. Lazarus / Lazarus Group) may resolve
                    # only when all matching documented aliases name one entity.
                    # Collisions remain ambiguous; observations are never merged.
                    parameters[0] = value + '%'
                    matches = [json.loads(row[0]) for row in db.execute(base + 'a.value LIKE ?' + origin, parameters)]
                return matches
            resolved = []
            for term in plan.entities:
                matches = lookup(term)
                identities = {row['canonical_id'] for row in matches}
                if len(identities) != 1:
                    return {'status': 'ambiguous' if identities else 'not_found', 'term': term,
                            'candidates': [{'id': row['public_id'] or row['id'], 'name': row['name'], 'source': row['source']} for row in matches]}
                resolved.append({'canonical': next(iter(identities)), 'observations': matches})
            if not resolved:
                return {'status': 'not_found', 'term': '', 'candidates': []}
            if plan.operation == 'intersection' and (len(resolved)<2 or len({x['canonical'] for x in resolved})!=len(resolved)):
                return {'status':'ambiguous','term':', '.join(plan.entities),'candidates':[]}
            if plan.operation == 'entity':
                return {'status': 'ok', 'entities': resolved, 'rows': [], 'plan': plan.model_dump()}
            target_ids = None
            if plan.targets:
                target_ids = set()
                for term in plan.targets:
                    identities = {row['canonical_id'] for row in lookup(term)}
                    if len(identities) != 1:
                        return {'status': 'ambiguous' if identities else 'not_found', 'term': term, 'candidates': []}
                    target_ids.update(identities)
            sets, results = [], {}
            for identity in resolved:
                found = set()
                for direction in (['outgoing', 'incoming'] if plan.direction == 'both' else [plan.direction]):
                    column, other_column = ('source', 'target') if direction == 'outgoing' else ('target', 'source')
                    sql = f'SELECT id,{other_column} FROM edges WHERE {column}=? AND active=1'
                    parameters = [identity['canonical']]
                    if plan.relationship:
                        sql += ' AND relationship=?'
                        parameters.append(plan.relationship)
                    if plan.source != 'all':
                        sql += ' AND origin=?'
                        parameters.append(plan.source)
                    for edge_id, target in db.execute(sql, parameters):
                        if target_ids is not None and target not in target_ids:
                            continue
                        candidates = records(target)
                        if plan.source != 'all':
                            candidates = [row for row in candidates if row['source'] == plan.source]
                        kinds = set(plan.target_types) or ({plan.target_type} if plan.target_type != 'any' else set())
                        candidates = [row for row in candidates if not kinds or row['type'] in kinds]
                        if not candidates:
                            continue
                        found.add(target)
                        result = results.setdefault(target, {'id': target, 'entities': candidates, 'evidence': {}})
                        result['evidence'][edge_id] = read(edge_id)
                        if plan.operation=='intersection':
                            result.setdefault('proof_by_entity',{}).setdefault(identity['canonical'],[]).append((edge_id,direction))
                sets.append(found)
            allowed = set.intersection(*sets) if plan.operation == 'intersection' else set.union(*sets)
            rows = [{**results[key], 'evidence': list(results[key]['evidence'].values())} for key in sorted(allowed)]
            return {'status': 'ok', 'entities': resolved, 'rows': rows, 'total': len(rows), 'plan': plan.model_dump()}

    def observation(self, identifier):
        if not self.path.is_file():
            raise ValueError('La evidencia no está disponible.')
        with closing(sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True)) as db:
            result = db.execute('SELECT record FROM observations WHERE id=? AND active=1', (identifier,)).fetchone()
        if result is None:
            raise ValueError('La evidencia no está disponible.')
        return json.loads(result[0])
