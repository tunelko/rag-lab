import json
import unittest
from unittest.mock import patch

import rag
from jobs_runtime import JobFailure
from list_queries import extract_batch, list_request
from tests.unit import test_web


class EnumerationTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    wait = test_web.WebTests.wait

    def index(self, files):
        for name, body in files.items():
            r = self.client.post('/api/admin/upload', headers=self.headers,
                files={'files': (name, body.encode(), 'text/plain')}, data={'auto_index': 'false'})
            self.assertEqual(r.status_code, 200, r.text)
        self.wait(self.client.post('/api/admin/index', headers=self.headers).json()['job_id'])

    def ask(self, question, topic=None):
        response = self.client.post('/api/query', headers=self.headers, json={'question': question, 'k': 2, 'topic_id': topic})
        self.assertEqual(response.status_code, 202, response.text)
        return self.wait(response.json()['job_id'])

    def test_arbitrary_record_type_scope_order_duplicates_and_limit(self):
        files = {f'{n}.md': f'---\ntype: constellation\ntitle: {title}\n---\n# {title}'
                 for n,title in enumerate(['Orion', 'ORION', 'Ursa Minor', 'Andromeda', 'Cassiopeia', 'Lyra'])}
        self.index(files)
        service = self.client.app.state.service
        topic = service.topics.create('Ciel nocturne')
        service.topics.assign(sorted(files)[:-1], topic)
        with patch('rag.generate_ollama', side_effect=AssertionError('structured titles need no inference')):
            job = self.ask('List up to 3 constellations', topic)
        self.assertEqual(job['metrics']['distinct_items'], 4)
        self.assertEqual(job['metrics']['documents_scanned'], 5)
        self.assertEqual([s['file'] for s in job['sources']], ['3.md','4.md','0.md'])
        self.assertIn('Andromeda [1]', job['answer'])
        self.assertNotIn('Lyra', job['answer'])

    def test_content_scans_every_published_passage_before_limiting(self):
        files = {f'{n}.txt': f'The supplier is {name}.' for n,name in enumerate(['Zulu', 'Zulu', 'Yankee', 'Delta', 'Alpha'])}
        self.index(files)
        seen = set()
        def generate(messages, model, **options):
            query = json.loads(messages[-1]['content'])
            self.assertNotIn('up to 2', query['target'])
            output = {}
            for key, evidence in query['passages'].items():
                seen.add(evidence['file'])
                output[key] = [evidence['text'].split(' is ')[1][:-1]]
            return json.dumps(output), {'done_reason':'stop'}
        with patch.object(rag,'generate_ollama',side_effect=generate):
            job = self.ask('List up to 2 suppliers mentioned in the documents')
        self.assertEqual(seen, set(files))
        self.assertEqual(job['metrics']['distinct_items'], 4)
        self.assertEqual(job['metrics']['passages_scanned'], 5)
        self.assertIn('1. Alpha [1]', job['answer'])
        self.assertIn('2. Delta [2]', job['answer'])
        for source in job['sources']:
            self.assertEqual(self.client.get(source['url']).text, files[source['file']])

    def test_missing_passage_or_invented_value_cannot_be_published_as_complete(self):
        row = [('Company Acme.', {'file':'one.txt'})]
        for output in ({}, {'p1':['Other Company']}):
            with self.assertRaises(JobFailure):
                extract_batch(row, list_request('List companies'), [], lambda *args: (json.dumps(output), {}))

    def test_display_limit_is_separate_from_the_extraction_question(self):
        for question, subject, limit in [
            ('Muestra no aplicable', None, None),
            ('¿Cómo configuro un volumen de hasta 100 GB?', None, None),
            ('Dame las primeras 12 entradas', 'entradas', 12),
            ('List up to 100 catalog titles', 'titles', 100),
            ('De los documentos seleccionados, enumera las empresas', 'empresas', 100),
            ('Nombres de grupos, hasta 100', 'grupos', 100),
        ]:
            with self.subTest(question=question):
                request = list_request(question)
                self.assertEqual((request.subject, request.limit) if request else (None, None), (subject, limit))
