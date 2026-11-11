"""Offline tests of the focused retrieval pipeline with hand-made vectors."""
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import rag
import retrieval_pipeline
from retrieval_pipeline import automatic_scope_spans, retrieve, without_identifier


class FakeEmbeddings:
    """Every query maps to the same vector; the questions asked are recorded."""
    def __init__(self):
        self.queries = []

    def embed_query(self, text):
        self.queries.append(text)
        return [1.0, 0.0, 0.0, 0.0]


ROWS = [
    # id, text, file, page, chunk, item_type, vector
    ('z-title', 'PRO Z790 Motherboard user guide', 'PRO-Z790.pdf', 1, 0, 'text', [0.0, 1.0, 0.0, 0.0]),
    ('z-caption', 'Descripción de la página de especificaciones de memoria.', 'PRO-Z790.pdf', 5, 1,
     'page_image', [1.0, 0.0, 0.0, 0.0]),
    ('z-text', 'Memory: supports up to 192GB.', 'PRO-Z790.pdf', 5, 2, 'text', [0.8, 0.2, 0.0, 0.0]),
    ('z-scan', 'Página escaneada: el puente JP1 borra la CMOS.', 'PRO-Z790.pdf', 9, 3,
     'page_image', [0.5, 0.5, 0.0, 0.0]),
    ('b-text', 'Memory: supports up to 128GB.', 'B550M.pdf', 5, 0, 'text', [0.9, 0.1, 0.0, 0.0]),
]


