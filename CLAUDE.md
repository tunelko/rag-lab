# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

RAG Local: a single-user, local, multimodal document assistant (Windows x64 / Linux x86_64, Python **3.13**). FastAPI backend + a no-build vanilla JS UI. Embeddings run on CPU (SentenceTransformers, `intfloat/multilingual-e5-small`); generation and vision captions go to a local Ollama. Vectors live in SQLite with exact NumPy cosine search (ChromaDB was removed for security reasons — do not reintroduce it or `langchain-chroma`).

UI strings, job phases and most error messages are Spanish; docs exist in English (`README.md`, most of `docs/`) and Spanish (`README.es.md`, `docs/REFERENCE.es.md`, `docs/DESARROLLO.md`). Keep both READMEs in sync when changing user-facing behaviour.

## Commands

Run everything from the project root. Tests need an environment with `requirements/torch-cpu.txt` then `requirements/dev.txt` (dev = Ollama runtime + Textual wizard + Playwright).

```text
python -m unittest discover -s tests -t . -v            # all Python tests (offline, fake models/vectors/sensors)
python -m unittest -v tests.unit.test_rag               # one module
python -m unittest -v tests.unit.test_rag.<Class>.<test_name>   # one test
node --test tests/frontend/*.test.mjs                   # JS tests
for m in dist/*.js; do node --check "$m"; done          # JS syntax check (CI does this; there is no bundler/linter)
python -m playwright install msedge                     # once, for browser tests
python -m tests.browser.qa_redesign                     # isolated browser acceptance: own server + corpus on port 8767 (--port), never calls Ollama
python -m tests.browser.qa_web --url http://127.0.0.1:8765   # read-only smoke check of a running server
python benchmarks/benchmark_local.py --output reports/local-benchmark.json   # synthetic CPU-only diagnostics
python -m tools.benchmark_vector_store --output reports/vector-benchmark.json # current-index mechanics, no model
python -m tools.evaluate_retrieval --dataset <private.jsonl>                 # labeled retrieval metrics
```

There is no Python linter/formatter configured. CI (`.github/workflows/ci.yml`) runs the unittest suite, JS tests and browser QA on Windows and Linux (Linux pinned to `ubuntu-24.04`; the browser is one the runner image ships, selected through `RAG_BROWSER_CHANNEL`), with `CUDA_VISIBLE_DEVICES=''` and HF offline.

Server lifecycle (background process managed by `server_control.py`; never touches Ollama):

```text
.\Start-Web.ps1 start|status|restart|stop [-Port 8766] [-Host <RFC1918 ip>] [-WhenIdle]    # Windows
bash web.sh start|status|restart|stop [--port 8766] [--host ...] [--when-idle]             # Linux
```

Stop/restart refuse while jobs are active (use `--when-idle`). Logs: `reports/rag-server-runtime*.log`. **Python changes require a server restart; JS/CSS changes only need a page reload.**

Setup wizard (Textual TUI, isolated in `.cache/wizard-venv`): `Configurar.ps1` / `bash configurar.sh` (`-Check`/`--check`, `-Start`/`--start`). `Setup-Lab.ps1` is the advanced CUDA/HF-NF4 path.

CLI without the web UI: `python rag.py ingest [dir] [--download-embeddings]`, `python rag.py search "q" [-k N]`, `python rag.py ask "q" [--preview] [--backend ollama|hf4bit]`. Legacy vector migration: `python -m tools.migrate_vectors` (see `docs/VECTOR_MIGRATION.md`).

## Architecture

Flat layout: all backend modules sit at the repo root and import each other by bare name (`import rag`), so run from the root. `dist/` is **source** (hand-written ES modules, CSS, locales, vendored Marked/DOMPurify/fonts), not build output; `dist/vendor/**` must stay byte-identical (recorded hashes).

### Request flow

`web_app.py` → `create_app()` builds the FastAPI app around one `RagService(JobRuntime)`.

- **One sequential job queue.** `jobs_runtime.JobRuntime` owns a `ThreadPoolExecutor(max_workers=1)`. `prepare`, `index` and `query` are all jobs: endpoints return `202 {job_id}` and the browser polls `/api/jobs/{id}` (the streamed answer is accumulated in the job dict). Queries wait behind indexing. Job state and results are in memory only (last 200 terminal jobs); only statistics persist (`statistics_store.py`, SQLite, 7-day retention). Cancellation is cooperative (`cancelled()` / `check_cancel`), and refused once the `indexing` stage is writing.
- **Settings are captured at submission.** Endpoints snapshot `SettingsStore` preferences and the topic's document membership (`frozenset`) before enqueueing, and pass them into the job closure. Preserve this when adding parameters — a queued job must not adopt later settings or a broadened scope.
- **Query planning without an LLM** (`library_queries.query_intent`): `catalog_*` (inventory answered from the real register, no model), `focused` (top-K via `retrieval_pipeline.retrieve`: native-text channel, automatic scope to the board model named in the question, optional cross-encoder reranking from `RAG_RERANKER`; k from settings 2/4/6/8 — the same code `tools/evaluate_retrieval` measures, so change it only with a measurement on `data/evaluation/`), or `global` (every indexed file in scope contributes evidence: per-document dense + BM25 with reciprocal rank fusion in `global_retrieve`; answer forced through Ollama structured output and validated row-per-document by `global_answers.GlobalAnswer`). Global queries fail explicitly rather than silently dropping documents. Details: `docs/RETRIEVAL.md`.
- **Model routing** (`model_routing.select_model`): regex/rule based choice between the configured text and vision models from the question, hits and available Ollama capabilities; retrieved figure PNGs and user-attached images (`query_images.py`) are sent only on the vision route.
- All Ollama HTTP goes through `ollama_http.open_ollama` (no ambient proxies, no redirects). Remote/cloud Ollama models are filtered out everywhere.

