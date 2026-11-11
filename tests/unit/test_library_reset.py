"""Emptying the library moves it to a backup folder; real files, fake embeddings."""
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["RAG_RERANKER"] = ""

from pathlib import Path
import tempfile
import threading
import time
import unittest

from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
import library_reset
import rag
from web_app import RagService, create_app


class OfflineService(RagService):
    def model_capabilities(self, model):
        return []

    def ollama_status(self):
        return {"connected": True, "models": ["test-local:latest"], "default_model": "test-local:latest", "loaded": []}

    def get_embeddings(self, download=False):
        if self.embeddings is None:
            self.embeddings = DeterministicFakeEmbedding(size=32)
        return self.embeddings


class EmptyLibraryTests(unittest.TestCase):
    def setUp(self):
        root = rag.ROOT / "data" / "test-chroma"
        root.mkdir(parents=True, exist_ok=True)
        self.case = Path(tempfile.mkdtemp(prefix="empty-", dir=root))
        self.service = OfflineService(rag.Config(db=self.case / "db", collection="empty_test"), self.case / "documents")
        self.client = TestClient(create_app(self.service), client=("127.0.0.1", 50000))
        self.client.__enter__()
        self.headers = {"X-RAG-Token": self.client.get("/api/session").json()["token"]}

    def tearDown(self):
        self.client.__exit__(None, None, None)

    def wait(self, job_id):
        for _ in range(200):
            result = self.client.get(f"/api/jobs/{job_id}").json()
            if result["status"] in {"done", "error"}:
                self.assertEqual(result["status"], "done", result)
                return result
            time.sleep(0.05)
        self.fail("Job did not complete")

    def upload(self, name="manual.txt", content=b"Backups run every Friday."):
        response = self.client.post("/api/admin/upload", headers=self.headers,
            files=[("files", (name, content, "text/plain"))], data={"auto_index": "true"})
        self.assertEqual(response.status_code, 200, response.text)
        self.wait(response.json()["job_id"])

    def empty(self, **body):
        return self.client.post("/api/admin/library/empty", headers=self.headers, json={"confirm": "empty-library", **body})

    def backups(self):
        folder = self.case / "library-backups"
        return sorted(folder.iterdir()) if folder.exists() else []

    def test_library_is_moved_to_a_backup_and_a_new_one_can_be_indexed(self):
        self.upload()
        topic = self.client.post("/api/admin/topics", headers=self.headers, json={"name": "Manuales"}).json()["id"]
        self.client.post("/api/admin/library/assign", headers=self.headers, json={"names": ["manual.txt"], "topic_id": topic})
        (self.service.visual_root).mkdir(parents=True, exist_ok=True)
        (self.service.visual_root / "figure.png").write_bytes(b"png")
        self.assertGreater(self.client.get("/api/status").json()["chunk_count"], 0)

        response = self.empty()
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["documents"], 1)
        self.assertGreater(result["chunks"], 0)

        status = self.client.get("/api/status").json()
        self.assertEqual((status["document_count"], status["chunk_count"], status["embedding_dimensions"]), (0, 0, None))
        self.assertEqual(self.client.get("/api/library").json()["topics"], [])
        self.assertEqual(list(self.service.visual_root.iterdir()), [])
        backup, = self.backups()
        self.assertEqual((backup / "documents" / "manual.txt").read_bytes(), b"Backups run every Friday.")
        self.assertEqual((backup / "visual" / "figure.png").read_bytes(), b"png")
        for name in ("empty_test-manifest.json", "empty_test-vectors.sqlite3", "empty_test-topics.sqlite3"):
            self.assertTrue((backup / "index" / name).is_file(), name)
        self.assertTrue((backup / "LEEME-README.txt").is_file())

        # The emptied library is immediately usable and never sees the old vectors.
        self.upload("nuevo.txt", b"The new library mentions only Tuesday.")
        status = self.client.get("/api/status").json()
        self.assertEqual([item["name"] for item in status["documents"]], ["nuevo.txt"])
        search = self.client.post("/api/query", headers=self.headers, json={"question": "Friday backups", "search_only": True})
        sources = self.wait(search.json()["job_id"])["sources"]
        self.assertTrue(sources)
        self.assertEqual({item["file"] for item in sources}, {"nuevo.txt"})

    def test_caption_cache_can_be_kept(self):
        self.upload()
        self.service.visual_root.mkdir(parents=True, exist_ok=True)
        (self.service.visual_root / "figure.png").write_bytes(b"png")
        self.assertEqual(self.empty(keep_captions=True).status_code, 200)
        self.assertTrue((self.service.visual_root / "figure.png").is_file())
        self.assertFalse((self.backups()[0] / "visual").exists())

    def test_requires_confirmation_the_server_host_and_an_idle_queue(self):
        self.upload()
        self.assertEqual(self.client.post("/api/admin/library/empty", headers=self.headers, json={}).status_code, 422)
        self.assertEqual(self.client.post("/api/admin/library/empty", json={"confirm": "empty-library"}).status_code, 403)
        remote = TestClient(self.client.app, client=("192.168.1.25", 50001))
        self.assertEqual(remote.post("/api/admin/library/empty", headers=self.headers, json={"confirm": "empty-library"}).status_code, 403)
        release, entered = threading.Event(), threading.Event()

        def hold(job_id):
            entered.set()
            release.wait(5)
            return {}

        job = self.service.start("prepare", hold)["job_id"]
        try:
            self.assertTrue(entered.wait(2))
            self.assertEqual(self.empty().status_code, 409)
        finally:
            release.set()
        self.wait(job)
        self.assertEqual(self.backups(), [])
        self.assertEqual(self.client.get("/api/status").json()["document_count"], 1)
        self.assertEqual(self.empty().status_code, 200)
        self.assertEqual(self.empty().status_code, 409)  # already empty

    def test_a_file_in_use_leaves_everything_in_place(self):
        self.upload()
        before = self.client.get("/api/status").json()
        original = library_reset._rename

        def busy(source, target, *args):
            if Path(source) == self.service.documents:
                error = PermissionError(13, "in use")
                error.filename = str(source)
                raise error
            return original(source, target, *args)

        library_reset._rename = busy
        try:
            response = self.empty()
        finally:
            library_reset._rename = original
        self.assertEqual(response.status_code, 409, response.text)
        after = self.client.get("/api/status").json()
        self.assertEqual((after["document_count"], after["chunk_count"]), (before["document_count"], before["chunk_count"]))
        self.assertEqual(self.backups(), [])
        search = self.client.post("/api/query", headers=self.headers, json={"question": "Friday backups", "search_only": True})
        self.assertEqual({item["file"] for item in self.wait(search.json()["job_id"])["sources"]}, {"manual.txt"})

    def test_works_when_the_manifest_belongs_to_another_configuration(self):
        self.upload()
        manifest = self.case / "db" / "empty_test-manifest.json"
        manifest.write_text(manifest.read_text(encoding="utf-8").replace('"chunk_size": 500', '"chunk_size": 501'), encoding="utf-8")
        with self.assertRaises(ValueError):
            rag.load_state(self.service.config)
        self.assertEqual(self.empty().status_code, 200)
        self.assertEqual(self.client.get("/api/status").json()["document_count"], 0)


if __name__ == "__main__":
    unittest.main()
