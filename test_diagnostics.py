"""Offline job timing and library polling checks; no inference or sensors."""
import threading
import time
import unittest

import test_web


class DiagnosticTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    wait = test_web.WebTests.wait

    def test_queue_time_is_separate_and_performance_logs_omit_prompts(self):
        service = self.client.app.state.service
        entered, release = threading.Event(), threading.Event()
        def blocking(job_id):
            entered.set()
            release.wait(5)
        with self.assertLogs('rag.performance', level='INFO') as captured:
            first = service.start('prepare', blocking)['job_id']
            try:
                self.assertTrue(entered.wait(2))
                second = service.start('query', lambda job_id: {}, question='private question')['job_id']
                time.sleep(.03)
            finally:
                release.set()
            self.wait(first)
            completed = self.wait(second)
        self.assertGreaterEqual(completed['queue_seconds'], .02)
        self.assertGreaterEqual(completed['elapsed_seconds'], 0)
        self.assertIn('started_at', completed)
        self.assertTrue(any('queue_s=' in message and 'work_s=' in message for message in captured.output))
        self.assertFalse(any('private question' in message for message in captured.output))

    def test_library_polling_reuses_file_fingerprints(self):
        response = self.client.post('/api/admin/upload', headers=self.headers,
            files=[('files', ('manual.txt', b'local manual', 'text/plain'))], data={'auto_index': 'false'})
        self.assertEqual(response.status_code, 200)
        for _ in range(3):
            self.assertEqual(self.client.get('/api/status').status_code, 200)
        cache = self.client.get('/api/statistics').json()['diagnostics']['fingerprints']
        self.assertEqual(cache['misses'], 1)
        self.assertGreaterEqual(cache['hits'], 2)
