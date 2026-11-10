"""Local API tests: real persistence, fake embeddings and mocked LLM responses."""
import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["OMP_NUM_THREADS"] = "2"

from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from langchain_core.embeddings import DeterministicFakeEmbedding
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


class WebTests(unittest.TestCase):
    def setUp(self):
        root = rag.ROOT / "data" / "test-chroma"
        root.mkdir(parents=True, exist_ok=True)
        self.case = Path(tempfile.mkdtemp(prefix="web-", dir=root))
        service = OfflineService(rag.Config(db=self.case / "db", collection="web_test"), self.case / "documents")
        self.client = TestClient(create_app(service))
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

    def test_user_and_admin_pages_are_local_and_served(self):
        for path in ("/", "/admin", "/assets/app.css", "/assets/app.js"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertEqual(self.client.get("/api/status").json()["chunk_count"], 0)

    def test_upload_index_query_sources_and_metrics(self):
        result = self.client.post("/api/admin/upload", headers=self.headers,
            files=[("files", ("manual.txt", "La copia de seguridad se realiza cada viernes.".encode(), "text/plain"))],
            data={"auto_index": "true"})
        self.assertEqual(result.status_code, 200, result.text)
        self.wait(result.json()["job_id"])
        status = self.client.get("/api/status").json()
        self.assertEqual(status["document_count"], 1)
        self.assertEqual(status["chunk_count"], 1)

        def mock_generate(messages, model, on_chunk=None, cancelled=None):
            self.assertIn("cada viernes", messages[1]["content"])
            on_chunk("Se realiza cada ")
            on_chunk("viernes [1].")
            return "Se realiza cada viernes [1].", {"generation_tok_s": 80, "output_tokens": 12, "model": model}

        with patch.object(rag, "generate_ollama", side_effect=mock_generate) as generate:
            request = self.client.post("/api/query", headers=self.headers, json={"question": "¿Cuándo se realiza la copia?", "model": "test-local:latest", "k": 4})
            self.assertEqual(request.status_code, 202, request.text)
            reply = self.wait(request.json()["job_id"])
            self.assertIn("viernes [1]", reply["answer"])
            self.assertEqual(reply["sources"][0]["file"], "manual.txt")
            self.assertEqual(reply["metrics"]["generation_tok_s"], 80)
            for name in ('embedding_setup_seconds', 'vector_search_seconds', 'source_preparation_seconds', 'routing_seconds', 'generation_wall_seconds'):
                self.assertGreaterEqual(reply['metrics'][name], 0)
            self.assertGreaterEqual(reply['queue_seconds'], 0)
            recent = self.client.get('/api/statistics').json()['recent'][0]
            self.assertEqual(recent['queue_seconds'], reply['queue_seconds'])
            generate.assert_called_once()
        source = self.client.get(reply["sources"][0]["url"])
        self.assertEqual(source.status_code, 200)
        self.assertIn("cada viernes", source.text)

        with patch.object(rag, "generate_ollama") as generate:
            request = self.client.post("/api/query", headers=self.headers, json={"question": "copia", "search_only": True})
            reply = self.wait(request.json()["job_id"])
            self.assertEqual(len(reply["sources"]), 1)
            generate.assert_not_called()

    def test_mutations_require_session_token_and_same_origin(self):
        self.assertEqual(self.client.post("/api/admin/prepare").status_code, 403)
        self.assertEqual(self.client.post("/api/admin/prepare", headers={**self.headers, "Origin": "https://example.org"}).status_code, 403)
        self.assertEqual(self.client.get("/api/status", headers={"Host": "other.example"}).status_code, 400)

    def test_chroma_server_collection_endpoints_are_not_exposed(self):
        endpoint = '/api/v2/tenants/default_tenant/databases/default_database/collections'
        for method, path in (('GET', endpoint), ('POST', endpoint), ('PATCH', endpoint + '/unused-id')):
            response = self.client.request(method, path, headers=self.headers)
            self.assertEqual(response.status_code, 404, response.text)

    def test_pdf_sources_distinguish_physical_pages_and_repeated_document_labels(self):
        import pymupdf
        from langchain_core.documents import Document

        service = self.client.app.state.service
        path = service.documents / "manual multilingue.pdf"
        with pymupdf.open() as pdf:
            for _ in range(169):
                pdf.new_page()
            pdf.set_page_labels([
                {"startpage": 0, "style": "D", "firstpagenum": 1},
                {"startpage": 156, "style": "D", "firstpagenum": 1},
                {"startpage": 168, "style": "D", "firstpagenum": 36},
            ])
            pdf.save(path)
        documents = [Document(page_content=f"Vista de página {page}\nDescripción técnica de BIOS.",
                     metadata={"page": page, "item_type": "page_image"}) for page in (164, 169)]
        rag.ingest(service.config, service.documents, service.get_embeddings(), loader=lambda _: documents)
        hits = [(Document(page_content=document.page_content,
                 metadata={**document.metadata, "file": path.name, "source": str(path)}), .1)
                for document in documents]
        with patch.object(rag, "retrieve", return_value=hits), patch.object(rag, "generate_ollama") as generate:
            request = self.client.post("/api/query", headers=self.headers,
                                       json={"question": "Explica BIOS", "search_only": True})
            self.assertEqual(request.status_code, 202, request.text)
            reply = self.wait(request.json()["job_id"])
            generate.assert_not_called()
        self.assertEqual([(source["page"], source["page_label"]) for source in reply["sources"]],
                         [(164, "8"), (169, "36")])
        for source in reply["sources"]:
            self.assertTrue(source["url"].endswith(f"#page={source['page']}"))
            response = self.client.get(source["url"])
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/pdf")
            self.assertTrue(response.headers["content-disposition"].startswith("inline;"))
            self.assertEqual(response.content, path.read_bytes())

    def test_file_paths_types_size_and_empty_index(self):
        for name in ("../../escape.txt", "script.html", "folder\\escape.txt"):
            response = self.client.post("/api/admin/upload", headers=self.headers,
                files=[("files", (name, b"test", "text/plain"))], data={"auto_index": "false"})
            self.assertEqual(response.status_code, 422)
        with patch("web_app.MAX_FILE", 10):
            response = self.client.post("/api/admin/upload", headers=self.headers,
                files=[("files", ("large.txt", b"x" * 11, "text/plain"))])
            self.assertEqual(response.status_code, 413)
        self.assertEqual(self.client.get("/api/source?file=../../.env").status_code, 404)
        self.assertEqual(self.client.post("/api/query", headers=self.headers, json={"question": "test"}).status_code, 409)

    def test_upload_retry_does_not_duplicate_or_overwrite(self):
        for content in (b"first", b"first", b"updated"):
            response = self.client.post("/api/admin/upload", headers=self.headers,
                files=[("files", ("note.txt", content, "text/plain"))], data={"auto_index": "false"})
            self.assertEqual(response.status_code, 200)
        documents = self.client.get("/api/status").json()["documents"]
        self.assertEqual(len(documents), 2)
        self.assertEqual((self.case / "documents" / "note.txt").read_bytes(), b"first")
        self.assertEqual((self.case / "documents" / "note-2.txt").read_bytes(), b"updated")


if __name__ == "__main__":
    unittest.main(verbosity=2)
