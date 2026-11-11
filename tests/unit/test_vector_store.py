"""Storage/migration contract tests: known vectors, no model downloads."""
from dataclasses import replace
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
from langchain_core.documents import Document
import rag
from tools.migrate_vectors import migrate
from vector_store import VectorStore


class VectorStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.root=Path(self.temp.name)
        self.store=VectorStore(self.root/'vectors.sqlite3')
        self.store.upsert_vectors(['a','b','c'],['A','B','C'],
            [{'file':'cpu.pdf','page':1},{'file':'farm.pdf','page':2},{'file':'cpu.pdf','page':3}],
            [[1,0],[0,1],[.8,.6]])

    def tearDown(self):self.temp.cleanup()

    def test_known_cosine_ranking_and_filters_apply_before_top_k(self):
        class Embeddings:
            def embed_query(self,text):return [0,1]
        self.store.embeddings=Embeddings()
        hits=self.store.similarity_search_with_score('query',1,filter={'file':{'$in':['cpu.pdf']}})
        self.assertEqual(hits[0][0].page_content,'C');self.assertAlmostEqual(hits[0][1],.4,places=5)
        hits=self.store.similarity_search_with_score('query',1,ids=['a','c'])
        self.assertEqual(hits[0][0].page_content,'C')
        self.assertEqual(self.store.similarity_search_with_score('query',1,ids=[]),[])

    def test_large_published_id_filters_and_literal_file_names(self):
        ids=['absent-'+str(i) for i in range(40000)]+['a']
        self.assertEqual(self.store.get(ids=ids)['ids'],['a'])
        self.assertEqual(self.store.get(where={'file':"cpu.pdf' OR 1=1 --"})['ids'],[])

    def test_invalid_vector_batch_is_atomic_and_existing_ids_are_upserted(self):
        for vectors in ([[1,0,0]],[[float('nan'),1]],[[0,0]]):
            with self.assertRaises(ValueError):self.store.upsert_vectors(['a'],['bad'],[{}],vectors)
        self.assertEqual(self.store.get(ids=['a'])['documents'],['A'])
        self.store.upsert_vectors(['a'],['updated'],[{'file':'new.txt'}],[[0,1]])
        reopened=VectorStore(self.store.path)
        self.assertEqual(reopened.get(ids=['a'])['documents'],['updated'])
        self.assertEqual(len(reopened.get()['ids']),3)

    def test_search_cache_is_reused_and_invalidated_by_committed_writes(self):
        class Embeddings:
            def embed_query(self,text):return [1,0]
        self.store.embeddings=Embeddings()
        self.store.similarity_search_with_score('first',2)
        first=self.store.cache_info()
        self.assertEqual(first['loads'],1);self.assertEqual(first['rows'],3)
        reopened=VectorStore(self.store.path,Embeddings())
        reopened.similarity_search_with_score('second',2)
        second=reopened.cache_info()
        self.assertEqual(second['loads'],1);self.assertGreaterEqual(second['hits'],1)
        reopened.upsert_vectors(['d'],['D'],[{'file':'new.pdf','page':4}],[[1,0]])
        hits=self.store.similarity_search_with_score('after write',4)
        self.assertIn('D',[document.page_content for document,_ in hits])
        self.assertEqual(self.store.cache_info()['loads'],2)

    def test_fts_is_transactionally_kept_in_sync_and_hybrid_respects_scope(self):
        class Embeddings:
            def embed_query(self,text):return [0,1]
        self.store.embeddings=Embeddings()
        self.store.upsert_vectors(['pin'],['ATX_12V EPS power connector pinout'],
            [{'file':'board.pdf','page':12}],[[1,0]])
        with self.assertRaisesRegex(RuntimeError,'prepare_lexical_index'):
            self.store.lexical_search('ATX_12V',3)
        self.assertEqual(self.store.dense_search('ATX_12V',1)[0]['id'],'b')
        self.store.ensure_lexical_index()
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT COUNT(*) FROM fts_rows').fetchone()[0],4)
            plan=' '.join(row[3] for row in db.execute(
                "EXPLAIN QUERY PLAN SELECT docid FROM fts_rows WHERE id='pin'"))
            self.assertIn('INDEX',plan.upper())
        lexical=self.store.lexical_search('ATX_12V pinout',3)
        self.assertEqual(lexical[0]['id'],'pin')
        hybrid=self.store.hybrid_search('ATX_12V pinout',2,filter={'file':'board.pdf'})
        self.assertEqual([item['id'] for item in hybrid],['pin'])
        self.store.upsert_vectors(['pin'],['memory slots only'],
            [{'file':'board.pdf','page':13}],[[1,0]])
        self.assertEqual(self.store.lexical_search('ATX_12V',3),[])
        self.store.delete(['pin'])
        self.assertEqual(self.store.lexical_search('memory',3),[])

    def test_version_one_prepares_optional_fts_without_changing_base_schema(self):
        path=self.root/'version-one.sqlite3'
        db=sqlite3.connect(path)
        try:
            db.executescript('''
                CREATE TABLE vectors (id TEXT PRIMARY KEY,text TEXT NOT NULL,
                    metadata TEXT NOT NULL,file TEXT NOT NULL,vector BLOB NOT NULL);
                CREATE INDEX vector_files ON vectors(file);
                CREATE TABLE info (key TEXT PRIMARY KEY,value TEXT NOT NULL);
                PRAGMA user_version=1;
            ''')
            db.execute("INSERT INTO info VALUES ('dimensions','2')")
            db.execute('INSERT INTO vectors VALUES (?,?,?,?,?)',
                       ('old','legacy ATX_12V text','{"file":"old.pdf","page":7}',
                        'old.pdf',np.asarray([1,0],dtype='<f4').tobytes()))
            db.commit()
        finally:
            db.close()
        upgraded=VectorStore(path)
        with upgraded.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
            self.assertIsNone(db.execute(
                "SELECT 1 FROM sqlite_master WHERE name='vectors_fts'").fetchone())
        self.assertFalse(upgraded.lexical_index_available())
        self.assertEqual(upgraded.get(ids=['old'],include=['documents'])['documents'],
                         ['legacy ATX_12V text'])
        upgraded.ensure_lexical_index()
        self.assertEqual(upgraded.lexical_search('ATX_12V',1)[0]['id'],'old')
        with upgraded.connect() as db:
            self.assertEqual(db.execute('PRAGMA user_version').fetchone()[0],1)
        upgraded.remove_lexical_index()
        self.assertFalse(upgraded.lexical_index_available())

    def test_migration_preserves_exact_vectors_text_metadata_manifest_and_backup(self):
        db=self.root/'legacy';db.mkdir()
        config=rag.Config(db=db,collection='example')
        state={'config':config.identity(),'files':{'source':{'file':'cpu.pdf','ids':['a','c']}}}
        rag.save_state(config,state)
        original=(db/'example-manifest.json').read_bytes()
        source=self.store
        report=migrate(config,reader=lambda directory,name:source)
        migrated=rag.open_store(config,None).get(include=['documents','metadatas','embeddings'])
        expected=source.get(ids=['a','c'],include=['documents','metadatas','embeddings'])
        self.assertEqual(migrated['ids'],expected['ids'])
        self.assertEqual(migrated['documents'],expected['documents'])
        self.assertEqual(migrated['metadatas'],expected['metadatas'])
        self.assertTrue(np.array_equal(migrated['embeddings'],expected['embeddings']))
        self.assertEqual((db/'example-manifest.json').read_bytes(),original)
        self.assertEqual((Path(report['backup'])/'example-manifest.json').read_bytes(),original)
        self.assertEqual(report['embedding_calls'],0)
        with self.assertRaises(FileExistsError):migrate(config,reader=lambda *args:source)

    def test_failed_migration_does_not_publish_or_replace_originals(self):
        db=self.root/'legacy';db.mkdir();config=rag.Config(db=db,collection='example')
        rag.save_state(config,{'config':config.identity(),'files':{'source':{'ids':['missing']}}})
        original=(db/'example-manifest.json').read_bytes()
        with self.assertRaisesRegex(ValueError,'missing published'):
            migrate(config,reader=lambda *args:self.store)
        self.assertFalse((db/'example-vectors.sqlite3').exists())
        self.assertEqual((db/'example-manifest.json').read_bytes(),original)
        with self.assertRaisesRegex(ValueError,'Migra el índice'):
            rag.open_store(config,None)
