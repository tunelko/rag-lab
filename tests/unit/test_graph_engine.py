"""Real SQLite/ASGI regression checks. No model stubs, downloads or GPU calls."""
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import os
import threading
import tempfile
import time
import unittest

from fastapi.testclient import TestClient

from file_fingerprints import sha256_file
from graph_engine import augment, canonical_groups, current_rows, identity_candidates, mentions
from graph_jobs import generate_passage, graph_store
from graph_store import identifier
from rag import Config
from web_app import RagService, create_app


def publish(service, file, subject, target, *, polarity='positive', qualifiers=None, text=None):
    path = service.documents / file
    text = text or f'{subject} supports {target} memory.'
    path.write_text(text, 'utf-8')
    unit = {'id': identifier(file, text), 'file': file, 'version': sha256_file(path),
            'page': None, 'ordinal': 1, 'kind': 'text', 'text': text}
    store = graph_store(service)
    store.publish_units([unit], [file])
    run = store.start_run({'signature': 'source-fixture'}, [unit['id']])
    store.publish_extraction(run, unit['id'], {'entities': [
        {'key': 's', 'label': subject, 'kind': 'board'}, {'key': 'o', 'label': target, 'kind': 'memory'}],
        'assertions': [{'subject': 's', 'predicate': 'supports', 'object': 'o', 'quote': text,
                        'basis': 'explicit', 'polarity': polarity, 'qualifiers': qualifiers or {}}]}, {},
        assessments=[{'index': 0, 'status': 'supported', 'reason': 'The complete relationship is stated verbatim in this source.', 'evidence_id': 'E1'}])
    store.complete(run)
    return next(row for row in store.accepted_rows() if row['unit'] == unit['id'])


def link(service, left, right, *, decision='same', policy='test'):
    store = graph_store(service)
    store.publish_identities([{'left': left, 'right': right, 'decision': decision,
        'verification': 'supported', 'reason': 'Both source passages identify this exact memory standard.',
        'verification_reason': 'Both excerpts explicitly name the same concrete DDR4 memory standard.'}],
        {'signature': policy}, mentions(current_rows(service)))


def wait_job(client, job):
    for _ in range(200):
        state = client.get('/api/jobs/' + job).json()
        if state['status'] not in {'queued', 'running'}:
            return state
        time.sleep(.01)
    raise AssertionError('Job did not finish')


class GraphEngineTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.service = RagService(Config(db=root / 'vectors'), root / 'documents')
        self.client = TestClient(create_app(self.service), client=('127.0.0.1', 40000))
        self.client.__enter__()
        self.headers = {'X-RAG-Token': self.client.get('/api/session').json()['token']}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def test_equivalence_is_required_and_collection_topic_and_versions_are_preserved(self):
        a = publish(self.service, 'alpha.md', 'Board Alpha', 'DDR4')
        b = publish(self.service, 'beta.md', 'Board Beta', 'DDR4')
        question = 'What memory does Board Alpha share with another board?'
        hits, _ = augment(self.service, question, [], files={'alpha.md', 'beta.md'})
        self.assertEqual({doc.metadata['file'] for doc, _ in hits}, {'alpha.md'})
        link(self.service, a['object'], b['object'])
        hits, trace = augment(self.service, question, [], files={'alpha.md', 'beta.md'})
        self.assertEqual({doc.metadata['file'] for doc, _ in hits}, {'alpha.md', 'beta.md'})
        self.assertEqual(trace['graph_documents'], 2)
        self.assertTrue(all(len(path) <= 3 for path in trace['graph_paths']))
        scoped, _ = augment(self.service, question, [], files={'alpha.md'})
        self.assertEqual({doc.metadata['file'] for doc, _ in scoped}, {'alpha.md'})
        none, _ = augment(self.service, question, [], files=set())
        self.assertEqual(none, [])
        (self.service.documents / 'beta.md').write_text('A new version supports DDR5.', 'utf-8')
        fresh, _ = augment(self.service, question, [], files={'alpha.md', 'beta.md'})
        self.assertEqual({doc.metadata['file'] for doc, _ in fresh}, {'alpha.md'})

    def test_negative_identity_prevents_transitive_merge(self):
        entities = {key: {} for key in ('a', 'b', 'c')}
        rows = [{'left_entity': left, 'right_entity': right, 'decision': decision, 'verification': 'supported'}
                for left, right, decision in (('a', 'b', 'same'), ('b', 'c', 'same'), ('a', 'c', 'different'))]
        groups, conflicts = canonical_groups(entities, rows)
        self.assertNotEqual(groups['a'], groups['c'])
        self.assertEqual(conflicts, 1)
        groups, _ = canonical_groups(entities, rows + [{'left_entity': 'a', 'right_entity': 'b',
            'decision': 'uncertain', 'verification': 'uncertain'}])
        self.assertNotEqual(groups['a'], groups['b'])

    def test_different_aliases_with_a_source_identifier_are_candidates_but_not_automatically_merged(self):
        a = publish(self.service, 'a.md', 'Alpha', 'DDR4',
                    text='[Alpha](https://example.org/products/42) supports DDR4 memory.')
        b = publish(self.service, 'b.md', 'Beta', 'DDR4',
                    text='[Beta](https://example.org/products/42) supports DDR4 memory.')
        entities, pairs = identity_candidates(current_rows(self.service), [], 12)
        self.assertIn(tuple(sorted((a['subject'], b['subject']))), pairs)
        groups, _ = canonical_groups(entities, [])
        self.assertNotEqual(groups[a['subject']], groups[b['subject']])

    def test_identity_publication_rolls_back_unknown_and_withdrawn_entities(self):
        a = publish(self.service, 'alpha.md', 'Board Alpha', 'DDR4')
        b = publish(self.service, 'beta.md', 'Board Beta', 'DDR4')
        store = graph_store(self.service)
        with self.assertRaises(ValueError):
            link(self.service, a['object'], 'foreign-collection-id')
        self.assertEqual(store.identity_decisions(), [])
        old_entities = mentions(current_rows(self.service))
        store.publish_units([], ['beta.md'])
        with self.assertRaises(ValueError):
            store.publish_identities([{'left': a['object'], 'right': b['object'], 'decision': 'same',
                'verification': 'supported', 'reason': 'A complete source-based decision.',
                'verification_reason': 'A complete source-based verification.'}], {}, old_entities)
        self.assertEqual(store.identity_decisions(), [])

    def test_live_asgi_search_uses_graph_and_exposes_citations_without_loading_embeddings(self):
        a = publish(self.service, 'alpha.md', 'Board Alpha', 'DDR4')
        b = publish(self.service, 'beta.md', 'Board Beta', 'DDR4', polarity='negative', qualifiers={'condition': 'BIOS revision 2'})
        link(self.service, a['object'], b['object'])
        response = self.client.post('/api/query', headers=self.headers, json={
            'question': 'What memory does Board Alpha share with another board?', 'search_only': True})
        self.assertEqual(response.status_code, 202, response.text)
        result = wait_job(self.client, response.json()['job_id'])
        self.assertEqual(result['status'], 'done', (result, self.service.list_jobs()['recent']))
        self.assertEqual({row['file'] for row in result['sources']}, {'alpha.md', 'beta.md'})
        self.assertIsNone(self.service.embeddings)
        source = next(row for row in result['sources'] if row['file'] == 'beta.md')
        self.assertEqual(source['graph_relation']['polarity'], 'negative')
        self.assertEqual(source['graph_relation']['qualifiers']['condition'], 'BIOS revision 2')
        self.assertEqual(self.client.get(source['url']).status_code, 200)
        disabled = self.client.post('/api/query', headers=self.headers, json={
            'question': 'What does Board Alpha support?', 'search_only': True, 'graph_retrieval': False})
        self.assertEqual(disabled.status_code, 409)
        snapshot = self.client.get('/api/graph').json()
        self.assertEqual(snapshot['assessment_counts'], {'accepted': 2, 'rejected': 0, 'unverified': 0})
        self.assertEqual(snapshot['rows'][0]['passage_total'], 1)
        connections = self.client.get('/api/graph/connections').json()
        self.assertEqual(connections['canonical_entities'], 3)
        self.client.post('/api/collections', headers=self.headers, json={'id': 'other', 'name': 'Other'})
        self.assertEqual(self.client.get('/api/graph/connections?collection=other').json()['entities'], [])

    def test_forged_quote_and_rejected_claim_never_reach_chat(self):
        a = publish(self.service, 'alpha.md', 'Board Alpha', 'DDR4')
        store = graph_store(self.service)
        with store.connection() as db:
            db.execute('UPDATE graph_assertions SET quote=? WHERE id=?', ('An invented source sentence.', a['id']))
        self.assertEqual(current_rows(self.service), [])
        hits, _ = augment(self.service, 'What does Board Alpha support?', [], files={'alpha.md'})
        self.assertEqual(hits, [])
        store.review(a['id'], 'rejected')
        self.assertEqual(current_rows(self.service), [])

    def test_task_budget_recovery_and_no_credential_persistence(self):
        store = graph_store(self.service)
        config = {'files': ['blank.md'], 'provider': 'ollama', 'model': 'not-invoked', 'max_units': 1,
                  'max_total_units': 1, 'max_seconds': 10, 'pause_seconds': .1, 'max_rss_mb': 8192}
        task = store.task_create(config)
        with self.assertRaises(ValueError):
            store.task_create(config)
        store.task_update(task, processed=1)
        queued = self.service.start('graph', lambda job: self.service.graph_continuation.step(task, job))
        self.assertEqual(wait_job(self.client, queued['job_id'])['status'], 'done')
        self.assertEqual(store.tasks(task)['status'], 'paused_budget')
        self.assertEqual(self.client.post('/api/graph/continuous/' + task + '/resume', headers=self.headers).status_code, 409)
        other = store.task_create(config)
        store.recover()
        self.assertEqual(store.tasks(other)['status'], 'interrupted')
        self.assertNotIn('api_key', store.tasks(other)['config'])

    def test_continuation_waiting_between_units_stays_visible_and_blocks_library_reset(self):
        (self.service.documents / 'blank.md').write_text('An actual document.', 'utf-8')
        store = graph_store(self.service)
        task = store.task_create({'files': ['blank.md'], 'provider': 'ollama', 'model': 'not-invoked'})
        jobs = self.client.get('/api/jobs?active=1&all_collections=1').json()['jobs']
        self.assertTrue(any(job.get('graph_task') == task for job in jobs))
        blocked = self.client.post('/api/admin/library/empty', headers=self.headers, json={'confirm': 'empty-library'})
        self.assertEqual(blocked.status_code, 409)
        self.service.graph_continuation.pause(task)
        jobs = self.client.get('/api/jobs?active=1&all_collections=1').json()['jobs']
        self.assertFalse(any(job.get('graph_task') == task for job in jobs))

    def test_shared_queue_shutdown_closes_default_collection_producers_first(self):
        self.client.post('/api/collections', headers=self.headers, json={'id': 'other', 'name': 'Other'})
        self.client.get('/api/graph?collection=other')
        store = graph_store(self.service)
        task = store.task_create({'files': ['blank.md'], 'provider': 'ollama', 'model': 'not-invoked'})
        observed = []
        shutdown = self.service.executor.shutdown

        def observe_shutdown(*args, **kwargs):
            observed.append((self.service.closed, self.service.graph_continuation.closed))
            return shutdown(*args, **kwargs)

        self.service.executor.shutdown = observe_shutdown
        self.service.collection_pool.close()
        self.assertTrue(observed)
        self.assertTrue(all(closed and producer_closed for closed, producer_closed in observed))
        self.assertEqual(store.tasks(task)['status'], 'interrupted')

    def test_empty_selection_pauses_with_preserved_error_without_model_or_timer_leak(self):
        (self.service.documents / 'blank.md').write_text('   \n', 'utf-8')
        response = self.client.post('/api/graph/continuous', headers=self.headers, json={
            'files': ['blank.md'], 'provider': 'ollama', 'model': 'not-invoked', 'max_total_units': 1,
            'max_seconds': 10, 'pause_seconds': .1})
        self.assertEqual(response.status_code, 202, response.text)
        task = response.json()['task_id']
        for _ in range(100):
            result = self.client.get('/api/graph/continuous/' + task).json()
            if result['status'] not in {'queued', 'running'}:
                break
            time.sleep(.01)
        self.assertEqual(result['status'], 'paused_error', result)
        self.assertIn('texto', result['error'])
        self.assertEqual(result['units_total'], 0)
        self.assertNotIn(task, self.service.graph_continuation.timers)
        for job in self.service.list_jobs()['jobs']:
            if job.get('graph_task') == task:
                wait_job(self.client, job['id'])
        denied = self.client.post('/api/graph/continuous', headers=self.headers, json={
            'files': ['blank.md'], 'provider': 'groq', 'model': 'not-invoked', 'remote_content': False})
        self.assertEqual(denied.status_code, 422)

    def test_queued_cancellation_releases_the_continuation_for_resume(self):
        release = threading.Event()
        blocker = self.service.start('query', lambda _job: release.wait(5) and {})
        try:
            (self.service.documents / 'blank.md').write_text('   \n', 'utf-8')
            from graph_continuation import ContinuousRequest
            body = ContinuousRequest(files=['blank.md'], provider='ollama', model='not-invoked')
            controller = self.service.graph_continuation
            task = controller.start(body)['task_id']
            for _ in range(100):
                if task in controller.jobs:
                    break
                time.sleep(.01)
            queued = controller.jobs[task]
            self.service.cancel(queued)
            self.assertEqual(graph_store(self.service).tasks(task)['status'], 'paused')
            self.assertNotIn(task, controller.jobs)
            controller.resume(task)
            for _ in range(100):
                if task in controller.jobs:
                    break
                time.sleep(.01)
            controller.pause(task)
            self.assertEqual(graph_store(self.service).tasks(task)['status'], 'paused')
        finally:
            release.set()
            wait_job(self.client, blocker['job_id'])

    def test_real_http_model_unavailable_preserves_evidence_and_explains_the_failure(self):
        # This server implements only an HTTP failure, not a simulated model.
        class Unavailable(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                body = b'{"error":"CUDA initialization failed"}'
                self.send_response(500)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        publish(self.service, 'alpha.md', 'Board Alpha', 'DDR4')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Unavailable)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        previous = os.environ.get('RAG_OLLAMA_URL')
        try:
            worker.start()
            os.environ['RAG_OLLAMA_URL'] = f'http://127.0.0.1:{server.server_port}'
            result = self.service.start('graph', lambda job: generate_passage(self.service, job,
                [{'role': 'user', 'content': 'No model runs for this transport check.'}],
                {'provider': 'ollama', 'model': 'not-invoked'}, None, {}))
            state = wait_job(self.client, result['job_id'])
            self.assertEqual(state['status'], 'error')
            self.assertEqual(state['error_info']['code'], 'GRAPH_OLLAMA_UNAVAILABLE')
            self.assertIn('CUDA', state['error_info']['detail'])
            self.assertEqual(graph_store(self.service).snapshot()['assessment_counts']['accepted'], 1)
        finally:
            if previous is None:
                os.environ.pop('RAG_OLLAMA_URL', None)
            else:
                os.environ['RAG_OLLAMA_URL'] = previous
            server.shutdown()
            server.server_close()
            worker.join(timeout=1)

    def test_cancellation_during_real_http_is_not_reported_as_an_ollama_failure(self):
        service, ready, submitted = self.service, threading.Event(), {}

        class CancelHeartbeat(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', '0')))
                self.send_response(200)
                self.send_header('Content-Type', 'application/x-ndjson')
                self.send_header('Content-Length', '1')
                self.end_headers()
                if ready.wait(2):
                    service.cancel(submitted['job_id'])
                self.wfile.write(b'\n')  # Heartbeat only: no generated model content.

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(('127.0.0.1', 0), CancelHeartbeat)
        worker = threading.Thread(target=lambda: server.serve_forever(poll_interval=.01), daemon=True)
        previous = os.environ.get('RAG_OLLAMA_URL')
        try:
            worker.start()
            os.environ['RAG_OLLAMA_URL'] = f'http://127.0.0.1:{server.server_port}'
            submitted.update(service.start('graph', lambda job: generate_passage(service, job,
                [{'role': 'user', 'content': 'Transport cancellation check only.'}],
                {'provider': 'ollama', 'model': 'not-invoked'}, None, {'on_chunk': lambda _piece: None})))
            ready.set()
            state = wait_job(self.client, submitted['job_id'])
            self.assertEqual(state['status'], 'cancelled', state)
            self.assertNotIn('error_info', state)
        finally:
            ready.set()
            if previous is None:
                os.environ.pop('RAG_OLLAMA_URL', None)
            else:
                os.environ['RAG_OLLAMA_URL'] = previous
            server.shutdown()
            server.server_close()
            worker.join(timeout=1)


if __name__ == '__main__':
    unittest.main()
