"""Verify cache invalidation and bounded memory with temporary files."""
import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import file_fingerprints as fingerprints


class FingerprintTests(unittest.TestCase):
    def test_cached_reads_are_reused_and_edits_invalidate(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'document.pdf'
            path.write_bytes(b'original')
            cache = fingerprints.FileFingerprints()
            with patch.object(fingerprints, 'sha256_file', wraps=fingerprints.sha256_file) as read:
                first = cache.digest(path)
                self.assertEqual(cache.digest(path), first)
                self.assertEqual(read.call_count, 1)
                path.write_bytes(b'new content')
                self.assertNotEqual(cache.digest(path), first)
                self.assertEqual(read.call_count, 2)
            self.assertEqual(cache.snapshot()['hits'], 1)
            self.assertEqual(cache.snapshot()['misses'], 2)

    def test_atomic_replacement_invalidates_even_for_equal_size(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'document.pdf'
            path.write_bytes(b'aaaa')
            cache = fingerprints.FileFingerprints()
            first = cache.digest(path)
            replacement = path.with_suffix('.pending')
            replacement.write_bytes(b'bbbb')
            replacement.replace(path)
            self.assertNotEqual(cache.digest(path), first)

    def test_lru_is_bounded_and_missing_files_are_not_cached(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths = [root / str(number) for number in range(3)]
            for path in paths:
                path.write_bytes(b'data')
            cache = fingerprints.FileFingerprints(limit=2)
            for path in paths:
                cache.digest(path)
            self.assertEqual(cache.snapshot()['entries'], 2)
            self.assertNotIn(paths[0].resolve(), cache.entries)
            paths[2].unlink()
            with self.assertRaises(FileNotFoundError):
                cache.digest(paths[2])

    def test_changed_during_hash_is_retried(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'document.pdf'
            path.write_bytes(b'first')
            original = fingerprints.sha256_file
            def changing(file):
                value = original(file)
                if file.read_bytes() == b'first':
                    file.write_bytes(b'second content')
                return value
            cache = fingerprints.FileFingerprints()
            with patch.object(fingerprints, 'sha256_file', side_effect=changing):
                self.assertEqual(cache.digest(path), hashlib.sha256(b'second content').hexdigest())

    def test_ingest_hash_helper_reads_without_materializing_the_file(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'document.pdf'
            path.write_bytes(b'content')
            with patch.object(Path, 'read_bytes', side_effect=AssertionError('must stream')):
                self.assertEqual(fingerprints.sha256_file(path), hashlib.sha256(b'content').hexdigest())
