"""Acceptance QA with real local HTTP/indexing, fake CPU vectors and a fake LLM.

The fixture corpus is isolated under data/test-chroma. Nothing touches Ollama/GPU.
No fixture data or fabricated progress is used by the production application.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parent


def fixture_server(port):
    os.environ["CUDA_VISIBLE_DEVICES"] = ""
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["OMP_NUM_THREADS"] = "2"
    from langchain_core.embeddings import DeterministicFakeEmbedding
    import rag
    from web_app import RagService, create_app
    import uvicorn

    class SlowVectors(DeterministicFakeEmbedding):
        def embed_documents(self, texts):
            time.sleep(.12)
            return super().embed_documents(texts)

    class FixtureService(RagService):
        def get_embeddings(self, download=False):
            if self.embeddings is None:
                self.embeddings = SlowVectors(size=32)
            return self.embeddings

        def ollama_status(self):
            return {"connected": True, "models": ["qa-local:latest"], "default_model": "qa-local:latest", "loaded": []}

    attempted_errors = set()

    def fake_generate(messages, model, on_chunk=None, cancelled=None):
        question = messages[-1]["content"]
        if "__QA_ERROR__" in question and "error" not in attempted_errors:
            attempted_errors.add("error")
            raise OSError("Synthetic Ollama failure for UI acceptance testing")
        pieces = ["La copia de seguridad se realiza ", "**cada viernes** [1]."]
        if "__QA_SLOW__" in question:
            pieces = ["Texto parcial. "] * 300
        for piece in pieces:
            time.sleep(.08)
            if cancelled and cancelled():
                raise InterruptedError("Cancelled QA generation")
            if on_chunk:
                on_chunk(piece)
        return "".join(pieces), {"model": model, "generation_tok_s": 80, "output_tokens": 18, "first_content_seconds": .08, "retrieval_seconds": .05}

    rag.generate_ollama = fake_generate
    sandbox = ROOT / "data" / "test-chroma"
    sandbox.mkdir(parents=True, exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix="redesign-browser-", dir=sandbox))
    service = FixtureService(rag.Config(db=case / "db", collection="redesign_qa"), case / "documents")
    uvicorn.run(create_app(service), host="127.0.0.1", port=port, access_log=False)


def run_browser(url):
    from playwright.sync_api import sync_playwright, expect
    reports = ROOT / "reports"
    errors, external, uploads = [], [], []
    checks = []

    def check(name):
        checks.append(name)
        print("OK:", name, flush=True)

    def dialog_close(page):
        if page.locator("#result-dialog").is_visible():
            page.keyboard.press("Escape")
            expect(page.locator("#result-dialog")).not_to_be_visible()

    def clear_queue(page):
        dialog_close(page)
        if page.locator("#clear-queue").is_enabled():
            page.locator("#clear-queue").click()
            expect(page.locator(".queue-row[data-state=indexed],.queue-row[data-state=cancelled]")).to_have_count(0)
        while page.locator("#upload-queue button[aria-label^='Quitar ']").count():
            count = page.locator("#upload-queue button[aria-label^='Quitar ']").count()
            page.locator("#upload-queue button[aria-label^='Quitar ']").first.click()
            expect(page.locator("#upload-queue button[aria-label^='Quitar ']")).to_have_count(count - 1)

    def file(name, content=b"La copia de seguridad se realiza cada viernes."):
        return {"name": name, "mimeType": "application/octet-stream", "buffer": content}

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="msedge", headless=True)
        context = browser.new_context(viewport={"width": 1440, "height": 1100}, reduced_motion="reduce", permissions=["clipboard-read", "clipboard-write"])
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        def requested(request):
            if request.url.startswith("http") and not request.url.startswith(url + "/"):
                external.append(request.url)
            if request.url.endswith("/api/admin/upload"):
                uploads.append(request.url)
        page.on("request", requested)
        page.goto(url, wait_until="networkidle")
        expect(page.locator("#stat-engine")).to_have_text("Ollama conectado")
        expect(page.locator("#ask-button")).to_be_disabled()
        expect(page.locator("#empty-banner")).to_be_visible()
        assert page.evaluate("document.fonts.check('16px \\\"Instrument Sans\\\"')")
        page.screenshot(path=str(reports / "redesign-consulta.png"), full_page=True)
        page.locator("#nav-admin").click()
        expect(page.locator("#breadcrumb-current")).to_have_text("Biblioteca")
        check("Empty states, local fonts and client-side navigation")

        page.locator("#file-input").set_input_files([
            file("manual.txt"), file("guia.md"), file("procedimiento.markdown"), file("notas.txt"),
            file("demasiado-grande.pdf", b"x" * (21 * 1024**2)), file("formato.docx"),
        ])
        expect(page.locator(".queue-row")).to_have_count(6)
        expect(page.locator(".queue-row[data-state=rejected]")).to_have_count(2)
        page.evaluate("window.percentages=[];new MutationObserver(()=>window.percentages.push(Number(document.getElementById('total-progress').getAttribute('aria-valuenow')))).observe(document.getElementById('total-progress'),{attributes:true,attributeFilter:['aria-valuenow']})")
        page.locator("#upload-button").click()
        expect(page.locator("#result-dialog")).to_be_visible()
        expect(page.locator("#result-dialog")).to_have_attribute("role", "alertdialog")
        expect(page.locator(".dialog-error-card")).to_have_count(2)
        expect(page.locator("#dialog-description")).to_contain_text("4 documentos")
        assert len(uploads) == 4, uploads
        uploaded_names = [document["name"] for document in page.request.get(url + "/api/status").json()["documents"]]
        assert sorted(uploaded_names) == sorted(["manual.txt", "guia.md", "procedimiento.markdown", "notas.txt"]), uploaded_names
        values = page.evaluate("window.percentages")
        assert values == sorted(values), values
        assert page.evaluate("document.activeElement.textContent") == "Cerrar"
        for _ in range(16):
            page.keyboard.press("Tab")
            assert page.evaluate("document.getElementById('result-dialog').contains(document.activeElement)")
        page.screenshot(path=str(reports / "redesign-modal-error.png"), full_page=True)
        dialog_close(page)
        assert page.evaluate("document.activeElement !== document.body")
        check("6-file batch: 4 real uploads, 2 client rejections, monotonic total, error modal and focus trap")

        clear_queue(page)
        page.locator("#auto-index").uncheck()
        page.locator("#file-input").set_input_files(file("sin-indexar.txt"))
        page.locator("#upload-button").click()
        expect(page.locator("#dialog-title")).to_have_text("Subida completada")
        dialog_close(page)
        expect(page.locator(".queue-row[data-state=unindexed]")).to_have_count(1)
        page.get_by_role("button", name="Indexar ahora", exact=True).click()
        expect(page.locator("#dialog-title")).to_have_text("Indexación completada")
        page.screenshot(path=str(reports / "redesign-modal-exito.png"), full_page=True)
        dialog_close(page)
        page.locator("#auto-index").check()
        page.locator("#prepare-button").click()
        expect(page.locator("#dialog-title")).to_have_text("Modelo de búsqueda listo")
        assert page.evaluate("document.activeElement.textContent").startswith("Hacer una consulta")
        dialog_close(page)
        assert page.locator("#prepare-button").evaluate("element=>element===document.activeElement")
        check("Upload without indexing, explicit indexing, preparation and success modal focus restoration")

        page.locator("#nav-query").click()
        page.locator("#question").fill("¿Cuándo se realiza la copia de seguridad?")
        page.locator("#question").press("Control+Enter")
        expect(page.locator("#answer-status")).to_have_text("Completada")
        expect(page.locator("#answer")).to_contain_text("cada viernes")
        expect(page.locator("#answer .citation")).to_have_count(1)
        page.locator("#answer .citation").click()
        expect(page.locator("#src-1")).to_be_visible()
        assert page.locator("#sources mark").count() > 0
        page.locator("#copy-answer").click()
        expect(page.locator("#toast")).to_contain_text("Respuesta copiada")
        page.locator("#search-only").check()
        page.locator("#ask-button").click()
        expect(page.locator("#answer")).to_contain_text("fragmentos más relevantes")
        page.locator("#search-only").uncheck()
        page.locator("#question").fill("__QA_ERROR__")
        page.locator("#ask-button").click()
        expect(page.locator("#query-error")).to_be_visible()
        expect(page.locator("#query-error")).to_contain_text("Ollama no responde")
        assert not page.locator("#result-dialog").is_visible()
        page.locator("#retry-query").click()
        expect(page.locator("#answer-status")).to_have_text("Completada")
        page.locator("#question").fill("__QA_SLOW__")
        page.locator("#ask-button").click()
        expect(page.locator("#ask-button")).to_contain_text("Detener")
        page.locator("#ask-button").click()
        expect(page.locator("#answer-status")).to_have_text("Cancelada")
        page.locator("#question").fill("¿Cuándo se realiza la copia de seguridad?")
        page.locator("#ask-button").click()
        expect(page.locator("#answer-status")).to_have_text("Completada")
        page.screenshot(path=str(reports / "redesign-consulta-con-respuesta.png"), full_page=True)
        check("Query shortcut, streamed text, citations, highlighting, copying, search-only, inline error/retry and stop")

        page.locator("#nav-admin").click()
        clear_queue(page)
        cdp = context.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 20, "downloadThroughput": 2_000_000, "uploadThroughput": 32 * 1024})
        payload = (b"A backup procedure is performed every Friday.\n" * 3000)[:128 * 1024]
        page.locator("#file-input").set_input_files([file(f"cola-{i}.txt", payload) for i in range(4)])
        page.locator("#upload-button").click()
        expect(page.locator(".queue-row[data-state=uploading]")).to_have_count(2)
        page.locator("#pause-queue").click()
        for _ in range(100):
            values = page.locator('.queue-row[data-state=uploading]').evaluate_all('(rows)=>rows.map(row=>Number(row.dataset.uploadProgress))')
            if any(0 < value < 100 for value in values):
                break
            page.wait_for_timeout(100)
        else:
            raise AssertionError('No intermediate native upload percentage observed')
        page.locator(".queue-row[data-state=uploading]").first.get_by_role("button", name=re.compile("^Cancelar ")).click()
        expect(page.locator(".queue-row[data-state=cancelled]")).to_have_count(1)
        expect(page.locator(".queue-row[data-state=queued]")).to_have_count(2)
        page.locator("#pause-queue").click()
        expect(page.locator("#result-dialog")).to_be_visible(timeout=60000)
        expect(page.locator("#dialog-description")).to_contain_text("3 documentos")
        dialog_close(page)
        cdp.send("Network.emulateNetworkConditions", {"offline": False, "latency": 0, "downloadThroughput": -1, "uploadThroughput": -1})
        check("Two concurrent native XHR uploads, real intermediate percentage, pause, abort and continuation")

        clear_queue(page)
        large = (b"Backup records and procedures remain local to this machine.\n" * 20000)[:1024*1024]
        page.locator("#file-input").set_input_files(file("recuperacion.txt", large))
        page.locator("#upload-button").click()
        expect(page.locator(".queue-row[data-state=indexing]")).to_have_count(1, timeout=30000)
        page.reload(wait_until="networkidle")
        expect(page.locator(".queue-row[data-state=indexed]")).to_have_count(1, timeout=60000)
        expect(page.locator("#result-dialog")).to_be_visible()
        dialog_close(page)
        check("Page reload recovers a genuine server-side index job")

        clear_queue(page)
        evil = '<img src=x onerror=alert(1)>.pdf'
        page.locator("#file-input").set_input_files(file(evil))
        expect(page.locator(".queue-name")).to_have_text(evil)
        page.locator("#upload-button").click()
        expect(page.locator("#result-dialog")).to_be_visible()
        expect(page.locator(".dialog-error-card>strong")).to_have_text(evil)
        assert page.locator("img").count() == 0
        dialog_close(page)
        clear_queue(page)
        page.locator("#auto-index").uncheck()
        page.locator("#file-input").set_input_files([file(f"extra-{i:02}.txt") for i in range(20)])
        page.locator("#upload-button").click()
        expect(page.locator("#result-dialog")).to_be_visible()
        dialog_close(page)
        expect(page.locator("#documents-table tr")).to_have_count(20)
        page.locator("#next-page").click()
        assert page.locator("#documents-table tr").count() < 20
        page.locator("#document-search").fill("extra-19")
        expect(page.locator("#documents-table tr")).to_have_count(1)
        expect(page.locator("#documents-table")).to_contain_text("extra-19")
        page.locator("#document-search").fill("")
        page.locator("[data-filter=indexed]").click()
        expect(page.locator("#documents-table .status-dot.moss")).not_to_have_count(0)
        page.locator("[data-filter=all]").click()
        check("Literal hostile filenames, 20-document pagination, debounced search and status filters")

        page.screenshot(path=str(reports / "redesign-biblioteca.png"), full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(reports / "redesign-biblioteca-mobile.png"), full_page=True)
        page.locator("#nav-query").click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator(".sidebar").bounding_box()["y"] == 0
        assert page.locator("#stat-chunks").bounding_box()["y"] > page.locator("#stat-docs").bounding_box()["y"]
        page.screenshot(path=str(reports / "redesign-consulta-mobile.png"), full_page=True)
        assert page.locator("#ask-button").evaluate("e=>getComputedStyle(e).transitionDuration") in {"0s", "0s, 0s"}
        assert not external, external
        assert not errors, errors
        check("390px layouts, stacked statistics, reduced motion and no external browser requests/errors")
        browser.close()
    report={"status":"OK","checks":checks,"external_requests":external,"page_errors":errors,
            "llm":"fake test adapter; no Ollama/GPU calls","embeddings":"deterministic test vectors; no model downloads",
            "progress":"real XHR byte events and real local Chroma batch completion","screenshots":"Fixture corpus; response metrics are test data, not benchmarks."}
    (reports/"redesign-qa.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False),flush=True)


def main():
    parser=argparse.ArgumentParser();parser.add_argument("--server",action="store_true");parser.add_argument("--port",type=int,default=8767);args=parser.parse_args()
    if args.server:
        fixture_server(args.port);return
    reports=ROOT/"reports";reports.mkdir(exist_ok=True)
    with (reports/"redesign-qa-server.log").open("w",encoding="utf-8") as log:
        process=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),"--server","--port",str(args.port)],cwd=ROOT,stdout=log,stderr=log,
            creationflags=getattr(subprocess,"CREATE_NO_WINDOW",0),env=dict(os.environ,PYTHONUTF8="1"))
        url=f"http://127.0.0.1:{args.port}"
        try:
            for _ in range(100):
                if process.poll() is not None: raise RuntimeError("QA server failed; see reports/redesign-qa-server.log")
                try:
                    with urllib.request.urlopen(url+"/api/session",timeout=1): break
                except OSError: time.sleep(.1)
            else: raise RuntimeError("QA server did not become ready")
            run_browser(url)
        finally:
            process.terminate();process.wait(timeout=15)


if __name__=="__main__":main()
