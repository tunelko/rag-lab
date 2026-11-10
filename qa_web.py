"""Browser QA with installed Edge. Query/upload requests are mocked; no models run."""
import argparse
import json
from pathlib import Path

from playwright.sync_api import sync_playwright, expect

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8765")
    args = parser.parse_args()
    reports = ROOT / "reports"
    reports.mkdir(exist_ok=True)
    errors = []
    fixture_status = {
        "documents": [{"name": "procedimiento.txt", "bytes": 1500, "indexed": True, "chunks": 3}],
        "document_count": 1, "chunk_count": 3, "collection": "documents",
        "embedding_model": "intfloat/multilingual-e5-small", "embedding_device": "cpu", "embedding_loaded": True,
        "chunk_size": 500, "chunk_overlap": 80, "active_job": None,
        "ollama": {"connected": True, "models": ["test-local:latest"], "default_model": "test-local:latest", "loaded": []},
    }
    source = {"citation": 1, "file": "procedimiento.txt", "page": 0, "cosine_distance": 0.12,
              "text": "La copia de seguridad se realiza cada viernes y se verifica su integridad.",
              "url": "/api/source?file=procedimiento.txt"}
    query_reply = {"id": "qa-query", "kind": "query", "status": "done", "phase": "Completado",
                   "answer": "La copia de seguridad se realiza **cada viernes** y se verifica su integridad [1].",
                   "sources": [source], "elapsed_seconds": 2.4,
                   "metrics": {"generation_tok_s": 81.2, "output_tokens": 26, "first_content_seconds": 0.7, "retrieval_seconds": 0.1}}
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(channel="msedge", headless=True)
        page = browser.new_page(viewport={"width": 1440, "height": 1080}, device_scale_factor=1)
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(args.url, wait_until="networkidle")
        expect(page.locator("#stat-engine")).not_to_have_text("Comprobando")
        expect(page.locator("#query-view")).to_be_visible()
        expect(page.locator("#admin-view")).to_be_hidden()
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
        page.screenshot(path=str(reports / "web-consulta-desktop.png"), full_page=True)

        # Test the browser UI with fixtures; API behavior is covered by test_web.py.
        page.route("**/api/status", lambda route: route.fulfill(json=fixture_status))
        page.route("**/api/query", lambda route: route.fulfill(status=202, json={"job_id": "qa-query"}))
        page.route("**/api/jobs/qa-query", lambda route: route.fulfill(json=query_reply))
        page.locator("#refresh-button").click()
        expect(page.locator("#stat-docs")).to_have_text("1")
        page.locator("#question").fill("¿Cuándo se realiza la copia de seguridad?")
        page.locator("#ask-button").click()
        expect(page.locator("#answer")).to_contain_text("cada viernes")
        expect(page.locator("#answer-metrics")).to_contain_text("tokens/s")
        expect(page.locator("#source-count")).to_have_text("1")
        page.locator("#answer .citation").click()
        assert page.locator("#source-1").evaluate("element => element.open")
        page.screenshot(path=str(reports / "web-consulta-fixture.png"), full_page=True)

        page.locator("#nav-admin").click()
        expect(page.locator("#admin-view")).to_be_visible()
        expect(page.locator("#query-view")).to_be_hidden()
        page.route("**/api/admin/upload", lambda route: route.fulfill(json={"saved": ["nota.txt"], "job_id": "qa-index"}))
        page.route("**/api/jobs/qa-index", lambda route: route.fulfill(json={
            "id": "qa-index", "kind": "index", "status": "done", "phase": "Completado", "message": "Índice actualizado",
            "answer": "", "sources": [], "metrics": {}, "elapsed_seconds": 0.8,
        }))
        page.locator("#file-input").set_input_files({"name": "nota.txt", "mimeType": "text/plain", "buffer": b"QA fixture"})
        expect(page.locator("#upload-button")).to_be_enabled()
        page.locator("#upload-button").click()
        expect(page.locator("#notice")).to_have_text("Índice actualizado")
        expect(page.locator("#selected-files")).to_be_empty()
        page.screenshot(path=str(reports / "web-admin-desktop.png"), full_page=True)

        page.set_viewport_size({"width": 390, "height": 844})
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Admin horizontal overflow"
        page.screenshot(path=str(reports / "web-admin-mobile.png"), full_page=True)
        page.locator("#nav-query").click()
        expect(page.locator("#query-view")).to_be_visible()
        expect(page.locator("#answer")).to_contain_text("cada viernes")
        assert page.evaluate("document.documentElement.scrollWidth <= innerWidth"), "Query horizontal overflow"
        page.screenshot(path=str(reports / "web-consulta-mobile.png"), full_page=True)
        page.locator("#search-only").check()
        expect(page.locator("#model")).to_be_disabled()
        expect(page.locator("#ask-button")).to_contain_text("Buscar")
        assert not errors, errors
        browser.close()
    report = {"browser": "Microsoft Edge headless", "desktop": "1440x1080", "mobile": "390x844",
              "console_errors": errors, "status": "OK", "llm_inference": "not run", "embedding_download": "not run",
              "notes": "Query/upload UI tests used fixtures; screenshots containing a response show test data."}
    (reports / "web-qa.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    main()
