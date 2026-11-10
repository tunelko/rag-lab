# Library-wide queries

The query planner distinguishes three cases locally, without a classifier LLM:

- **Inventory:** questions such as «¿Cuántos documentos tienes indexados?» read the
  actual library register. Listings link every indexed file. They do not call a model.
- **Focused question:** the usual semantic top-K retrieval, using the configured
  2/4/6/8 fragment count.
- **Explicit global question:** «De todos los documentos, enumera…» or a question
  about each manual/device scans the stored vectors and text per indexed file.
  Every file contributes evidence, with equal context allowance.

Global retrieval embeds a focused version of the question in CPU, calculates exact
cosine scores per document and combines dense and BM25 rankings through reciprocal
rank fusion. Short explicit field phrases and native text/table fields take precedence
over generic visual descriptions. A small bilingual technical alias set handles
terms such as processor/CPU and soporta/support. These rules need corpus-specific
evaluation; they are not a guarantee of semantic completeness.

The rank-fusion method follows [Cormack et al., SIGIR 2009](https://cormack.uwaterloo.ca/cormacksigir09-rrf.pdf).
No neural reranker or additional GPU model is loaded.

If an indexed document cannot be retrieved, or the configured context cannot fit
the minimum allowance for every file, the query stops explicitly. It never silently
sends four sources and labels them the full library. Metrics expose
`documents_indexed`, `documents_retrieved`, `coverage_complete` and `retrieval_mode`.
Statistics shows document coverage; chat shows progress while files are inspected.

Coverage means each file is represented in the prompt, not that every page is sent
to the LLM. The model receives the best matching evidence per file, with citations,
and is instructed to keep one table row per file, including language variants,
identify missing details and keep source identities separate. A manual listing CPU
families does not become an exhaustive list
of individual supported processor SKUs. Original sources remain the authority.

Global generation explicitly requests an 8192-token context by default through
`RAG_OLLAMA_GLOBAL_CONTEXT`, without changing global Ollama/OMP settings. The evidence
character budget remains `RAG_CONTEXT_CHARS`. Character limits are not exact token
counts, especially for multilingual content. Global output uses
`RAG_GLOBAL_MAX_NEW_TOKENS` (1536 by default), leaving the focused-question
`RAG_MAX_NEW_TOKENS` setting unchanged. These are bounded per-request options,
not changes to global Ollama settings.

These changes reuse existing vectors and visual descriptions; reindexing is not
required. Restart the RAG server after a Python update, then refresh the web UI.
