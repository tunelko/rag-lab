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
    def test_remote_model_metadata_is_excluded_from_response_choices(self):
        import io,json
        tags={'models':[{'name':'local:latest'},{'name':'cloud:CLOUD'},
                        {'name':'hidden:latest','remote_model':'upstream'},
                        {'name':'other:latest','remote_host':'https://example.invalid'}]}
        with patch('web_app.open_ollama',side_effect=[io.BytesIO(json.dumps(tags).encode()),io.BytesIO(b'{"models":[]}')]):
            state=RagService.ollama_status(self.client.app.state.service)
        self.assertTrue(state['connected'])
        self.assertEqual(state['models'],['local:latest'])

    def test_queued_query_keeps_preferences_from_submission(self):
        import threading
        service=self.client.app.state.service
        upload=self.client.post('/api/admin/upload',headers=self.headers,
            files=[('files',('manual.txt',b'Backups run every Friday.','text/plain'))],data={'auto_index':'true'})
        self.wait(upload.json()['job_id'])
        original=service.settings.get().model_copy(update={'response_mode':'manual','response_model':'test-local:latest'})
        service.settings.save(original)
        release=threading.Event();entered=threading.Event()
        def hold(job_id):
            entered.set();release.wait(5);return {}
        service.start('prepare',hold);self.assertTrue(entered.wait(2))
        try:
            with patch.object(rag,'generate_ollama',return_value=('Friday [1].',{'output_tokens':5})) as generate:
                response=self.client.post('/api/query',headers=self.headers,json={'question':'When are backups?'})
                service.settings.save(original.model_copy(update={'response_model':'not-installed:latest'}))
                release.set();self.wait(response.json()['job_id'])
                self.assertEqual(generate.call_args.args[1],'test-local:latest')
        finally:release.set()

    def test_multi_file_upload_indexes_only_the_uploaded_batch(self):
        service=self.client.app.state.service
        first=self.client.post('/api/admin/upload',headers=self.headers,
            files=[('files',('existing.txt',b'Existing document remains untouched.','text/plain'))],data={'auto_index':'false'})
        self.assertEqual(first.status_code,200)
        result=self.client.post('/api/admin/upload',headers=self.headers,
            files=[('files',('new-a.txt',b'New document A.','text/plain')),('files',('new-b.txt',b'New document B.','text/plain'))],data={'auto_index':'true'})
        self.wait(result.json()['job_id'])
        documents={item['name']:item for item in service.list_documents()}
        self.assertFalse(documents['existing.txt']['indexed'])
        self.assertTrue(documents['new-a.txt']['indexed'])
        self.assertTrue(documents['new-b.txt']['indexed'])

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

    def test_explicit_lan_host_keeps_host_and_origin_validation(self):
        with patch.dict(os.environ,{'RAG_WEB_HOST':'192.168.1.111'}):
            app=create_app(self.client.app.state.service)
        client=TestClient(app)
        host={'Host':'192.168.1.111:8765'}
        token=client.get('/api/session',headers=host).json()['token']
        self.assertEqual(client.get('/api/status',headers=host).status_code,200)
        self.assertEqual(client.get('/api/status',headers=host).json()['server'],{'bind_host':'192.168.1.111','access':'lan'})
        response=client.post('/api/query',headers={**host,'X-RAG-Token':token,'Origin':'http://192.168.1.112:8765'},json={'question':'example'})
        self.assertEqual(response.status_code,403)
        self.assertEqual(client.get('/api/status',headers={'Host':'192.168.1.112:8765'}).status_code,400)

    def test_chroma_server_collection_endpoints_are_not_exposed(self):
        endpoint = '/api/v2/tenants/default_tenant/databases/default_database/collections'
        for method, path in (('GET', endpoint), ('POST', endpoint), ('PATCH', endpoint + '/unused-id')):
            response = self.client.request(method, path, headers=self.headers)
            self.assertEqual(response.status_code, 404, response.text)

    def test_response_language_is_per_query_and_validated(self):
        uploaded = self.client.post('/api/admin/upload', headers=self.headers,
            files=[('files', ('manual.txt', b'Backups run every Friday.', 'text/plain'))],
            data={'auto_index': 'true'})
        self.wait(uploaded.json()['job_id'])
        def generate(messages, model, **kwargs):
            self.assertIn('Answer in English', messages[0]['content'])
            self.assertIn('Backups run every Friday.', messages[-1]['content'])
            return 'Every Friday [1].', {'output_tokens': 5}
        with patch.object(rag, 'generate_ollama', side_effect=generate):
            result = self.client.post('/api/query', headers=self.headers,
                json={'question': 'When are backups?', 'language': 'en'})
            answer = self.wait(result.json()['job_id'])
        self.assertEqual(answer['answer'], 'Every Friday [1].')
        self.assertEqual(answer['metrics']['language'], 'en')
        self.assertEqual(answer['sources'][0]['file'], 'manual.txt')
        response = self.client.post('/api/query', headers=self.headers,
            json={'question': 'When are backups?', 'language': 'fr'})
        self.assertEqual(response.status_code, 422)

    def test_reasoning_model_empty_answer_keeps_metrics_and_reports_a_specific_error(self):
        uploaded=self.client.post('/api/admin/upload',headers=self.headers,
            files=[('files',('manual.txt',b'Backups run every Friday.','text/plain'))],data={'auto_index':'true'})
        self.wait(uploaded.json()['job_id'])
        with patch.object(rag,'generate_ollama',return_value=('',{'output_tokens':512,'done_reason':'length'})):
            response=self.client.post('/api/query',headers=self.headers,json={'question':'When are backups?'})
            for _ in range(100):
                job=self.client.get('/api/jobs/'+response.json()['job_id']).json()
                if job['status']=='error':break
                time.sleep(.02)
        self.assertEqual(job['status'],'error')
        self.assertEqual(job['error_info']['code'],'EMPTY_RESPONSE')
        self.assertEqual(job['metrics']['output_tokens'],512)
        self.assertEqual(job['metrics']['done_reason'],'length')
        self.assertIn('512',job['error_info']['detail'])

    def test_reasoning_model_receives_a_separate_output_budget(self):
        uploaded=self.client.post('/api/admin/upload',headers=self.headers,
            files=[('files',('manual.txt',b'Backups run every Friday.','text/plain'))],data={'auto_index':'true'})
        self.wait(uploaded.json()['job_id'])
        with patch.object(self.client.app.state.service,'model_capabilities',return_value=['thinking']), \
             patch.object(rag,'generate_ollama',return_value=('Friday [1].',{'output_tokens':20})) as generate, \
             patch.dict(os.environ,{'RAG_MAX_NEW_TOKENS':'512','RAG_REASONING_MAX_NEW_TOKENS':'2048'}):
            response=self.client.post('/api/query',headers=self.headers,json={'question':'When are backups?'})
            self.wait(response.json()['job_id'])
        self.assertEqual(generate.call_args.kwargs['max_output_tokens'],2048)

    def test_query_image_reaches_vision_model_without_becoming_a_document(self):
        import base64
        from tests.unit.test_query_images import picture
        service=self.client.app.state.service
        upload=self.client.post('/api/admin/upload',headers=self.headers,files=[('files',('manual.txt',b'The connector supplies 12 volts.','text/plain'))],data={'auto_index':'true'})
        self.wait(upload.json()['job_id'])
        encoded='data:image/png;base64,'+base64.b64encode(picture()).decode()
        with patch.object(service,'model_capabilities',return_value=['vision']):
            settings=service.settings.get().model_copy(update={'vision_model':'test-local:latest','max_images':1})
            service.settings.save(settings)
            def generate(messages,model,**kwargs):
                self.assertEqual(model,'test-local:latest')
                self.assertEqual(len(messages[-1]['images']),1)
                self.assertIn('aportada por el usuario',messages[-1]['content'])
                return '12 volts [1]',{'output_tokens':5}
            with patch.object(rag,'generate_ollama',side_effect=generate):
                request=self.client.post('/api/query',headers=self.headers,json={'question':'What connector is shown?','images':[encoded]})
                self.assertEqual(request.status_code,202,request.text);job=self.wait(request.json()['job_id'])
        self.assertEqual(job['metrics']['images_used'],1)
        self.assertEqual(job['metrics']['visual_mode'],'images')
        self.assertEqual(self.client.get('/api/status').json()['document_count'],1)
        archive=self.client.get('/api/statistics').json()['recent']
        self.assertNotIn(encoded,str(archive))
        for body in [{'question':'connector','images':[encoded,encoded]}, {'question':'connector','images':[encoded],'search_only':True}]:
            self.assertEqual(self.client.post('/api/query',headers=self.headers,json=body).status_code,422)

    def test_query_trace_filter_cannot_include_other_queries(self):
        from datetime import datetime,timezone
        service=self.client.app.state.service
        for identifier in ['first','second']:
            service.statistics_archive.save_job({'id':identifier,'kind':'query','created_at':datetime.now(timezone.utc).isoformat(),'status':'done','question':identifier,'metrics':{}})
        result=self.client.get('/api/statistics?query_id=second').json()
        self.assertEqual(result['queries'],1)
        self.assertEqual([row['id'] for row in result['recent']],['second'])
        self.assertEqual(self.client.get('/api/statistics',params={'query_id':"' OR 1=1--"}).json()['queries'],0)

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
