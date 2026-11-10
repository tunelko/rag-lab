"""Offline tests with deterministic fake vectors. No LLM/embedding downloads or GPU work."""

import os
os.environ["CUDA_VISIBLE_DEVICES"] = ""
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["ANONYMIZED_TELEMETRY"] = "False"
os.environ["OMP_NUM_THREADS"] = "2"

from dataclasses import replace
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from langchain_core.documents import Document
from langchain_core.embeddings import DeterministicFakeEmbedding
import rag


class RagSmokeTests(unittest.TestCase):
    def test_ingest_persist_deduplicate_update_and_retrieve(self):
        # Chroma holds open file handles on Windows; retain this tiny isolated test DB.
        sandbox_root = rag.ROOT / "data" / "test-chroma"
        sandbox_root.mkdir(parents=True, exist_ok=True)
        case_root = Path(tempfile.mkdtemp(prefix="smoke-", dir=sandbox_root))
        documents = case_root / "documents"
        documents.mkdir()
        note = documents / "manual.md"
        note.write_text("El laboratorio utiliza ChromaDB local y embeddings en CPU.", encoding="utf-8")
        config = rag.Config(db=case_root / "database", collection="smoke_test")
        embeddings = DeterministicFakeEmbedding(size=32)

        first = rag.ingest(config, documents, embeddings)
        self.assertEqual(first["indexed_files"], 1)
        second = rag.ingest(config, documents, embeddings)
        self.assertEqual(second["skipped_files"], 1)
        self.assertEqual(len(rag.open_store(config, embeddings).get()["ids"]), 1)

        query = note.read_text(encoding="utf-8")
        hits = rag.retrieve(config, query, embeddings, 1)
        self.assertEqual(hits[0][0].metadata["file"], "manual.md")
        self.assertAlmostEqual(float(hits[0][1]), 0, places=4)
        old_ids = set(rag.open_store(config, embeddings).get()["ids"])

        note.write_text("El laboratorio conserva referencias a sus documentos originales.", encoding="utf-8")
        changed = rag.ingest(config, documents, embeddings)
        self.assertEqual(changed["indexed_files"], 1)
        new_ids = set(rag.open_store(config, embeddings).get()["ids"])
        self.assertEqual(len(new_ids), 1)
        self.assertTrue(old_ids.isdisjoint(new_ids))
        # Reopen in a fresh interpreter: this proves disk persistence, not just caching.
        reader = subprocess.run(
            [sys.executable, "-c",
             "import sys; from pathlib import Path; import rag; "
             "from langchain_core.embeddings import DeterministicFakeEmbedding; "
             "c=rag.Config(db=Path(sys.argv[1]),collection='smoke_test'); "
             "print(len(rag.open_store(c,DeterministicFakeEmbedding(size=32)).get()['ids']))",
             str(config.db)],
            cwd=rag.ROOT, capture_output=True, text=True, timeout=60,
        )
        self.assertEqual(reader.returncode, 0, reader.stderr)
        self.assertEqual(reader.stdout.strip(), "1")
        with self.assertRaises(ValueError):
            rag.load_state(replace(config, embedding_model="another/model"))

    def test_prompt_has_source_page_and_bounded_evidence(self):
        document = Document(page_content="A" * 900, metadata={"file": "manual.pdf", "source": "manual.pdf", "page": 3})
        messages, sources = rag.build_messages("Pregunta de prueba", [(document, 0.1)], 200)
        self.assertIn("[1] manual.pdf; page 3", messages[1]["content"])
        self.assertNotIn("A" * 201, messages[1]["content"])
        self.assertEqual(sources[0]["page"], 3)

    def test_txt_markdown_and_empty_pdf_loading(self):
        from pypdf import PdfWriter
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "nota.txt"
            path.write_text("Contenido en español.", encoding="utf-8")
            self.assertEqual(rag.read_documents(path)[0].page_content, "Contenido en español.")
            pdf = root / "scan.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=100, height=100)
            with pdf.open("wb") as output:
                writer.write(output)
            loaded = rag.read_documents(pdf)
            self.assertEqual(loaded[0].metadata["page"], 1)
            self.assertEqual(loaded[0].page_content, "")

    def test_overlap_validation(self):
        with self.assertRaises(ValueError):
            rag.Config(chunk_size=100, chunk_overlap=100)

    def test_retrieval_skips_empty_captions_and_searches_beyond_them(self):
        junk=[(Document(page_content='---'),.1)]*24
        evidence=(Document(page_content='CPU: Supports Intel Core generation 12.'),.2)
        requested=[]
        def search(question,k):
            requested.append(k)
            return (junk+[evidence])[:k]
        with tempfile.TemporaryDirectory() as directory:
            config=rag.Config(db=Path(directory))
            (config.db/f'{config.collection}-manifest.json').touch()
            with patch.object(rag,'load_state'), patch.object(rag,'open_store',return_value=SimpleNamespace(similarity_search_with_score=search)):
                hits=rag.retrieve(config,'CPU admitidas',object(),1)
        self.assertEqual(hits,[evidence])
        self.assertGreater(len(requested),1)
        self.assertFalse(rag.usable_evidence('---\n _ *'))
        self.assertTrue(rag.usable_evidence('处理器'))

    def test_empty_index_never_calls_generator(self):
        with patch("sys.argv", ["rag.py", "ask", "Pregunta"]), \
             patch.object(rag, "make_embeddings", return_value=object()), \
             patch.object(rag, "load_state", return_value={}), \
             patch.object(rag, "retrieve", return_value=[]), \
             patch.object(rag, "generate_ollama") as generator:
            self.assertEqual(rag.main(), 0)
            generator.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
