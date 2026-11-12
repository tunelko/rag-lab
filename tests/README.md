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
`RAG_BROWSER_CHANNEL` selects another browser: `chrome` or `msedge` for an installed
one, `chromium` for the Chromium that Playwright downloads.

CI runs the browser acceptance suite on Linux and Windows with a browser that the
runner image already ships (Chrome on Linux, Edge on Windows), so it needs neither a
package mirror nor a browser download. Only if an image has no browser does it
download Playwright Chromium, with three attempts and step timeouts; a failure
there fails the job, and browser checks are never skipped or marked successful.
Linux is pinned to `ubuntu-24.04` rather than `ubuntu-latest`. Each job writes a
summary with the number of Python tests and browser checks, and uploads `reports/`.

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

Laboratory tests use genuine SQLite snapshots and fake local embeddings. They
verify effective chat-parameter capture before queueing, validation before model
loading, committed WAL copying, unchanged active vector/manifest bytes, partial
results after cancellation/reload, startup cleanup, and the Empty Library guard.
Browser acceptance exercises the mirror in ES/EN and light/dark mobile layouts.
Its evidence percentages are fixture checks, not accuracy benchmarks of a user's
corpus or answer model.
