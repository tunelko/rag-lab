"""Actual source/SQLite audits, no model or sensor simulations."""
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import document_audit
from rag import Config


class AuditTests(unittest.TestCase):
    def test_every_file_is_checked_duplicates_and_bad_utf8_are_reported(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            docs = root / 'documents'
            docs.mkdir()
            (docs / 'first.md').write_text('An actual source document.', 'utf-8')
            (docs / 'duplicate.md').write_text('An actual source document.', 'utf-8')
            (docs / 'broken.txt').write_bytes(b'\xff\xfe\x00')
            (docs / 'unhandled.bin').write_bytes(b'An unsupported source must not be silently skipped.')
            service = SimpleNamespace(config=Config(db=root / 'chroma'), documents=docs, collection_key='test',
                                      check_cancel=lambda job: None, update=lambda job, **kw: None)
            service.collection_pool = SimpleNamespace(default=service)
            result = document_audit.audit(service, 'audit', [service])['audit']
            self.assertEqual(result['checked_documents'], 4)
            self.assertEqual(result['total_documents'], 4)
            self.assertEqual(len(result['duplicate_groups']), 1)
            self.assertEqual(len(result['issues']), 2)
            self.assertEqual(result['issues'][0]['file'], 'broken.txt')
            self.assertEqual(result['status'], 'requires_review')
            self.assertTrue((root / 'audits' / 'latest.json').is_file())


if __name__ == '__main__':
    unittest.main()
