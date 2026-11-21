"""Read archived, content-addressed evidence for previously saved citations."""
from contextlib import closing
import hashlib
from pathlib import Path
import sqlite3
import zlib

MAX_TEXT = 8 * 1024 ** 2


class EvidenceStore:
    def __init__(self, path):
        self.path = Path(path)

    def get(self, version):
        if not self.path.is_file():
            raise ValueError('Evidencia no disponible.')
        try:
            with closing(sqlite3.connect(self.path.absolute().as_uri() + '?mode=ro', uri=True)) as db:
                row = db.execute('SELECT name,content FROM evidence WHERE version=?', (version,)).fetchone()
        except sqlite3.Error as error:
            raise ValueError('Evidencia no disponible.') from error
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
