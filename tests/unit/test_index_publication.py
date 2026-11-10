"""Failed index writes must never become evidence or erase the published version."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import subprocess
import sys

from langchain_core.embeddings import DeterministicFakeEmbedding
import rag


class PublicationTests(unittest.TestCase):
    def setUp(self):
        base=rag.ROOT/'data/test-chroma';base.mkdir(parents=True,exist_ok=True)
        self.root=Path(tempfile.mkdtemp(prefix='publication-',dir=base))
        self.docs=self.root/'documents';self.docs.mkdir()
        self.path=self.docs/'manual.txt'
        self.path.write_text('Published specification: CPU supports socket LGA1700. '*4,encoding='utf-8')
        self.config=rag.Config(db=self.root/'db',collection='publication',chunk_size=100,chunk_overlap=0)
        self.embeddings=DeterministicFakeEmbedding(size=32)
        rag.ingest(self.config,self.docs,self.embeddings)
        self.previous=rag.load_state(self.config)
        self.old=self.previous['files'][str(self.path.resolve())]
        self.store=rag.open_store(self.config,self.embeddings)

    def edit(self):
        self.path.write_text('Unpublished replacement: agriculture requires weekly irrigation. '*6,encoding='utf-8')

    def test_partially_written_vectors_are_not_searchable(self):
        self.edit();add=self.store.add_documents
        def fail_after_write(*args,**kwargs):
            add(*args,**kwargs)
            raise OSError('simulated interrupted vector write')
        with patch.object(rag,'open_store',return_value=self.store),patch.object(self.store,'add_documents',side_effect=fail_after_write):
            result=rag.ingest(self.config,self.docs,self.embeddings)
        self.assertEqual(result['errors'],1)
        self.assertEqual(rag.load_state(self.config),self.previous)
        hits=rag.retrieve(self.config,'agriculture irrigation',self.embeddings,12)
        self.assertTrue(hits)
        self.assertTrue(all(doc.metadata['sha256']==self.old['sha256'] for doc,_ in hits))

    def test_manifest_write_failure_keeps_published_vectors(self):
        self.edit()
        with patch.object(rag,'save_state',side_effect=OSError('simulated manifest disk failure')):
            result=rag.ingest(self.config,self.docs,self.embeddings)
        self.assertEqual(result['errors'],1)
        self.assertEqual(rag.load_state(self.config),self.previous)
        self.assertEqual(set(self.store.get(ids=self.old['ids'])['ids']),set(self.old['ids']))

    def test_global_retrieval_reads_only_the_published_generation(self):
        from library_queries import global_retrieve
        from langchain_core.documents import Document
        self.store.add_documents([Document(page_content='CPU support Unpublished poison generation',metadata={
            'file':'manual.txt','source':str(self.path.resolve()),'sha256':'not-published','chunk':999,'page':0})],ids=['orphan-unpublished'])
        hits,scope=global_retrieve(self.config,[{'name':'manual.txt','indexed':True}],self.embeddings,'List CPU support')
        self.assertTrue(scope['coverage_complete'])
        self.assertTrue(all(doc.metadata['sha256']==self.old['sha256'] for doc,_ in hits))

    def test_failed_cleanup_does_not_unpublish_the_new_version(self):
        self.edit()
        with patch.object(rag,'open_store',return_value=self.store),patch.object(self.store,'delete',side_effect=OSError('simulated GC failure')):
            result=rag.ingest(self.config,self.docs,self.embeddings)
        self.assertEqual(result['errors'],0)
        self.assertEqual(result['indexed_files'],1)
        committed=rag.load_state(self.config)['files'][str(self.path.resolve())]
        self.assertNotEqual(committed['sha256'],self.old['sha256'])
        hits=rag.retrieve(self.config,'CPU specification',self.embeddings,12)
        self.assertTrue(hits)
        self.assertTrue(all(doc.metadata['sha256']==committed['sha256'] for doc,_ in hits))

    def test_source_changed_while_parsing_is_not_published(self):
        def loader(path):
            documents=rag.read_documents(path)
            path.write_text('Changed again while the reader was running.',encoding='utf-8')
            return documents
        self.edit()
        result=rag.ingest(self.config,self.docs,self.embeddings,loader=loader)
        self.assertEqual(result['errors'],1)
        self.assertEqual(rag.load_state(self.config),self.previous)
        self.assertEqual(set(self.store.get()['ids']),set(self.old['ids']))

    def test_cancellation_after_vector_write_keeps_the_published_version(self):
        self.edit();written=False;add=self.store.add_documents
        def write_then_cancel(*args,**kwargs):
            nonlocal written
            result=add(*args,**kwargs);written=True
            return result
        with patch.object(rag,'open_store',return_value=self.store),patch.object(self.store,'add_documents',side_effect=write_then_cancel):
            with self.assertRaises(InterruptedError):
                rag.ingest(self.config,self.docs,self.embeddings,cancelled=lambda:written)
        self.assertEqual(rag.load_state(self.config),self.previous)
        self.assertEqual(set(self.store.get()['ids']),set(self.old['ids']))

    def test_writer_lock_excludes_another_process_and_releases_after_exit(self):
        from index_lock import index_writer
        code='''import sys
from pathlib import Path
from types import SimpleNamespace
from index_lock import index_writer, IndexBusyError
try:
 with index_writer(SimpleNamespace(db=Path(sys.argv[1]),collection=sys.argv[2])): pass
except IndexBusyError: sys.exit(73)
'''
        arguments=[sys.executable,'-c',code,str(self.config.db),self.config.collection]
        with index_writer(self.config):
            result=subprocess.run(arguments,cwd=rag.ROOT,capture_output=True,text=True)
            self.assertEqual(result.returncode,73,result.stderr)
        result=subprocess.run(arguments,cwd=rag.ROOT,capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
