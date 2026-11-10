"""Library scope and inventory regression checks without model execution."""
import unittest
import json
from unittest.mock import patch
from types import SimpleNamespace

import numpy as np
import rag

from library_queries import best_evidence, catalog_answer, global_retrieve, query_intent, topic_query
from tests.unit import test_web


class ScopeTests(unittest.TestCase):
    def test_inventory_is_distinct_from_content_questions(self):
        for text in ('y cuantos tienes indexado ?', '¿Cuántos documentos hay indexados?', 'How many indexed documents do you have?'):
            self.assertEqual(query_intent(text).mode, 'catalog_count', text)
        self.assertEqual(query_intent('¿Qué documentos tenemos indexados hasta ahora?').mode, 'catalog_list')
        text = 'De todas los documentos indexados, enumera las CPU soportadas por cada una de las placas madre.'
        self.assertEqual(query_intent(text).mode, 'global')
        self.assertEqual(topic_query(text), 'cpu soportadas')
        self.assertEqual(query_intent('¿Cuántos procesadores admite esta placa?').mode, 'focused')
        for text in ('Dame la lista de cpus admitidas', 'Lista de procesadores compatibles', 'List supported CPUs'):
            self.assertEqual(query_intent(text).mode, 'global', text)
        self.assertEqual(topic_query('Dame la lista de cpus admitidas'), 'cpu admitidas')
        for text in ('Dame la lista de cpus admitidas por esta placa', 'Lista de CPU de la B250M', 'Lista de puertos del manual.pdf'):
            self.assertEqual(query_intent(text).mode, 'focused', text)

    def test_inventory_counts_all_files_and_excludes_pending(self):
        documents = [{'name': f'manual-{n}.pdf', 'indexed': True, 'chunks': 100} for n in range(17)]
        documents.append({'name': 'pending.pdf', 'indexed': False, 'chunks': 0})
        answer, metrics = catalog_answer(documents, 'catalog_list')
        self.assertIn('**17 documentos indexados**', answer)
        self.assertIn('**1.700 fragmentos**', answer)
        self.assertEqual(answer.count('/api/source?file='), 17)
        self.assertEqual(metrics['documents_indexed'], 17)

    def test_explicit_native_fields_outrank_generic_cpu_mentions(self):
        texts = ['CPU supports PCIe lanes. More details about the PCI slot.',
                 'Specifications\nCPU\nSupports generation 12 and 13 processors for socket LGA1700.',
                 '| CPU | Soporta la familia de procesadores indicada en la tabla. |']
        metadata = [{'item_type': 'text'}, {'item_type': 'text'}, {'item_type': 'page_image'}]
        self.assertEqual(best_evidence(texts, np.array([.99, .7, .9]), 'cpu soportadas', metadata), 1)
        japanese = ['CPU socket installation instructions', 'CPU\nIntel Core 14/13/12 サポート']
        self.assertEqual(best_evidence(japanese, np.array([.99, .7]), 'cpu soportadas', [{'item_type': 'text'}]*2), 1)
        self.assertEqual(best_evidence(['---', 'CPU\nSupports generation 12.'], np.array([.99,.7]), 'cpus admitidas'),1)
        with self.assertRaises(ValueError):
            best_evidence(['---','***'],np.array([.99,.7]),'cpus admitidas')
        self.assertEqual(best_evidence(['Antes de instalar el procesador conserve su cubierta.',
            'Procesador: Soporte para procesadores Intel Core de 14/13/12 generaciones.'],np.array([.99,.7]),'cpu admitidas'),1)

    def test_global_retrieval_covers_every_document_with_a_bounded_context(self):
        documents = [{'name':f'board-{n}.txt', 'indexed':True} for n in range(9)]
        def rows(where, **kwargs):
            name = where['file']
            return {'ids':['a','b'], 'documents':['CPU socket notes','CPU\nSupports generation 12.'],
                    'metadatas':[{'file':name,'source':name,'item_type':'text'}]*2,
                    'embeddings':np.array([[1.,0.],[.8,.2]])}
        store = SimpleNamespace(get=rows)
        embeddings = SimpleNamespace(embed_query=lambda question:[1.,0.])
        with patch.object(rag,'open_store',return_value=store):
            hits, metrics = global_retrieve(rag.Config(context_chars=3000), documents, embeddings,
                'De todos los documentos, enumera las CPU soportadas por cada placa.')
        self.assertEqual(len(hits),9)
        self.assertEqual(len({doc.metadata['file'] for doc,_ in hits}),9)
        self.assertTrue(all('Supports generation' in doc.page_content for doc,_ in hits))
        self.assertLessEqual(sum(len(doc.page_content) for doc,_ in hits),3000)
        self.assertTrue(metrics['coverage_complete'])

    def test_global_retrieval_refuses_silent_omissions(self):
        documents = [{'name':f'board-{n}.txt','indexed':True} for n in range(3)]
        embeddings = SimpleNamespace(embed_query=lambda question:[1.,0.])
        with self.assertRaisesRegex(ValueError, 'contexto configurado'):
            global_retrieve(rag.Config(context_chars=200),documents,embeddings,'Todos los documentos CPU')
        store = SimpleNamespace(get=lambda **kwargs:{'ids':[]})
        with patch.object(rag,'open_store',return_value=store), self.assertRaisesRegex(ValueError, 'no omitir'):
            global_retrieve(rag.Config(),documents,embeddings,'Todos los documentos CPU')


class CatalogApiTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    wait = test_web.WebTests.wait

    def test_empty_inventory_never_loads_embeddings_or_a_generator(self):
        service = self.client.app.state.service
        with patch.object(service, 'get_embeddings') as embeddings, patch('rag.generate_ollama') as generate:
            response = self.client.post('/api/query', headers=self.headers, json={'question': '¿Cuántos documentos hay indexados?'})
            self.assertEqual(response.status_code, 202, response.text)
            job = self.wait(response.json()['job_id'])
            self.assertIn('**0 documentos indexados**', job['answer'])
            embeddings.assert_not_called()
            generate.assert_not_called()

    def test_global_query_gives_the_generator_every_indexed_document(self):
        for number in range(6):
            self.client.post('/api/admin/upload',headers=self.headers,
                files=[('files',(f'board-{number}.txt',f'CPU\nSupports generation {number+1} processors.'.encode(),'text/plain'))],
                data={'auto_index':'false'})
        response=self.client.post('/api/admin/index',headers=self.headers)
        self.wait(response.json()['job_id'])
        def generate(messages,model,on_chunk=None,cancelled=None,context_length=None,max_output_tokens=None,response_format=None):
            self.assertEqual(context_length,8192)
            self.assertEqual(max_output_tokens,2048)
            self.assertEqual(sum(f'board-{n}.txt' in messages[-1]['content'] for n in range(6)),6)
            return json.dumps({key:'CPU soportadas en esta fuente.' for key in response_format['required']}), {'model':model}
        with patch('rag.generate_ollama',side_effect=generate):
            response=self.client.post('/api/query',headers=self.headers,json={
                'question':'Dame la lista de cpus admitidas','k':2})
            job=self.wait(response.json()['job_id'])
        self.assertEqual(len(job['sources']),6)
        self.assertEqual(job['metrics']['documents_retrieved'],6)
        self.assertTrue(job['metrics']['coverage_complete'])
        self.assertTrue(job['metrics']['answer_coverage_complete'])
        for number in range(1,7):
            self.assertIn(f'[{number}]',job['answer'])
