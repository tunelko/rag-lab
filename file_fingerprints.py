"""Bounded, process-local SHA-256 cache for read-only library polling."""
from collections import OrderedDict
import hashlib
from pathlib import Path
import threading


def sha256_file(path: Path):
    # hashlib streams the file instead of allocating an entire PDF/ZIP in RAM.
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def signature(stat):
    return (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class FileFingerprints:
    def __init__(self, limit=512):
        if limit < 1:
            raise ValueError('The cache limit must be positive.')
        self.limit = limit
        self.entries = OrderedDict()
        self.lock = threading.RLock()
        self.hits = 0
        self.misses = 0

    def digest(self, path: Path):
        path = path.resolve()
        with self.lock:
            before = signature(path.stat())
            cached = self.entries.get(path)
            if cached and cached[0] == before:
                self.hits += 1
                self.entries.move_to_end(path)
                return cached[1]
            self.misses += 1
            # A changed file must not be cached under an earlier file signature.
            for _ in range(2):
                value = sha256_file(path)
                after = signature(path.stat())
                if before == after:
                    self.entries[path] = (after, value)
                    self.entries.move_to_end(path)
                    while len(self.entries) > self.limit:
                        self.entries.popitem(last=False)
                    return value
                before = after
            self.entries.pop(path, None)
            raise OSError('El documento está cambiando; vuelve a consultar su estado.')

    def snapshot(self):
        with self.lock:
            return {'entries': len(self.entries), 'limit': self.limit, 'hits': self.hits, 'misses': self.misses}
