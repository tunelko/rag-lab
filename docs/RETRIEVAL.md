# Library-wide queries

The query planner distinguishes three cases locally, without a classifier LLM:

All cases respect the **Sources** topic selection. A category includes its subcategories;
an empty topic returns no evidence, and an unknown topic is rejected. Organization
does not require reindexing. See [library topics](LIBRARY.md).

- **Inventory:** questions such as «¿Cuántos documentos tienes indexados?» read the
  actual library register. Listings link every indexed file. They do not call a model.
- **Focused question:** the usual semantic top-K retrieval, using the configured
  2/4/6/8 fragment count.
- **Library-wide request:** «De todos los documentos, enumera…», a question about
  each manual/device, or a general list without a specific device selector such
  as «Dame la lista de cpus admitidas» scans the stored vectors and text per indexed file.
  Every file contributes evidence, with equal context allowance.

## Focused retrieval pipeline

A focused question goes through `retrieval_pipeline.retrieve`, the same code that
`tools/evaluate_retrieval` measures as `dense-channel-scoped-reranked`:

1. **Native channel.** The search covers text and table fragments, plus an image
   caption only when its page has no native text. Captions are written in the
   interface language; searched together with the originals they take almost every
   position for a question in that language and push out the text that holds the
   answer. A question that asks about a figure, diagram or location keeps every
   channel, using the same rule that routes it to the vision model.
2. **Automatic document scope.** When the question names a board model, the search
   is limited to the documents of that model, inside whatever **Sources** scope the
   user chose; it never widens it. Model names come from file names, first-page
   titles and the chipset stated on the first native pages. The words that spell the
   model are removed from the search text, because fragments never repeat the
   product name. A question that names no model searches the whole chosen scope.
3. **Reordering (optional).** With `RAG_RERANKER` set, a local cross-encoder
   reorders the first `RAG_RERANK_WINDOW` candidates (default 50). It is loaded on
   the first question from the local model cache and is never downloaded by the
   service; if it cannot be loaded, or fails, answers use the dense order and the
   server log says so. `RAG_RERANK_DEVICE` (`auto`, `cuda`, `cpu`) and
   `RAG_RERANK_HALF` (half precision on CUDA, on by default) control where it runs.

Job metrics record the strategy, whether a scope was applied and to how many files,
the candidate count and the reordering time, never the question text.

Measured on 68 labelled questions written after the configuration was fixed
(`evidence_recall@8`, the share of questions whose answer text reaches the model):
0.53 for the previous all-channel search, 0.88 with steps 1 and 2, 0.91 adding
`cross-encoder/mmarco-mMiniLMv2-L12-H384-v1` and 0.97 adding
`BAAI/bge-reranker-v2-m3`. On an RTX 3080 the larger model in half precision uses
about 1.2 GB of VRAM and 0.2 s per question; on CPU it needs several seconds, so a
CPU-only installation should use the smaller model or none. These figures describe
one library of motherboard manuals in eight languages; see
[evaluation](EVALUATION.md) for the method and its limits. They measure retrieval,
not whether the generated answer is correct.

Global retrieval embeds a focused version of the question in CPU, calculates exact
cosine scores per document and combines dense and BM25 rankings through reciprocal
rank fusion. Short explicit field phrases and native text/table fields take precedence
over generic visual descriptions. A small bilingual technical alias set handles
terms such as processor/CPU and soporta/support. These rules need corpus-specific
evaluation; they are not a guarantee of semantic completeness.
CPU specification fields listing processor families also qualify when the manual
states a socket/type without an explicit verb such as “supports”.

The rank-fusion method follows [Cormack et al., SIGIR 2009](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf).
No neural reranker or additional GPU model is loaded.

Punctuation-only fragments such as `---` are excluded from both retrieval modes.
Focused searches rank their safety window once when empty captions may occupy the
nearest positions; the question is not embedded again while widening a window.
The exact vector matrix is reused from a validated in-memory snapshot and reloaded
after index changes. Existing vectors remain usable; no destructive reindexing is needed.
Lists naming a specific model/file or referring to “esta placa” stay focused.

SQLite FTS5 and dense/lexical reciprocal-rank fusion are available to the offline
[evaluation harness](EVALUATION.md). Focused production retrieval remains exact dense
until labeled measurements justify changing it.

If an indexed document cannot be retrieved, or the configured context cannot fit
the minimum allowance for every file, the query stops explicitly. It never silently
sends four sources and labels them the full library. Metrics expose
`documents_indexed`, `documents_retrieved`, `coverage_complete` and `retrieval_mode`.
Statistics shows document coverage; chat shows progress while files are inspected.

Coverage means each file is represented in the prompt, not that every page is sent
to the LLM. The model receives the best matching evidence per file, with citations,
and returns a structured answer with a required string for every source ID. The
application renders the Markdown table and inserts document names and citations;
these identities do not depend on the model copying a filename correctly. Fully
decoded rows appear during streaming. Missing/invalid rows fail explicitly instead
of marking the answer complete. Metrics also include `answer_documents` and
`answer_coverage_complete`. This validates structural coverage, not factual truth.

This uses Ollama's [local structured outputs](https://docs.ollama.com/capabilities/structured-outputs).
CPU-support lists additionally compare explicit numbers and socket claims with each
source. On a detected mismatch, or a false absence of named CPU families, that row
shows a literal source excerpt. `answer_grounding_fallbacks` records the count.
This is a conservative check, not a general proof of factual entailment; translations
or reformulations can also fall back to the original excerpt.
The model must identify missing details and keep source identities separate. A manual listing CPU
families does not become an exhaustive list
of individual supported processor SKUs. Original sources remain the authority.

Global generation explicitly requests an 8192-token context by default through
`RAG_OLLAMA_GLOBAL_CONTEXT`, without changing global Ollama/OMP settings. The evidence
character budget remains `RAG_CONTEXT_CHARS`. Character limits are not exact token
counts, especially for multilingual content. Global output uses
`RAG_GLOBAL_MAX_NEW_TOKENS` (2048 by default), leaving the focused-question
`RAG_MAX_NEW_TOKENS` setting unchanged. These are bounded per-request options,
not changes to global Ollama settings.

These changes reuse existing vectors and visual descriptions; reindexing is not
required. Restart the RAG server after a Python update, then refresh the web UI.

PDF citations retain the physical, one-based page position used during indexing.
The UI labels it **pág. PDF** and also shows the PDF's declared document numbering
when it differs (for example, physical page 164 may carry label 8 in a multilingual
manual). Labels can repeat across language sections. They are read from the
original PDF, never inferred from OCR. A missing label remains unknown. Opening
a PDF source uses its physical `#page=` position in a new tab; no reindex is needed.
