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
