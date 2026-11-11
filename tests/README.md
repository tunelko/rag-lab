# Tests

Run from the project root in an environment installed with
`requirements/torch-cpu.txt` followed by `requirements/dev.txt`.

| Suite | Command |
|---|---|
| Python / terminal wizard | `python -m unittest discover -s tests -t . -v` |
| JavaScript | `node --test tests/frontend/*.test.mjs` |
| Isolated browser acceptance | `python -m tests.browser.qa_redesign` |
| Read-only running-server check | `python -m tests.browser.qa_web --url http://127.0.0.1:8765` |

Browser tests use Edge by default for local QA: `python -m playwright install msedge`.
CI sets `RAG_BROWSER_CHANNEL=chromium` and installs Playwright Chromium with its
Linux system libraries. Set the same variable locally to test with bundled Chromium.

Unit tests use fake vectors/models/sensors and temporary files.
Lifecycle tests start real Uvicorn workers with a temporary fixture app and verify
restart, graceful stop, busy-job refusal and preservation of unrelated listeners.
They do not load the RAG application or call models. Browser acceptance
starts and closes its own server/corpus (port 8767, configurable with `--port`) and
never calls Ollama. Reports go to ignored `reports/`. Test metrics are fixtures,
not GPU benchmarks.

Index-publication tests use a real isolated SQLite store with deterministic fake
embeddings. They simulate partial writes, manifest disk failure, cancelled staging,
orphan vectors and failed cleanup; a separate process checks writer exclusion.
Ollama transport tests use two local fake HTTP endpoints to verify proxy bypass
and redirect refusal. No production documents or inference models are required.

Browser recovery checks distinguish acknowledged uploads from filename/size
collisions, ignore other tabs' queries, and recover this tab's completed jobs.
See the [implementation audit](../docs/AUDIT.md) for the limits these tests do not prove.
