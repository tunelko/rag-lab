"""Topic persistence and retrieval boundaries with real vectors and no GPU/LLM."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from library_topics import TopicStore, TopicError
from tests.unit import test_web
import rag


class TopicStoreTests(unittest.TestCase):
    def test_hierarchy_persistence_rename_and_protected_deletion(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'topics.sqlite3'
            store=TopicStore(path)
            cabinet=store.create('Placas base')
            drawer=store.create('MSI',cabinet)
            with self.assertRaises(TopicError):store.create(' msi ',cabinet)
            with self.assertRaises(TopicError):store.create('Too deep',drawer)
            with self.assertRaises(TopicError):store.create('Bad\x00name')
            store.assign(['a.pdf'],drawer)
            with self.assertRaises(TopicError):store.remove(drawer)
            with self.assertRaises(TopicError):store.remove(cabinet)
            store.rename(cabinet,'Hardware')
            store=TopicStore(path)
            docs=[{'name':'a.pdf','topic_id':store.assignments()['a.pdf'],'indexed':True,'chunks':7},
                  {'name':'b.pdf','topic_id':None,'indexed':False,'chunks':0}]
            selected,label=store.scope(cabinet,docs)
            self.assertEqual(label,'Hardware')
            self.assertEqual([item['name'] for item in selected],['a.pdf'])
            self.assertEqual(store.snapshot(docs)['unassigned_count'],1)
            self.assertEqual(store.scope(drawer,docs)[1],'Hardware / MSI')
            store.assign(['a.pdf'],None);store.remove(drawer);store.remove(cabinet)
            self.assertEqual(store.assignments(),{})


class TopicWebTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    wait = test_web.WebTests.wait
    def topic(self,name,parent=None):
        result=self.client.post('/api/admin/topics',headers=self.headers,json={'name':name,'parent_id':parent})
        self.assertEqual(result.status_code,201,result.text)
        return result.json()['id']

    def upload(self,name,content,topic=None):
        response=self.client.post('/api/admin/upload',headers=self.headers,
            files=[('files',(name,content.encode(),'text/plain'))],data={'auto_index':'true',**({'topic_id':topic} if topic else {})})
        self.assertEqual(response.status_code,200,response.text)
        self.wait(response.json()['job_id'])

    def ask(self,question,topic):
        response=self.client.post('/api/query',headers=self.headers,json={'question':question,'topic_id':topic,'search_only':True})
        self.assertEqual(response.status_code,202,response.text)
        return self.wait(response.json()['job_id'])

    def test_scope_precedes_vector_search_and_covers_drawers_and_catalog(self):
        cabinet=self.topic('Hardware');drawer=self.topic('MSI',cabinet);farm=self.topic('Agricultura');empty=self.topic('Empty')
        self.upload('board.txt','CPU compatible Intel Core socket LGA1700.',drawer)
        self.upload('farm.txt','El cultivo requiere riego semanal.',farm)
        self.upload('loose.txt','Documento sin clasificar sobre redes.')
        with patch.object(rag,'generate_ollama') as generate:
            for scope in [cabinet,drawer]:
                reply=self.ask('cultivo riego CPU',scope)
                self.assertEqual([item['file'] for item in reply['sources']],['board.txt'])
                self.assertEqual(reply['metrics']['scope_documents'],1)
                inventory=self.ask('Lista de documentos indexados',scope)
                self.assertIn('board.txt',inventory['answer']);self.assertNotIn('farm.txt',inventory['answer'])
                global_reply=self.ask('Enumera el contenido de todos los documentos',scope)
                self.assertTrue(global_reply['metrics']['coverage_complete'])
                self.assertEqual([item['file'] for item in global_reply['sources']],['board.txt'])
            self.assertEqual([item['file'] for item in self.ask('riego',farm)['sources']],['farm.txt'])
            self.assertEqual([item['file'] for item in self.ask('redes','unassigned')['sources']],['loose.txt'])
            self.assertEqual(self.ask('riego',empty)['sources'],[])
            generate.assert_not_called()
        response=self.client.post('/api/query',headers=self.headers,json={'question':'test','topic_id':'missing'})
        self.assertEqual(response.status_code,404)

    def test_move_is_atomic_preserves_vectors_and_identical_upload_keeps_topic(self):
        cabinet=self.topic('Hardware');farm=self.topic('Agricultura');service=self.client.app.state.service
        self.upload('board.txt','CPU Intel socket LGA1700.',cabinet)
        before=rag.load_state(service.config)
        store=rag.open_store(service.config,None)
        vectors=store.get(include=['embeddings'])['embeddings'].copy()
        response=self.client.post('/api/admin/library/assign',headers=self.headers,json={'names':['board.txt','missing.txt'],'topic_id':farm})
        self.assertEqual(response.status_code,404)
        self.assertEqual(service.list_documents()[0]['topic_id'],cabinet)
        response=self.client.post('/api/admin/library/assign',headers=self.headers,json={'names':['board.txt'],'topic_id':farm})
        self.assertEqual(response.status_code,200)
        self.assertEqual(rag.load_state(service.config),before)
        self.assertTrue((store.get(include=['embeddings'])['embeddings']==vectors).all())
        self.upload('board.txt','CPU Intel socket LGA1700.',cabinet)
        self.assertEqual(service.list_documents()[0]['topic_id'],farm)
        self.assertEqual(rag.load_state(service.config),before)
        self.assertEqual(self.client.delete('/api/admin/topics/'+farm,headers=self.headers).status_code,409)
        self.assertEqual(self.client.post('/api/admin/topics',json={'name':'Forbidden'}).status_code,403)

    def test_queued_query_keeps_accepted_membership_after_organization_changes(self):
        cabinet=self.topic('Hardware');other=self.topic('Agricultura')
        self.upload('board.txt','CPU Intel socket LGA1700.',cabinet)
        self.upload('farm.txt','Riego del cultivo.',other)
        service=self.client.app.state.service
        with patch.object(service,'start',return_value={'job_id':'deferred'}) as start:
            result=self.client.post('/api/query',headers=self.headers,json={'question':'CPU','topic_id':cabinet,'search_only':True})
        self.assertEqual(result.status_code,202)
        callback=start.call_args.args[1]
        service.topics.assign(['farm.txt'],cabinet)
        with patch.object(service,'query',return_value={}) as query:
            callback('job')
        self.assertEqual(query.call_args.args[2],frozenset({'board.txt'}))
