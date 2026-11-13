"""Local SQLite vectors with exact NumPy, FTS5 and process-local read caching."""
from collections import OrderedDict
from contextlib import contextmanager
import json
from pathlib import Path
import re
import sqlite3
import threading

import numpy as np
from langchain_core.documents import Document
from retrieval_terms import FTS_STOP_WORDS, FTS_TOKENIZER


SCHEMA_VERSION = 1
_TOKEN = re.compile(r"\w+", re.UNICODE)


class _CacheState:
    def __init__(self):
        self.lock = threading.RLock()
        self.generation = None
        self.fallback_signature = None
        self.snapshot = None
        self.loads = 0
        self.hits = 0


class VectorStore:
    """Persistent exact vector store with a validated in-memory search snapshot."""

    _cache_lock = threading.RLock()
    _caches = OrderedDict()
    _max_cached_databases = 8

    def __init__(self, path, embeddings=None):
        self.path = Path(path).resolve()
        self.embeddings = embeddings
        self._lexical_ready = False
        self._ensure_schema()

    @contextmanager
    def connect(self, *, write=False):
        db = sqlite3.connect(str(self.path) if write else self.path.as_uri() + '?mode=ro',
                             uri=not write, timeout=30)
        try:
            if write:
                db.execute('PRAGMA synchronous=FULL')
            with db:
                yield db
        finally:
            db.close()

    def _ensure_schema(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with self.connect() as db:
                current = db.execute('PRAGMA user_version').fetchone()[0]
            if current == SCHEMA_VERSION:
                return
        changed = False
        with self.connect(write=True) as db:
            db.execute('PRAGMA journal_mode=WAL')
            version = db.execute('PRAGMA user_version').fetchone()[0]
            if version == 0:
                db.executescript('''
                    CREATE TABLE vectors (
                        id TEXT PRIMARY KEY, text TEXT NOT NULL,
                        metadata TEXT NOT NULL, file TEXT NOT NULL,
                        vector BLOB NOT NULL);
                    CREATE INDEX vector_files ON vectors(file);
                    CREATE TABLE info (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    INSERT INTO info VALUES ('generation','0');
                ''')
                db.execute(f'PRAGMA user_version={SCHEMA_VERSION}')
                changed = True
            elif version != SCHEMA_VERSION:
                raise ValueError('Unsupported vector database format.')
        if changed:
            self._invalidate_cache()

    def ensure_lexical_index(self):
        """Explicitly prepare optional FTS; dense search does not require FTS5."""
        if self._lexical_ready:
            return
        with self.connect(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vectors_fts'").fetchone()
            marker = db.execute("SELECT value FROM info WHERE key='fts_schema'").fetchone()
            if table and marker and marker[0] == '1':
                self._lexical_ready = True
                return
            self._install_fts(db)
            db.execute("INSERT OR REPLACE INTO info VALUES ('fts_schema','1')")
        self._lexical_ready = True

    def lexical_index_available(self):
        if self._lexical_ready:
            return True
        with self.connect() as db:
            table = db.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='vectors_fts'").fetchone()
            marker = db.execute("SELECT value FROM info WHERE key='fts_schema'").fetchone()
        self._lexical_ready = bool(table and marker and marker[0] == '1')
        return self._lexical_ready

    def remove_lexical_index(self):
        """Remove only the optional derived index; vector rows stay untouched."""
        with self.connect(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            for statement in ('DROP TRIGGER IF EXISTS vectors_ai',
                              'DROP TRIGGER IF EXISTS vectors_ad',
                              'DROP TRIGGER IF EXISTS vectors_au',
                              'DROP TABLE IF EXISTS vectors_fts',
                              'DROP TABLE IF EXISTS fts_rows',
                              "DELETE FROM info WHERE key='fts_schema'"):
                db.execute(statement)
        self._lexical_ready = False

    @staticmethod
    def _install_fts(db):
        try:
            for statement in (
                'DROP TRIGGER IF EXISTS vectors_ai',
                'DROP TRIGGER IF EXISTS vectors_ad',
                'DROP TRIGGER IF EXISTS vectors_au',
                'DROP TABLE IF EXISTS vectors_fts',
                'DROP TABLE IF EXISTS fts_rows',
                f'''CREATE VIRTUAL TABLE vectors_fts USING fts5(
                    text, file UNINDEXED,
                    tokenize='{FTS_TOKENIZER}')''',
                'CREATE TABLE fts_rows (id TEXT PRIMARY KEY, docid INTEGER UNIQUE NOT NULL)',
                'INSERT INTO vectors_fts(rowid,text,file) SELECT rowid,text,file FROM vectors',
                'INSERT INTO fts_rows(id,docid) SELECT id,rowid FROM vectors',
                '''CREATE TRIGGER vectors_ai AFTER INSERT ON vectors BEGIN
                    INSERT INTO vectors_fts(text,file) VALUES (new.text,new.file);
                    INSERT INTO fts_rows(id,docid) VALUES (new.id,last_insert_rowid()); END''',
                '''CREATE TRIGGER vectors_ad AFTER DELETE ON vectors BEGIN
                    DELETE FROM vectors_fts WHERE rowid=(SELECT docid FROM fts_rows WHERE id=old.id);
                    DELETE FROM fts_rows WHERE id=old.id; END''',
                '''CREATE TRIGGER vectors_au AFTER UPDATE ON vectors BEGIN
                    DELETE FROM vectors_fts WHERE rowid=(SELECT docid FROM fts_rows WHERE id=old.id);
                    INSERT INTO vectors_fts(text,file) VALUES (new.text,new.file);
                    UPDATE fts_rows SET id=new.id,docid=last_insert_rowid() WHERE id=old.id; END''',
            ):
                db.execute(statement)
        except sqlite3.OperationalError as error:
            raise RuntimeError('This Python SQLite build does not provide FTS5.') from error

    @classmethod
    def clear_cache(cls, path=None):
        """Drop process-local snapshots; persistent data is not altered."""
        with cls._cache_lock:
            if path is None:
                cls._caches.clear()
            else:
                cls._caches.pop(str(Path(path).resolve()), None)

    def _cache_state(self):
        key = str(self.path)
        with self._cache_lock:
            state = self._caches.get(key)
            if state is None:
                state = _CacheState()
                self._caches[key] = state
                while len(self._caches) > self._max_cached_databases:
                    self._caches.popitem(last=False)
            else:
                self._caches.move_to_end(key)
            return state

    def _file_signature(self):
        result = []
        for path in (self.path, Path(str(self.path) + '-wal')):
            try:
                stat = path.stat()
                result.append((stat.st_mtime_ns, stat.st_size))
            except FileNotFoundError:
                result.append(None)
        return tuple(result)

    def _invalidate_cache(self):
        state = self._cache_state()
        with state.lock:
            state.generation = None
            state.fallback_signature = None

    def _load_snapshot(self):
        state = self._cache_state()
        with state.lock:
            signature = self._file_signature()
            with self.connect() as db:
                db.execute('BEGIN')
                generation_row = db.execute(
                    "SELECT value FROM info WHERE key='generation'").fetchone()
                generation = int(generation_row[0]) if generation_row else None
                current = (state.snapshot is not None and
                           ((generation is not None and state.generation == generation)
                            or (generation is None and state.fallback_signature == signature)))
                if current:
                    state.hits += 1
                    return state.snapshot
                rows = db.execute(
                    'SELECT id,text,metadata,file,vector FROM vectors ORDER BY id').fetchall()
                dimension = db.execute(
                    "SELECT value FROM info WHERE key='dimensions'").fetchone()
            dimensions = int(dimension[0]) if dimension else 0
            ids = tuple(row[0] for row in rows)
            texts = tuple(row[1] for row in rows)
            metadatas = tuple(json.loads(row[2]) for row in rows)
            files = np.asarray([row[3] for row in rows], dtype=object)
            if rows:
                vectors = [np.frombuffer(row[4], dtype='<f4') for row in rows]
                if not dimensions:
                    dimensions = len(vectors[0])
                if any(len(vector) != dimensions for vector in vectors):
                    raise ValueError('Stored embedding dimensions are inconsistent.')
                matrix = np.stack(vectors).astype(np.float32, copy=False)
                norms = np.linalg.norm(matrix, axis=1)
                if not np.all(np.isfinite(matrix)) or np.any(~np.isfinite(norms)) or np.any(norms == 0):
                    raise ValueError('Invalid stored embeddings.')
                matrix /= norms[:, None]
            else:
                matrix = np.empty((0, dimensions), dtype=np.float32)
            matrix.setflags(write=False)
            files.setflags(write=False)
            buckets = {}
            for index, name in enumerate(files):
                buckets.setdefault(name, []).append(index)
            file_positions = {}
            for name, values in buckets.items():
                positions = np.asarray(values, dtype=np.intp)
                positions.setflags(write=False)
                file_positions[name] = positions
            snapshot = {
                'ids': ids, 'texts': texts, 'metadatas': metadatas, 'files': files,
                'matrix': matrix, 'file_positions': file_positions,
                'id_set': frozenset(ids),
                'positions': {identifier: index for index, identifier in enumerate(ids)},
            }
            state.snapshot = snapshot
            state.generation = generation
            state.fallback_signature = signature if generation is None else None
            state.loads += 1
            return snapshot

    def cache_info(self):
        state = self._cache_state()
        with state.lock:
            snapshot = state.snapshot
            return {
                'loaded': snapshot is not None,
                'rows': len(snapshot['ids']) if snapshot else 0,
                'dimensions': int(snapshot['matrix'].shape[1]) if snapshot else 0,
                'vector_bytes': int(snapshot['matrix'].nbytes) if snapshot else 0,
                'loads': state.loads,
                'hits': state.hits,
                'generation': state.generation,
            }

    def upsert_vectors(self, ids, texts, metadatas, vectors):
        """Accept supplied vectors for indexing/migration; never load a model here."""
        if not ids:
            return
        if len(set(ids)) != len(ids) or any(not isinstance(item, str) or not item for item in ids):
            raise ValueError('Vector IDs must be unique nonempty strings.')
        matrix = np.asarray(vectors, dtype='<f4')
        if (matrix.ndim != 2 or matrix.shape[0] != len(ids) or not matrix.shape[1]
                or len(texts) != len(ids) or len(metadatas) != len(ids)
                or not np.all(np.isfinite(matrix)) or np.any(np.linalg.norm(matrix, axis=1) == 0)):
            raise ValueError('Invalid vector batch or dimensions.')
        rows = []
        for identifier, text, metadata, vector in zip(ids, texts, metadatas, matrix):
            if not isinstance(text, str) or not isinstance(metadata, dict):
                raise ValueError('Invalid vector text or metadata.')
            rows.append((identifier, text, json.dumps(metadata, ensure_ascii=False, allow_nan=False),
                         str(metadata.get('file', '')), vector.tobytes()))
        with self.connect(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR IGNORE INTO info VALUES ('generation','0')")
            dimension = db.execute("SELECT value FROM info WHERE key='dimensions'").fetchone()
            if dimension is not None and int(dimension[0]) != matrix.shape[1]:
                raise ValueError('Embedding dimensions differ from this collection.')
            db.execute("INSERT OR IGNORE INTO info VALUES ('dimensions',?)", (str(matrix.shape[1]),))
            db.executemany('''INSERT INTO vectors VALUES (?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET
                text=excluded.text,metadata=excluded.metadata,file=excluded.file,vector=excluded.vector''', rows)
            db.execute("UPDATE info SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
        self._invalidate_cache()

    def add_documents(self, documents, ids):
        if not documents:
            return []
        if self.embeddings is None:
            raise ValueError('An embedding model is required for new text.')
        texts = [document.page_content for document in documents]
        self.upsert_vectors(ids, texts, [document.metadata for document in documents],
                            self.embeddings.embed_documents(texts))
        return ids

    def delete(self, ids):
        if not ids:
            return
        with self.connect(write=True) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("INSERT OR IGNORE INTO info VALUES ('generation','0')")
            db.executemany('DELETE FROM vectors WHERE id=?', [(identifier,) for identifier in ids])
            db.execute("UPDATE info SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
        self._invalidate_cache()

    @staticmethod
    def _validate_filter(where):
        if where is None:
            return None
        if set(where) != {'file'}:
            raise ValueError('Only file filters are supported.')
        value = where['file']
        if isinstance(value, str):
            return (value,)
        if (isinstance(value, dict) and set(value) == {'$in'}
                and isinstance(value['$in'], (list, tuple))
                and all(isinstance(name, str) for name in value['$in'])):
            return tuple(value['$in'])
        raise ValueError('Invalid file filter.')

    def _selected_indices(self, snapshot, ids=None, where=None):
        names = self._validate_filter(where)
        if ids is not None and not ids:
            return np.empty(0, dtype=np.intp)
        if names is not None and not names:
            return np.empty(0, dtype=np.intp)
        if ids is not None:
            if len(ids) == len(snapshot['ids']) and set(ids) == snapshot['id_set']:
                indices = np.arange(len(snapshot['ids']), dtype=np.intp)
            else:
                indices = np.asarray(sorted({snapshot['positions'][identifier] for identifier in ids
                                             if identifier in snapshot['positions']}), dtype=np.intp)
        elif names is not None:
            groups = [snapshot['file_positions'][name] for name in names
                      if name in snapshot['file_positions']]
            indices = np.sort(np.concatenate(groups)) if groups else np.empty(0, dtype=np.intp)
        else:
            indices = np.arange(len(snapshot['ids']), dtype=np.intp)
        if ids is not None and names is not None and len(indices):
            indices = indices[np.isin(snapshot['files'][indices], names)]
        return indices

    def get(self, ids=None, where=None, include=None, limit=None, *, cached=False):
        include = ['documents', 'metadatas'] if include is None else include
        if not set(include) <= {'documents', 'metadatas', 'embeddings'}:
            raise ValueError('Unsupported vector fields.')
        if limit is not None and (not isinstance(limit, int) or limit < 1):
            raise ValueError('Invalid limit.')
        if cached:
            snapshot = self._load_snapshot()
            indices = self._selected_indices(snapshot, ids, where)
            if limit is not None:
                indices = indices[:limit]
            result = self.empty(include)
            result['ids'] = [snapshot['ids'][index] for index in indices]
            if 'documents' in include:
                result['documents'] = [snapshot['texts'][index] for index in indices]
            if 'metadatas' in include:
                result['metadatas'] = [dict(snapshot['metadatas'][index]) for index in indices]
            if 'embeddings' in include:
                result['embeddings'] = snapshot['matrix'][indices]
            return result

        conditions = []
        parameters = []
        names = self._validate_filter(where)
        if names is not None:
            if not names:
                return self.empty(include)
            if len(names) == 1:
                conditions.append('v.file=?')
                parameters.append(names[0])
            else:
                conditions.append('v.file IN (SELECT name FROM selected_files)')
        if ids is not None and not ids:
            return self.empty(include)
        columns = ['v.id']
        for field, column in [('documents', 'v.text'), ('metadatas', 'v.metadata'),
                              ('embeddings', 'v.vector')]:
            if field in include:
                columns.append(column)
        with self.connect() as db:
            join = ''
            if ids is not None:
                db.execute('CREATE TEMP TABLE selected_ids (id TEXT PRIMARY KEY)')
                db.executemany('INSERT OR IGNORE INTO selected_ids VALUES (?)', [(item,) for item in ids])
                join = ' JOIN selected_ids s ON s.id=v.id'
            if names is not None and len(names) > 1:
                db.execute('CREATE TEMP TABLE selected_files (name TEXT PRIMARY KEY)')
                db.executemany('INSERT OR IGNORE INTO selected_files VALUES (?)', [(name,) for name in names])
            sql = 'SELECT ' + ','.join(columns) + ' FROM vectors v' + join
            if conditions:
                sql += ' WHERE ' + ' AND '.join(conditions)
            sql += ' ORDER BY v.id'
            if limit is not None:
                sql += ' LIMIT ?'
                parameters.append(limit)
            rows = db.execute(sql, parameters).fetchall()
        result = self.empty(include)
        result['ids'] = [row[0] for row in rows]
        offset = 1
        for field in ('documents', 'metadatas', 'embeddings'):
            if field not in include:
                continue
            values = [row[offset] for row in rows]
            offset += 1
            if field == 'metadatas':
                values = [json.loads(value) for value in values]
            if field == 'embeddings' and values:
                values = np.stack([np.frombuffer(value, dtype='<f4') for value in values])
            result[field] = values
        return result

    @staticmethod
    def empty(include):
        return {'ids': [], **{field: [] for field in include}}

    @staticmethod
    def _top_k(indices, distances, identifiers, k):
        count = min(k, len(indices))
        if not count:
            return np.empty(0, dtype=np.intp)
        if len(indices) > count:
            threshold = np.partition(distances, count - 1)[count - 1]
            candidates = np.flatnonzero(distances <= threshold)
        else:
            candidates = np.arange(len(indices), dtype=np.intp)
        names = np.asarray([identifiers[indices[item]] for item in candidates], dtype=object)
        order = np.lexsort((names, distances[candidates]))[:count]
        return candidates[order]

    def embed_query(self, question):
        if self.embeddings is None:
            raise ValueError('An embedding model is required for a query.')
        vector = np.asarray(self.embeddings.embed_query(question), dtype=np.float32)
        norm = np.linalg.norm(vector)
        if vector.ndim != 1 or not np.isfinite(norm) or norm == 0:
            raise ValueError('Invalid query embedding or dimensions.')
        return vector / norm

    def dense_search_vector(self, vector, k=4, *, ids=None, filter=None):
        if k < 1:
            raise ValueError('k must be positive.')
        snapshot = self._load_snapshot()
        indices = self._selected_indices(snapshot, ids, filter)
        if not len(indices):
            return []
        vector = np.asarray(vector, dtype=np.float32)
        matrix = snapshot['matrix']
        norm = np.linalg.norm(vector)
        if (vector.ndim != 1 or matrix.shape[1] != len(vector)
                or not np.isfinite(norm) or norm == 0):
            raise ValueError('Invalid query embedding or dimensions.')
        vector = vector / norm
        all_distances = 1 - np.clip(matrix @ vector, -1, 1)
        distances = all_distances[indices]
        if not np.all(np.isfinite(distances)):
            raise ValueError('Invalid stored embeddings.')
        selected = self._top_k(indices, distances, snapshot['ids'], k)
        return [{
            'id': snapshot['ids'][indices[item]],
            'text': snapshot['texts'][indices[item]],
            'metadata': dict(snapshot['metadatas'][indices[item]]),
            'score': float(distances[item]),
        } for item in selected]

    def dense_search(self, question, k=4, *, ids=None, filter=None):
        return self.dense_search_vector(self.embed_query(question), k, ids=ids, filter=filter)

    @staticmethod
    def _fts_query(question):
        raw = list(dict.fromkeys(token.casefold() for token in _TOKEN.findall(question)))
        tokens = [token for token in raw if token not in FTS_STOP_WORDS]
        if not tokens:
            tokens = raw
        return ' OR '.join('"' + token.replace('"', '""') + '"' for token in tokens)

    def lexical_search(self, question, k=4, *, ids=None, filter=None):
        if k < 1:
            raise ValueError('k must be positive.')
        if not self.lexical_index_available():
            raise RuntimeError('Optional lexical index unavailable. Run: '
                               'python -m tools.prepare_lexical_index')
        query = self._fts_query(question)
        if not query or ids is not None and not ids:
            return []
        names = self._validate_filter(filter)
        if names is not None and not names:
            return []
        if ids is not None:
            snapshot = self._load_snapshot()
            if len(ids) == len(snapshot['ids']) and set(ids) == snapshot['id_set']:
                ids = None
        conditions = ['vectors_fts MATCH ?']
        parameters = [query]
        if names is not None:
            if len(names) == 1:
                conditions.append('v.file=?')
                parameters.append(names[0])
            else:
                conditions.append('v.file IN (SELECT name FROM selected_files)')
        with self.connect() as db:
            joins = ['JOIN fts_rows f ON f.docid=vectors_fts.rowid',
                     'JOIN vectors v ON v.id=f.id']
            if ids is not None:
                db.execute('CREATE TEMP TABLE selected_ids (id TEXT PRIMARY KEY)')
                db.executemany('INSERT OR IGNORE INTO selected_ids VALUES (?)', [(item,) for item in ids])
                joins.append('JOIN selected_ids s ON s.id=v.id')
            if names is not None and len(names) > 1:
                db.execute('CREATE TEMP TABLE selected_files (name TEXT PRIMARY KEY)')
                db.executemany('INSERT OR IGNORE INTO selected_files VALUES (?)', [(name,) for name in names])
            parameters.append(k)
            rows = db.execute('''SELECT v.id,v.text,v.metadata,bm25(vectors_fts) AS score
                FROM vectors_fts ''' + ' '.join(joins) + ' WHERE ' + ' AND '.join(conditions) +
                ' ORDER BY score,v.id LIMIT ?', parameters).fetchall()
        return [{'id': row[0], 'text': row[1], 'metadata': json.loads(row[2]),
                 'score': float(row[3])} for row in rows]

    def hybrid_search(self, question, k=4, *, ids=None, filter=None, candidate_k=50, rrf_k=60,
                      vector=None):
        if k < 1 or candidate_k < k or rrf_k < 1:
            raise ValueError('Invalid hybrid search limits.')
        vector = self.embed_query(question) if vector is None else vector
        dense = self.dense_search_vector(vector, candidate_k, ids=ids, filter=filter)
        lexical = self.lexical_search(question, candidate_k, ids=ids, filter=filter)
        scores = {}
        records = {}
        for ranking in (dense, lexical):
            for rank, record in enumerate(ranking, 1):
                records[record['id']] = record
                scores[record['id']] = scores.get(record['id'], 0.0) + 1 / (rrf_k + rank)
        order = sorted(scores, key=lambda identifier: (-scores[identifier], identifier))[:k]
        return [{**records[identifier], 'score': -scores[identifier]} for identifier in order]

    @staticmethod
    def _documents(records):
        return [(Document(page_content=record['text'], metadata=record['metadata']), record['score'])
                for record in records]

    def similarity_search_with_score(self, question, k=4, *, ids=None, filter=None):
        return self._documents(self.dense_search(question, k, ids=ids, filter=filter))

    def lexical_search_with_score(self, question, k=4, *, ids=None, filter=None):
        return self._documents(self.lexical_search(question, k, ids=ids, filter=filter))

    def hybrid_search_with_score(self, question, k=4, *, ids=None, filter=None):
        return self._documents(self.hybrid_search(question, k, ids=ids, filter=filter))
