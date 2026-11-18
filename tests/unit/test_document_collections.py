"""Collection separation through real ASGI HTTP, files and library stores."""
from pathlib import Path
import tempfile
import time
import unittest

from fastapi.testclient import TestClient
from rag import Config
from web_app import RagService, create_app
from document_evidence import EvidenceStore
from file_fingerprints import signature, sha256_file
from cti_sources import write_json


class CollectionTests(unittest.TestCase):
    def test_chat_lists_names_from_the_selected_category_without_a_model_or_vector_index(self):
        from knowledge_store import KnowledgeStore
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = RagService(Config(db=root / 'chroma'), root / 'documents')
            app = create_app(service)
            with TestClient(app, client=('127.0.0.1', 45000)) as client:
                token = client.get('/api/session').json()['token']
                created = client.post('/api/collections', headers={'X-RAG-Token': token},
                                      json={'id': 'cti', 'name': 'CTI', 'adapter': 'cti'})
                self.assertEqual(created.status_code, 201)
                child = app.state.collections.get('cti')
                child.settings.save(child.settings.get().model_copy(update={'response_provider': 'groq', 'external_model': ''}))
                topic = child.topics.create('groups')
                evidence = child.documents / 'cti'
                (evidence / 'groups').mkdir(parents=True)
                rows = []
                for n in range(10):
                    name = f'Group {n}'
                    path = f'groups/{n}.md'
                    (evidence / path).write_text('# ' + name, encoding='utf-8')
                    if n < 8:
                        child.topics.assign(['cti/' + path], topic)
                    rows.append(dict(id=str(n), canonical_id=str(n), type='intrusion-set', source='mitre',
                                     document=path, active=True, name=name, aliases=[]))
                receipt = {'status': 'done', 'evidence_root': str(evidence)}
                KnowledgeStore(child.config.db.parent / 'knowledge.sqlite3').publish(rows, receipt)
                write_json(child.config.db.parent / 'dataset-status.json', receipt)
                headers = {'X-RAG-Collection': 'cti'}
                headers['X-RAG-Token'] = client.get('/api/session', headers=headers).json()['token']
                response = client.post('/api/query', headers=headers, json={
                    'question': 'nombrs de grupos, hasta 100', 'topic_id': topic, 'k': 2})
                self.assertEqual(response.status_code, 202, response.text)
                url = '/api/jobs/' + response.json()['job_id']
                for _ in range(100):
                    job = client.get(url, headers=headers).json()
                    if job['status'] not in ('running', 'queued'):
                        break
                    time.sleep(0.05)
                self.assertEqual(job['status'], 'done', job.get('error'))
                self.assertEqual(job['metrics']['knowledge_total'], 8)
                self.assertEqual(job['metrics']['routing_mode'], 'catalog')
                self.assertEqual([s['text'] for s in job['sources']], [f'Group {n}' for n in range(8)])
                for source in job['sources']:
                    self.assertEqual(client.get(source['url']).text, '# ' + source['text'])

    def test_libraries_sessions_and_document_routes_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = RagService(Config(db=root / 'chroma'), root / 'documents')
            app = create_app(service)
            with TestClient(app, client=('127.0.0.1', 45000)) as client:
                default_token = client.get('/api/session').json()['token']
                default_headers = {'X-RAG-Token': default_token}
                created = client.post('/api/collections', headers=default_headers,
                                      json={'id': 'cti', 'name': 'CTI', 'adapter': 'cti'})
                self.assertEqual(created.status_code, 201, created.text)
                selected = {'X-RAG-Collection': 'cti'}
                child_token = client.get('/api/session', headers=selected).json()['token']
                self.assertNotEqual(child_token, default_token)
                self.assertIs(app.state.collections.get('cti').executor, service.executor)
                self.assertEqual(client.get('/api/collections', headers=selected).json()['selected'], 'cti')
                for headers, payload in [(default_headers, b'Original library'),
                                         ({**selected, 'X-RAG-Token': child_token}, b'CTI library')]:
                    result = client.post('/api/admin/upload', headers=headers,
                                         data={'auto_index': 'false'}, files={'files': ('same.md', payload, 'text/markdown')})
                    self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(client.get('/api/source?file=same.md').content, b'Original library')
                self.assertEqual(client.get('/api/source?file=same.md&collection=cti').content, b'CTI library')
                self.assertEqual(client.get('/api/source?file=same.md', headers=selected).content, b'CTI library')
                self.assertEqual(client.get('/api/source?file=../../../documents/same.md&collection=cti').status_code, 404)
                self.assertEqual(client.get('/api/session', headers={'X-RAG-Collection': '../escape'}).status_code, 400)
                self.assertEqual(client.get('/api/session', headers={'X-RAG-Collection': 'unknown'}).status_code, 404)
                self.assertEqual(client.post('/api/collections', headers=default_headers,
                                            json={'id': '../escape', 'name': 'bad'}).status_code, 422)
                self.assertEqual(client.get('/api/jobs?active=1&all_collections=1').json()['jobs'], [])
                child = app.state.collections.get('cti')
                source = child.documents / 'same.md'
                write_json(child.config.db.parent / 'managed-documents.json', {'same.md': {
                    'source': str(source.resolve()), 'sha256': sha256_file(source),
                    'bytes': source.stat().st_size, 'signature': list(signature(source.stat())),
                    'updated_at': '2026-01-01T00:00:00Z'}})
                self.assertEqual(child.list_documents()[0]['bytes'], len(b'CTI library'))
                source.write_text('Changed outside the dataset importer', 'utf-8')
                self.assertEqual(child.list_documents()[0]['bytes'], source.stat().st_size)
                cache = EvidenceStore(child.config.db.parent / 'evidence-cache.sqlite3')
                cache.put('source.md', 'A versioned source')
                emptied = client.post('/api/admin/library/empty', headers={**selected, 'X-RAG-Token': child_token},
                                      json={'confirm': 'empty-library'})
                self.assertEqual(emptied.status_code, 200, emptied.text)
                self.assertFalse(cache.path.exists())
                self.assertEqual(client.get('/api/source?file=same.md&collection=cti').status_code, 404)
                self.assertEqual(client.get('/api/source?file=same.md').content, b'Original library')
