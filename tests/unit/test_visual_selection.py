"""Synthetic PDFs verify conservative triage and honest label-based evaluation."""
import json
from pathlib import Path
import tempfile
import unittest

import pymupdf
from tools.visual_audit import audit, evaluate
from visual_selection import analyze_page


class VisualSelectionTests(unittest.TestCase):
    def fixture(self):
        pdf=pymupdf.open()
        page=pdf.new_page()
        page.insert_textbox((50,70,540,700),'Technical specifications and ordinary body text. '*40,fontsize=10)
        page.draw_line((30,40),(565,40))
        page=pdf.new_page()
        page.insert_text((70,90),'ATX_12V connector pinout')
        for x in (90,130,170): page.draw_line((x,120),(x,220))
        page.draw_rect((80,120,190,220))
        page=pdf.new_page()
        pixmap=pymupdf.Pixmap(pymupdf.csRGB,(0,0,120,80),False)
        pixmap.clear_with(150)
        page.insert_image((80,100,500,500),stream=pixmap.tobytes('png'))
        pdf.new_page()
        return pdf

    def test_text_decoration_is_skipped_but_vector_pinouts_and_large_images_survive(self):
        with self.fixture() as pdf:
            decisions=[analyze_page(page) for page in pdf]
        self.assertFalse(decisions[0]['candidate'])
        self.assertEqual(decisions[1]['reason'],'technical-diagram-cue')
        self.assertTrue(decisions[1]['candidate'])
        self.assertTrue(decisions[2]['candidate'])
        self.assertFalse(decisions[3]['candidate'])
        self.assertTrue(decisions[3]['features']['legacy_visual'])

    def test_report_is_non_destructive_and_recall_waits_for_real_labels(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            path=root/'manual.pdf'
            with self.fixture() as pdf: pdf.save(path)
            original=path.read_bytes()
            dataset=audit([path],root/'report',previews=4)
            self.assertEqual(path.read_bytes(),original)
            self.assertEqual(dataset['summary']['model_calls'],0)
            self.assertFalse(dataset['summary']['applied_to_ingestion'])
            self.assertIsNone(evaluate(dataset,{})['recall_reviewed'])
            labels={dataset['pages'][1]['id']:'relevant',dataset['pages'][0]['id']:'irrelevant'}
            metrics=evaluate(dataset,labels)
            self.assertEqual(metrics['reviewed'],2)
            self.assertEqual(metrics['recall_reviewed'],1)
            self.assertTrue((root/'report/index.html').is_file())
            self.assertEqual(len(list((root/'report/previews').glob('*.png'))),4)
            self.assertFalse((root/'data').exists())
