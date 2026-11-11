"""Storage/migration contract tests: known vectors, no model downloads."""
from dataclasses import replace
import json
from pathlib import Path
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
