"""Real local previews: physical pages, bounded rendering and source containment."""
import unittest
from unittest.mock import patch

import pymupdf
from tests.unit import test_web
import document_preview


class DocumentPreviewTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown

    def test_physical_pdf_page_and_label_with_bounded_rendering_and_unchanged_file(self):
        service = self.client.app.state.service
        path = service.documents / 'preview.pdf'
        with pymupdf.open() as pdf:
            pdf.new_page(width=10000, height=20000).draw_rect((0, 0, 10000, 20000), fill=(1, 0, 0))
            pdf.new_page(width=300, height=200).draw_rect((0, 0, 300, 200), fill=(0, 0, 1))
            pdf.set_page_labels([{'startpage': 1, 'style': 'D', 'firstpagenum': 8}])
            pdf.save(path)
        original = path.read_bytes()
        with patch.object(service, 'get_embeddings') as embeddings:
            details = self.client.get('/api/source/preview', params={'file': path.name, 'page': 2})
            self.assertEqual(details.status_code, 200, details.text)
            self.assertEqual((details.json()['page'], details.json()['page_label'], details.json()['pages']), (2, '8', 2))
            image = self.client.get(details.json()['url'])
            self.assertEqual(image.headers['content-type'], 'image/png')
            pixels = pymupdf.Pixmap(image.content)
            self.assertEqual(pixels.pixel(10, 10), (0, 0, 255))
            large = self.client.get('/api/source/page', params={'file': path.name, 'page': 1})
            pixels = pymupdf.Pixmap(large.content)
            self.assertLessEqual(max(pixels.width, pixels.height), document_preview.MAX_SIDE)
            embeddings.assert_not_called()
        self.assertEqual(path.read_bytes(), original)
        self.assertIn("object-src 'none'", image.headers['content-security-policy'])
        self.assertIn("frame-ancestors 'none'", image.headers['content-security-policy'])
        for page in (0, 3):
            self.assertEqual(self.client.get('/api/source/preview', params={'file': path.name, 'page': page}).status_code, 422)

    def test_preview_containment_missing_files_and_inert_bounded_text(self):
        service = self.client.app.state.service
        (service.documents / 'notes.md').write_text('<script>window.__active=true</script>' + 'x' * document_preview.TEXT_LIMIT, encoding='utf-8')
        result = self.client.get('/api/source/preview', params={'file': 'notes.md'}).json()
        self.assertEqual(result['kind'], 'text')
        self.assertTrue(result['truncated'])
        self.assertTrue(result['text'].startswith('<script>'))
        self.assertEqual(len(result['text']), document_preview.TEXT_LIMIT)
        outside = self.case / 'outside.txt'
        outside.write_text('not a source', encoding='utf-8')
        for route in ('preview', 'page'):
            for name in ('../../.env', str(outside), 'missing.pdf'):
                self.assertEqual(self.client.get('/api/source/' + route, params={'file': name}).status_code, 404)
        self.assertEqual(self.client.get('/api/source/page?file=notes.md').status_code, 422)

    def test_original_images_are_local_and_unreadable_files_fail_explicitly(self):
        service = self.client.app.state.service
        path = service.documents / 'figure.png'
        pixels = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 80, 40), False)
        pixels.clear_with(200);pixels.save(path)
        details = self.client.get('/api/source/preview?file=figure.png').json()
        self.assertEqual(details['kind'], 'image')
        self.assertEqual(self.client.get(details['url']).status_code, 200)
        (service.documents / 'broken.pdf').write_bytes(b'not a PDF')
        self.assertEqual(self.client.get('/api/source/preview?file=broken.pdf').status_code, 422)
        self.assertEqual(self.client.get('/api/source/page?file=broken.pdf').status_code, 422)
