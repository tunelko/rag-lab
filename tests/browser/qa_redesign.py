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


def launch_browser(playwright):
    """Use bundled Chromium in CI and Edge by default for local Windows QA."""
    channel = os.getenv("RAG_BROWSER_CHANNEL", "msedge").strip().lower()
    if channel in {"", "chromium", "bundled"}:
        return playwright.chromium.launch(headless=True)
    return playwright.chromium.launch(channel=channel, headless=True)

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
    os.environ["RAG_RERANKER"] = ""  # The fixture must not load the deployment's model.
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
        def index(self, job_id, only_file=None, **kwargs):
            if only_file and Path(only_file).name.startswith('cancelar-todo-'):
                # Hold this fixture before index writes so cancellation tests do
                # not race with a runner that finishes parsing/writing sooner.
                self.update(job_id,stage='parse',phase='QA: esperando cancelación antes de escribir')
                if not self.cancels[job_id].wait(60):
                    raise AssertionError('QA did not request cancellation of the held index job')
                self.check_cancel(job_id)
            return super().index(job_id,only_file,**kwargs)

        def model_capabilities(self, model):
            return ["vision"] if model == "qa-vision:latest" else []

        def get_embeddings(self, download=False):
            if self.embeddings is None:
                self.embeddings = SlowVectors(size=32)
            return self.embeddings

        def ollama_status(self):
            return {"connected": True, "models": ["qa-local:latest", "qa-vision:latest"], "default_model": "qa-local:latest", "loaded": []}

    attempted_errors = set()

    def fake_generate(messages, model, on_chunk=None, cancelled=None, context_length=None, max_output_tokens=None, response_format=None):
        question = messages[-1]["content"]
        if "__QA_ERROR__" in question and "error" not in attempted_errors:
            attempted_errors.add("error")
            raise OSError("Synthetic Ollama failure for UI acceptance testing")
        pieces = ["La copia de seguridad se realiza ", "**cada viernes** [1]."]
        if response_format:
            payload = json.dumps({key: 'Datos disponibles en esta fuente.' for key in response_format['required']})
            pieces = [payload[start:start+40] for start in range(0,len(payload),40)]
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
    import retrieval_pipeline
    real_retrieve = retrieval_pipeline.retrieve
    def fixture_retrieve(config, question, embeddings, k, *, files=None, visual=False,
                         reranker=None, rerank_window=50):
        # Thousands of identical synthetic text vectors can swamp HNSW's candidates.
        # Scope routing UI tests to their real indexed fixtures, not ANN recall quality.
        fixture_file = ('qa-figure.png' if question.startswith("qa-figure\n") else
                        'manual.txt' if 'copia de seguridad' in question.casefold() else None)
        if fixture_file and (files is None or fixture_file in files):
            if fixture_file == 'manual.txt':
                path = service.documents / fixture_file
                hits = [(rag.Document(
                    page_content='La copia de seguridad se realiza cada viernes.',
                    metadata={'file': fixture_file, 'source': str(path), 'page': 1, 'item_type': 'text'}), 0.0)]
                return hits, {"strategy": "qa-fixture", "candidates": 1, "reranked": False}
            rows = rag.open_store(config, embeddings).get(where={"file": fixture_file})
            assert rows['ids'], f'Missing indexed routing fixture: {fixture_file}'
            hits = [(rag.Document(page_content=text, metadata=metadata), 0.0)
                    for text, metadata in zip(rows["documents"][:k], rows["metadatas"][:k])]
            return hits, {"strategy": "qa-fixture", "candidates": len(hits), "reranked": False}
        return real_retrieve(config, question, embeddings, k, files=files, visual=visual,
                             reranker=reranker, rerank_window=rerank_window)
    retrieval_pipeline.retrieve = fixture_retrieve
    import multimodal
    multimodal.describe_image = lambda *args, **kwargs: "Captura de prueba con 4 puertos USB."
    sandbox = ROOT / "data" / "test-chroma"
    sandbox.mkdir(parents=True, exist_ok=True)
    case = Path(tempfile.mkdtemp(prefix="redesign-browser-", dir=sandbox))
    service = FixtureService(rag.Config(db=case / "db", collection="redesign_qa"), case / "documents")
    from settings_store import Preferences
    service.settings.save(Preferences(response_model="qa-local:latest"))
    from datetime import datetime, timedelta, timezone
    fake_hardware = {"time": datetime.now(timezone.utc).replace(microsecond=0).isoformat(), "logical_cpus": 16, "cpu_percent": 12.5,
        "ram_used_bytes": 8 * 1024**3, "ram_total_bytes": 32 * 1024**3, "ram_percent": 25,
        "processes": [{"name": "qa-runner", "pid": 1, "role": "ollama", "cpu_percent": 6.25, "ram_bytes": 2 * 1024**3}],
        "gpu": {"devices": [{"name": "GPU simulada para QA", "utilization_percent": 87.5, "memory_used_bytes": 8 * 1024**3, "memory_total_bytes": 10 * 1024**3, "temperature_c": 70, "power_w": 280}]}}
    end=datetime.fromisoformat(fake_hardware["time"])
    fake_hardware["history"]=[{"time":(end-timedelta(seconds=(29-i)*2)).isoformat(),"cpu_percent":6+(i%5)*3,
        "gpu_percent":70+(i%4)*5,"ram_used_bytes":(7+i/29)*1024**3,"ram_total_bytes":32*1024**3,
        "vram_used_bytes":8*1024**3,"vram_total_bytes":10*1024**3} for i in range(30)]
    fake_hardware["history"][-1].update(cpu_percent=12.5,gpu_percent=87.5)
    for days,cpu in ((6,25),(2,45)):
        service.statistics_archive.save_sample({**fake_hardware['history'][0],
            'time':(end-timedelta(days=days)).isoformat(),'cpu_percent':cpu},force=True)
    service.hardware.sample = lambda: fake_hardware
    uvicorn.run(create_app(service), host="127.0.0.1", port=port, access_log=False)


