"""Local categories and subcategories, independent of PDFs, vectors and embeddings."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3
import unicodedata
from uuid import uuid4


class TopicError(ValueError):
    pass


class TopicStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS topics (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, name_key TEXT NOT NULL,
                    parent_id TEXT REFERENCES topics(id) ON DELETE RESTRICT);
                CREATE UNIQUE INDEX IF NOT EXISTS topic_names
                    ON topics(COALESCE(parent_id, ''), name_key);
                CREATE TABLE IF NOT EXISTS assignments (
                    name TEXT PRIMARY KEY, topic_id TEXT NOT NULL
                    REFERENCES topics(id) ON DELETE RESTRICT);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA foreign_keys=ON')
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def clean_name(name):
        name = unicodedata.normalize('NFC', name).strip()
        if not name or len(name) > 80 or any(unicodedata.category(char).startswith('C') for char in name):
            raise TopicError('Usa un nombre de entre 1 y 80 caracteres, sin caracteres de control.')
        return name

    def create(self, name, parent_id=None):
        name = self.clean_name(name)
        identifier = uuid4().hex
        try:
            with self.connect() as db:
                if parent_id:
                    db.execute('BEGIN IMMEDIATE')
                    parent = db.execute('SELECT parent_id FROM topics WHERE id=?', (parent_id,)).fetchone()
                    if parent is None or parent['parent_id'] is not None:
                        raise TopicError('La subcategoría debe pertenecer a una categoría principal existente.')
                db.execute('INSERT INTO topics VALUES (?,?,?,?)', (identifier, name, name.casefold(), parent_id))
        except sqlite3.IntegrityError as error:
            raise TopicError('Ya existe ese nombre en este nivel.') from error
        return identifier

    def rename(self, identifier, name):
        name = self.clean_name(name)
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if not db.execute('UPDATE topics SET name=?, name_key=? WHERE id=?', (name, name.casefold(), identifier)).rowcount:
                    raise TopicError('La categoría ya no está disponible.')
        except sqlite3.IntegrityError as error:
            raise TopicError('Ya existe ese nombre en este nivel.') from error

    def remove(self, identifier, *, existing_names=None):
        try:
            with self.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if existing_names is not None:
                    existing=set(existing_names)
                    orphaned=[(row['name'],) for row in db.execute('SELECT name FROM assignments WHERE topic_id=?',(identifier,)) if row['name'] not in existing]
                    db.executemany('DELETE FROM assignments WHERE name=?',orphaned)
                if not db.execute('DELETE FROM topics WHERE id=?', (identifier,)).rowcount:
                    raise TopicError('La categoría ya no está disponible.')
        except sqlite3.IntegrityError as error:
            raise TopicError('Mueve primero los documentos y elimina las subcategorías vacías de esta categoría.') from error

    def assign(self, names, topic_id):
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            # Check the destination in the same transaction, including for empty batches.
            if topic_id and not db.execute('SELECT 1 FROM topics WHERE id=?', (topic_id,)).fetchone():
                raise TopicError('La categoría ya no está disponible.')
            if topic_id:
                db.executemany('INSERT INTO assignments VALUES (?,?) ON CONFLICT(name) DO UPDATE SET topic_id=excluded.topic_id',
                               [(name, topic_id) for name in names])
            else:
                db.executemany('DELETE FROM assignments WHERE name=?', [(name,) for name in names])

    def assignments(self):
        with self.connect() as db:
            return {row['name']: row['topic_id'] for row in db.execute('SELECT name,topic_id FROM assignments')}

    def snapshot(self, documents):
        with self.connect() as db:
            topics = [dict(row) for row in db.execute('SELECT id,name,parent_id FROM topics ORDER BY name_key,id')]
        for topic in topics:
            ids = {topic['id'], *(child['id'] for child in topics if child['parent_id'] == topic['id'])}
            members = [item for item in documents if item.get('topic_id') in ids]
            topic.update(document_count=len(members), indexed_count=sum(bool(item.get('indexed')) for item in members),
                         chunk_count=sum(item.get('chunks', 0) for item in members))
        return {'topics': topics, 'unassigned_count': sum(not item.get('topic_id') for item in documents)}

    def scope(self, topic_id, documents):
        if topic_id is None:
            return documents, None
        if topic_id == 'unassigned':
            return [item for item in documents if not item.get('topic_id')], 'unassigned'
        topics = self.snapshot(documents)['topics']
        topic = next((item for item in topics if item['id'] == topic_id), None)
        if topic is None:
            raise TopicError('La categoría ya no está disponible.')
        parent = next((item for item in topics if item['id'] == topic['parent_id']), None)
        ids = {topic_id, *(item['id'] for item in topics if item['parent_id'] == topic_id)}
        label = f"{parent['name']} / {topic['name']}" if parent else topic['name']
        return [item for item in documents if item.get('topic_id') in ids], label
