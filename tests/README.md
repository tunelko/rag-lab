# Tests

Run from the project root with Python 3.13 and `requirements/dev.txt` installed
after the appropriate PyTorch profile. See [security scope](../SECURITY.md#real-security-checks).

## Real application security

```text
python -m tests.security --download-embeddings
python -m tests.security --llm-model deepseek-r1:8b --require-llm --max-output-tokens 4096
```

The first command explicitly permits downloading the real E5 embedding model at
a fixed commit. Subsequent runs reuse its local cache. The second needs an already
installed local Ollama model and a running Ollama server. It never downloads an
LLM or calls a cloud provider. Select another installed model with `--llm-model`;
its actual digest, generated tokens and answers are recorded. Reasoning and
visible output share the chosen 512–4096 token budget. The default is 2048;
exhaustion without a visible answer is an error, not a passed check.

Each invocation starts and stops its own unmodified Musa server on a free
loopback port with separate documents, index, settings, credentials and history
under ignored `reports/security/<run>/instance/`. Documents are uploaded and
indexed through the real API. E5 runs on CPU; Ollama uses its usual device when
LLM checks are requested. Production services and documents are not changed.
There are no patched transports, fixture answers or fake embeddings.

Results are JSON and Markdown: `pass`, `fail`, `error` and `not_run`. Web-only runs
record all eight model cases as `not_run`. `--require-llm` refuses a missing model
selection. Failed/errored checks return nonzero. Python `-O` is refused.

The browser defaults to Edge on Windows. `--browser-channel chrome` selects
installed Chrome; `--browser-channel chromium` selects the browser installed by
`python -m playwright install chromium`. No model response is replaced.

## Dependency and source audit

Install scanners separately to preserve the runtime's CUDA dependencies:

```text
python -m venv .cache/security-tools
```

Windows:

```powershell
.\.cache\security-tools\Scripts\python.exe -m pip install -r requirements/security.txt
.\.venv\Scripts\python.exe -m tools.security_audit --tool-python .cache/security-tools/Scripts/python.exe
```

Linux:

```bash
.cache/security-tools/bin/python -m pip install -r requirements/security.txt
.venv/bin/python -m tools.security_audit --tool-python .cache/security-tools/bin/python
```

The audit runs Bandit on actual source, pip-audit/OSV on the application
interpreter's installed packages including transitives, OSV on vendored npm
packages, and SHA-256 checks on their files. Advisories require network access;
`--scope static` does not. Output is `reports/security-audit/`; subsequent scans
replace those files. Choose `--output-dir` to retain another scan. Vulnerable or
unaudited packages, incomplete scans and HIGH Bandit findings block. Medium/low
findings stay visible as `review`, not a clean scan.

## CI

Linux (`ubuntu-24.04`) and Windows execute real HTTP/browser/encryption/vector
checks with pinned real E5 weights, source/dependency audits, JavaScript function
tests and syntax checks. They use an installed browser, downloading Playwright
Chromium only as fallback. Summaries and reports are uploaded after failures too.
Hosted runners do not execute the LLM cases: they are explicitly `not_run`, with
no LLM-security verdict. Run the model command above against your actual model.

**CI excludes the simulated-model Python suites and fixture browser suite below.**
They remain optional local regressions; their vectors, models, sensors and timing
data are fixtures, not security or accuracy evidence.

## Optional local regressions

| Suite | Command |
|---|---|
| Python / terminal wizard | `python -m unittest discover -s tests -t . -v` |
| JavaScript | `node --test tests/frontend/*.test.mjs` |
| Isolated browser acceptance | `python -m tests.browser.qa_redesign` |
| Read-only running-server check | `python -m tests.browser.qa_web --url http://127.0.0.1:8765` |

Browser tests use Edge by default for local QA: `python -m playwright install msedge`.
`RAG_BROWSER_CHANNEL` selects another browser: `chrome` or `msedge` for an installed
one, `chromium` for the Chromium that Playwright downloads.

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

Laboratory tests use genuine SQLite snapshots and fake local embeddings. They
verify effective chat-parameter capture before queueing, validation before model
loading, committed WAL copying, unchanged active vector/manifest bytes, partial
results after cancellation/reload, startup cleanup, and the Empty Library guard.
Browser acceptance exercises the mirror in ES/EN and light/dark mobile layouts.
Its evidence percentages are fixture checks, not accuracy benchmarks of a user's
corpus or answer model.