def run_browser(url):
    from playwright.sync_api import sync_playwright, expect
    reports = ROOT / "reports"
    errors, external, uploads, queries = [], [], [], []
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
        browser = launch_browser(p)
        context = browser.new_context(locale="es-ES", viewport={"width": 1440, "height": 1100}, reduced_motion="reduce", permissions=["clipboard-read", "clipboard-write"])
        context.add_init_script("Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true})")
        page = context.new_page()
        page.set_default_timeout(30000)
        page.on("pageerror", lambda error: errors.append(str(error)))

        def requested(request):
            if request.url.startswith("http") and not request.url.startswith(url + "/"):
                external.append(request.url)
            if request.url.endswith("/api/admin/upload"):
                uploads.append(request.url)
            if request.url.endswith('/api/query') and request.method=='POST':
                queries.append(request.url)
        page.on("request", requested)
        page.goto(url, wait_until="networkidle")
        english = browser.new_context(locale='en-US', viewport={'width':390,'height':844})
        english_page = english.new_page();locale_mutations=[]
        english_page.on('request',lambda request:locale_mutations.append(request.url) if request.method in {'POST','PUT','DELETE','PATCH'} else None)
        english_page.goto(url,wait_until='networkidle')
        expect(english_page.locator('html')).to_have_attribute('lang','en')
        expect(english_page.locator('#nav-admin [data-i18n]')).to_have_text('Library')
        expect(english_page.locator('#language-flag')).to_have_attribute('src','/assets/icons/flags/en.svg')
        english_page.locator('#question').fill('Keep this unsent question <script>literal</script>')
        english_page.locator('#nav-settings').click();english_page.locator("#top-k [data-k='8']").click()
        english_page.locator('[data-response-mode=manual]').click()
        expect(english_page.locator('#settings-message')).to_have_text('Saved')
        before_locale=len(locale_mutations)
        english_page.locator('#language-selection').select_option('es')
        expect(english_page.locator('#nav-admin [data-i18n]')).to_have_text('Biblioteca')
        expect(english_page.locator('#language-flag')).to_have_attribute('src','/assets/icons/flags/es.svg')
        expect(english_page.locator("#top-k [data-k='8']")).to_have_attribute('aria-checked','true')
        expect(english_page.locator('#model-selection')).to_have_value('manual')
        expect(english_page.locator('#question')).to_have_value('Keep this unsent question <script>literal</script>')
        english_page.locator('#language-selection').select_option('en')
        assert len(locale_mutations)==before_locale
        assert english_page.evaluate('document.documentElement.scrollWidth<=innerWidth+1')
        english_page.screenshot(path=str(reports/'redesign-english-mobile.png'),full_page=True)
        english_page.locator("#top-k [data-k='4']").click();english_page.locator('[data-response-mode=automatic]').click()
        expect(english_page.locator('#settings-message')).to_have_text('Saved')
        before_locale=len(locale_mutations)
        english_page.reload(wait_until='networkidle')
        expect(english_page.locator('html')).to_have_attribute('lang','en')
        expect(english_page.locator('#language-selection')).to_have_value('en')
        english_page.locator('#language-selection').select_option('auto')
        expect(english_page.locator('html')).to_have_attribute('lang','en')
        for control,heading in [('#nav-statistics','Statistics'),('#nav-console','Console'),('#nav-admin','Library')]:
            english_page.locator(control).click();expect(english_page.locator('h1:visible')).to_have_text(heading)
            assert english_page.evaluate('document.documentElement.scrollWidth<=innerWidth+1'),heading
        assert len(locale_mutations)==before_locale
        english.close()
        fallback=browser.new_context(locale='en-US');fallback_page=fallback.new_page()
        fallback_page.route('**/assets/locales/en.json',lambda route:route.fulfill(status=404,body='unavailable'))
        fallback_page.goto(url,wait_until='networkidle');expect(fallback_page.locator('html')).to_have_attribute('lang','es')
        expect(fallback_page.locator('#nav-admin [data-i18n]')).to_have_text('Biblioteca');fallback.close()
        check('Local i18n and flags: detection, live selection, persistence, autosaved settings and no language-induced mutation')
        # Exercise real source rendering, including an older saved conversation
        # whose PDF URL did not contain a page fragment.
        page_locations = page.evaluate('''async () => {
            const {renderSources} = await import('/assets/ui.js');
            const container = document.createElement('div');
            renderSources([
                {citation: 1, file: 'manual.pdf', page: 164, page_label: '8', url: '/api/source?file=manual.pdf'},
                {citation: 2, file: 'manual.pdf', page: 169, page_label: '36', url: '/api/source?file=manual.pdf#page=36'},
                {citation: 3, file: 'manual.pdf', page: 12, url: '/api/source?file=manual.pdf'},
                {citation: 4, file: 'notes.txt', page: 0, url: '/api/source?file=notes.txt'},
            ], '', container, 'qa-page');
            return [...container.querySelectorAll('.source')].map(card => ({
                location: card.querySelector('.source-meta > span').textContent,
                href: card.querySelector('.source-link').getAttribute('href'),
                target: card.querySelector('.source-link').target,
            }));
        }''')
        assert 'pág. PDF 164 · numeración del documento: 8' == page_locations[0]['location']
        assert 'pág. PDF 169 · numeración del documento: 36' == page_locations[1]['location']
        assert page_locations[0]['href'].endswith('#page=164')
        assert page_locations[1]['href'].endswith('#page=169')
        assert page_locations[2]['location'] == 'pág. PDF 12'
        assert page_locations[2]['href'].endswith('#page=12')
        assert page_locations[3]['href'] == '/api/source?file=notes.txt'
        assert all(item['target'] == '_blank' for item in page_locations)
        check('PDF physical page navigation and independent document numbering')
        expect(page.locator("#system-ollama")).to_have_text("Conectado")
        expect(page.locator("#ask-button")).to_be_disabled()
        expect(page.locator("#empty-banner")).to_be_visible()
        assert page.evaluate("document.fonts.check('16px \\\"Orbitron\\\"')")
        page.screenshot(path=str(reports / "redesign-consulta.png"), full_page=True)
        page.locator("#nav-admin").click()
        expect(page.locator("#admin-view h1")).to_have_text("Biblioteca")
        check("Empty states, local fonts and client-side navigation")

        for width in [1440,1280,1024,800,640,390,320]:
            page.set_viewport_size({'width':width,'height':1000})
            assert page.evaluate('()=>document.documentElement.scrollWidth<=innerWidth+1'),(width,page.evaluate('()=>[...document.querySelectorAll("body *")].filter(e=>e.offsetWidth&&e.getBoundingClientRect().right>innerWidth+1).slice(0,16).map(e=>[e.tagName,e.id,e.className,e.getBoundingClientRect().right])'))
            assert page.evaluate('''()=>{
                const items=[...document.querySelector('.topbar__bar').children].filter(e=>e.offsetWidth&&e.offsetHeight).map(e=>e.getBoundingClientRect());
                return items.every((a,i)=>items.every((b,j)=>i===j||a.right<=b.left+1||b.right<=a.left+1||a.bottom<=b.top+1||b.bottom<=a.top+1));
            }'''),f'Overlapping header at {width}px'
            if width>=1024:
                assert page.locator('.topbar__bar').bounding_box()['height']==52
                assert page.locator('.topnav').evaluate('e=>e.scrollWidth<=e.clientWidth+1'),width
        page.set_viewport_size({'width':1440,'height':1100})
        page.locator('#command-open').focus();page.keyboard.press('Control+k')
        expect(page.locator('#command-palette')).to_be_visible();expect(page.locator('#command-query')).to_be_focused()
        page.locator('#command-query').fill('Pregunta de prueba sin enviar');page.keyboard.press('Enter')
        expect(page.locator('#question')).to_have_value('Pregunta de prueba sin enviar')
        expect(page.locator('#question')).to_be_focused();page.locator('#question').fill('')
        expect(page.locator('.chat-turn')).to_have_count(0)
        page.locator('#system-button').click();expect(page.locator('#system-popover')).to_be_visible()
        expect(page.locator('#exposure-detail')).to_have_text('Solo este equipo')
        page.keyboard.press('Escape');expect(page.locator('#system-button')).to_be_focused()
        sensor=page.request.get(url+'/api/telemetry?history_seconds=60').json()
        sensor['gpu']['devices'][0]['memory_used_bytes']=9.5*1024**3
        page.evaluate('data=>window.dispatchEvent(new CustomEvent("rag:telemetry",{detail:data}))',sensor)
        expect(page.locator('#system-vram-row')).to_have_attribute('data-level','warn')
        page.evaluate('()=>window.dispatchEvent(new CustomEvent("rag:job",{detail:{id:"header-qa",kind:"query",status:"running",model:"qa-local:latest",elapsed_seconds:2}}))')
        expect(page.locator('.pstep[data-pipeline="5"]')).to_have_attribute('data-state','done')
        expect(page.locator('.pstep[data-pipeline="6"]')).to_have_attribute('data-state','run')
        page.evaluate('()=>window.dispatchEvent(new CustomEvent("rag:job",{detail:{id:"header-qa",kind:"query",status:"done",metrics:{routing_mode:"catalog"}}}))')
        expect(page.locator('#pipeline-state')).to_have_text('Inactivo')
        page.locator('#nav-admin').click()
        check('Header has no overlapping controls at 1440/1280/1024/800/640/390/320px; command palette, keyboard focus, VRAM warning, loopback exposure and live pipeline')

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
        expect(page.locator(".nav-item[aria-current=page]")).to_be_focused()
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
        expect(page.locator('#prepare-button')).to_be_focused()
        check("Upload without indexing, explicit indexing, preparation and success modal focus restoration")

        page.locator("#top-k [data-k='6']").click()
        expect(page.locator("#settings-message")).to_have_text("Guardado")
        page.reload(wait_until="networkidle")
        expect(page.locator("#top-k [data-k='6']")).to_have_attribute("aria-checked", "true")
        expect(page.locator("#model-selection")).to_have_value("automatic")
        page.locator('#response-provider').select_option('xai')
        expect(page.locator('#settings-message')).to_have_text('Guardado')
        expect(page.locator('#external-model-field')).to_be_visible()
        expect(page.locator('#credential-panel')).to_be_visible()
        expect(page.locator('#provider-api-key')).to_have_attribute('type', 'password')
        expect(page.locator('#local-model-field')).to_be_hidden()
        credential_status = page.request.get(url + '/api/settings').json()['credentials']
        expect(page.locator('#credential-state')).to_have_text(
            'Bloqueado' if credential_status['locked'] else 'Sin configurar')
        if credential_status['locked']:
            expect(page.locator('#credential-unlock')).to_be_visible()

        # Exercise the real selector and autosave using fake catalogs only.
        # The isolated server stores test credentials; no provider is contacted.
        catalog = {'models': ['qa-cloud-one', 'qa-cloud-two'], 'status': 200, 'hold': False}
        pending_catalog = []

        def provider_catalog(route):
            if catalog['hold']:
                pending_catalog.append(route)
                return
            route.fulfill(status=catalog['status'], json=(
                {'provider': route.request.url.split('/')[-2], 'models': catalog['models'],
                 'vision_models': [name for name in catalog['models'] if name == 'qwen/qwen3.8-27b'],
                 'image_limits': {'qwen/qwen3.8-27b': 3}} if catalog['status'] == 200
                else {'detail': 'Catálogo de prueba temporalmente no disponible'}))

        page.route('**/api/providers/*/models', provider_catalog)
        tariffs = {'status': 200, 'price': '1.8'}

        def provider_tariffs(route):
            if '/groq/' in route.request.url:
                route.fulfill(json={'provider': 'groq', 'supported': False, 'models': {}})
                return
            route.fulfill(status=tariffs['status'], json={
                'provider': 'xai', 'supported': True, 'retrieved_at': '2026-11-11T14:00:00+00:00',
                'models': {'qa-cloud-two': {
                    'input': {'amount': tariffs['price'], 'currency': 'EUR', 'unit': 'million_tokens'},
                    'output': {'amount': '5.5', 'currency': 'EUR', 'unit': 'million_tokens'},
                    'cached_input': {'amount': '0.12', 'currency': 'EUR', 'unit': 'million_tokens'}}}})

        page.route('**/api/providers/*/pricing', provider_tariffs)
        expect(page.locator('select#external-model')).to_be_disabled()
        if credential_status['locked']:
            page.locator('#credential-passphrase').fill('qa-only-master-password')
            page.locator('#unlock-credentials').click()
            expect(page.locator('#save-api-key')).to_be_enabled()
        page.locator('#provider-api-key').fill('qa-only-provider-secret')
        page.locator('#save-api-key').click()
        expect(page.locator('#provider-models-state')).to_have_text('2 modelos disponibles')
        expect(page.locator('#external-model')).to_have_value('')
        expect(page.locator('#external-model option')).to_have_count(3)
        expect(page.locator('#provider-api-key')).to_have_value('')
        with page.expect_response(lambda r: r.url.endswith('/api/settings') and r.request.method == 'PUT'):
            page.locator('#external-model').select_option('qa-cloud-two')
        assert page.request.get(url + '/api/settings').json()['settings']['external_model'] == 'qa-cloud-two'
        expect(page.locator('#model-price-input')).to_contain_text('1,80')
        expect(page.locator('#model-price-output')).to_contain_text('5,50')
        expect(page.locator('#model-price-cache')).to_contain_text('0,12')
        expect(page.locator('#model-pricing-updated')).to_contain_text('Consultado:')
        tariffs['price'] = '2.1'
        page.locator('#refresh-provider-models').click()
        expect(page.locator('#model-price-input')).to_contain_text('2,10')
        tariffs['status'] = 502
        page.locator('#refresh-provider-models').click()
        expect(page.locator('#model-pricing-state')).to_contain_text('No se pudieron consultar los precios')
        expect(page.locator('#model-pricing-rates')).to_be_hidden()
        expect(page.locator('#external-model')).to_be_enabled()
        tariffs['status'] = 200
        page.reload(wait_until='networkidle')
        expect(page.locator('#provider-models-state')).to_have_text('2 modelos disponibles')
        expect(page.locator('#external-model')).to_have_value('qa-cloud-two')

        catalog['models'] = []
        page.locator('#refresh-provider-models').click()
        expect(page.locator('#provider-models-state')).to_have_text('0 modelos disponibles')
        expect(page.locator('#external-model')).to_be_disabled()
        expect(page.locator('#external-model')).to_have_value('qa-cloud-two')
        expect(page.locator('#external-model option:checked')).to_contain_text('No aparece en el catálogo actual')
        catalog['status'] = 502
        page.locator('#refresh-provider-models').click()
        expect(page.locator('#provider-models-state')).to_contain_text('temporalmente no disponible')
        assert page.request.get(url + '/api/settings').json()['settings']['external_model'] == 'qa-cloud-two'

        catalog.update(models=['qa-cloud-one', 'qa-cloud-two'], status=200)
        page.locator('#refresh-provider-models').click()
        expect(page.locator('#external-model')).to_be_enabled()
        catalog['hold'] = True
        with page.expect_request('**/api/providers/xai/models'):
            page.locator('#refresh-provider-models').click()
        expect(page.locator('#external-model')).to_be_disabled()
        page.locator('#response-provider').select_option('scaleway')
        expect(page.locator('#credential-state')).to_have_text('Sin configurar')
        assert pending_catalog
        for delayed in pending_catalog:
            delayed.fulfill(json={'provider': 'xai', 'models': ['wrong-provider-model']})
        # Changing back must load a new catalog, not the delayed previous one.
        catalog['hold'] = False
        page.locator('#response-provider').select_option('xai')
        expect(page.locator('#provider-models-state')).to_have_text('2 modelos disponibles')
        expect(page.locator('#external-model option[value="wrong-provider-model"]')).to_have_count(0)
        expect(page.locator('#external-model')).to_have_value('')
        page.locator('#delete-api-key').click()
        expect(page.locator('#credential-state')).to_have_text('Sin configurar')
        expect(page.locator('#external-model')).to_be_disabled()
        expect(page.locator('#external-model option')).to_have_count(1)
        catalog['models'] = ['qa-cloud-one', 'qwen/qwen3.8-27b']
        page.locator('#response-provider').select_option('groq')
        expect(page.locator('#provider-credential-hint')).to_contain_text('GroqCloud')
        expect(page.locator('#external-model')).to_be_disabled()
        page.locator('#provider-api-key').fill('gsk_qa-only-provider-secret')
        page.locator('#save-api-key').click()
        expect(page.locator('#provider-models-state')).to_have_text('2 modelos disponibles')
        expect(page.locator('#provider-api-key')).to_have_value('')
        with page.expect_response(lambda r: r.url.endswith('/api/settings') and r.request.method == 'PUT'):
            page.locator('#external-model').select_option('qa-cloud-one')
        page.reload(wait_until='networkidle')
        expect(page.locator('#response-provider')).to_have_value('groq')
        expect(page.locator('#external-model')).to_have_value('qa-cloud-one')
        expect(page.locator('#model-pricing-rates')).to_be_hidden()
        expect(page.locator('#vision-model option')).to_have_count(2)
        expect(page.locator('#vision-model')).to_have_value('')
        expect(page.locator('#vision-model option[value="qa-cloud-one"]')).to_have_count(0)
        # A rejected reindex from Settings must be a persistent error, not a
        # three-second success toast. Force the autosave/index race in one tick.
        def reindex_status(route):
            response = route.fetch()
            data = response.json()
            data['documents'][0]['needs_reindex'] = True
            route.fulfill(response=response, json=data)
        index_attempts = []
        def reject_index(route):
            saved = page.request.get(url + '/api/settings').json()['settings']
            index_attempts.append(saved['k'])
            route.fulfill(status=422, json={'detail': 'Selecciona un modelo visual del proveedor en Configuración.'})
        page.route('**/api/status', reindex_status)
        page.route('**/api/admin/index', reject_index)
        expect(page.locator('#reindex-settings')).to_be_visible(timeout=10000)
        page.locator('#reindex-settings').focus()
        page.evaluate("document.querySelector('#top-k [data-k=\"8\"]').click();document.getElementById('reindex-settings').click()")
        expect(page.locator('#result-dialog')).to_have_attribute('role', 'alertdialog')
        expect(page.locator('#dialog-title')).to_have_text('No se pudo actualizar el índice')
        expect(page.locator('.dialog-error-card')).to_contain_text('Selecciona un modelo visual')
        assert index_attempts == [8], 'The pending preferences must be saved before indexing'
        page.wait_for_timeout(3100)
        expect(page.locator('#result-dialog')).to_be_visible()
        dialog_close(page)
        expect(page.locator('#reindex-settings')).to_be_enabled()
        page.unroute('**/api/admin/index', reject_index)
        page.unroute('**/api/status', reindex_status)
        page.locator('#top-k [data-k="6"]').click()
        expect(page.locator('#settings-message')).to_have_text('Guardado')
        check('Rejected index from Settings shows a persistent actionable error and flushes pending preferences before POST')
        with page.expect_response(lambda r: r.url.endswith('/api/settings') and r.request.method == 'PUT'):
            page.locator('#vision-model').select_option('qwen/qwen3.8-27b')
        page.reload(wait_until='networkidle')
        expect(page.locator('#vision-model')).to_have_value('qwen/qwen3.8-27b')
        expect(page.locator('#index-vision-model')).not_to_have_value('qwen/qwen3.8-27b')
        expect(page.locator('#vision-model-hint')).to_contain_text('3 imágenes')
        saved_vision = page.request.get(url + '/api/settings').json()['settings']
        assert saved_vision['external_vision_model'] == 'qwen/qwen3.8-27b'
        local_vision = saved_vision['vision_model']
        catalog['models'] = ['qa-cloud-one']
        page.locator('#refresh-provider-models').click()
        expect(page.locator('#vision-model')).to_be_disabled()
        expect(page.locator('#vision-model option:checked')).to_contain_text('No aparece en el catálogo actual')
        catalog['models'] = ['qa-cloud-one', 'qwen/qwen3.8-27b']
        page.locator('#refresh-provider-models').click()
        expect(page.locator('#vision-model')).to_be_enabled()
        with page.expect_response(lambda r: r.url.endswith('/api/settings') and r.request.method == 'PUT'):
            page.locator('#vision-model').select_option('')
        page.locator('#delete-api-key').click()
        expect(page.locator('#credential-state')).to_have_text('Sin configurar')
        check('Groq provider, encrypted test credential, live catalog selector and persisted model; no invented pricing')
        page.unroute('**/api/providers/*/models', provider_catalog)
        page.unroute('**/api/providers/*/pricing', provider_tariffs)
        check('Provider model dropdown: automatic catalog, explicit choice, autosave/reload, empty/error recovery and stale provider responses')
        page.locator('#response-provider').select_option('ollama')
        expect(page.locator('#settings-message')).to_have_text('Guardado')
        expect(page.locator('#vision-model')).to_have_value(local_vision)
        expect(page.locator('#vision-model option[value="qwen/qwen3.8-27b"]')).to_have_count(0)
        check('Vision dropdown follows provider: confirmed models only, explicit choice, reload, missing-model recovery and preserved local model')
        page.screenshot(path=str(reports / "redesign-configuracion.png"), full_page=True)
        page.locator("#nav-query").click()
        expect(page.locator("#model")).not_to_be_visible()
        expect(page.locator("#stat-docs")).not_to_be_visible()
        page.locator("#question").fill("¿Cuándo se realiza la copia de seguridad?")
        previous_queries=len(queries)
        page.locator("#question").press("Control+Enter")
        expect(page.locator('#question')).to_have_value('¿Cuándo se realiza la copia de seguridad?\n')
        assert len(queries)==previous_queries, 'Ctrl+Enter must insert a newline without sending'
        page.locator("#question").press("Enter")
        # The first retrieval can initialise the persisted vector snapshot; keep
        # the acceptance test's wait aligned with the page-wide 30 s timeout.
        expect(page.locator(".chat-turn").last).to_have_attribute("aria-busy", "false", timeout=30000)
        expect(page.locator(".chat-turn .answer").last).to_contain_text("cada viernes")
        expect(page.locator("#context-panel")).not_to_be_visible()
        page.locator(".chat-turn .citation").last.click()
        expect(page.locator(".chat-sources").last.locator(".source").first).to_be_visible()
        assert page.locator(".chat-sources mark").count() > 0
        page.keyboard.press("Escape")
        expect(page.locator("#context-panel")).not_to_be_visible()
        expect(page.locator("#open-context")).to_have_attribute("aria-expanded", "false")
        page.evaluate("() => {window.__sourceIdentity=document.querySelector('#sources .source');}")
        for _ in range(3):
            page.locator('#system-button').click()
            with page.expect_response('**/api/status'):page.locator('#refresh-button').click()
            page.keyboard.press('Escape')
            page.evaluate('() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
            assert page.evaluate("() => document.querySelector('#sources .source')===window.__sourceIdentity"), 'Source panel was rebuilt during status refresh'
        page.locator(".trace-actions button[aria-label='Copiar respuesta']").last.click()
        expect(page.locator("#toast")).to_contain_text("Respuesta copiada")
        page.locator("#nav-settings").click()
        page.locator("#search-only").check()
        expect(page.locator("#settings-message")).to_have_text("Guardado")
        page.locator("#nav-query").click()
        page.locator("#question").fill("Busca copias de seguridad")
        page.locator("#ask-button").click()
        expect(page.locator(".chat-turn .answer").last).to_contain_text("Fragmentos recuperados")
        page.locator("#nav-settings").click()
        page.locator("#search-only").uncheck()
        expect(page.locator("#settings-message")).to_have_text("Guardado")
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
        page.locator("#hardware-range [data-seconds='86400']").click()
        expect(page.locator("#chart-cpu-range")).to_have_text("Hace 1 día")
        page.locator("#hardware-range [data-seconds='604800']").click()
        expect(page.locator("#chart-cpu-range")).to_have_text("Hace 1 semana")
        expect(page.locator('#chart-cpu')).to_have_attribute('data-samples','3')
        page.reload(wait_until='networkidle')
        expect(page.locator("#hardware-range [data-seconds='604800']")).to_have_attribute('aria-pressed','true')
        expect(page.locator('#chart-cpu')).to_have_attribute('data-samples','3')
        page.locator('#chart-cpu').focus();page.keyboard.press('Home')
        assert '/' in page.locator('#tip-cpu').inner_text(), 'Week tooltips must show the date'
        page.locator("#statistics-history-range [data-history-seconds='86400']").click()
        expect(page.locator('#statistics-scope')).to_contain_text('1 día')
        page.locator("#statistics-history-range [data-history-seconds='604800']").click()
        expect(page.locator('#statistics-scope')).to_contain_text('7 días')
        page.locator('.ingestion-history>summary').click()
        expect(page.locator('#ingestion-statistics tr')).not_to_have_count(0)
        page.locator('.ingestion-history>summary').click()
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
        check("Real-sample resource charts: fixed scales, weekly SQLite history across reloads, dated tooltips, ingestion history and pause/resume")
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
        page.locator("#close-context").click()
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
        for _ in range(200):
            pending=page.request.get(url+'/api/jobs?active=1').json()['jobs']
            pending=[job for job in pending if job.get('document_name','').startswith('cancelar-todo-')]
            if len(pending)>=2 and any(job['status']=='running' and job['stage']=='parse' for job in pending):
                break
            page.wait_for_timeout(100)
        else:
            raise AssertionError(f'Cancellation fixture never became ready: {pending}')
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
        completed=page.request.get(url+'/api/status').json()['documents']
        assert next(item for item in completed if item['name']=='recuperacion.txt')['indexed']
        dialog_close(page)
        check("Visible cancel-processing button stops uploads and queued parsing jobs while preserving completed index writes")

        clear_queue(page)
        evil = '<img src=x onerror=alert(1)>.pdf'
        page.locator("#file-input").set_input_files(file(evil))
        expect(page.locator(".queue-name")).to_have_text(evil)
        page.locator("#upload-button").click()
        expect(page.locator("#result-dialog")).to_be_visible()
        expect(page.locator(".dialog-error-card>strong")).to_have_text(evil)
        assert page.locator('#documents-table img,.dialog-error-card img,.queue-row img').count() == 0
        expect(page.locator('#language-flag')).to_have_count(1)
        dialog_close(page)
        clear_queue(page)
        page.locator("#auto-index").uncheck()
        for offset in (0,20):
            clear_queue(page)
            page.locator("#file-input").set_input_files([file(f"extra-{i:02}.txt") for i in range(offset,offset+20)])
            page.locator("#upload-button").click();expect(page.locator("#result-dialog")).to_be_visible();dialog_close(page)
        expect(page.locator("#documents-table tr")).to_have_count(50)
        page.locator("#next-page").click();assert 0<page.locator("#documents-table tr").count()<50
        page.locator("#document-search").fill("extra-19")
        expect(page.locator("#documents-table tr")).to_have_count(1)
        expect(page.locator("#documents-table")).to_contain_text("extra-19")
        page.locator("#document-search").fill("")
        page.locator("[data-filter=indexed]").click()
        expect(page.locator("#documents-table .status-dot.success")).not_to_have_count(0)
        page.locator("[data-filter=all]").click()
        check("Literal hostile filenames, 50-document pagination, debounced search and status filters")

        page.locator("#nav-settings").click()
        page.locator("#multimodal").check()
        page.locator("#index-vision-model").select_option("qa-vision:latest")
        page.locator("#vision-model").select_option("qa-vision:latest")
        page.locator("#model").select_option("qa-local:latest")
        page.locator("[data-response-mode=automatic]").click()
        expect(page.locator("#settings-message")).to_have_text("Guardado")
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
        page.locator("#open-context").click()
        expect(source_details.locator(".source-figure").first).to_be_visible()
        page.wait_for_function('image=>image.complete&&image.naturalWidth>0',arg=source_details.locator('.source-figure').first.element_handle())
        page.screenshot(path=str(reports / "redesign-fuentes-multimodales.png"), full_page=True)
        page.locator("#close-context").click()
        page.locator("#nav-statistics").click()
        expect(page.locator("#query-statistics")).to_contain_text("VISUAL")
        visual_metrics = page.request.get(url + "/api/statistics").json()["recent"][0]["metrics"]
        assert visual_metrics["model"] == "qa-vision:latest", visual_metrics
        assert visual_metrics["routing_mode"] == "automatic"
        assert visual_metrics["images_used"]==1, visual_metrics
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

        indexed_count = sum(document['indexed'] for document in page.request.get(url + '/api/status').json()['documents'])
        previous_turns = page.locator('.chat-turn').count()
        page.locator('#question').fill('¿Cuántos documentos hay indexados?')
        page.locator('#ask-button').click()
        expect(page.locator('.chat-turn')).to_have_count(previous_turns + 1)
        expect(page.locator('.chat-turn').last).to_have_attribute('aria-busy','false')
        expect(page.locator('.chat-turn .answer').last).to_contain_text(f'{indexed_count} documentos indexados')
        inventory = page.request.get(url + '/api/statistics').json()['recent'][0]['metrics']
        assert inventory['backend'] == 'catalog', inventory
        previous_turns = page.locator('.chat-turn').count()
        page.locator('#question').fill('Dame la lista de cpus admitidas')
        page.locator('#ask-button').click()
        expect(page.locator('.chat-turn')).to_have_count(previous_turns + 1)
        expect(page.locator('.chat-turn').last).to_have_attribute('aria-busy','false')
        coverage = page.request.get(url + '/api/statistics').json()['recent'][0]['metrics']
        assert coverage['documents_retrieved'] == indexed_count, coverage
        assert coverage['coverage_complete'], coverage
        check('Exact library inventory and global retrieval cover every indexed fixture, independent of top K')

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
        # Lost upload acknowledgements/session state must reconcile with the durable library.
        indexed=next(item for item in page.request.get(url+'/api/status').json()['documents'] if item.get('indexed'))
        before_uploads=len(uploads)
        # A different local file can have the same name and byte count.
        # A missing acknowledgement must not mark it as already indexed.
        page.evaluate('''doc=>sessionStorage.setItem('rag-local-upload-queue-v2',JSON.stringify({entries:[{uid:'unconfirmed',name:doc.name,size:doc.bytes,state:'interrupted',error:{code:'NETWORK',retryable:true}}]}))''',indexed)
        page.reload(wait_until='networkidle');page.locator('#nav-admin').click()
        expect(page.locator('.queue-row[data-state="interrupted"]')).to_have_count(1)
        expect(page.locator('.queue-row[data-state="indexed"]')).to_have_count(0)
        page.locator('#nav-console').click()
        page.evaluate('''doc=>sessionStorage.setItem('rag-local-upload-queue-v2',JSON.stringify({entries:[{uid:'lost-ack',name:doc.name,serverName:doc.name,documentId:doc.document_id,size:doc.bytes,state:'interrupted',upload:100,process:0,batchId:'lost-ack-batch',error:{code:'NETWORK',message:'Vuelve a añadir el archivo para reintentar.',retryable:true}}],batch:{id:'lost-ack-batch',ids:['lost-ack'],started:Date.now(),finished:true},notified:['lost-ack-batch']}))''',indexed)
        page.reload(wait_until='networkidle')
        page.locator('#nav-admin').click()
        expect(page.locator('.queue-row[data-state="indexed"]')).to_have_count(1)
        expect(page.locator('.queue-error-text')).to_have_count(0)
        recovered=page.evaluate("JSON.parse(sessionStorage.getItem('rag-local-upload-queue-v2')).entries[0]")
        assert recovered['chunks']==indexed['chunks'] and recovered['serverName']==indexed['name']
        assert len(uploads)==before_uploads, 'Recovery must not upload or reindex the PDF'
        if page.locator('#result-dialog').is_visible():dialog_close(page)
        page.locator('#nav-settings').click()
        denied={'count':0}
        def expired_token(route):
            if route.request.method=='PUT' and denied['count']==0:
                denied['count']+=1
                route.fulfill(status=403,content_type='application/json',body=json.dumps({'detail':'Recarga esta página antes de continuar.'}))
            else:route.continue_()
        page.route('**/api/settings',expired_token)
        page.locator('#max-visuals').fill('300');page.locator('#max-visuals').press('Tab')
        expect(page.locator('#settings-message')).to_have_text('Guardado')
        assert denied['count']==1
        page.unroute('**/api/settings',expired_token)
        check('Indexed upload queue survives lost acknowledgements/reloads without duplicate uploads; expired session token refreshes safely')
        page.locator("#nav-admin").click()

        # Structured records are query scopes, not mutable document folders.
        expect(page.locator('#topic-tree [data-topic="records"]')).to_contain_text('Ciberseguridad')
        expect(page.locator('#topic-tree [data-topic="records:vulnerabilities"]')).to_contain_text('Vulnerabilidades')
        expect(page.locator('#topic-tree [data-topic="records"] + .category-menu')).to_have_count(0)
        expect(page.locator('#upload-topic option[value="records"]')).to_have_count(0)
        expect(page.locator('#upload-topic option[value="records:vulnerabilities"]')).to_have_count(0)
        expect(page.locator('#move-topic option[value="records"]')).to_have_count(0)
        expect(page.locator('#chat-scope option[value="records"]')).to_have_count(1)
        expect(page.locator('#chat-scope option[value="records:vulnerabilities"]')).to_have_count(1)
        page.locator('#topic-tree [data-topic="records:vulnerabilities"]').click()
        expect(page.locator('#library-location')).to_have_text('Ciberseguridad / Vulnerabilidades')
        expect(page.locator('#documents-table tr')).to_have_count(0)
        page.locator('#topic-filters [data-topic=""]').click()
        check('System-managed cybersecurity/vulnerability query scopes stay separate from upload, move and category editing')

        # Virtual organization must not re-embed or alter the existing manifest.
        before_library=page.request.get(url+'/api/status').json()
        page.locator('#new-topic').click();page.locator('#topic-name').fill('Hardware QA');page.locator('#save-topic').click()
        expect(page.locator('#topic-tree')).to_contain_text('Hardware QA')
        library=page.request.get(url+'/api/library').json()
        cabinet=next(item['id'] for item in library['topics'] if item['name']=='Hardware QA')
        category=page.locator('.category-branch').filter(has_text='Hardware QA')
        category.locator('.category-menu summary').click()
        category.get_by_role('button',name='Nueva subcategoría',exact=True).click()
        page.locator('#topic-name').fill('Manuales');page.locator('#save-topic').click()
        expect(page.locator('#topic-tree')).to_contain_text('Manuales')
        library=page.request.get(url+'/api/library').json();drawer=next(item['id'] for item in library['topics'] if item['name']=='Manuales')
        page.locator('#document-search').fill('manual.txt')
        expect(page.locator('#documents-table tr')).to_have_count(1)
        page.locator('#documents-table .document-select').check();page.locator('#move-topic').select_option(drawer);page.locator('#move-documents').click()
        expect(page.locator('#selected-documents')).to_have_text('0 seleccionados')
        page.locator('#document-search').fill('')
        page.locator(f'#topic-tree [data-topic="{cabinet}"]').click()
        expect(page.locator('#documents-table tr')).to_have_count(1)
        expect(page.locator('#upload-topic')).to_have_value(cabinet)
        expect(page.locator('#upload-destination-hint')).to_contain_text('Hardware QA')
        expect(page.locator('#documents-table')).to_contain_text('Hardware QA / Manuales')
        after_library=page.request.get(url+'/api/status').json()
        assert before_library['chunk_count']==after_library['chunk_count']
        assert before_library['document_count']==after_library['document_count']
        page.locator('#nav-query').click();page.locator('#chat-scope').select_option(cabinet)
        page.locator('#question').fill('Lista de documentos indexados')
        with page.expect_request('**/api/query') as scoped_request:page.locator('#ask-button').click()
        assert scoped_request.value.post_data_json['topic_id']==cabinet
        assert scoped_request.value.post_data_json['history']==[], 'A new topic must not inherit another topic history'
        expect(page.locator('.chat-turn').last).to_have_attribute('aria-busy','false')
        expect(page.locator('.chat-turn .answer').last).to_contain_text('1 documento indexado')
        expect(page.locator('.chat-turn .answer').last).to_contain_text('manual.txt')
        page.reload(wait_until='networkidle');expect(page.locator('#chat-scope')).to_have_value(cabinet)
        expect(page.locator('#context-panel')).not_to_be_visible()
        assert page.locator('.conversation-card').bounding_box()['width']>1300
        page.locator('#open-context').click();expect(page.locator('#context-panel')).to_be_visible()
        page.keyboard.press('Escape');expect(page.locator('#open-context')).to_be_focused()
        page.locator('#chat-scope').select_option('')
        page.locator('#nav-admin').click();page.locator('#topic-filters [data-topic=""]').click()
        check('Categories/subcategories, bulk move without reindexing, contextual upload destination, scoped chat history, persistent selection and accessible full-width chat drawer')

        # Shared server jobs must not become another tab's conversation. A known
        # job may have finished while this tab was closed and must still be read.
        recovery=context.new_page();recovery.on('pageerror',lambda error:errors.append(str(error)))
        shared_status=page.request.get(url+'/api/status').json()
        shared_status['jobs']=[{'id':'foreign-active','kind':'query','status':'running','question':'FOREIGN PRIVATE QUESTION','stage':'generate','phase':'Generando respuesta con Ollama'}]
        recovery.route('**/api/status',lambda route:route.fulfill(json=shared_status))
        detail_reads=[]
        def recovered_job(route):
            detail_reads.append(route.request.url.rsplit('/',1)[-1])
            route.fulfill(json={'id':'owned-finished','kind':'query','status':'done','answer':'Respuesta recuperada de mi consulta.','sources':[],'metrics':{}})
        recovery.route('**/api/jobs/*',recovered_job)
        recovery.goto(url,wait_until='networkidle')
        expect(recovery.locator('.chat-turn')).to_have_count(0)
        assert not detail_reads,detail_reads
        recovery.evaluate('''()=>sessionStorage.setItem('rag-local-chat-v3',JSON.stringify([
            {id:'owned-turn',question:'Mi pregunta',status:'running',job_id:'owned-finished'},
            {id:'unconfirmed-turn',question:'Pregunta sin confirmación',status:'queued'}
        ]))''')
        recovery.reload(wait_until='networkidle')
        expect(recovery.locator('.chat-turn .answer').first).to_contain_text('Respuesta recuperada de mi consulta.')
        expect(recovery.locator('.chat-turn .chat-state').last).to_contain_text('No se recibió la confirmación')
        expect(recovery.locator('.chat-turn[aria-busy="true"]')).to_have_count(0)
        expect(recovery.locator('#chat-thread')).not_to_contain_text('FOREIGN PRIVATE QUESTION')
        assert detail_reads==['owned-finished'],detail_reads
        recovery.close()
        check('Chat recovery ignores foreign jobs, reads owned completed jobs and releases unacknowledged turns without resubmission')

        page.screenshot(path=str(reports / "redesign-biblioteca.png"), full_page=True)
        page.set_viewport_size({"width":390,"height":844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(reports / "redesign-biblioteca-mobile.png"), full_page=True)
        page.locator("#nav-query").click()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        assert page.locator(".app-header").bounding_box()["y"] == 0
        expect(page.locator("#stat-docs")).not_to_be_visible()
        page.locator("#nav-statistics").click()
        # Read both positions in the same layout; telemetry can resize cards between calls.
        page.wait_for_function("()=>(document.querySelector('.resource-charts .resource-card:nth-child(2)').getBoundingClientRect().top > document.querySelector('.resource-charts .resource-card').getBoundingClientRect().top)")
        page.locator("#nav-query").click()
        page.screenshot(path=str(reports / "redesign-consulta-mobile.png"), full_page=True)
        assert page.locator("#ask-button").evaluate("e=>getComputedStyle(e).transitionDuration") in {"0s", "0s, 0s"}
        assert not external, external
        assert not errors, errors
        check("390px layouts, stacked statistics, reduced motion and no external browser requests/errors")

        # Last on purpose: it moves the fixture library into a backup folder.
        page.set_viewport_size({"width":1280,"height":900})
        page.locator("#nav-admin").click()
        documents=page.evaluate("fetch('/api/status').then(r=>r.json()).then(s=>s.document_count)")
        assert documents>0
        expect(page.locator("#empty-library-button")).to_be_enabled()
        page.locator("#empty-library-button").click()
        expect(page.locator("#empty-library-dialog")).to_be_visible()
        expect(page.locator("#empty-library-word")).to_be_focused()
        expect(page.locator("#empty-library-confirm")).to_be_disabled()
        page.locator("#empty-library-word").fill("no")
        page.keyboard.press("Enter")
        expect(page.locator("#empty-library-confirm")).to_be_disabled()
        page.keyboard.press("Escape")
        expect(page.locator("#empty-library-dialog")).not_to_be_visible()
        assert page.evaluate("fetch('/api/status').then(r=>r.json()).then(s=>s.document_count)")==documents
        page.locator("#empty-library-button").click()
        page.locator("#empty-library-word").fill("vaciar" if page.evaluate("document.documentElement.lang")=="es" else "empty")
        page.locator("#empty-library-confirm").click()
        expect(page.locator("#toast")).to_contain_text("library-backups")
        expect(page.locator("#empty-library-dialog")).not_to_be_visible()
        expect(page.locator("#stat-docs")).to_have_text("0")
        expect(page.locator("#library-empty")).to_be_visible()
        expect(page.locator("#empty-library-button")).to_be_disabled()
        status=page.evaluate("fetch('/api/status').then(r=>r.json())")
        assert (status["document_count"],status["chunk_count"],status["library"]["topics"])==(0,0,[]), status
        assert not errors, errors
        check("Empty library: typed confirmation, cancel without changes, backup instead of deletion and a clean empty state")
        browser.close()
    report={"status":"OK","checks":checks,"external_requests":external,"page_errors":errors,
            "llm":"fake test adapter; no Ollama/GPU calls","embeddings":"deterministic test vectors; no model downloads",
            "progress":"real XHR byte events and real local SQLite batch completion","screenshots":"Fixture corpus; response metrics are test data, not benchmarks."}
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
