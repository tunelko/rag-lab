"""Local catalog replies do not call translation services or modify filenames."""
import unittest

from localization import text
from library_queries import catalog_answer


class LocalizationTests(unittest.TestCase):
    def test_local_ui_catalogs_are_shared_with_the_backend(self):
        self.assertEqual(text('language.label', 'en'), 'Language')
        self.assertEqual(text('language.label', 'es'), 'Idioma')
        self.assertEqual(text('language.label', 'unknown'), 'Idioma')
        self.assertEqual(text('server.table', 'en', done=1, total=2), 'Generating table · 1/2 documents')

    def test_catalog_inventory_in_english_preserves_source_identity(self):
        documents = [{'name': 'Manual técnico.pdf', 'indexed': True, 'chunks': 1234},
                     {'name': 'pendiente.pdf', 'indexed': False, 'chunks': 0}]
        answer, metrics = catalog_answer(documents, 'catalog_list', language='en')
        self.assertIn('indexed document', answer)
        self.assertIn('1,234 chunks', answer)
        self.assertIn('Manual técnico', answer)
        self.assertIn('Manual%20t%C3%A9cnico.pdf', answer)
        self.assertIn('does not yet have an up-to-date index', answer)
        self.assertEqual(metrics['model'], 'Index catalog')
