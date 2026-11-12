# Laboratory: design and delivery boundaries

The Laboratory will explain whether the **evidence needed to answer a question
actually reaches the model's context**. It is a mirror of the chat's retrieval,
not another search engine. It does not promise that an answer will be correct.

**Current delivery: phase 0, shared engine and offline contract tests.** There is
no Laboratory web page, API, experiment queue or persistent history yet. The
running server and the user's library have not been changed. This design was
agreed between Codex and Claude; the next phase is reviewed separately.

## Three rules

1. **One engine.** Chat and evaluation call `retrieval_pipeline.retrieve` and use
   the exact source-text clipping from `rag.context_fragments`, which also feeds
   `rag.build_messages`. The reordering model, scope, visual/text routing, K and
   evidence budget must match the effective configuration being measured.
2. **An isolated, read-only experiment.** Production vectors, the publication
   manifest and chat settings are never modified by a run. Experimental lexical
   indexes belong only to a temporary snapshot, when comparison is added.
3. **Explicit work.** No network sources, implicit model downloads, hidden jobs
   or automatic provider calls. A user starts a visible, cancellable experiment.

## Four pieces

| Piece | Identity and responsibility | Planned private storage |
| --- | --- | --- |
| Dataset | Immutable JSONL; question, reference file/page or vector ID, optional literal answer evidence. Identified by SHA-256. | `data/lab/datasets/` |
| Snapshot | One coherent SQLite backup plus publication manifest, under `index_writer`. Only published vector IDs are eligible. | `data/lab/snapshots/run-…/` |
| Run | Dataset + snapshot + captured parameters, code identity, status and resource estimate. | `data/lab/lab.sqlite3` |
| Results | Completed questions, exact retrieved IDs and context text, references, metrics and timings. | Same private SQLite database |

The snapshot uses SQLite's backup API, including committed WAL data. The source
and manifest are captured under the same writer lock. Snapshot size is shown
before starting; copying can temporarily require about another index's worth of
disk space. A changed corpus while queued invalidates the request explicitly.
Snapshots are removed on completion, cancellation and startup recovery.

The run uses the existing single job queue; it refuses to start behind an active
or queued ingestion. Its name, phase and cancellation action remain visible from
the rest of the application. Cancelling retains completed measurements. Emptying
the library identifies an active experiment rather than reporting generic work.
Those lifecycle integrations are **phase 1 work, not implemented in phase 0**.

## What a score means

The headline is **literal evidence in the actual context**, with both the raw
count and denominator, for example `27 / 30 annotated questions (90%)`. Questions
without annotated evidence appear separately and are excluded from that metric.
No percentage is displayed as a confidence level or a guarantee of truth.

Evidence matching follows the existing evaluator: Unicode NFKC, case folding,
whitespace normalization, and any supplied alternative answer anchor within a
relevant page's selected source text. It never matches the user's question,
source filenames or citation labels. Evidence can span ordered chunks of one
physical page; text from different pages is not joined. This is a literal
coverage test, not semantic answer grading or verification of OCR accuracy.

Document/page recall, raw-fragment recall, MRR and nDCG are secondary, explicitly
labelled retrieval measurements. Timings describe retrieval, not generation or
tokens per second. No answer model or REMi evaluator is involved.

## Phases

| Phase | Deliverable | Decision supported | Status |
| --- | --- | --- | --- |
| 0 — Engine | Shared evaluator, immutable baseline fixtures, parity with focused chat retrieval and exact context clipping. Existing CLI stays compatible. | Establish a trustworthy measurement path. | Prepared; offline tests and review required before phase 1. |
| 1 — Mirror | One screen: upload/validate JSONL, run current retrieval, inspect evidence coverage and exact context per question. Persist partial progress for reload/cancellation. | Is the relevant evidence reaching the context? | Planned. |
| 2 — Compare | Explicit variants of K, context budget, reranking and retrieval strategy on the same dataset and snapshot. | Which configuration improves the measured cases? | Planned; after review of phase 1. |
| 3 — History | Run browser, comparisons across dates and safe report exports. Bounded retention and deletion per run. | Has behavior improved or regressed? | Planned; after review of phase 2. |
| 4 — Answers | Generation, prompt experiments and separately labelled answer evaluation. | Are the answers grounded and correct? | Separate future decision. |

Phase 1 measures **focused, standalone questions** without chat history or
user-attached images. Inventory and whole-library questions use other chat paths
and are rejected explicitly in this first scope. Retrieved captions can be
measured as text; this does not evaluate the model's visual understanding.

## Controls and provenance

- Phase 1 observes the current configuration. It does not add active-chat tuning.
- Phase 2 parameters belong to an experiment. K can later be applied explicitly
  through existing settings; context budget and reranker currently require
  infrastructure configuration/restart. Changing production retrieval strategy
  remains a measured code change.
- A different embedding or chunking policy requires a separate collection and
  reindex. Existing vectors cannot be reused with an arbitrary new model.
- Capture parameters on submission, including reranker ID/device/precision/window.
  Reuse the existing cached scorer for that configuration. If it is missing,
  explain the failure before measuring; continuing without it is an explicitly
  labelled variant. CUDA reordering can compete with Ollama for VRAM.
- Each run identifies its dataset, corpus, collection, published IDs, file hashes,
  `Config.identity()`, K, context budget and reranker. Code identity hashes a fixed,
  declared file list, not an import traversal. A later commit label may supplement
  it. Reports from an older/emptied library remain readable as historical runs.

## Input and privacy boundaries

The existing JSONL schema is retained; see [evaluation](EVALUATION.md) for examples
and equivalent-judgment groups. The web adapter will accept at most 1 MiB and 100
questions, with bounded fields and explicit errors by line. References must name
published documents/pages or published vector IDs before any model is loaded.
The browser uploads content, not an arbitrary server-side path. All content stays
under ignored `data/`; it is never sent to the response provider by this stage.

Phase 0's `evaluate_snapshot` accepts an **already frozen collection** supplied by
its caller. It does not yet implement snapshot creation, job persistence or the
web lifecycle. Its caller supplies the embedding factory and optional reranker;
importing the module loads neither. It invokes no generator. The existing research
CLI's experimental functions live in `retrieval_experiments.py`; `tools/` remains
a wrapper with its previous explicit download opt-in.

The contract tests in `tests/unit/test_retrieval_evaluation.py` compare against
fixed outputs from `654abed`: complete and clipped contexts, figure captions,
native/visual channels, automatic source scope and reranking. Other cases cover
unpublished vectors, validation before embedding loading, missing annotations,
cancellation with partial results, and unchanged vector/manifest bytes. They
use fabricated CPU vectors, not a measurement of the user's corpus.

No new paid fonts, providers, profiles or external ingestion connectors are part
of this delivery. Audio/video, enrichment, anonymization, late chunking and
multi-model answer evaluation remain separate proposals.
