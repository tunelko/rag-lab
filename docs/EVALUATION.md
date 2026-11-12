# Retrieval and RAG evaluation

The planned web Laboratory and its delivery boundaries are described in
[Laboratory design](LABORATORY.md). Phase 0 adds a shared production-path engine
and equivalence tests; it does not yet add a web screen or change the live chat.

> **Experimental:** this harness does not alter production retrieval. Keep the
> optional lexical index disabled on a real library until its preparation and
> adoption checks have been validated for that release.

RAG Local can compare exact dense retrieval, SQLite FTS5/BM25 and reciprocal-rank
fusion against the same explicit relevance judgments. Evaluation is local: it does
not call Ollama, send documents to a service or treat generated text as ground truth.
The command does not switch the production retriever.

## Create a small trusted set first

Start with about 50 questions from the current corpus. Include direct facts, exact
technical identifiers, tables and multi-document questions. A human must identify
the source file and physical PDF page (or exact vector
ID) that constitutes relevant evidence.

Save one JSON object per line, for example `benchmarks/manuals.jsonl`:

```json
{"id":"cpu-001","question":"¿Qué zócalo usa esta placa?","relevant":[{"file":"manual.pdf","page":8,"relevance":2}]}
{"id":"cpu-002","question":"¿Dónde está el conector ATX_12V?","relevant":[{"file":"manual.pdf","page":26}]}
```

`file` is the relative name shown in Library and `page` is the physical, one-based
PDF page. Omit `page` only when any page from that file is a correct target. Use
`vector_id` instead of `file` for exact-chunk judgments. `relevance` defaults to 1;
larger positive values enable graded nDCG. An optional `files` array restricts a
query to the same topic/source scope used in the UI.
Use one judgment granularity within each question: all exact chunks, all pages or
all whole files. Mixed granularities are rejected rather than scored ambiguously.

When several editions or translations contain interchangeable evidence, give those
judgments the same nonempty `group`. The group is covered when any member is found
and contributes only once to Recall, Fragment Recall and nDCG:

```json
{"id":"z790-reset","question":"Where is JBAT1?","relevant":[{"file":"PROZ790-AMAXWIFI_EN.pdf","page":36,"group":"reset-location"},{"file":"PROZ790-AMAXWIFI_DE.pdf","page":36,"group":"reset-location"}]}
```

Members of one group must have the same relevance grade. Without `group`, every
distinct target remains independently relevant. A target cannot belong to multiple
groups within one question. Use `files` instead when a question deliberately tests
retrieval from one particular language or edition.
Add `strata` such as `same-language`, `cross-language`, `cjk`, `table`, `visual` or
`exact-identifier`; the report keeps separate metrics for each declared group.

An optional nonempty `evidence` array adds literal answer anchors. Matching applies
Unicode NFKC, case folding and whitespace normalization. Anchors may span multiple
selected chunks from the same relevant physical page, ordered by their stored chunk
number; text from different pages is never joined. This measures whether the actual
Top-K context contains an expected answer string, rather than merely reaching the
right page. Arbitrary regular expressions are deliberately not accepted.

Do not manufacture labels from the current retriever or LLM response. That would
make the benchmark reward its own errors. Keep private corpus judgments out of Git.

## Run the comparison

Dense evaluation needs no index change. Lexical and hybrid evaluation use an
optional derived FTS5 index. Prepare it as a separate, explicit operation while
the server is stopped or idle:

```text
python -m tools.prepare_lexical_index
```

This takes the collection writer lock, makes a verified SQLite/manifest backup in
`data/vector-backups/`, builds FTS and leaves the base `user_version=1`. It does not
load a model or use the network. Check with `--status`; remove only the derived
index with `--remove`.

From the project root:

```powershell
.\.venv\Scripts\python.exe -m tools.evaluate_retrieval `
  --dataset benchmarks\manuals.jsonl `
  --mode dense lexical hybrid
```

For the diagnostic channel experiment, use `--mode dense dense-native
dense-routed`. `dense-native` removes `page_image`/`figure` captions from the
candidate ranking. `dense-routed` keeps all channels for an explicitly visual
question; otherwise native text/tables are ranked first and visual captions only
fill unused positions. Both preserve the requested K context budget and leave
production retrieval unchanged.

The cumulative no-model experiment adds two more offline modes:

```text
--mode dense dense-native dense-native-scoped dense-native-scoped-expanded
```

`dense-native-scoped` derives model identifiers from document filenames and native
first-page titles. When a question contains an unambiguous identifier, search is
restricted to every matching edition; explicit dataset `files` always wins and no
match falls back to the complete library. Each decision and matched alias is written
to the report. `dense-native-scoped-expanded` then adds the immediately previous and
next native chunk from each selected fragment's physical page, deduplicates them and
applies `RAG_CONTEXT_CHARS` exactly as answer construction does. It reports seed and
expanded evidence separately, context characters and fragment/channel counts. These
modes do not modify the index or production query path.

The `dense-native*` modes filter the global 512-candidate window after ranking. For
a question in a language other than the document's, that window can be almost all
captions and leave only a handful of native candidates. The channel modes rank the
native channel itself:

```text
--mode dense dense-channel dense-channel-scoped dense-channel-scoped-reranked
```

- `dense-channel` searches text and table fragments, plus a caption only when its
  page has no native text at all. The report states `channel_fragments`.
- `dense-channel-scoped` adds automatic scope with two extra alias sources: the
  letters-and-digits prefix of a long filename token (`Intel600BIOS` answers
  "Intel 600") and the chipset code stated on the first five native pages
  (`Intel H110 Chipset` answers "H110" for a file named `M7994v1.4.pdf`). When the
  scope comes from an alias, the words that spell it are removed from the search
  query, recorded as `search_query`: fragments never contain the product name, so
  keeping it only favours covers and notices.
