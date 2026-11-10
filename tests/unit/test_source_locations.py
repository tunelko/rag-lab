"""PDF navigation uses physical positions independently of document labels."""

from pathlib import Path
import tempfile
import unittest

import pymupdf

from source_locations import pdf_page_label


class SourceLocationTests(unittest.TestCase):
    def test_labels_can_repeat_and_include_roman_front_matter(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual.pdf"
            with pymupdf.open() as pdf:
                for _ in range(4):
                    pdf.new_page()
                pdf.set_page_labels([
                    {"startpage": 0, "style": "R", "firstpagenum": 1},
                    {"startpage": 1, "style": "D", "firstpagenum": 1},
                    {"startpage": 3, "style": "D", "firstpagenum": 1},
                ])
                pdf.save(path)
            self.assertEqual([pdf_page_label(path, i) for i in range(1, 5)], ["I", "1", "2", "1"])
            self.assertIsNone(pdf_page_label(path, 0))
            self.assertIsNone(pdf_page_label(path, 5))

    def test_replacing_a_pdf_invalidates_cached_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual.pdf"
            for prefix in ("old-", "replacement-"):
                with pymupdf.open() as pdf:
                    pdf.new_page()
                    pdf.set_page_labels([{"startpage": 0, "prefix": prefix, "style": "D", "firstpagenum": 8}])
                    pdf.save(path)
                self.assertEqual(pdf_page_label(path, 1), prefix + "8")

    def test_missing_labels_are_not_guessed_from_page_text(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "manual.pdf"
            with pymupdf.open() as pdf:
                pdf.new_page().insert_text((50, 50), "36")
                pdf.save(path)
            self.assertIsNone(pdf_page_label(path, 1))
            self.assertIsNone(pdf_page_label(path.with_suffix('.txt'), 1))
            self.assertIsNone(pdf_page_label(path.with_name('missing.pdf'), 1))
