"""Phase 0 contracts against frozen baseline outputs; no models, GPU or network."""
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from langchain_core.documents import Document
import rag
import retrieval_evaluation as lab
import retrieval_pipeline
from tests.unit.test_retrieval_pipeline import FakeEmbeddings, ROWS
from vector_store import VectorStore

BASELINE = json.loads((Path(__file__).parents[1] / 'fixtures' / 'laboratory-baseline.json').read_text(encoding='utf-8'))


class LaboratoryEngineTests(unittest.TestCase):
    def setUp(self):
        root = rag.ROOT / 'data' / 'test-chroma'
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='laboratory-', dir=root)
        self.config = rag.Config(db=Path(self.temporary.name) / 'db', collection='lab_test')
        self.embeddings = FakeEmbeddings()
        self.store = rag.open_store(self.config, None)
        self.store.upsert_vectors([row[0] for row in ROWS], [row[1] for row in ROWS],
            [{'file': row[2], 'source': row[2], 'page': row[3], 'chunk': row[4], 'item_type': row[5]}
             for row in ROWS], [row[6] for row in ROWS])
        files = {}
        for row in ROWS:
            files.setdefault(row[2], {'sha256': 'x', 'file': row[2], 'ids': []})['ids'].append(row[0])
        self.state = {'config': self.config.identity(), 'files': files}
        rag.save_state(self.config, self.state)
        retrieval_pipeline.clear_catalogs()

    def tearDown(self):
        VectorStore.clear_cache(self.store.path)
        retrieval_pipeline.clear_catalogs()
        self.temporary.cleanup()

    @staticmethod
    def scorer(query, texts):
        return [float('192GB' in text) for text in texts]

    def content(self, **overrides):
        return json.dumps({'id': 'q1', 'question': 'PRO Z790 max memory?',
            'relevant': [{'file': 'PRO-Z790.pdf', 'page': 5}], 'evidence': ['192GB'], **overrides})

    def test_context_extraction_keeps_baseline_messages_and_sources_byte_identical(self):
        for case in BASELINE['context']:
            with self.subTest(case=case['name']):
                hits = [(Document(page_content=item['text'], metadata=item['metadata']), item['distance'])
                        for item in case['hits']]
                messages, sources = rag.build_messages('Which connector?', hits, case['budget'], case['language'])
                self.assertEqual(messages, case['messages'])
                self.assertEqual(sources, case['sources'])
                for document, _distance in rag.context_fragments(hits, case['budget']):
                    self.assertIn(document.page_content, messages[-1]['content'])

    def test_retrieval_extraction_preserves_baseline_order_metadata_and_trace(self):
        scorer = self.scorer
        scorer.model_id = 'fixture-order'
        for case in BASELINE['retrieval']:
            with self.subTest(options=case['options']):
                options = case['options']
                arguments = {key: options[key] for key in ('visual', 'files') if key in options}
                if options.get('reranked'):
                    arguments['reranker'] = scorer
                with patch('time.perf_counter', return_value=1.0):
                    hits, trace = retrieval_pipeline.retrieve(self.config, options['question'],
                        self.embeddings, options['k'], **arguments)
                self.assertEqual(trace, case['trace'])
                self.assertEqual(len(hits), len(case['hits']))
                for (document, distance), expected in zip(hits, case['hits']):
                    self.assertEqual(document.page_content, expected['text'])
                    self.assertEqual(document.metadata, expected['metadata'])
                    self.assertAlmostEqual(distance, expected['score'], places=6)

    def test_lab_matches_chat_path_including_scope_reranking_and_exact_context(self):
        row = lab.parse_dataset(self.content())[0]
        scorer = self.scorer
        hits, trace = retrieval_pipeline.retrieve(self.config, row['question'], self.embeddings, 3,
            files=frozenset(self.state['files']), reranker=scorer, include_ids=True)
        with patch('time.perf_counter', return_value=1.0):
            result = lab.evaluate_question(self.config, self.embeddings, row, k=3,
                reranker=scorer, files=frozenset(self.state['files']))
        self.assertEqual(result['retrieved_ids'], [doc.metadata['vector_id'] for doc, _ in hits])
        self.assertEqual([item['text'] for item in result['context']],
                         [doc.page_content for doc, _ in rag.context_fragments(hits, self.config.context_chars)])
        self.assertEqual(result['trace']['scope_aliases'], trace['scope_aliases'])
        self.assertTrue(result['trace']['reranked'])

    def test_a_correct_page_is_not_a_hit_if_evidence_was_truncated(self):
        self.store.upsert_vectors(['z-text'], ['A' * 240 + ' SECRET'],
            [{'file': 'PRO-Z790.pdf', 'source': 'PRO-Z790.pdf', 'page': 5, 'chunk': 2, 'item_type': 'text'}],
            [[1, 0, 0, 0]])
        row = lab.parse_dataset(self.content(question='PRO Z790 SECRET?', evidence=['SECRET']))[0]
        result = lab.evaluate_question(replace(self.config, context_chars=200), self.embeddings, row, k=1)
        self.assertEqual(result['metrics']['fragment_recall@1'], 1)
        self.assertFalse(result['evidence_found'])
        self.assertEqual(result['context_chars'], 200)
        self.assertTrue(lab.evaluate_question(self.config, self.embeddings, row, k=1)['evidence_found'])

    def test_absent_evidence_is_reported_apart_not_as_a_failure(self):
        annotated = lab.evaluate_question(self.config, self.embeddings, lab.parse_dataset(self.content())[0], k=4)
        without = self.content(id='q2')
        row = json.loads(without)
        del row['evidence']
        unannotated = lab.evaluate_question(self.config, self.embeddings, lab.parse_dataset(json.dumps(row))[0], k=4)
        summary = lab.summarize([annotated, unannotated], 2)
        self.assertEqual((summary['found'], summary['annotated'], summary['without_evidence']), (1, 1, 1))
        self.assertEqual(summary['evidence_recall'], 1)
        self.assertIsNone(lab.summarize([unannotated], 1)['evidence_recall'])

    def test_invalid_reference_fails_before_loading_embeddings(self):
        factory = Mock(side_effect=AssertionError('a model must not be loaded'))
        with self.assertRaises(lab.LabError) as caught:
            lab.evaluate_snapshot(self.config, self.content(relevant=[{'file': 'missing.pdf'}]), factory)
        self.assertEqual((caught.exception.code, caught.exception.line), ('missing_file', 1))
        factory.assert_not_called()
        with self.assertRaises(lab.LabError) as caught:
            lab.evaluate_snapshot(self.config, self.content(relevant=[{'file': 'PRO-Z790.pdf', 'page': 999}]), factory)
        self.assertEqual(caught.exception.code, 'missing_page')
        factory.assert_not_called()

    def test_line_validation_and_limits_are_explicit(self):
        for content, code, line in [('\n{broken}', 'json', 2),
                (self.content(relevant=[{'file': '../secret.txt'}]), 'reference', 1),
                (self.content(evidence=[]), 'evidence', 1),
                (self.content(question='List all indexed documents'), 'focused', 1),
                (self.content() + '\n' + self.content(), 'id', 2),
                (' ' * (lab.MAX_BYTES + 1), 'size', 0)]:
            with self.subTest(code=code):
                with self.assertRaises(lab.LabError) as caught:
                    lab.parse_dataset(content)
                self.assertEqual((caught.exception.code, caught.exception.line), (code, line))

    def test_cancellation_keeps_completed_results_and_never_calls_a_generator(self):
        observed = []
        with (patch.object(rag, 'generate_ollama', side_effect=AssertionError('no generation')),
              patch.object(rag, 'make_embeddings', side_effect=AssertionError('no downloads'))):
            report = lab.evaluate_snapshot(self.config, self.content() + '\n' + self.content(id='q2'),
                lambda: self.embeddings, cancelled=lambda: bool(observed),
                observer=lambda row, summary: observed.append(row['id']))
        self.assertEqual(report['status'], 'cancelled')
        self.assertEqual((report['summary']['completed'], report['summary']['total']), (1, 2))
        self.assertEqual(observed, ['q1'])
        self.assertFalse(report['generation_evaluated'])

    def test_no_writes_fts_or_unpublished_evidence(self):
        self.store.upsert_vectors(['orphan'], ['secret 192GB'],
            [{'file': 'PRO-Z790.pdf', 'source': 'PRO-Z790.pdf', 'page': 5, 'chunk': 9}], [[1, 0, 0, 0]])
        manifest = self.config.db / f'{self.config.collection}-manifest.json'
        def hashes():
            # WAL readers may create coordination sidecars; the database payload
            # and publication manifest must remain byte-identical.
            return {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.store.path, manifest)}
        before = hashes()
        report = lab.evaluate_snapshot(self.config, self.content(), lambda: self.embeddings)
        self.assertEqual(hashes(), before)
        self.assertNotIn('orphan', report['queries'][0]['retrieved_ids'])
        self.assertFalse(self.store.lexical_index_available())
        self.assertEqual(json.loads(manifest.read_text(encoding='utf-8')), self.state)

    def test_report_contains_explicit_code_and_corpus_fingerprints(self):
        report = lab.evaluate_snapshot(self.config, self.content(), lambda: self.embeddings)
        self.assertEqual(len(report['code']['sha256']), 64)
        self.assertEqual(set(report['code']['files']), set(lab.CODE_FILES))
        self.assertEqual(report['corpus']['published_chunks'], len(ROWS))
        self.assertEqual(report['status'], 'done')
        for row in report['queries']:
            self.assertTrue(all('source' not in hit for hit in row['context']))
