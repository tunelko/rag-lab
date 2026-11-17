"""Actual SQLite transactions, evidence versions and ASGI graph preparation."""
from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from graph_store import GraphStore, Extraction, VerificationRequired, identifier, literal_span
from web_app import RagService, create_app
from rag import Config


def unit(text='The board supports DDR4 memory.', version='v1'):
    return {'id': identifier('board.md', version), 'file': 'board.md', 'version': version,
            'page': 1, 'ordinal': 1, 'kind': 'text', 'text': text}


def extraction(quote='The board supports DDR4 memory.'):
    return {'entities': [{'key': 'b', 'label': 'Board', 'kind': 'component'},
                         {'key': 'm', 'label': 'DDR4', 'kind': 'memory'}],
            'assertions': [{'subject': 'b', 'predicate': 'supports', 'object': 'm',
                            'quote': quote, 'basis': 'explicit'}]}


class GraphTests(unittest.TestCase):
    def test_pdf_whitespace_keeps_original_words_and_offsets(self):
        source = 'CPU socket\n\tLGA 1151\nThis device does not support DDR5.'
        start, end = literal_span(source, 'CPU socket LGA 1151')
        self.assertEqual(source[start:end], 'CPU socket\n\tLGA 1151')
        with self.assertRaises(ValueError):
            literal_span(source, 'This device supports DDR5.')
        with self.assertRaises(ValueError):
            literal_span(source, 'CPU socket LGA 1200')

    def test_atomic_publication_and_literal_evidence(self):
        with tempfile.TemporaryDirectory() as folder:
            store = GraphStore(Path(folder) / 'graph.sqlite3')
            first = unit()
            store.publish_units([first], ['board.md'])
            run = store.start_run({'signature': 'test'}, [first['id']])
            with self.assertRaises(ValueError):
                store.publish_extraction(run, first['id'], extraction('Invented evidence is rejected.'), {})
            self.assertEqual(store.snapshot()['assertions'], 0)
            store.publish_extraction(run, first['id'], extraction(), {})
            self.assertEqual(store.snapshot()['assertions'], 0)  # Staged until the complete job commits.
            store.publish_extraction(run, first['id'], extraction(), {})  # Checkpoint replay is idempotent.
            store.complete(run)
            row = store.snapshot()['rows'][0]
            self.assertEqual(row['review'], 'proposed')
            with self.assertRaises(VerificationRequired):
                store.review(row['id'], 'accepted')
            store.verify(row['id'], 'supported', 'The passage explicitly states that this board supports DDR4 memory.')
            store.review(row['id'], 'accepted')
            self.assertEqual(store.snapshot()['rows'][0]['verification'], 'supported')
            store.verify(row['id'], 'uncertain', 'A later review cannot establish the required scope of this relationship.')
            self.assertEqual(store.snapshot()['rows'][0]['review'], 'proposed')
            store.review(row['id'], 'rejected')
            self.assertEqual(store.snapshot()['rows'][0]['review'], 'rejected')
            store.review(row['id'], 'proposed')
            self.assertEqual(store.snapshot()['rows'][0]['review'], 'proposed')
            self.assertEqual(store.evidence(row['unit'])['text'][row['start']:row['end']], row['quote'])
            self.assertEqual(store.cached_units({'signature': 'test'}), {first['id']})
            self.assertEqual(store.cached_units({'signature': 'different-model'}), set())
            second = unit('The board supports DDR5 memory.', 'v2')
            store.publish_units([second], ['board.md'])
            self.assertEqual(store.snapshot()['assertions'], 0)
            self.assertEqual(store.evidence(first['id'])['text'], first['text'])

    def test_recovery_and_backup_keep_confirmed_batches(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'graph.sqlite3'
            store = GraphStore(path)
            source = unit()
            store.publish_units([source], ['board.md'])
            run = store.start_run({'signature': 'test'}, [source['id']])
            store.state(run, 'running')
            store.publish_extraction(run, source['id'], extraction(), {})
            GraphStore(path).recover()
            self.assertEqual(store.run(run)['status'], 'interrupted')
            self.assertEqual(store.run(run)['batches'][0]['status'], 'done')
            store.complete(run)
            backup = Path(folder) / 'backup.sqlite3'
            with closing(sqlite3.connect(path)) as source_db, closing(sqlite3.connect(backup)) as backup_db:
                source_db.backup(backup_db)
            self.assertEqual(GraphStore(backup).snapshot()['assertions'], 1)

    def test_missing_entities_are_rejected_without_domain_restrictions(self):
        for kind in ('ingredient', 'contract-requirement', 'component', 'intrusion-set'):
            payload = extraction()
            payload['entities'][0]['kind'] = kind
            self.assertEqual(Extraction.model_validate(payload).entities[0].kind, kind)
        payload = extraction()
        payload['assertions'][0]['subject'] = 'unknown'
        with self.assertRaises(ValueError):
            Extraction.model_validate(payload)

    def test_real_prepare_http_and_collection_boundaries(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            service = RagService(Config(db=root / 'chroma'), root / 'documents')
            with TestClient(create_app(service), client=('127.0.0.1', 40000)) as client:
                headers = {'X-RAG-Token': client.get('/api/session').json()['token']}
                (service.documents / 'board.md').write_text(unit()['text'], 'utf-8')
                result = client.post('/api/graph/prepare', headers=headers, json={'files': ['../escape.md']})
                self.assertEqual(result.status_code, 422)
                result = client.post('/api/graph/prepare', headers=headers, json={'files': ['board.md']})
                self.assertEqual(result.status_code, 202, result.text)
                for _ in range(100):
                    job = client.get('/api/jobs/' + result.json()['job_id']).json()
                    if job['status'] in {'done', 'error'}:
                        break
                    time.sleep(.01)
                self.assertEqual(job['status'], 'done', job)
                self.assertEqual(client.get('/api/graph').json()['documents'], 1)
                self.assertFalse((service.config.db / 'documents-manifest.json').exists())
                data = client.post('/api/graph/extract', headers=headers,
                    json={'files': ['board.md'], 'provider': 'groq', 'model': 'model', 'remote_content': False})
                self.assertEqual(data.status_code, 422)
                self.assertEqual(client.get('/api/graph/evidence/not-an-id').status_code, 404)
                source_id = GraphStore(service.config.db.parent / 'graph.sqlite3').units()[0]['id']
                self.assertEqual(client.get('/api/graph/source?unit=' + source_id).text, unit()['text'])
                store = GraphStore(service.config.db.parent / 'graph.sqlite3')
                run = store.start_run({'signature': 'http-test'}, [source_id])
                store.publish_extraction(run, source_id, extraction(), {})
                store.complete(run)
                claim = store.snapshot()['rows'][0]['id']
                route = '/api/graph/assertions/' + claim
                self.assertEqual(client.post(route + '/review', headers=headers, json={'state': 'accepted'}).status_code, 422)
                verified = client.post(route + '/verification', headers=headers,
                    json={'status': 'supported', 'note': 'The actual source explicitly links the board to DDR4 memory.'})
                self.assertEqual(verified.status_code, 200, verified.text)
                self.assertEqual(client.post(route + '/review', headers=headers, json={'state': 'accepted'}).status_code, 200)
                (service.documents / 'board.md').write_text('A changed document version.', 'utf-8')
                self.assertEqual(client.post(route + '/review', headers=headers, json={'state': 'accepted'}).status_code, 422)
                created = client.post('/api/collections', headers=headers, json={'id': 'other', 'name': 'Other', 'adapter': None})
                self.assertEqual(created.status_code, 201)
                self.assertEqual(client.get('/api/graph?collection=other').json()['documents'], 0)
                self.assertEqual(client.get('/api/graph/source?unit=' + source_id + '&collection=other').status_code, 404)
                self.assertEqual(client.get('/grafo').status_code, 200)


if __name__ == '__main__':
    unittest.main()