- `dense-channel-scoped-reranked` reorders the first `--rerank-window` candidates
  (default 50) with a local cross-encoder, `--reranker` (default
  `BAAI/bge-reranker-v2-m3`), on `--rerank-device` (`auto` uses CUDA when available).
  Nothing is downloaded without `--allow-download`. The report records the model,
  device, window, peak VRAM and reranking milliseconds per query.

`dense` and the channel modes report `candidate_evidence_recall@20/50/100/512`,
measured before reranking: the share of questions whose answer a reordering stage
could still reach.

Linux:

```bash
.venv/bin/python -m tools.evaluate_retrieval \
  --dataset benchmarks/manuals.jsonl \
  --mode dense lexical hybrid
```

The embedding model must already be cached. Add `--allow-download` only when an
explicit first download is intended. Reports are written below
`reports/evaluation/` and include the dataset hash, corpus identity, per-query hits,
cache diagnostics and aggregate metrics. Both locations are ignored by Git.

## Metrics

| Metric | Meaning |
| --- | --- |
| Fragment Recall@K | Relevant evidence groups covered by the first K raw fragments; this matches what production sends. |
| Recall@K | Recall after collapsing duplicate chunks to the labeled file/page/chunk unit. |
| Precision@K | Distinct relevant units among K collapsed positions. |
| MRR@K | Reciprocal rank of the first relevant collapsed unit. |
| nDCG@K | Collapsed rank quality with optional graded relevance. |
| Evidence Recall@K | Questions whose actual Top-K relevant-page text contains a declared literal answer anchor. |
| Native/Visual fragments@K | Mean channel composition of the context budget. |
| Candidate Evidence Recall@20/50/100/512 | Dense upper bound before Top-K selection; separates missing candidates from poor ordering. |
| Context chars/fragments@K | Actual expanded context after the configured character budget. |
| P50/P95 | Median and tail retrieval latency, including query embedding for dense modes. |

Default cutoffs are 1, 4 and 8 because the UI offers 2/4/6/8 fragments and defaults
to 4. The adoption verdict is computed, not merely described: it requires at least
50 questions, protected `exact-identifier`, `cross-language` and `cjk` strata,
non-regressing Fragment Recall@4, an nDCG@4 gain whose deterministic bootstrap
interval excludes zero, and a bounded absolute P95 overhead. Missing strata produce
`insufficient_data`; a small or incomplete set cannot approve hybrid retrieval.

Questions with no valid answer belong to answer-side abstention evaluation, not to
this retrieval-ranking command, which deliberately requires at least one explicit
relevance judgment per query. Use `score_abstention` for those cases until an
end-to-end answer evaluator is added.

The report records the evaluated hybrid parameters (`candidate_window=512` and
`rrf_k=60`). If hybrid retrieval is adopted, production must use those same values;
changing either one requires rerunning the benchmark and adoption checks.

`evaluation.py` also provides ANLS for short document answers, page/file citation
precision and recall, and abstention decision metrics. These are answer-side building
blocks; the retrieval CLI does not claim end-to-end answer quality.

Visual page selection is evaluated separately with [the visual audit](VISUAL_AUDIT.md).
Its precision/recall applies only to manually reviewed pages and must not be confused
with retrieval Recall@K.

## Public datasets

Public suites are useful only for the pipeline stage they actually exercise:

- BEIR/SciFact: retriever ranking and embeddings.
- FinanceBench and RAGBench (`emanual`, `techqa`): grounded long-document answers.
- DocVQA: page understanding and ANLS after the target page is known; it does not
  by itself test selecting the correct page from a PDF collection.
- CRAG/CRAG-MM: later web or multimodal experiments, not the first local PDF baseline.
- MITRE ATT&CK, NVD and CISA KEV: valuable technical corpora, but they need a curated
  question/relevance set before producing comparable metrics.

Adapters or downloads should be added only after the 50-question local baseline is
stable. Dataset licenses and original splits must be preserved.

## Storage and performance interpretation

SQLite is the durable source of truth. Exact dense search keeps one validated matrix
snapshot in process memory and checks a transactional generation counter to reload
after changes. Metadata and vectors remain queryable exactly from SQLite.
Top-K uses `numpy.argpartition`. The separate preparation command creates FTS5
under the index writer lock; it is a derived index maintained by SQLite triggers.
Dense production search neither creates it nor requires an SQLite build with FTS5.

The resident cache uses a generation counter advanced in the same SQLite transaction
as every new write. Legacy v1 stores without that metadata use a file signature only
until the first write creates the counter. Reports warm the model/cache before timing,
record embedding and search separately, and include cache loads/hits and vector bytes.
Rankings are collapsed to the labeled file/page/chunk granularity before scoring, so
two adjacent chunks from the same relevant PDF page are not counted as two results.
Fragment Recall@K is also recorded before that collapse to reflect the exact context
production would deliver.
Exact NumPy remains the default until measured quality and latency justify a production
change. FAISS or an ANN engine is not required for the current corpus size.

To isolate the storage/search improvement on the current index without loading the
embedding model or calling Ollama:

```text
python -m tools.benchmark_vector_store --output reports/vector-benchmark.json
```

This compares the previous reconstruct-and-full-sort mechanics with cold and warm
resident exact Top-K. It deliberately says nothing about answer quality.
Add `--lexical-copy` to measure FTS build/search and 500-row update/delete behavior
on an automatically discarded SQLite backup. The real index remains unchanged.
