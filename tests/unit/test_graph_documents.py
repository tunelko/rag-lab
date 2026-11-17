"""Actual file batches, durable SQLite checkpoints and HTTP status; no LLM stubs."""
from pathlib import Path
import tempfile
import unittest

from fastapi.testclient import TestClient

from graph_jobs import ExtractRequest, extraction_configuration, graph_store
from rag import Config
from web_app import RagService, create_app
from tests.unit.test_graph_engine import wait_job


class DocumentCheckpointTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        root = Path(self.folder.name)
        self.service = RagService(Config(db=root / 'index'), root / 'documents')
        self.client = TestClient(create_app(self.service), client=('127.0.0.1', 40000))
        self.client.__enter__()
        self.headers = {'X-RAG-Token': self.client.get('/api/session').json()['token']}

    def tearDown(self):
        self.client.__exit__(None, None, None)
        self.folder.cleanup()

    def prepare(self, names):
        response = self.client.post('/api/graph/prepare', headers=self.headers, json={'files': names})
        self.assertEqual(response.status_code, 202, response.text)
        return wait_job(self.client, response.json()['job_id'])

    def status(self, model='checkpoint-model', **parameters):
        response = self.client.get('/api/graph/documents', params={'model': model, **parameters})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def stage(self, file, *, model='checkpoint-model', assessed=False, publish=False, empty=False, quote=None, other_files=()):
        store = graph_store(self.service)
        unit = store.units({file})[0]
        config = extraction_configuration(ExtractRequest(files=[file, *other_files], provider='ollama', model=model))
        run = store.start_run(config, [unit['id'], *[row['id'] for row in store.units(set(other_files))]])
        payload = {'entities': [], 'assertions': []} if empty else {
            'entities': [{'key': 's', 'label': 'Board Alpha', 'kind': 'board'},
                         {'key': 'o', 'label': 'DDR4', 'kind': 'memory'}],
            'assertions': [{'subject': 's', 'predicate': 'supports', 'object': 'o',
                            'quote': quote or unit['text'], 'basis': 'explicit'}]}
        store.publish_extraction(run, unit['id'], payload, {'validated_schema': True})
        if assessed and not empty:
            store.assess_existing_batch(run, unit['id'], [{'index': 0, 'status': 'supported',
                'reason': 'The exact source sentence explicitly supports this relationship.', 'evidence_id': 'E1'}], {})
        if publish:
            store.complete(run)
        else:
            store.state(run, 'error', 'Interrupted after the durable checkpoint.')
        return store, unit, config, run

    def test_selection_is_paged_in_100_and_never_returns_an_oversized_next_batch(self):
        for index in range(205):
            (self.service.documents / f'doc-{index:03}.md').write_text('An actual document.', 'utf-8')
        result = self.status(offset=100)
        self.assertEqual(result['total_documents'], 205)
        self.assertEqual(result['max_batch_size'], 100)
        self.assertEqual(len(result['documents']), 100)
        self.assertEqual(result['documents'][0]['file'], 'doc-100.md')
        self.assertEqual(len(result['next_batch']), 100)
        self.assertEqual(len(set(result['next_batch'])), 100)
        self.assertEqual(len(self.status(offset=200)['documents']), 5)
        self.assertEqual(self.client.get('/api/graph/documents?limit=101').status_code, 422)
        response = self.client.post('/api/graph/documents/check', headers=self.headers,
            json={'files': [f'doc-{index:03}.md' for index in range(101)]})
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(self.service.embeddings)

    def test_only_confirmed_steps_are_checked_and_assessment_reanchors_the_saved_quote(self):
        file = 'board.md'
        text = 'This board supports memory.\nBoard Alpha supports DDR4 memory.'
        (self.service.documents / file).write_text(text, 'utf-8')
        self.assertEqual(self.prepare([file])['status'], 'done')
        store, unit, config, run = self.stage(file, quote='This board supports memory.')
        row = self.status()['documents'][0]
        self.assertTrue(row['checks']['preparation']['checked'])
        self.assertTrue(row['checks']['extraction']['checked'])
        self.assertFalse(row['checks']['assessment']['checked'])
        self.assertFalse(row['checks']['publication']['checked'])
        self.assertTrue(row['eligible'])
        self.assertEqual(row['resumable_runs'], [run])
        self.assertEqual(store.cached_units(config), set())
        with self.assertRaises(ValueError):
            store.complete(run)
        result = store.batch_for_assessment(run, unit['id'])
        result.assertions[0].quote = 'Board Alpha supports DDR4 memory.'
        result.assertions[0].source_start = text.index('Board Alpha')
        result.assertions[0].source_end = len(text)
        store.assess_existing_batch(run, unit['id'], [{'index': 0, 'status': 'supported',
            'reason': 'The second source sentence explicitly names the board and its memory.', 'evidence_id': 'E1'}], {}, extraction=result)
        store.complete(run)
        row = self.status()['documents'][0]
        self.assertTrue(row['complete'])
        self.assertFalse(row['eligible'])
        self.assertTrue(all(step['checked'] for step in row['checks'].values()))
        self.assertEqual(self.status()['next_batch'], [])
        claim = store.snapshot()['rows'][0]
        self.assertEqual(claim['quote'], text[claim['start']:claim['end']])
        self.assertEqual(claim['quote'], 'Board Alpha supports DDR4 memory.')

    def test_assessed_first_passage_does_not_mark_an_unfinished_document_verified(self):
        text = 'Board Alpha supports DDR4 memory.\n' + ('More source text. ' * 190)
        (self.service.documents / 'long.md').write_text(text, 'utf-8')
        self.prepare(['long.md'])
        self.stage('long.md', quote='Board Alpha supports DDR4 memory.', assessed=True, publish=True)
        row = self.status()['documents'][0]
        self.assertGreater(row['units_total'], 1)
        self.assertEqual(row['units_assessed'], 1)
        self.assertFalse(row['checks']['assessment']['checked'])
        self.assertEqual(row['checks']['assessment']['state'], 'partial')
        self.assertEqual(self.status()['selection_checks']['assessment']['done'], 0)

    def test_model_change_and_source_change_cannot_keep_unrelated_green_checks(self):
        file = 'board.md'
        (self.service.documents / file).write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        self.prepare([file])
        store, unit, first_config, _ = self.stage(file, assessed=True, publish=True)
        self.assertTrue(self.status()['documents'][0]['complete'])
        other = self.status(model='another-model')['documents'][0]
        self.assertTrue(other['checks']['preparation']['checked'])
        self.assertFalse(other['checks']['extraction']['checked'])
        self.assertTrue(other['eligible'])
        self.stage(file, model='another-model', assessed=True, publish=True)
        self.assertTrue(self.status()['documents'][0]['complete'])  # Historical matching verification is reusable.
        self.assertIn(unit['id'], store.cached_units(first_config))
        (self.service.documents / file).write_text('Board Alpha supports DDR5 memory.', 'utf-8')
        stale = self.status()['documents'][0]
        self.assertEqual(stale['status'], 'stale')
        self.assertTrue(stale['eligible'])
        self.assertFalse(any(step['checked'] for step in stale['checks'].values()))

    def test_active_documents_are_reserved_but_pausing_does_not_lock_them_forever(self):
        (self.service.documents / 'a.md').write_text('An actual document.', 'utf-8')
        (self.service.documents / 'b.md').write_text('Another actual document.', 'utf-8')
        store = graph_store(self.service)
        task = store.task_create({'files': ['a.md'], 'provider': 'ollama', 'model': 'checkpoint-model'})
        result = self.status()
        row = next(row for row in result['documents'] if row['file'] == 'a.md')
        self.assertTrue(row['in_use'])
        self.assertFalse(row['eligible'])
        self.assertNotIn('a.md', result['next_batch'])
        self.service.graph_continuation.pause(task)
        self.assertIn('a.md', self.status()['next_batch'])

    def test_no_claims_means_assessment_not_required_instead_of_a_fictitious_check(self):
        (self.service.documents / 'text.md').write_text('A heading without any relationship.', 'utf-8')
        self.prepare(['text.md'])
        self.stage('text.md', empty=True, publish=True)
        row = self.status()['documents'][0]
        self.assertTrue(row['complete'])
        self.assertEqual(row['checks']['assessment']['state'], 'not_required')
        self.assertFalse(row['checks']['assessment']['checked'])
        self.assertEqual(row['claims'], 0)
        self.assertFalse(self.status()['reconciliation_checked'])
        self.assertEqual(self.status()['reconciliation_state'], 'not_required')

    def test_preparation_failure_keeps_prior_files_and_reuses_their_confirmed_passages(self):
        (self.service.documents / 'good.md').write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        (self.service.documents / 'blank.md').write_text('   \n', 'utf-8')
        result = self.prepare(['good.md', 'blank.md'])
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['error_info']['code'], 'GRAPH_NO_TEXT')
        rows = {row['file']: row for row in self.status()['documents']}
        self.assertTrue(rows['good.md']['checks']['preparation']['checked'])
        self.assertFalse(rows['blank.md']['checks']['preparation']['checked'])
        again = self.prepare(['good.md'])
        self.assertEqual(again['status'], 'done')
        self.assertEqual(again['preparation']['reused_files'], 1)

    def test_process_resumes_a_verified_checkpoint_without_calling_an_unavailable_model(self):
        (self.service.documents / 'board.md').write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        self.prepare(['board.md'])
        store, _, _, run = self.stage('board.md', model='not-an-installed-model', assessed=True)
        response = self.client.post('/api/graph/process', headers=self.headers, json={
            'files': ['board.md'], 'provider': 'ollama', 'model': 'not-an-installed-model', 'max_units': 1})
        self.assertEqual(response.status_code, 202, response.text)
        result = wait_job(self.client, response.json()['job_id'])
        self.assertEqual(result['status'], 'done', result)
        self.assertEqual(result['graph_run'], run)
        self.assertEqual(store.run(run)['status'], 'done')
        self.assertIsNone(result.get('graph_generation'))
        check = self.client.post('/api/graph/documents/check', headers=self.headers,
            json={'files': ['board.md'], 'model': 'not-an-installed-model'}).json()
        self.assertTrue(all(step['checked'] for step in check['selection_checks'].values()))

    def test_checkpoint_adoption_charges_only_the_current_continuation_budget(self):
        (self.service.documents / 'board.md').write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        self.prepare(['board.md'])
        store, _, _, run = self.stage('board.md', assessed=True)
        task = store.task_create({'files': ['board.md'], 'provider': 'ollama', 'model': 'checkpoint-model'})
        store.complete(run, task=task)
        store.complete(run, task=task)
        self.assertEqual(store.tasks(task)['processed'], 1)

    def test_smaller_selection_replays_only_its_saved_unit_without_processing_other_files(self):
        for name in ('a.md', 'b.md'):
            (self.service.documents / name).write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        self.prepare(['a.md', 'b.md'])
        store, _, _, old_run = self.stage('a.md', model='not-an-installed-model', assessed=True, other_files=('b.md',))
        response = self.client.post('/api/graph/process', headers=self.headers, json={
            'files': ['a.md'], 'provider': 'ollama', 'model': 'not-an-installed-model', 'max_units': 1})
        self.assertEqual(response.status_code, 202, response.text)
        result = wait_job(self.client, response.json()['job_id'])
        self.assertEqual(result['status'], 'done', result)
        self.assertNotEqual(result['graph_run'], old_run)
        self.assertTrue(result['graph_checkpoint_reused'])
        self.assertIsNone(result.get('graph_generation'))
        self.assertEqual(store.run(old_run)['status'], 'error')
        rows = {row['file']: row for row in self.status(model='not-an-installed-model')['documents']}
        self.assertTrue(rows['a.md']['complete'])
        self.assertFalse(rows['b.md']['checks']['extraction']['checked'])

    def test_replay_of_an_unassessed_extraction_never_invents_a_semantic_check(self):
        (self.service.documents / 'a.md').write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        self.prepare(['a.md'])
        store, unit, config, _ = self.stage('a.md')
        new_run = store.start_run(config, [unit['id']])
        self.assertTrue(store.reuse_checkpoint(new_run, unit['id'], config))
        self.assertIsNotNone(store.batch_for_assessment(new_run, unit['id']))
        with self.assertRaises(ValueError):
            store.complete(new_run)
        self.assertEqual(store.snapshot()['assertions'], 0)

    def test_assessment_cannot_check_a_different_relationship_than_the_saved_proposal(self):
        (self.service.documents / 'a.md').write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        self.prepare(['a.md'])
        store, unit, _, run = self.stage('a.md')
        proposal = store.batch_for_assessment(run, unit['id'])
        proposal.assertions[0].predicate = 'does not support'
        with self.assertRaises(ValueError):
            store.assess_existing_batch(run, unit['id'], [{'index': 0, 'status': 'supported',
                'reason': 'This reason must never validate a different saved relationship.', 'evidence_id': 'E1'}], {}, extraction=proposal)
        proposal = store.batch_for_assessment(run, unit['id'])
        proposal.entities[0].label = 'A different board'
        with self.assertRaises(ValueError):
            store.assess_existing_batch(run, unit['id'], [{'index': 0, 'status': 'supported',
                'reason': 'This reason must not validate different entity labels either.', 'evidence_id': 'E1'}], {}, extraction=proposal)
        row = self.status()['documents'][0]
        self.assertFalse(row['checks']['assessment']['checked'])
        self.assertFalse(row['checks']['publication']['checked'])

    def test_continuation_groups_connections_without_repeating_saved_unit_work(self):
        from graph_continuation import ContinuousRequest
        import time

        for name in ('a.md', 'b.md'):
            (self.service.documents / name).write_text('Board Alpha supports DDR4 memory.', 'utf-8')
        self.prepare(['a.md', 'b.md'])
        self.stage('a.md', model='not-an-installed-model', assessed=True)
        self.stage('b.md', model='not-an-installed-model', assessed=True)
        # Availability metadata only; extraction and assessment are actual saved
        # checkpoints. No generated model response is stubbed or sent to Ollama.
        self.service.ollama_status = lambda: {'connected': False, 'loaded': []}
        body = ContinuousRequest(files=['a.md', 'b.md'], provider='ollama', model='not-an-installed-model',
                                 max_total_units=2, pause_seconds=60, reconcile_every_units=5)
        controller = self.service.graph_continuation
        task = controller.start(body)['task_id']
        try:
            for _ in range(200):
                jobs = [row for row in self.service.list_jobs()['jobs'] if row.get('graph_task') == task]
                if jobs and jobs[0]['status'] not in {'queued', 'running'}:
                    break
                time.sleep(.01)
            result = jobs[0]
            self.assertEqual(result['status'], 'done', result)
            self.assertTrue(result['reconciliation']['deferred'])
            self.assertFalse(result['reconciliation']['checked'])
            self.assertIsNone(result.get('graph_generation'))
            self.assertEqual(graph_store(self.service).tasks(task)['processed'], 1)
        finally:
            controller.pause(task)


if __name__ == '__main__':
    unittest.main()
