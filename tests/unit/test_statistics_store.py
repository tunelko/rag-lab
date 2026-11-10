"""Seven-day persistence, bounded real samples and query history without sensors/models."""
from datetime import datetime, timezone
from pathlib import Path
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from hardware_monitor import HardwareMonitor
from statistics_store import StatisticsStore, RETENTION
from tests.unit import test_web
from web_app import create_app


def stamp(seconds):
    return datetime.fromtimestamp(seconds,timezone.utc).isoformat()


def point(seconds,cpu=20,gpu=40):
    return {'time':stamp(seconds),'cpu_percent':cpu,'gpu_percent':gpu,
        'ram_used_bytes':2*1024**3,'ram_total_bytes':8*1024**3,'vram_used_bytes':1024**3,'vram_total_bytes':10*1024**3}


class StoreTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory()
        self.path=Path(self.directory.name)/'statistics.sqlite3'
        self.store=StatisticsStore(self.path)

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def test_week_survives_reopen_and_old_data_expires_without_invented_readings(self):
        now=int(time.time())
        self.store.save_sample(point(now-8*86400))
        self.store.save_sample(point(now-6*86400))
        self.store.save_sample(point(now-6*86400+1))
        self.store.save_sample(point(now-3600))
        self.store.close()
        self.store=StatisticsStore(self.path)
        week=self.store.history(RETENTION,end=now)
        day=self.store.history(86400,end=now)
        self.assertEqual(week['history_sample_count'],2)
        self.assertEqual(day['history_sample_count'],1)
        self.assertTrue(week['history'][-1]['break_before'])

    def test_decimation_keeps_real_points_gaps_and_statistics_from_all_samples(self):
        now=int(time.time())
        records=[point(now-3600,1),point(now-3570,99,None),point(now-3540,2),point(now,3)]
        for record in records:
            self.store.save_sample(record,force=True)
        data=self.store.history(86400,end=now,limit=2)
        self.assertEqual([p['time'] for p in data['history']],[records[0]['time'],records[-1]['time']])
        self.assertEqual(data['history_sample_count'],4)
        self.assertEqual(data['history_summary']['cpu']['max'],99)
        self.assertEqual(data['history_summary']['cpu']['mean'],26.25)
        self.assertTrue(data['history'][-1]['break_before'])
        self.assertIn('gpu',data['history'][-1]['break_before_keys'])

    def test_query_metrics_persist_paginate_and_mark_unfinished_runs_honestly(self):
        now=time.time()
        for index,age in enumerate((3600,2*86400)):
            self.store.save_job({'id':str(index),'kind':'query','created_at':stamp(now-age),
                'question':'Pregunta guardada','status':'done','metrics':{'output_tokens':12,'generation_tok_s':8}})
        self.store.save_job({'id':'pending','kind':'query','created_at':stamp(now),'question':'Pendiente','status':'running'})
        self.store.close()
        self.store=StatisticsStore(self.path)
        self.store.mark_interrupted()
        week=self.store.queries(per_page=1)
        day=self.store.queries(86400)
        self.assertEqual(week['queries'],3)
        self.assertEqual(week['pages'],3)
        self.assertEqual(week['output_tokens'],24)
        self.assertEqual(week['recent'][0]['status'],'interrupted')
        self.assertEqual(day['queries'],2)
        self.assertEqual(self.store.queries(page=2,per_page=1)['recent'][0]['metrics']['generation_tok_s'],8)

    def test_background_collects_with_no_page_requests(self):
        self.store=StatisticsStore(self.path,sample_interval=0)
        collected=threading.Event()
        count=0
        def gpu():
            nonlocal count
            count+=1
            if count>=3: collected.set()
            return {'available':False,'devices':[]}
        with patch('hardware_monitor.psutil.cpu_percent',return_value=21), \
             patch('hardware_monitor.psutil.virtual_memory',return_value=SimpleNamespace(used=1,total=2,percent=50)), \
             patch('hardware_monitor.psutil.cpu_count',return_value=4), \
             patch('hardware_monitor.psutil.process_iter',return_value=[]):
            monitor=HardwareMonitor(interval=0,gpu_reader=gpu,archive=self.store)
            try:
                monitor.start(collector_interval=.02)
                self.assertTrue(collected.wait(2))
            finally:
                monitor.stop()
        self.assertGreaterEqual(self.store.history(3600)['history_sample_count'],3)

    def test_ingestion_history_keeps_timings_and_document_metadata_without_sources(self):
        self.store.save_job({'id':'index-1','kind':'index','created_at':stamp(time.time()),'status':'done',
            'document_name':'manual.pdf','elapsed_seconds':120,'queue_seconds':4,'chunks':22,
            'sources':[{'text':'private document text'}]})
        self.store.close()
        self.store=StatisticsStore(self.path)
        history=self.store.ingestions()
        self.assertEqual(history['total'],1)
        self.assertEqual(history['recent'][0]['elapsed_seconds'],120)
        self.assertEqual(history['recent'][0]['chunks'],22)
        self.assertNotIn('sources',history['recent'][0])


class HistoryApiTests(unittest.TestCase):
    setUp=test_web.WebTests.setUp
    tearDown=test_web.WebTests.tearDown
    wait=test_web.WebTests.wait

    def test_completed_query_is_visible_after_recreating_the_service(self):
        original=self.client.app.state.service
        response=self.client.post('/api/query',headers=self.headers,json={'question':'¿Cuántos documentos hay indexados?'})
        self.wait(response.json()['job_id'])
        self.client.__exit__(None,None,None)
        replacement=test_web.OfflineService(original.config,original.documents)
        self.client=TestClient(create_app(replacement))
        self.client.__enter__()
        result=self.client.get('/api/statistics?history_seconds=604800').json()
        self.assertEqual(result['queries'],1)
        self.assertIn('Cuántos',result['recent'][0]['question'])
        self.assertEqual(result['recent'][0]['status'],'done')
        self.assertEqual(replacement.jobs,{})

    def test_completed_index_job_can_be_polled_after_restarting_the_service(self):
        original=self.client.app.state.service
        response=self.client.post('/api/admin/upload',headers=self.headers,
            files=[('files',('manual.txt',b'Persistent text for indexing.','text/plain'))],data={'auto_index':'true'})
        job_id=response.json()['job_id']
        self.wait(job_id)
        self.client.__exit__(None,None,None)
        replacement=test_web.OfflineService(original.config,original.documents)
        self.client=TestClient(create_app(replacement));self.client.__enter__()
        response=self.client.get('/api/jobs/'+job_id)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['status'],'done')
        self.assertEqual(response.json()['chunks'],1)
        self.assertTrue(response.json()['recovered'])
        self.assertEqual(self.client.get('/api/jobs/not-a-real-job').status_code,404)
