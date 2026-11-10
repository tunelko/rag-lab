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

ROOT = Path(__file__).resolve().parents[2]

MARKDOWN_RESPONSE = '''## Resumen técnico

**Equipo revisado**, con *evidencia documental* [1].

- Primer punto con una cita [1].
- Segundo punto con `código inline`.

| Componente | Cantidad |
| :--- | ---: |
| Puertos USB | 4 |
| Ranuras | 2 |

> Comprueba siempre la revisión de la placa.

```python
print("<script>literal</script>")
# [1] se conserva como texto dentro del código
```

- [x] Manual revisado
- [ ] Pendiente de contraste

[Documentación](https://example.com/docs)

[Enlace no permitido](javascript:alert(1))

![Imagen remota no cargada](https://example.invalid/tracking.png)

<script>window.__qaMarkdownXss = true</script>
<img src="https://example.invalid/pixel.png" onerror="window.__qaMarkdownXss = true">

Fuente desconocida [99].
'''


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
        def model_capabilities(self, model):
            return ["vision"] if model == "qa-vision:latest" else []

        def get_embeddings(self, download=False):
            if self.embeddings is None:
                self.embeddings = SlowVectors(size=32)
            return self.embeddings

        def ollama_status(self):
            return {"connected": True, "models": ["qa-local:latest", "qa-vision:latest"], "default_model": "qa-local:latest", "loaded": []}

    attempted_errors = set()

    def fake_generate(messages, model, on_chunk=None, cancelled=None):
        question = messages[-1]["content"]
        if "__QA_ERROR__" in question and "error" not in attempted_errors:
            attempted_errors.add("error")
            raise OSError("Synthetic Ollama failure for UI acceptance testing")
        pieces = ["La copia de seguridad se realiza ", "**cada viernes** [1]."]
        if "__QA_MARKDOWN__" in question:
            pieces = [MARKDOWN_RESPONSE[:170], MARKDOWN_RESPONSE[170:500], MARKDOWN_RESPONSE[500:]]
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
    real_retrieve = rag.retrieve
    def fixture_retrieve(config, question, embeddings, k):
        # Thousands of identical synthetic text vectors can swamp HNSW's candidates.
        # Scope routing UI tests to their real indexed fixtures, not ANN recall quality.
        fixture_file = ('qa-figure.png' if question.startswith("qa-figure\n") else
                        'manual.txt' if question == 'La copia de seguridad se realiza cada viernes.' else None)
        if fixture_file:
            rows = rag.open_store(config, embeddings).get(where={"file": fixture_file})
            assert rows['ids'], f'Missing indexed routing fixture: {fixture_file}'
            return [(rag.Document(page_content=text, metadata=metadata), 0.0)
                    for text, metadata in zip(rows["documents"][:k], rows["metadatas"][:k])]
        return real_retrieve(config, question, embeddings, k)
    rag.retrieve = fixture_retrieve
    import multimodal
    multimodal.describe_image = lambda *args, **kwargs: "Captura de prueba con 4 puertos USB."
    sandbox = ROOT / "data" / "test-chroma"
    sandbox.mkdir(parents=True, exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix="redesign-browser-", dir=sandbox))
    service = FixtureService(rag.Config(db=case / "db", collection="redesign_qa"), case / "documents")
    from settings_store import Preferences
    service.settings.save(Preferences(response_model="qa-local:latest"))
    fake_hardware = {"time": "2026-11-10T10:00:00+00:00", "logical_cpus": 16, "cpu_percent": 12.5,
        "ram_used_bytes": 8 * 1024**3, "ram_total_bytes": 32 * 1024**3, "ram_percent": 25,
        "processes": [{"name": "qa-runner", "pid": 1, "role": "ollama", "cpu_percent": 6.25, "ram_bytes": 2 * 1024**3}],
        "gpu": {"devices": [{"name": "GPU simulada para QA", "utilization_percent": 87.5, "memory_used_bytes": 8 * 1024**3, "memory_total_bytes": 10 * 1024**3, "temperature_c": 70, "power_w": 280}]}}
    from datetime import datetime, timedelta
    end=datetime.fromisoformat(fake_hardware["time"])
    fake_hardware["history"]=[{"time":(end-timedelta(seconds=(29-i)*2)).isoformat(),"cpu_percent":6+(i%5)*3,
        "gpu_percent":70+(i%4)*5,"ram_used_bytes":(7+i/29)*1024**3,"ram_total_bytes":32*1024**3,
        "vram_used_bytes":8*1024**3,"vram_total_bytes":10*1024**3} for i in range(30)]
    fake_hardware["history"][-1].update(cpu_percent=12.5,gpu_percent=87.5)
    service.hardware.sample = lambda: fake_hardware
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
        page.locator("#nav-settings").click()
        page.locator("#prepare-button").click()
        expect(page.locator("#toast")).to_contain_text("Modelo de búsqueda listo")
        page.locator("#toast button").click()
        expect(page.locator("#dialog-title")).to_have_text("Modelo de búsqueda listo")
        assert page.evaluate("document.activeElement.textContent").startswith("Hacer una consulta")
        dialog_close(page)
        assert page.locator("#prepare-button").evaluate("element=>element===document.activeElement")
        check("Upload without indexing, explicit indexing, preparation and success modal focus restoration")

        page.locator("#top-k [data-k='6']").click()
        page.locator("#save-settings").click()
        expect(page.locator("#settings-message")).to_have_text("Configuración guardada")
        page.reload(wait_until="networkidle")
        expect(page.locator("#top-k [data-k='6']")).to_have_attribute("aria-pressed", "true")
        expect(page.locator("#model-selection")).to_have_value("automatic")
        page.screenshot(path=str(reports / "redesign-configuracion.png"), full_page=True)
        page.locator("#nav-query").click()
        expect(page.locator("#model")).not_to_be_visible()
        expect(page.locator("#stat-docs")).not_to_be_visible()
        page.locator("#question").fill("¿Cuándo se realiza la copia de seguridad?")
        page.locator("#question").press("Control+Enter")
        expect(page.locator(".chat-turn").last).to_have_attribute("aria-busy", "false")
        expect(page.locator(".chat-turn .answer").last).to_contain_text("cada viernes")
        assert not page.locator(".chat-sources").last.evaluate("e=>e.open")
        page.locator(".chat-turn .citation").last.click()
        expect(page.locator(".chat-sources").last.locator(".source").first).to_be_visible()
        assert page.locator(".chat-sources mark").count() > 0
        page.locator(".chat-actions button[aria-label='Copiar respuesta']").last.click()
        expect(page.locator("#toast")).to_contain_text("Respuesta copiada")
        page.locator("#nav-settings").click()
        page.locator("#search-only").check()
        page.locator("#save-settings").click()
        expect(page.locator("#settings-message")).to_have_text("Configuración guardada")
        page.locator("#nav-query").click()
        page.locator("#question").fill("Busca copias de seguridad")
        page.locator("#ask-button").click()
        expect(page.locator(".chat-turn .answer").last).to_contain_text("Fragmentos recuperados")
        page.locator("#nav-settings").click()
        page.locator("#search-only").uncheck()
        page.locator("#save-settings").click()
        expect(page.locator("#settings-message")).to_have_text("Configuración guardada")
        page.locator("#nav-query").click()
        page.locator("#question").fill("__QA_ERROR__")
        page.locator("#ask-button").click()
        expect(page.locator(".chat-turn .chat-state").last).to_contain_text("Ollama no responde")
        assert not page.locator("#result-dialog").is_visible()
        page.locator(".chat-actions button").filter(has_text="Reintentar").last.click()
        expect(page.locator(".chat-turn .answer").last).to_contain_text("cada viernes")
        expect(page.locator("#ask-button")).to_contain_text("Enviar")
        page.locator("#question").fill("__QA_SLOW__")
        page.locator("#ask-button").click()
        expect(page.locator("#ask-button")).to_contain_text("Detener")
        page.locator("#ask-button").click()
        expect(page.locator(".chat-turn .chat-state").last).to_contain_text("Consulta detenida")
        page.locator("#question").fill("¿Cuándo se realiza la copia de seguridad?")
        page.locator("#ask-button").click()
        expect(page.locator(".chat-turn .answer").last).to_contain_text("cada viernes")
        expect(page.locator("#ask-button")).to_contain_text("Enviar")
        page.screenshot(path=str(reports / "redesign-consulta-con-respuesta.png"), full_page=True)
        page.reload(wait_until="networkidle")
        expect(page.locator(".chat-turn")).to_have_count(5)
        page.locator("#nav-statistics").click()
        expect(page.locator("#query-statistics")).to_contain_text("80,00")
        expect(page.locator("#hardware-cpu")).to_have_text("12,5 %")
        expect(page.locator("#hardware-gpu")).to_have_text("87,5 %")
        expect(page.locator("#hardware-vram")).to_have_text("8,0 / 10,0 GiB")
        expect(page.locator("#hardware-processes")).to_contain_text("qa-runner")
        expect(page.locator("#chart-cpu")).to_have_attribute("data-samples","30")
        expect(page.locator("#chart-cpu .resource-line")).to_have_count(1)
        assert 'NaN' not in page.locator("#chart-cpu .resource-line").get_attribute("d")
        page.locator("#chart-cpu").focus();page.keyboard.press("ArrowLeft")
        expect(page.locator("#tip-cpu")).to_be_visible()
        expect(page.locator("#tip-cpu")).to_contain_text("CPU")
        page.locator("#hardware-range [data-seconds='300']").click()
        expect(page.locator("#chart-cpu-range")).to_have_text("Hace 5 min")
        page.locator("#hardware-pause").click()
        expect(page.locator("#hardware-status")).to_contain_text("pausada")
        page.locator("#hardware-pause").click()
        page.locator("#hardware-range [data-seconds='60']").click()
        assert page.locator(".resource-card").first.bounding_box()["height"] < 300
        page.get_by_role("button",name="Maximizar gráfica de GPU",exact=True).click()
        expect(page.locator("#resource-dialog")).to_be_visible()
        expect(page.locator("#resource-dialog-title")).to_have_text("Rendimiento de GPU")
        assert page.locator("#resource-dialog #chart-gpu").bounding_box()["height"] > 200
        page.keyboard.press("Escape")
        expect(page.locator("#resource-dialog")).not_to_be_visible()
        expect(page.get_by_role("button",name="Maximizar gráfica de GPU",exact=True)).to_be_focused()
        page.locator("#chart-gpu").hover()
        page.screenshot(path=str(reports / "redesign-graficas-rendimiento.png"),full_page=True)
        check("Real-sample resource charts: fixed scales, history ranges, keyboard tooltips and pause/resume")
        page.screenshot(path=str(reports / "redesign-estadisticas.png"), full_page=True)
        check("Clean chat, persisted configuration/history, collapsed citations, copy, search-only, retry, stop and separate real metrics")

        page.locator("#nav-query").click()
        page.locator("#question").fill("__QA_MARKDOWN__")
        page.locator("#ask-button").click()
        answer=page.locator(".chat-turn .answer").last
        expect(answer.locator("h3")).to_have_text("Resumen técnico")
        expect(page.locator("#ask-button")).to_contain_text("Enviar")
        expect(answer.locator("table")).to_have_count(1)
        expect(answer.locator("blockquote")).to_contain_text("revisión de la placa")
        expect(answer.locator(".markdown-task input:disabled")).to_have_count(2)
        expect(answer.locator("pre code")).to_contain_text('<script>literal</script>')
        expect(answer.locator("pre .citation")).to_have_count(0)
        assert answer.locator(".citation").count()>=2
        expect(answer.locator("script,img,iframe,svg,style,form")).to_have_count(0)
        assert not page.evaluate("window.__qaMarkdownXss")
        assert answer.locator("a[href^='javascript:']").count()==0
        expect(answer).to_contain_text("[99]")
        external_link=answer.get_by_role("link",name="Documentación",exact=True)
        expect(external_link).to_have_attribute("rel","noopener noreferrer")
        answer.get_by_role("button",name="Copiar bloque de código",exact=True).click()
        assert '<script>literal</script>' in page.evaluate("navigator.clipboard.readText()")
        answer.locator(".citation").first.click()
        expect(page.locator(".chat-sources").last.locator(".source").first).to_be_visible()
        page.locator(".chat-sources").last.locator("summary").click()
        answer.evaluate("e=>{const thread=e.closest('.chat-thread');thread.scrollTop=e.offsetTop-thread.offsetTop;}")
        page.screenshot(path=str(reports / "redesign-markdown.png"),full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        answer.evaluate("e=>{const thread=e.closest('.chat-thread');thread.scrollTop=e.offsetTop-thread.offsetTop;}")
        page.screenshot(path=str(reports / "redesign-markdown-mobile.png"),full_page=True)
        page.set_viewport_size({"width":1440,"height":1100})
        check("Local Markdown: streamed headings/lists/tables/code, code copying, source citations, disabled task lists and blocked HTML/remote images")

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
        page.locator("#file-input").set_input_files([file(f"cancelar-todo-{index}.txt", large) for index in range(3)])
        page.locator("#upload-button").click()
        expect(page.locator(".queue-row[data-state=uploading],.queue-row[data-state=uploaded],.queue-row[data-state=fragmenting],.queue-row[data-state=indexing]")).not_to_have_count(0)
        expect(page.locator("#cancel-pending")).to_have_text("Cancelar procesamiento")
        expect(page.locator("#cancel-pending")).to_be_enabled()
        page.locator("#cancel-pending").click()
        for _ in range(200):
            jobs = page.request.get(url + "/api/jobs?active=1").json()["jobs"]
            if not any(job["kind"] == "index" for job in jobs):
                break
            page.wait_for_timeout(100)
        else:
            raise AssertionError("Cancellation left pending indexing jobs")
        expect(page.locator(".queue-row[data-state=cancelled]")).not_to_have_count(0)
        expect(page.locator(".queue-row[data-state=cancelled] .status-pill", has_text="Cancelando")).to_have_count(0)
        expect(page.locator(".queue-row[data-state=uploading],.queue-row[data-state=uploaded],.queue-row[data-state=fragmenting],.queue-row[data-state=indexing]")).to_have_count(0)
        expect(page.locator("#cancel-pending")).to_be_disabled()
        dialog_close(page)
        check("Visible cancel-processing button stops uploads and queued parsing jobs while preserving completed index writes")

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

        page.locator("#nav-settings").click()
        page.locator("#multimodal").check()
        page.locator("#vision-model").select_option("qa-vision:latest")
        page.locator("#model").select_option("qa-local:latest")
        page.locator("#model-selection").select_option("automatic")
        page.locator("#save-settings").click()
        expect(page.locator("#settings-message")).to_have_text("Configuración guardada")
        page.locator("#nav-admin").click()
        clear_queue(page)
        page.locator("#auto-index").check()
        import io
        from PIL import Image
        image_buffer = io.BytesIO()
        Image.new("RGB", (120, 90), "white").save(image_buffer, format="PNG")
        page.locator("#file-input").set_input_files(file("qa-figure.png", image_buffer.getvalue()))
        page.locator("#upload-button").click()
        expect(page.locator("#dialog-title")).to_have_text("Indexación completada")
        dialog_close(page)
        page.locator("#nav-query").click()
        page.locator("#question").fill("qa-figure\nDescripción visual (puede contener errores de OCR):\nCaptura de prueba con 4 puertos USB.")
        previous_turns = page.locator('.chat-turn').count()
        page.locator("#ask-button").click()
        expect(page.locator('.chat-turn')).to_have_count(previous_turns + 1)
        expect(page.locator('.chat-turn').last).to_have_attribute('aria-busy', 'false')
        expect(page.locator("#ask-button")).to_contain_text("Enviar")
        source_details = page.locator(".chat-sources").last
        assert not source_details.evaluate("e=>e.open")
        source_details.locator("summary").click()
        expect(source_details.locator(".source-figure").first).to_be_visible()
        assert source_details.locator(".source-figure").first.evaluate("e=>e.complete&&e.naturalWidth>0")
        page.screenshot(path=str(reports / "redesign-fuentes-multimodales.png"), full_page=True)
        page.locator("#nav-statistics").click()
        expect(page.locator("#query-statistics")).to_contain_text("1 imágenes")
        visual_metrics = page.request.get(url + "/api/statistics").json()["recent"][0]["metrics"]
        assert visual_metrics["model"] == "qa-vision:latest", visual_metrics
        assert visual_metrics["routing_mode"] == "automatic"
        expect(page.locator("#query-statistics")).to_contain_text("Automática")
        page.locator("#nav-query").click()
        page.locator("#question").fill("La copia de seguridad se realiza cada viernes.")
        previous_turns = page.locator('.chat-turn').count()
        page.locator("#ask-button").click()
        expect(page.locator('.chat-turn')).to_have_count(previous_turns + 1)
        expect(page.locator('.chat-turn').last).to_have_attribute('aria-busy', 'false')
        expect(page.locator("#ask-button")).to_contain_text("Enviar")
        text_metrics = page.request.get(url + "/api/statistics").json()["recent"][0]["metrics"]
        assert text_metrics["model"] == "qa-local:latest", text_metrics
        assert text_metrics["images_used"] == 0
        check("Image upload, collapsed thumbnails and automatic vision/text selection with visible routing reasons using fake models")

        page.locator("#nav-console").click()
        expect(page.locator("#console-output")).to_contain_text("chunks")
        page.locator("#console-pause").click()
        expect(page.locator("#console-status")).to_contain_text("pausada")
        page.locator("#console-search").fill("chunks")
        assert page.locator(".console-line").count() > 0
        assert all("chunks" in text for text in page.locator(".console-message").all_text_contents())
        page.locator("#console-copy").click()
        with page.expect_download() as download:
            page.locator("#console-download").click()
        assert download.value.suggested_filename == "rag-servidor.log"
        page.locator("#console-clear").click()
        expect(page.locator(".console-line")).to_have_count(0)
        page.locator("#console-search").fill("")
        page.locator("#console-pause").click()
        page.screenshot(path=str(reports / "redesign-consola.png"), full_page=True)
        page.reload(wait_until="networkidle")
        expect(page.locator("#console-output")).to_contain_text("chunks")
        check("Read-only live console, safe log rendering, pause, filtering, copy, download, local clearing and direct route reload")
        page.locator("#nav-admin").click()

        page.screenshot(path=str(reports / "redesign-biblioteca.png"), full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(reports / "redesign-biblioteca-mobile.png"), full_page=True)
        page.locator("#nav-query").click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator(".sidebar").bounding_box()["y"] == 0
        expect(page.locator("#stat-docs")).not_to_be_visible()
        page.locator("#nav-statistics").click()
        # Read both positions in the same layout; telemetry can resize cards between calls.
        page.wait_for_function("document.querySelector('#stat-chunks').getBoundingClientRect().top > document.querySelector('#stat-docs').getBoundingClientRect().top")
        page.locator("#nav-query").click()
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
        process=subprocess.Popen([sys.executable,"-m","tests.browser.qa_redesign","--server","--port",str(args.port)],cwd=ROOT,stdout=log,stderr=log,
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