### Index and its publication boundary

Per collection (`RAG_COLLECTION`, default `documents`), under `data/chroma/` (historical directory name, no Chroma involved):

- `<collection>-vectors.sqlite3` — `vector_store.VectorStore`: id, text, JSON metadata, `file`, float32 blob. Exact NumPy cosine uses a normalized process-local snapshot, transactional generation invalidation and `argpartition` Top-K. The optional FTS5/BM25 index is derived and reversible; normal opens/dense queries never create it. Prepare/remove it explicitly with `python -m tools.prepare_lexical_index` after its automatic backup. The API intentionally mimics the subset of the Chroma/LangChain interface the code used (`get`, `add_documents`, `similarity_search_with_score`, `filter={'file': {'$in': [...]}}`).
- `<collection>-manifest.json` — `rag.load_state/save_state`. **The manifest is the publication boundary**: readers only accept vector IDs listed there (`published_ids`). Ingest order in `rag._ingest` is: fingerprint → parse → re-check fingerprint → upsert new vectors → re-check → fsync + atomic-replace manifest → garbage-collect stale vectors. Failure before publication restores the previous record and best-effort deletes staged vectors; orphan vectors are invisible, not harmful. Keep this ordering.
- `<collection>-writer.lock` — `index_lock.index_writer`, an OS file lock shared by CLI and web writers.
- `<collection>-topics.sqlite3` — `library_topics.TopicStore`: two-level categories/subcategories and document assignments. Purely virtual; they filter retrieval scope, never move files or re-embed, and are not access control.
- The manifest stores `Config.identity()` (embedding model, chunk size/overlap, E5 prefixes). A mismatch raises: changing embedding model or chunking requires a **new** `RAG_COLLECTION`, not an in-place change. Chunk IDs are `sha256(path:file_sha:parser_policy:index)`, so re-ingest is idempotent.
- Manifest keys are absolute source paths (plus a relative `file` field); indexes are not portable across roots without migration.

### Multimodal ingestion

`multimodal.VisualReader` (used as the `loader` passed into `rag.ingest` by `RagService.index`) extracts PDF text/tables, renders visual/scanned pages, and asks the local vision model for captions (cached; assets in `data/visual/<sha256>.png`). Captions are embedded as ordinary text chunks with `asset_id`/`item_type` metadata; the manifest record keeps a `visuals` list used at query time to attach figures to citations. `parser_policy` (`multimodal.policy(settings)`) is part of the chunk ID and the "needs reindex" check, so files indexed multimodally must be reindexed from the web UI, not the plain CLI. `visual_selection.py` is an offline classifier that has **not** replaced the production page-selection rule (see `docs/VISUAL_AUDIT.md`).

### Web security model

Loopback by default; `RAG_WEB_HOST`/`--host` accepts only loopback or an assigned RFC1918 address. Every non-GET request needs a matching `Origin` and the per-process `X-RAG-Token` from `/api/session` (a CSRF guard, not a login). Strict CSP (`script-src 'self'`, `style-src 'self'`) — no inline scripts/styles or CDN assets in `dist/`. Source/figure routes resolve paths and require containment in `data/documents` / `data/visual`. No accounts or per-user isolation; there is deliberately no per-document delete API. The only bulk operation is `POST /api/admin/library/empty` (`library_reset.py`): server-host only, refused while jobs exist, and it *moves* documents, index files, topics and the visual cache to `data/library-backups/<timestamp>/` (manifest first) instead of deleting them.

### Configuration

- `.env` (see `.env.example`) → infrastructure: `RAG_*` for port, embedding model/device, chunking, context chars, Ollama URL/model, token/context budgets. Read by `rag.Config` and `os.getenv` at call sites.
- `data/settings.json` (`settings_store.Preferences`) → UI preferences: response/vision model, multimodal on/off, k, search-only, max images.
- `configure_lab.py` must keep import and `--check` standard-library only (it runs before any venv exists).

## Conventions and constraints

- Nothing loads a model or touches the network at import/startup; embeddings load lazily on first job, downloads only when explicitly allowed (`allow_download`), `trust_remote_code=False` everywhere.
- Tests must stay offline and deterministic: fake embeddings/models/sensors, temp dirs, real isolated SQLite. Don't add tests that need Ollama, GPU, HF downloads or the user's documents.
- Runtime data is private and git-ignored: `.env`, `data/*` (except `data/documents/.gitkeep`), `reports/`, `.cache/`, manual folders copied at the root, `rag-local-rediseno/`. Never commit or paste their contents.
- Dependencies are exact pins split by profile in `requirements/` (see `requirements/README.md`); PyTorch CPU/CUDA is installed first from its own file. `tools/dependency_snapshot.py` (CI) fails on unpinned or conflicting declarations.
- Line endings are enforced in `.gitattributes` (`*.ps1` CRLF, everything else LF).
- Known limits and prioritized remaining work are tracked in `docs/AUDIT.md` (note: its Chroma remarks describe the pre-migration snapshot); the future multi-user/scale design is in `docs/SCALING.md`; dependency/security posture in `SECURITY.md`. Don't describe the project as vulnerability-free, multi-user, or benchmarked beyond what those documents state.