class RetrievalPipelineTests(unittest.TestCase):
    def setUp(self):
        sandbox = rag.ROOT / 'data' / 'test-chroma'
        sandbox.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix='pipeline-', dir=sandbox))
        self.config = rag.Config(db=root / 'database', collection='pipeline_test')
        self.embeddings = FakeEmbeddings()
        store = rag.open_store(self.config, self.embeddings)
        store.upsert_vectors(
            [row[0] for row in ROWS], [row[1] for row in ROWS],
            [{'file': row[2], 'source': row[2], 'page': row[3], 'chunk': row[4], 'item_type': row[5]}
             for row in ROWS], [row[6] for row in ROWS])
        files = {}
        for row in ROWS:
            files.setdefault(row[2], {'sha256': 'x', 'ids': [], 'file': row[2]})['ids'].append(row[0])
        rag.save_state(self.config, {'config': self.config.identity(), 'files': files})
        retrieval_pipeline._catalogs.clear()

    def ids(self, hits):
        return [document.page_content for document, _distance in hits]

    def test_native_channel_scope_and_identifier_free_query(self):
        hits, trace = retrieve(self.config, 'PRO Z790 max memory?', self.embeddings, 1)
        # The caption is the nearest vector and the other board's text the next one;
        # the native channel and the model scope leave the right manual's own text.
        self.assertEqual(self.ids(hits), ['Memory: supports up to 192GB.'])
        self.assertEqual(trace['strategy'], 'native-channel')
        self.assertTrue(trace['automatic_scope'])
        self.assertEqual(trace['search_query'], 'max memory?')
        self.assertEqual(self.embeddings.queries[-1], 'max memory?')
        self.assertAlmostEqual(hits[0][1], 1 - 0.8 / (0.8 ** 2 + 0.2 ** 2) ** .5, places=5)

    def test_caption_stands_in_only_for_pages_without_native_text(self):
        hits, _trace = retrieve(self.config, 'PRO Z790 jumper', self.embeddings, 3)
        contents = self.ids(hits)
        self.assertIn('Página escaneada: el puente JP1 borra la CMOS.', contents)
        self.assertNotIn('Descripción de la página de especificaciones de memoria.', contents)

    def test_visual_questions_keep_every_channel(self):
        hits, trace = retrieve(self.config, 'PRO Z790 max memory?', self.embeddings, 1, visual=True)
        self.assertEqual(self.ids(hits), ['Descripción de la página de especificaciones de memoria.'])
        self.assertEqual(trace['strategy'], 'all-channels')

    def test_user_scope_wins_and_no_model_means_whole_scope(self):
        hits, trace = retrieve(self.config, 'PRO Z790 max memory?', self.embeddings, 1,
                               files=['B550M.pdf'])
        self.assertEqual(self.ids(hits), ['Memory: supports up to 128GB.'])
        self.assertFalse(trace['automatic_scope'])
        hits, trace = retrieve(self.config, 'max memory?', self.embeddings, 1)
        self.assertEqual(self.ids(hits), ['Memory: supports up to 128GB.'])
        self.assertFalse(trace['automatic_scope'])
        self.assertEqual(retrieve(self.config, 'max memory?', self.embeddings, 1, files=[])[0], [])

    def test_reranker_reorders_candidates_and_failures_keep_dense_order(self):
        def reranker(query, texts):
            self.assertEqual(query, 'jumper')
            return [1.0 if 'JP1' in text else 0.0 for text in texts]
        reranker.model_id = 'fake-reranker'
        hits, trace = retrieve(self.config, 'PRO Z790 jumper', self.embeddings, 1, reranker=reranker)
        self.assertEqual(self.ids(hits), ['Página escaneada: el puente JP1 borra la CMOS.'])
        self.assertTrue(trace['reranked'])
        self.assertEqual(trace['reranker'], 'fake-reranker')

        def broken(_query, _texts):
            raise RuntimeError('out of memory')
        with self.assertLogs('rag.retrieval', level='WARNING'):
            hits, trace = retrieve(self.config, 'PRO Z790 jumper', self.embeddings, 1, reranker=broken)
        self.assertEqual(self.ids(hits), ['Memory: supports up to 192GB.'])
        self.assertFalse(trace['reranked'])

    def test_catalog_is_rebuilt_only_when_the_index_changes(self):
        with patch.object(retrieval_pipeline, 'channel_catalog',
                          wraps=retrieval_pipeline.channel_catalog) as catalog:
            retrieve(self.config, 'PRO Z790 max memory?', self.embeddings, 1)
            retrieve(self.config, 'PRO Z790 jumper', self.embeddings, 1)
            self.assertEqual(catalog.call_count, 1)
            state = rag.load_state(self.config)
            state['files']['B550M.pdf']['ids'] = []
            rag.save_state(self.config, state)
            hits, _trace = retrieve(self.config, 'max memory?', self.embeddings, 1)
            self.assertEqual(catalog.call_count, 2)
        # Unpublished vectors are never candidates.
        self.assertEqual(self.ids(hits), ['Memory: supports up to 192GB.'])

    def test_reranker_status_reports_configuration_without_loading(self):
        with patch.dict(os.environ, {'RAG_RERANKER': ''}):
            self.assertEqual(retrieval_pipeline.reranker_status()['model'], None)
        with patch.dict(os.environ, {'RAG_RERANKER': 'local/reranker', 'RAG_RERANK_WINDOW': '30'}),                 patch.dict(retrieval_pipeline._reranker, {'key': None, 'scorer': None}),                 patch.object(retrieval_pipeline, 'load_reranker', side_effect=AssertionError('must not load')):
            status = retrieval_pipeline.reranker_status()
            self.assertEqual((status['model'], status['window'], status['loaded'], status['unavailable']),
                             ('local/reranker', 30, False, False))
        with patch.dict(os.environ, {'RAG_RERANKER': 'local/reranker'}),                 patch.dict(retrieval_pipeline._reranker, {'key': None, 'scorer': None}),                 patch.object(retrieval_pipeline, 'load_reranker', side_effect=OSError('not cached')):
            with self.assertLogs('rag.retrieval', level='WARNING'):
                self.assertIsNone(retrieval_pipeline.configured_reranker())
            self.assertTrue(retrieval_pipeline.reranker_status()['unavailable'])

    def test_aliases_respect_token_boundaries_of_the_question(self):
        aliases = {'magb660mmortarddr4c': {'datasheet.pdf'},
                   'magb660mmortarddr4': {'manual.pdf', 'datasheet.pdf'}}
        files, matched, spans = automatic_scope_spans(
            'MAG B660M MORTAR DDR4 のクリアCMOSジャンパーをショートする時間は', aliases)
        self.assertEqual(files, ['datasheet.pdf', 'manual.pdf'])
        self.assertEqual(matched, ['magb660mmortarddr4'])
        self.assertEqual(
            without_identifier('MAG B660M MORTAR DDR4 のクリアCMOSジャンパーをショートする時間は', spans),
            'のクリアCMOSジャンパーをショートする時間は')
        self.assertEqual(automatic_scope_spans('unrelated ddr4cmos text', aliases)[0], [])


if __name__ == '__main__':
    unittest.main()
