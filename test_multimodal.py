"""Real parsing/index storage, fake embeddings and vision. Never loads a GPU model."""
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from PIL import Image
import pymupdf

import rag
from multimodal import VisualReader, VisualError, describe_image
from settings_store import Preferences, SettingsStore
import test_web


def png():
    buffer = io.BytesIO()
    Image.new("RGB", (80, 60), "white").save(buffer, format="PNG")
    return buffer.getvalue()


class ParsingTests(unittest.TestCase):
    def setUp(self):
        root = rag.ROOT / "data" / "test-chroma"
        root.mkdir(parents=True, exist_ok=True)
        self.case = Path(tempfile.mkdtemp(prefix="visual-", dir=root))
        self.calls = []
        self.settings = Preferences(multimodal=True, vision_model="fake-vision")
        def caption(data, model):
            self.calls.append(model)
            return "Temperatura 42 grados."
        self.reader = VisualReader(self.case / "visual", self.settings, caption)

    def archive(self, content, version="v2", image_path="images/figure.png", extra=None):
        path = self.case / "export.zip"
        suffix = "_v2" if version == "v2" else ""
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(f"result/doc_content_list{suffix}.json", json.dumps(content))
            archive.writestr("result/" + image_path, png())
            if extra:
                archive.writestr(extra, "x")
        return path

    def test_image_cache_and_page_metadata(self):
        path = self.case / "figure.png"
        path.write_bytes(png())
        first, = self.reader.load(path)
        again, = self.reader.load(path)
        self.assertEqual(self.calls, ["fake-vision"])
        self.assertEqual(first.metadata["asset_id"], again.metadata["asset_id"])
        self.assertEqual(first.metadata["page"], 1)
        self.assertIn("42", first.page_content)

    def test_pdf_text_and_vector_page_render(self):
        path = self.case / "drawing.pdf"
        with pymupdf.open() as pdf:
            page = pdf.new_page()
            page.insert_text((50, 50), "Manual de placa")
            page.draw_rect(pymupdf.Rect(50, 80, 150, 150))
            pdf.save(path)
        docs = self.reader.load(path)
        self.assertTrue(any("Manual de placa" in doc.page_content for doc in docs))
        self.assertTrue(any(doc.metadata.get("item_type") == "page_image" for doc in docs))
        self.assertTrue(all(doc.metadata["page"] == 1 for doc in docs))

    def test_mineru_v2_figures_and_tables(self):
        content = [[{"type": "paragraph", "content": {"paragraph_content": [{"content": "Texto de prueba"}]}},
                    {"type": "table", "content": {"html": "<table><tr><td>A</td><td>12</td></tr></table>"}},
                    {"type": "image", "content": {"image_caption": ["Figura 1"], "image_source": {"path": "images/figure.png"}}}]]
        docs = self.reader.load(self.archive(content))
        self.assertTrue(any("Texto de prueba" in doc.page_content for doc in docs))
        self.assertTrue(any("A | 12" in doc.page_content for doc in docs))
        self.assertTrue(any(doc.metadata.get("asset_id") for doc in docs))

    def test_mineru_v1_and_zero_based_page_conversion(self):
        content = [{"type": "image", "page_idx": 4, "img_path": "images/figure.png", "image_caption": ["Captura"]}]
        docs = self.reader.load(self.archive(content, "v1"))
        self.assertEqual({doc.metadata["page"] for doc in docs}, {5})

    def test_zip_traversal_and_external_image_rejected(self):
        with self.assertRaises(VisualError):
            self.reader.load(self.archive([], extra="../../outside.txt"))
        content = [[{"type": "image", "content": {"image_source": {"path": "https://outside/image.png"}}}]]
        with self.assertRaises(VisualError):
            self.reader.load(self.archive(content))
        self.assertFalse(self.calls)

    def test_limit_is_error_not_silent_partial_success(self):
        self.settings.max_visuals_per_document = 1
        item = {"type": "image", "content": {"image_source": {"path": "images/figure.png"}}}
        with self.assertRaises(VisualError):
            self.reader.load(self.archive([[item, item]]))

    def test_visual_stream_requires_completion_and_supports_cancellation(self):
        packets = b'{"message":{"content":"42 grados"},"done":false}\n{"done":true}\n'
        with patch("multimodal.urllib.request.urlopen", return_value=io.BytesIO(packets)):
            self.assertEqual(describe_image(png(), "fake", "http://localhost"), "42 grados")
        with patch("multimodal.urllib.request.urlopen", return_value=io.BytesIO(packets)):
            with self.assertRaises(InterruptedError):
                describe_image(png(), "fake", "http://localhost", cancelled=lambda: True)
        with patch("multimodal.urllib.request.urlopen", return_value=io.BytesIO(b'{"message":{"content":"partial"}}\n')):
            with self.assertRaises(VisualError):
                describe_image(png(), "fake", "http://localhost")


class IntegrationTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown
    wait = test_web.WebTests.wait

    def configure(self, **changes):
        settings = Preferences(response_model="test-local:latest", **changes).model_dump()
        result = self.client.put("/api/settings", headers=self.headers, json=settings)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["settings"]

    def upload(self, name, data):
        result = self.client.post("/api/admin/upload", headers=self.headers, files=[("files", (name, data))], data={"auto_index": "true"})
        self.assertEqual(result.status_code, 200, result.text)
        return self.wait(result.json()["job_id"])

    def test_settings_persist_validate_and_protect_writes(self):
        self.assertEqual(self.client.put("/api/settings", json={}).status_code, 403)
        self.configure(k=8, search_only=True)
        store = SettingsStore(self.client.app.state.service.settings.path)
        self.assertEqual(store.get().k, 8)
        self.assertTrue(store.get().search_only)
        self.assertEqual(self.client.put("/api/settings", headers=self.headers, json={"k": 3}).status_code, 422)
        for route in ("/configuracion", "/estadisticas"):
            self.assertEqual(self.client.get(route).status_code, 200)

    def test_chat_applies_shared_settings_without_llm(self):
        self.upload("manual.txt", b"Manual de placa y sus ajustes.")
        self.configure(k=2, search_only=True)
        with patch("rag.generate_ollama", side_effect=AssertionError("No LLM in search-only mode")):
            response = self.client.post("/api/query", headers=self.headers, json={"question": "ajustes"})
            job = self.wait(response.json()["job_id"])
        self.assertEqual(job["k"], 2)
        self.assertTrue(job["search_only"])
        self.assertEqual(self.client.get("/api/statistics").json()["completed"], 1)

    def test_visual_pipeline_query_images_and_text_fallback(self):
        service = self.client.app.state.service
        with patch.object(service, "model_capabilities", return_value=["vision"]), patch("multimodal.describe_image", return_value="Placa con 4 puertos USB") as describe:
            self.configure(multimodal=True, vision_model="test-local:latest")
            self.upload("board.png", png())
            # Reindex must skip an unchanged parser policy and reuse cached assets.
            self.wait(self.client.post("/api/admin/index", headers=self.headers).json()["job_id"])
            self.assertEqual(describe.call_count, 1)
            def generate(messages, model, **kwargs):
                self.assertEqual(len(messages[-1]["images"]), 1)
                self.assertEqual(messages[1]["content"], "Antes")
                return "4 puertos [1]", {"output_tokens": 8, "generation_tok_s": 12.5, "model": model}
            with patch("rag.generate_ollama", side_effect=generate):
                response = self.client.post("/api/query", headers=self.headers, json={"question": "puertos", "history": [{"role": "user", "content": "Antes"}]})
                job = self.wait(response.json()["job_id"])
        self.assertEqual(job["metrics"]["images_used"], 1)
        figure = job["sources"][0]["figures"][0]
        self.assertEqual(self.client.get(figure["url"]).headers["content-type"], "image/png")
        with patch.object(service, "model_capabilities", return_value=[]):
            def generate_text(messages, model, **kwargs):
                self.assertNotIn("images", messages[-1])
                return "4 [1]", {"output_tokens": 2}
            with patch("rag.generate_ollama", side_effect=generate_text):
                response = self.client.post("/api/query", headers=self.headers, json={"question": "puertos"})
                job = self.wait(response.json()["job_id"])
        self.assertEqual(job["metrics"]["visual_mode"], "descriptions")
        stats = self.client.get("/api/statistics").json()
        self.assertEqual(stats["visuals"], 1)
        self.assertEqual(stats["output_tokens"], 10)
        self.assertEqual(self.client.get("/api/figures/invalid").status_code, 404)

    def test_changed_visual_policy_requires_reindex(self):
        service = self.client.app.state.service
        with patch.object(service, "model_capabilities", return_value=["vision"]), patch("multimodal.describe_image", return_value="Descripción"):
            self.configure(multimodal=True, vision_model="test-local:latest")
            self.upload("image.png", png())
            self.configure(multimodal=True, vision_model="test-local:latest", max_visuals_per_document=30)
            self.assertEqual(service.list_documents()[0]["status"], "pending")
            job = self.wait(self.client.post("/api/admin/index", headers=self.headers).json()["job_id"])
        self.assertEqual(job["summary"]["indexed_files"], 1)
        self.assertEqual(job["summary"]["skipped_files"], 0)

    def test_visual_error_keeps_previous_index_intact(self):
        service = self.client.app.state.service
        with patch.object(service, "model_capabilities", return_value=["vision"]), patch("multimodal.describe_image", return_value="Descripción"):
            self.configure(multimodal=True, vision_model="test-local:latest")
            self.upload("keep.png", png())
        before = rag.load_state(service.config)
        # A new parser/model policy must fail before removing any previous chunks.
        settings = service.settings.get()
        settings.vision_model = "other-model"
        service.settings.save(settings)
        with patch.object(service, "model_capabilities", return_value=["vision"]), patch("multimodal.describe_image", side_effect=VisualError("fallo de visión")):
            job_id = service.start("index", service.index)["job_id"]
            import time
            for _ in range(100):
                job = service.snapshot(job_id)
                if job["status"] == "error":
                    break
                time.sleep(.02)
        self.assertEqual(job["error_info"]["code"], "VISUAL_IMPORT")
        self.assertEqual(rag.load_state(service.config), before)
        # The original text-only CLI must not erase the visual metadata either.
        summary = rag.ingest(service.config, service.documents, service.get_embeddings(), extensions={".png"})
        self.assertEqual(summary["errors"], 1)
        self.assertEqual(rag.load_state(service.config), before)

    def test_same_figure_on_two_pages_keeps_both_page_references(self):
        buffer = io.BytesIO()
        item = {"type": "image", "content": {"image_source": {"path": "images/repeated.png"}}}
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("manual_content_list_v2.json", json.dumps([[item], [item]]))
            archive.writestr("images/repeated.png", png())
        service = self.client.app.state.service
        with patch.object(service, "model_capabilities", return_value=["vision"]), patch("multimodal.describe_image", return_value="Diagrama") as describe:
            self.configure(multimodal=True, vision_model="test-local:latest")
            self.upload("repeated.zip", buffer.getvalue())
        record, = rag.load_state(service.config)["files"].values()
        self.assertEqual({visual["page"] for visual in record["visuals"]}, {1, 2})
        self.assertEqual(describe.call_count, 1)


if __name__ == "__main__":
    unittest.main()
