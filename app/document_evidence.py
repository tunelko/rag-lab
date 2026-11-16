"""Immutable, content-addressed source text for citations across dataset updates."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import zlib

MAX_TEXT = 8 * 1024 ** 2


class EvidenceStore:
    def __init__(self, path):
        self.path = Path(path)

    def put(self, name, content):
        raw = content.encode('utf-8')
        if len(raw) > MAX_TEXT:
            raise ValueError('La evidencia supera el límite de 8 MiB.')
        version = hashlib.sha256(name.encode('utf-8') + b'\0' + raw).hexdigest()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute('CREATE TABLE IF NOT EXISTS evidence(version TEXT PRIMARY KEY,name TEXT NOT NULL,content BLOB NOT NULL)')
            db.execute('INSERT OR IGNORE INTO evidence VALUES(?,?,?)', (version, name, zlib.compress(raw)))
        return version

    def get(self, version):
        if not self.path.is_file():
            raise ValueError('Evidencia no disponible.')
        with closing(sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True)) as db:
            row = db.execute('SELECT name,content FROM evidence WHERE version=?', (version,)).fetchone()
        if row is None:
            raise ValueError('Evidencia no disponible.')
        stream = zlib.decompressobj()
        try:
            content = stream.decompress(row[1], MAX_TEXT + 1)
        except zlib.error as error:
            raise ValueError('Evidencia dañada.') from error
        if not stream.eof or len(content) > MAX_TEXT:
            raise ValueError('Evidencia dañada o demasiado grande.')
        text = content.decode('utf-8')
        if hashlib.sha256(row[0].encode('utf-8') + b'\0' + content).hexdigest() != version:
            raise ValueError('La evidencia no supera la verificación de integridad.')
        return {'file': row[0], 'text': text}
