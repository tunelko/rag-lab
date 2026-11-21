"""Collection separation through real ASGI HTTP, files and library stores."""
from contextlib import closing
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import zlib

from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
from rag import Config, ingest
from web_app import RagService, create_app


class CollectionTests(unittest.TestCase):
    def test_existing_collection_keeps_its_documents_index_and_archived_citations(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            library = root / 'collections' / 'cti'
            documents = library / 'documents'
            documents.mkdir(parents=True)
            source = documents / 'example.md'
            source.write_text('An existing indexed document.', encoding='utf-8')
            registry = library.parent / 'registry.json'
            record = {'id': 'cti', 'name': 'CTI · APTs'}
            registry.write_text(json.dumps([{**record, 'adapter': 'cti'}]), encoding='utf-8')
            original_registry = registry.read_bytes()
            config = Config(db=root / 'chroma')
            legacy_config = replace(config, db=library / 'chroma', collection='cti')
            ingest(legacy_config, documents, DeterministicFakeEmbedding(size=32))
            manifest = legacy_config.db / 'cti-manifest.json'
            original_manifest = manifest.read_bytes()

            filename, text = 'archived.md', 'A versioned citation.'
            text_bytes = text.encode('utf-8')
            version = hashlib.sha256(filename.encode('utf-8') + b'\0' + text_bytes).hexdigest()
            cache = library / 'evidence-cache.sqlite3'
            with closing(sqlite3.connect(cache)) as connection, connection:
                connection.execute('CREATE TABLE evidence(version TEXT PRIMARY KEY, name TEXT, content BLOB)')
                connection.execute('INSERT INTO evidence VALUES(?,?,?)', (version, filename, zlib.compress(text_bytes)))
            citations = f'?record=archived&version={version}'

            service = RagService(config, root / 'documents')
            with TestClient(create_app(service), client=('127.0.0.1', 45000)) as client:
                scope = {'X-RAG-Collection': 'cti'}
                collections = client.get('/api/collections', headers=scope).json()
                self.assertEqual(collections['selected'], 'cti')
                self.assertIn(record, collections['collections'])
                status = client.get('/api/status', headers=scope).json()
                self.assertEqual(status['chunk_count'], 1)
                self.assertEqual([(row['name'], row['indexed']) for row in status['documents']], [('example.md', True)])
                self.assertEqual(client.get('/api/knowledge/source' + citations + '&collection=cti').text, text)
                preview = client.get('/api/knowledge/preview' + citations, headers=scope).json()
                self.assertEqual((preview['file'], preview['text']), (filename, text))
                self.assertEqual(client.get('/api/knowledge/source' + citations).status_code, 404)

                token = client.get('/api/session', headers=scope).json()['token']
                headers = {**scope, 'X-RAG-Token': token}
                self.assertEqual(client.post('/api/datasets/cti/sync', headers=headers, json={}).status_code, 404)
                self.assertEqual(client.post('/api/knowledge/query', headers=headers, json={}).status_code, 404)
                self.assertEqual(registry.read_bytes(), original_registry)
                self.assertEqual(manifest.read_bytes(), original_manifest)

                emptied = client.post('/api/admin/library/empty', headers=headers, json={'confirm': 'empty-library'})
                self.assertEqual(emptied.status_code, 200, emptied.text)
                self.assertTrue((Path(emptied.json()['backup']) / 'dataset' / cache.name).is_file())
                self.assertEqual(client.get('/api/knowledge/source' + citations, headers=scope).status_code, 404)

    def test_libraries_sessions_and_document_routes_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            service = RagService(Config(db=root / 'chroma'), root / 'documents')
            app = create_app(service)
            with TestClient(app, client=('127.0.0.1', 45000)) as client:
                default_token = client.get('/api/session').json()['token']
                default_headers = {'X-RAG-Token': default_token}
                record = {'id': 'references', 'name': 'References'}
                created = client.post('/api/collections', headers=default_headers, json=record)
                self.assertEqual(created.status_code, 201, created.text)
                self.assertEqual(created.json(), record)
                selected = {'X-RAG-Collection': 'references'}
                child_token = client.get('/api/session', headers=selected).json()['token']
                self.assertNotEqual(child_token, default_token)
                self.assertIs(app.state.collections.get('references').executor, service.executor)
                self.assertEqual(client.get('/api/collections', headers=selected).json()['selected'], 'references')
                for suffix, token, payload in [('', default_token, b'Original library'),
                                               ('?collection=references', child_token, b'Reference library')]:
                    result = client.post('/api/admin/upload' + suffix, headers={'X-RAG-Token': token},
                                         data={'auto_index': 'false'}, files={'files': ('same.md', payload, 'text/markdown')})
                    self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(client.get('/api/source?file=same.md').content, b'Original library')
                self.assertEqual(client.get('/api/source?file=same.md&collection=references').content, b'Reference library')
                self.assertEqual(client.get('/api/source?file=same.md', headers=selected).content, b'Reference library')
                self.assertEqual(client.get('/api/source?file=../../../documents/same.md&collection=references').status_code, 404)
                self.assertEqual(client.get('/api/session', headers={'X-RAG-Collection': '../escape'}).status_code, 400)
                self.assertEqual(client.get('/api/session', headers={'X-RAG-Collection': 'unknown'}).status_code, 404)
                self.assertEqual(client.post('/api/collections', headers=default_headers,
                                             json={'id': '../escape', 'name': 'bad'}).status_code, 422)
                self.assertEqual(client.post('/api/collections', headers=default_headers,
                                             json={'id': 'old-adapter', 'name': 'Legacy', 'adapter': 'cti'}).status_code, 422)
                self.assertEqual(client.get('/api/jobs?active=1&all_collections=1').json()['jobs'], [])
                emptied = client.post('/api/admin/library/empty', headers={**selected, 'X-RAG-Token': child_token},
                                      json={'confirm': 'empty-library'})
                self.assertEqual(emptied.status_code, 200, emptied.text)
                self.assertEqual(client.get('/api/source?file=same.md&collection=references').status_code, 404)
                self.assertEqual(client.get('/api/source?file=same.md').content, b'Original library')
