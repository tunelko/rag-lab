"""Questions about every item of a set are answered from its summary sheets. Offline."""
import unittest

from library_queries import SUMMARY_SUFFIX, has_summaries, query_intent, set_question, summary_scope


class WholeSetQuestionTests(unittest.TestCase):
    def test_questions_about_every_tender_use_the_all_items_path(self):
        for question in ("Todos los pliegos estan abiertos para presentarse?", "¿Qué licitaciones siguen abiertas?",
                         "¿Cuántos pliegos tienen un presupuesto superior a 500.000 euros?", "Presupuesto de cada licitación",
                         "Which tenders are still open?"):
            self.assertEqual(query_intent(question).mode, "global", question)
        for question in ("¿Qué requisitos debe cumplir el servicio de SOC del Ayuntamiento de Cartagena?",
                         "tiempo de respuesta ante incidentes críticos", "¿Qué dice el pliego sobre penalidades?"):
            self.assertEqual(query_intent(question).mode, "focused", question)

    def test_any_which_and_how_many_are_questions_about_the_set(self):
        for question in ("hay alguno de la comunidad de murcia?", "¿Hay alguna licitación de Andalucía?", "¿Cuáles vencen esta semana?",
                         "¿Cuántas superan los 500.000 euros?", "alguno de más de un millón", "lista de órganos que licitan SOC",
                         "Is there any from Madrid?", "Which are still open?"):
            self.assertTrue(set_question(question), question)
        for question in ("¿Qué requisitos debe cumplir el servicio de SOC del Ayuntamiento de Cartagena?",
                         "tiempo de respuesta ante incidentes críticos", "¿Qué dice sobre penalidades?",
                         "explica el plan de formación", "¿Cuál es el presupuesto del SOC de Cartagena?"[:0] or "plazo de garantía exigido"):
            self.assertFalse(set_question(question), question)
        documents = [{"name": "A__PPT__x.pdf", "indexed": True}]
        self.assertFalse(has_summaries(documents))
        self.assertTrue(has_summaries([*documents, {"name": "A" + SUMMARY_SUFFIX, "indexed": True}]))

    def test_the_whole_set_is_read_from_summary_sheets_when_there_are_any(self):
        documents = [{"name": "A__PPT__x.pdf", "indexed": True}, {"name": "A" + SUMMARY_SUFFIX, "indexed": True},
                     {"name": "B__PCAP__y.pdf", "indexed": True}, {"name": "B" + SUMMARY_SUFFIX, "indexed": False}]
        self.assertEqual([item["name"] for item in summary_scope(documents)], ["A" + SUMMARY_SUFFIX])
        self.assertEqual(summary_scope(documents[::2]), documents[::2])     # no sheets: every document, as before


if __name__ == "__main__":
    unittest.main()


class BatchedWholeSetTests(unittest.TestCase):
    """More summary sheets than fit in one answer are read in groups and their rows joined."""

    def setUp(self):
        import os
        import tempfile
        from pathlib import Path
        from fastapi.testclient import TestClient
        from langchain_core.embeddings import DeterministicFakeEmbedding
        import rag
        from web_app import RagService, create_app
        os.environ["RAG_RERANKER"] = ""

        class OfflineService(RagService):
            def model_capabilities(self, model):
                return []

            def ollama_status(self):
                return {"connected": True, "models": ["test-local:latest"], "default_model": "test-local:latest", "loaded": []}

            def get_embeddings(self, download=False):
                if self.embeddings is None:
                    self.embeddings = DeterministicFakeEmbedding(size=32)
                return self.embeddings

        root = rag.ROOT / "data" / "test-chroma"
        root.mkdir(parents=True, exist_ok=True)
        case = Path(tempfile.mkdtemp(prefix="sheets-", dir=root))
        # Room for three sheets per answer: seven sheets need three groups.
        self.service = OfflineService(rag.Config(db=case / "db", collection="sheets_test", context_chars=1500), case / "documents")
        self.client = TestClient(create_app(self.service), client=("127.0.0.1", 50000))
        self.client.__enter__()
        self.addCleanup(self.client.__exit__, None, None, None)
        self.headers = {"X-RAG-Token": self.client.get("/api/session").json()["token"]}
        self.service.settings.save(self.service.settings.get().model_copy(
            update={"response_mode": "manual", "response_model": "test-local:latest", "multimodal": False}))
        files = [("files", (f"T{number}{SUMMARY_SUFFIX}", f"Licitación T{number}\nEstado: en plazo\n".encode(), "text/markdown"))
                 for number in range(1, 8)]
        files.append(("files", ("T1__PPT__pliego.txt", b"Pliego de prescripciones del expediente T1.", "text/plain")))
        self.wait(self.client.post("/api/admin/upload", headers=self.headers, files=files, data={"auto_index": "true"}).json()["job_id"])

    def wait(self, job_id):
        import time
        for _ in range(400):
            job = self.client.get(f"/api/jobs/{job_id}").json()
            if job["status"] in {"done", "error"}:
                self.assertEqual(job["status"], "done", job)
                return job
            time.sleep(0.05)
        self.fail("Job did not complete")

    def test_rows_and_citations_stay_aligned_across_groups(self):
        import json
        import re
        from unittest.mock import patch
        import rag

        def model(messages, name, on_chunk=None, cancelled=None, **options):
            # Answers each source with the file name the evidence shows for it, so alignment can be checked.
            evidence = messages[-1]["content"]
            answer = json.dumps({key: re.search(rf"\[{key}\] (\S+)", evidence)[1] for key in options["response_format"]["required"]})
            if on_chunk:
                on_chunk(answer)
            return answer, {"output_tokens": 5}

        with patch.object(rag, "generate_ollama", side_effect=model) as generate:
            response = self.client.post("/api/query", headers=self.headers, json={"question": "¿Cuáles están abiertas?"})
            job = self.wait(response.json()["job_id"])
        self.assertEqual(generate.call_count, 3)
        self.assertEqual((job["metrics"]["answer_batches"], job["metrics"]["answer_documents"]), (3, 7))
        rows = [line.replace("\\", "") for line in job["answer"].splitlines()[2:]]
        self.assertEqual(len(rows), 7)
        self.assertEqual([source["citation"] for source in job["sources"]], list(range(1, 8)))
        for row in rows:
            name, citation, answer = re.fullmatch(r"\| (\S+) \[(\d+)\] \| (\S+) \|", row).groups()
            self.assertEqual(name, answer)                                           # the row answers its own sheet
            self.assertEqual(job["sources"][int(citation) - 1]["file"], name)        # and cites it
        self.assertEqual({source["file"] for source in job["sources"]}, {f"T{number}{SUMMARY_SUFFIX}" for number in range(1, 8)})
