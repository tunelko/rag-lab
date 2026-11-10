"""Progress/cancellation regression tests; no real embeddings or LLMs are loaded."""
import io
import json
import threading
import time
import unittest
from unittest.mock import patch

from pypdf import PdfWriter

import rag
import test_web


class RedesignTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    wait = test_web.WebTests.wait
    def test_queued_jobs_can_be_recovered_and_cancelled(self):
        service = self.client.app.state.service
        started, release = threading.Event(), threading.Event()

        def blocking(job_id):
            started.set()
            release.wait(5)

        first = service.start("prepare", blocking)["job_id"]
        try:
            self.assertTrue(started.wait(2))
            second = service.start("index", lambda job_id: {}, document_name="queued.txt")["job_id"]
            active = self.client.get("/api/jobs?active=1").json()["jobs"]
            self.assertEqual({item["id"] for item in active}, {first, second})
            reply = self.client.delete(f"/api/jobs/{second}", headers=self.headers)
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(reply.json()["status"], "cancelled")
        finally:
            release.set()
        self.wait(first)

    def test_index_commit_is_not_cancellable(self):
        service = self.client.app.state.service
        started, release = threading.Event(), threading.Event()

        def committing(job_id):
            service.update(job_id, stage="indexing", cancellable=False)
            started.set()
            release.wait(5)

        job = service.start("index", committing)["job_id"]
        try:
            self.assertTrue(started.wait(2))
            self.assertEqual(self.client.delete(f"/api/jobs/{job}", headers=self.headers).status_code, 409)
        finally:
            release.set()
        self.wait(job)

    def test_blank_pdf_is_structured_error_and_partial_success_is_preserved(self):
        writer = PdfWriter()
        writer.add_blank_page(width=100, height=100)
        blank = io.BytesIO()
        writer.write(blank)
        for name, payload in [("a-scan.pdf", blank.getvalue()), ("z-good.txt", b"A real text document.")]:
            response = self.client.post("/api/admin/upload", headers=self.headers,
                files=[("files", (name, payload))], data={"auto_index": "false"})
            self.assertEqual(response.status_code, 200)
        result = self.client.post("/api/admin/index", headers=self.headers).json()
        for _ in range(100):
            job = self.client.get(f"/api/jobs/{result['job_id']}").json()
            if job["status"] in {"done", "error"}:
                break
            time.sleep(.05)
        self.assertEqual(job["status"], "error", job)
        self.assertEqual(job["error_info"]["code"], "NO_TEXT")
        self.assertIsInstance(job["error"], str)  # Legacy field preserved.
        docs = {item["name"]: item for item in self.client.get("/api/status").json()["documents"]}
        self.assertEqual(docs["a-scan.pdf"]["status"], "error")
        self.assertEqual(docs["z-good.txt"]["status"], "indexed")

    def test_observer_uses_actual_completed_batches_and_scoped_paths(self):
        service = self.client.app.state.service
        target = service.documents / "selected.txt"
        target.write_text("\n".join(f"{i}: " + "x" * 450 for i in range(200)), encoding="utf-8")
        (service.documents / "untouched.txt").write_text("Do not index yet.", encoding="utf-8")
        events = []
        summary = rag.ingest(service.config, service.documents, service.get_embeddings(),
                             paths=[target], observer=lambda **event: events.append(event))
        self.assertEqual(summary["indexed_files"], 1)
        self.assertNotIn(str(service.documents / "untouched.txt"), rag.load_state(service.config)["files"])
        percentages = [event["stage_progress"] for event in events if event["stage"] == "indexing"]
        self.assertGreater(len(set(percentages)), 2)
        self.assertEqual(percentages, sorted(percentages))
        self.assertEqual(percentages[-1], 100)

    def test_generation_can_cancel_on_a_stream_packet(self):
        packet = b'{"message":{"thinking":"still thinking"},"done":false}\n'
        with patch("rag.urllib.request.urlopen", return_value=io.BytesIO(packet)):
            with self.assertRaises(InterruptedError):
                rag.generate_ollama([], "test", on_chunk=lambda text: None, cancelled=lambda: True)

    def test_filename_and_size_errors_are_structured(self):
        for name, code in [("bad.docx", "UNSUPPORTED_TYPE"), ("../bad.txt", "INVALID_NAME")]:
            response = self.client.post("/api/admin/upload", headers=self.headers,
                files=[("files", (name, b"text"))], data={"auto_index": "false"})
            self.assertEqual(response.json()["error_info"]["code"], code)


if __name__ == "__main__":
    import unittest
    unittest.main(verbosity=2)
