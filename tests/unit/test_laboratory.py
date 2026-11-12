"""Real lab jobs, SQLite snapshots and reload recovery; fake local models only."""
import os
os.environ['CUDA_VISIBLE_DEVICES'] = ''
os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['RAG_RERANKER'] = ''

import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
import rag
import retrieval_evaluation
import retrieval_pipeline
from web_app import RagService, create_app


class LocalService(RagService):
    def get_embeddings(self, download=False):
        if self.embeddings is None:
            self.embeddings = DeterministicFakeEmbedding(size=32)
        return self.embeddings

    def ollama_status(self):
        return {'connected': True, 'models': ['test:latest'], 'default_model': 'test:latest', 'loaded': []}


class LaboratoryTests(unittest.TestCase):
    def setUp(self):
        root = rag.ROOT / 'data' / 'test-chroma'
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='lab-api-', dir=root)
        self.root = Path(self.temporary.name)
        self.service = LocalService(rag.Config(db=self.root / 'db', collection='lab'), self.root / 'documents')
        self.client = TestClient(create_app(self.service), client=('127.0.0.1', 50000))
        self.client.__enter__()
        self.headers = {'X-RAG-Token': self.client.get('/api/session').json()['token']}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        from vector_store import VectorStore
        VectorStore.clear_cache()
        retrieval_pipeline.clear_catalogs()
        self.temporary.cleanup()

    def wait(self, identifier, expected='done'):
        for _ in range(500):
            job = self.client.get('/api/jobs/' + identifier).json()
            if job['status'] not in {'queued', 'running'}:
                self.assertEqual(job['status'], expected, job)
                return job
            time.sleep(.01)
        self.fail('Job did not finish')

    def upload(self, name='manual.txt', text='Backups run each Friday.'):
        response = self.client.post('/api/admin/upload', headers=self.headers,
            files=[('files', (name, text.encode(), 'text/plain'))], data={'auto_index': 'true'})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(response.json()['job_id'])

    def dataset(self, count=1, **overrides):
        return '\n'.join(json.dumps({'id': 'q' + str(i), 'question': 'When do backups run?',
            'relevant': [{'file': 'manual.txt'}], 'evidence': ['Friday'], **overrides}) for i in range(count))

    def start_run(self, content=None):
        response = self.client.post('/api/laboratory/runs', headers=self.headers,
            json={'name': 'My test', 'content': content or self.dataset()})
        self.assertEqual(response.status_code, 202, response.text)
        return response.json()

    def hashes(self):
        return {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in self.service.config.db.iterdir() if path.suffix in {'.sqlite3', '.json'}}

    def test_empty_state_and_validation_do_not_load_models(self):
        with patch.object(self.service, 'get_embeddings', side_effect=AssertionError('no model')) as load:
            self.assertEqual(self.client.get('/laboratorio').status_code, 200)
            meta = self.client.get('/api/laboratory').json()
            self.assertIsNone(meta['latest'])
            self.assertEqual(meta['corpus']['published_chunks'], 0)
            for content, code, line in [('\n{broken}', 'json', 2), (self.dataset(), 'no_index', 0)]:
                result = self.client.post('/api/laboratory/validate', headers=self.headers, json={'content': content})
                self.assertEqual(result.status_code, 422, result.text)
                self.assertEqual((result.json()['lab_error']['code'], result.json()['lab_error']['line']), (code, line))
            load.assert_not_called()

    def test_run_uses_snapshot_and_persists_exact_context_without_generating(self):
        self.upload()
        before = self.hashes()
        with (patch.object(rag, 'generate_ollama', side_effect=AssertionError('no generation')),
              patch('generation_providers.generate', side_effect=AssertionError('no cloud')),
              patch.object(self.service.credentials, 'get', side_effect=AssertionError('no credentials'))):
            result = self.start_run()
            self.wait(result['job_id'])
        run = self.client.get('/api/laboratory/runs/' + result['run_id']).json()
        self.assertEqual(run['report']['summary']['found'], 1)
        self.assertEqual(run['report']['summary']['annotated'], 1)
        self.assertEqual(run['report']['queries'][0]['context'][0]['text'], 'Backups run each Friday.')
        self.assertEqual(before, self.hashes())
        self.assertEqual(list(self.service.laboratory.snapshots.iterdir()), [])
        self.assertTrue(run['current_corpus'])
        self.assertFalse(run['report']['generation_evaluated'])
        self.assertNotIn('request', run)
        self.assertEqual(self.client.get('/api/laboratory').json()['latest']['id'], result['run_id'])

    def test_invalid_references_and_oversized_utf8_fail_before_models(self):
        self.upload()
        with patch.object(self.service, 'get_embeddings', side_effect=AssertionError('no model')) as load:
            for content, code in [
                (self.dataset(relevant=[{'file': 'absent.pdf'}]), 'missing_file'),
                (self.dataset(relevant=[{'file': 'manual.txt', 'page': 999}]), 'missing_page'),
                (self.dataset(question='Bad Unicode \ud800'), 'utf8'),
                ('é' * (retrieval_evaluation.MAX_BYTES // 2 + 1), 'size')]:
                response = self.client.post('/api/laboratory/runs', headers=self.headers, json={'content': content})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()['lab_error']['code'], code)
            load.assert_not_called()
        self.assertIsNone(self.service.laboratory.latest())

    def test_missing_required_fields_report_the_line_and_field_before_models(self):
        self.upload()
        with patch.object(self.service, 'get_embeddings', side_effect=AssertionError('no model')) as load:
            for field in ('id', 'question', 'relevant'):
                row = json.loads(self.dataset())
                row[field + 'e'] = row.pop(field)
                for endpoint in ('validate', 'runs'):
                    with self.subTest(field=field, endpoint=endpoint):
                        response = self.client.post('/api/laboratory/' + endpoint, headers=self.headers,
                            json={'content': '\n' + json.dumps(row)})
                        self.assertEqual(response.status_code, 422, response.text)
                        error = response.json()
                        self.assertEqual(error['lab_error'], {'code': 'missing_field', 'line': 2, 'field': field})
                        self.assertIn(field, error['detail'])
                        self.assertIn('2', error['detail'])
            load.assert_not_called()
        self.assertIsNone(self.service.laboratory.latest())

    def test_validation_releases_the_queue_lock_and_rechecks_before_submission(self):
        self.upload()
        original = self.service.validate_evaluation
        state = rag.load_state(self.service.config)
        for condition in ('changed', 'busy'):
            with self.subTest(condition=condition):
                entered, release = threading.Event(), threading.Event()
                validated, finish_validation = threading.Event(), threading.Event()
                hold = self.service.start('prepare', lambda job: (entered.set(), release.wait(10)) and {})
                self.assertTrue(entered.wait(2))
                index = None
                def validate(content):
                    result = original(content)
                    validated.set()
                    finish_validation.wait(10)
                    return result
                try:
                    with (patch.object(self.service, 'validate_evaluation', side_effect=validate),
                          patch.object(self.service, 'get_embeddings', side_effect=AssertionError('no model')) as load,
                          ThreadPoolExecutor(max_workers=2) as pool):
                        try:
                            request = pool.submit(self.client.post, '/api/laboratory/runs', headers=self.headers,
                                json={'content': self.dataset()})
                            self.assertTrue(validated.wait(2))
                            # A streaming worker can still publish progress while validation hashes documents.
                            progress = pool.submit(self.service.update, hold['job_id'], answer='Still streaming')
                            progress.result(timeout=2)
                            self.assertEqual(self.service.snapshot(hold['job_id'])['answer'], 'Still streaming')
                            if condition == 'changed':
                                changed = json.loads(json.dumps(state))
                                next(iter(changed['files'].values()))['sha256'] = 'different'
                                rag.save_state(self.service.config, changed)
                            else:
                                index = self.service.start('index', lambda job: {})
                            finish_validation.set()
                            response = request.result(timeout=3)
                            self.assertEqual(response.status_code, 422 if condition == 'changed' else 409, response.text)
                            self.assertEqual(response.json()['lab_error']['code'], condition)
                            self.assertIsNone(self.service.laboratory.latest())
                            load.assert_not_called()
                        finally:
                            finish_validation.set()
                finally:
                    release.set()
                    self.wait(hold['job_id'])
                    if index:
                        self.wait(index['job_id'])
                    rag.save_state(self.service.config, state)

    def test_lab_and_chat_capture_the_same_values_before_queue_settings_change(self):
        self.upload()
        self.service.config.context_chars = 700
        self.service.settings.save(self.service.settings.get().model_copy(update={'k': 6}))
        entered, release = threading.Event(), threading.Event()
        self.service.start('prepare', lambda job: (entered.set(), release.wait(5)) and {})
        self.assertTrue(entered.wait(2))
        def scorer(query, texts):
            return [1.0] * len(texts)
        scorer.model_id, scorer.device = 'fake/old', 'cpu'
        try:
            with (patch.dict(os.environ, {'RAG_RERANKER': 'fake/old', 'RAG_RERANK_DEVICE': 'cpu',
                    'RAG_RERANK_HALF': '0', 'RAG_RERANK_WINDOW': '7'}),
                  patch.object(retrieval_pipeline, 'cached_reranker', return_value=scorer) as cached,
                  patch.object(rag, 'build_messages', wraps=rag.build_messages) as messages):
                chat = self.client.post('/api/query', headers=self.headers,
                    json={'question': 'When do backups run?', 'search_only': True}).json()
                run = self.start_run()
                self.service.settings.save(self.service.settings.get().model_copy(update={'k': 2}))
                self.service.config.context_chars = 900
                os.environ.update(RAG_RERANKER='fake/new', RAG_RERANK_WINDOW='99')
                release.set()
                self.wait(chat['job_id']); self.wait(run['job_id'])
                self.assertEqual(messages.call_args.args[2], 700)
                self.assertEqual(cached.call_count, 2)
                self.assertTrue(all(call.args == ('fake/old', 'cpu', False) for call in cached.call_args_list))
                configuration = self.service.laboratory.get(run['run_id'])['report']['configuration']
                self.assertEqual((configuration['k'], configuration['context_chars'], configuration['rerank_window']), (6, 700, 7))
        finally:
            release.set()

    def test_cancel_preserves_partial_results_frees_queue_and_allows_emptying(self):
        self.upload()
        entered, release = threading.Event(), threading.Event()
        original = retrieval_evaluation.evaluate_question
        def hold_second(config, embeddings, row, **kwargs):
            if row['id'] == 'q1':
                entered.set(); release.wait(5)
            return original(config, embeddings, row, **kwargs)
        try:
            with patch.object(retrieval_evaluation, 'evaluate_question', side_effect=hold_second):
                result = self.start_run(self.dataset(3))
                self.assertTrue(entered.wait(2))
                partial = self.client.get('/api/laboratory').json()['latest']
                self.assertEqual(partial['report']['summary']['completed'], 1)
                refused = self.client.post('/api/admin/library/empty', headers=self.headers, json={'confirm': 'empty-library'})
                self.assertEqual(refused.status_code, 409)
                self.assertIn('Laboratorio', refused.json()['detail'])
                self.client.delete('/api/jobs/' + result['job_id'], headers=self.headers)
                release.set(); self.wait(result['job_id'], 'cancelled')
            persisted = self.client.get('/api/laboratory').json()['latest']
            self.assertEqual(persisted['status'], 'cancelled')
            self.assertEqual(persisted['report']['summary']['completed'], 2)
            self.assertEqual(list(self.service.laboratory.snapshots.iterdir()), [])
            emptied = self.client.post('/api/admin/library/empty', headers=self.headers, json={'confirm': 'empty-library'})
            self.assertEqual(emptied.status_code, 200, emptied.text)
            historical = self.client.get('/api/laboratory').json()['latest']
            self.assertFalse(historical['current_corpus'])
            self.assertEqual(historical['report']['summary']['completed'], 2)
        finally:
            release.set()

    def test_ingestion_blocks_experiments_and_queued_cancellation_creates_no_snapshot(self):
        self.upload()
        entered, release = threading.Event(), threading.Event()
        hold = self.service.start('index', lambda job: (entered.set(), release.wait(5)) and {})
        self.assertTrue(entered.wait(2))
        try:
            response = self.client.post('/api/laboratory/runs', headers=self.headers, json={'content': self.dataset()})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()['lab_error']['code'], 'busy')
            self.assertIsNone(self.service.laboratory.latest())
        finally:
            release.set(); self.wait(hold['job_id'])
        entered.clear(); release.clear()
        blocked = self.service.start('prepare', lambda job: (entered.set(), release.wait(5)) and {})
        self.assertTrue(entered.wait(2))
        try:
            result = self.start_run()
            self.client.delete('/api/jobs/' + result['job_id'], headers=self.headers)
            self.assertEqual(self.client.get('/api/laboratory').json()['latest']['status'], 'cancelled')
            self.assertFalse(self.service.laboratory.snapshots.exists())
        finally:
            release.set(); self.wait(blocked['job_id'])

    def test_stale_sources_are_excluded_like_the_chat_and_links_become_historical(self):
        self.upload()
        self.upload('old.txt', 'Old Friday schedule.')
        (self.service.documents / 'old.txt').write_text('Changed and not reindexed.', encoding='utf-8')
        result = self.start_run()
        self.wait(result['job_id'])
        report = self.service.laboratory.get(result['run_id'])['report']
        self.assertEqual(report['scope_files'], ['manual.txt'])
        self.assertTrue(all(hit['file'] == 'manual.txt' for row in report['queries'] for hit in row['context']))
        (self.service.documents / 'manual.txt').write_text('Changed after measurement.', encoding='utf-8')
        self.assertFalse(self.client.get('/api/laboratory').json()['latest']['current_corpus'])

    def test_recovery_removes_owned_snapshots_but_retains_partial_results(self):
        self.upload()
        result = self.start_run(); self.wait(result['job_id'])
        folder = self.service.laboratory.snapshots / 'run-orphan'
        folder.mkdir(); (folder / 'private.txt').write_text('temporary corpus', encoding='utf-8')
        self.service.laboratory.observe({'kind': 'evaluation', 'lab_run_id': result['run_id'], 'id': 'interrupted', 'status': 'running'})
        self.service.laboratory.recover()
        restored = self.service.laboratory.get(result['run_id'])
        self.assertEqual(restored['status'], 'interrupted')
        self.assertEqual(restored['report']['summary']['completed'], 1)
        self.assertFalse(folder.exists())

    def test_missing_provenance_file_is_historical_instead_of_a_server_error(self):
        self.upload()
        result = self.start_run(); self.wait(result['job_id'])
        report = self.service.laboratory.get(result['run_id'])['report']
        del report['corpus']['files']['manual.txt']
        self.service.laboratory.save_report(result['run_id'], report)
        response = self.client.get('/api/laboratory/runs/' + result['run_id'])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertFalse(response.json()['current_corpus'])
        self.assertEqual(response.json()['report']['summary']['found'], 1)
        self.assertFalse(self.client.get('/api/laboratory').json()['latest']['current_corpus'])

    def test_deferred_snapshot_cleanup_preserves_metrics_and_retries_at_startup(self):
        self.upload()
        # Simulate TemporaryDirectory ignoring a retained Windows handle: the owned copy remains.
        with patch('laboratory_store.tempfile.TemporaryDirectory._rmtree'):
            result = self.start_run(); self.wait(result['job_id'])
        store = self.service.laboratory
        pending = self.client.get('/api/laboratory').json()
        self.assertEqual(pending['latest']['status'], 'done')
        self.assertTrue(pending['cleanup_pending'])
        self.assertTrue(pending['latest']['report']['cleanup_pending'])
        summary = pending['latest']['report']['summary']
        self.assertEqual(summary['found'], 1)
        self.assertEqual(len(list(store.snapshots.iterdir())), 1)
        store.cleanup_marker.unlink()  # Recovery also notices an orphan left before its warning was written.
        with patch('laboratory_store.shutil.rmtree', side_effect=PermissionError('retained test handle')):
            store.recover()
        self.assertTrue(store.cleanup_marker.is_file())
        self.assertEqual(store.get(result['run_id'])['report']['summary'], summary)
        store.recover()
        self.assertFalse(store.cleanup_marker.exists())
        self.assertEqual(list(store.snapshots.iterdir()), [])
        restored = self.client.get('/api/laboratory').json()
        self.assertFalse(restored['cleanup_pending'])
        self.assertFalse(restored['latest']['report']['cleanup_pending'])
        self.assertEqual(restored['latest']['report']['summary'], summary)

    def test_csrf_guard_and_rejection_of_experiment_parameter_overrides(self):
        self.assertEqual(self.client.post('/api/laboratory/validate', json={'content': self.dataset()}).status_code, 403)
        self.assertEqual(self.client.post('/api/laboratory/runs', headers=self.headers,
            json={'content': self.dataset(), 'k': 8}).status_code, 422)

    def test_snapshot_includes_committed_wal_and_cleans_after_model_failure(self):
        self.upload()
        store = rag.open_store(self.service.config, None)
        identifier = rag.published_ids(rag.load_state(self.service.config))[0]
        with store.connect(write=True) as held:
            held.execute('UPDATE vectors SET text=? WHERE id=?', ('Backups run Friday. WAL MARKER', identifier))
            held.execute("UPDATE info SET value=CAST(value AS INTEGER)+1 WHERE key='generation'")
            held.commit()
            before = self.hashes()
            result = self.start_run(); self.wait(result['job_id'])
            text = self.service.laboratory.get(result['run_id'])['report']['queries'][0]['context'][0]['text']
            self.assertIn('WAL MARKER', text)
            self.assertEqual(before, self.hashes())
        with patch.object(self.service, 'get_embeddings', side_effect=OSError('not cached')):
            failed = self.start_run(); self.wait(failed['job_id'], 'error')
        self.assertEqual(self.service.laboratory.get(failed['run_id'])['error']['lab_error']['code'], 'embeddings')
        self.assertEqual(list(self.service.laboratory.snapshots.iterdir()), [])

    def test_a_queued_run_refuses_a_different_published_manifest(self):
        self.upload()
        entered, release = threading.Event(), threading.Event()
        block = self.service.start('prepare', lambda job: (entered.set(), release.wait(5)) and {})
        self.assertTrue(entered.wait(2))
        try:
            result = self.start_run()
            state = rag.load_state(self.service.config)
            state['files'][str((self.service.documents / 'manual.txt').resolve())]['sha256'] = 'different'
            rag.save_state(self.service.config, state)
            with patch.object(self.service, 'get_embeddings', side_effect=AssertionError('no model')) as load:
                release.set(); self.wait(block['job_id']); self.wait(result['job_id'], 'error')
                load.assert_not_called()
            self.assertEqual(self.service.laboratory.get(result['run_id'])['error']['lab_error']['code'], 'changed')
            self.assertFalse(self.service.laboratory.snapshots.exists())
        finally:
            release.set()
