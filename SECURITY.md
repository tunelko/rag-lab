# Security and dependency status

RAG Local is a shared personal application bound to loopback by default. An
explicit private-LAN bind is supported. It has no user accounts or tenant isolation.

## Remediation of the 23 alerts - 2026-11-11

The starting state was 20 Chroma alerts (four advisories repeated across five
requirements profiles) and three setuptools alerts. Chroma had no listed patched
release, so updating its version was not a solution.

| Dependency | Advisories | Remediation |
|---|---|---|
| ChromaDB | [GHSA-36p7-vc44-83pf](https://github.com/advisories/GHSA-36p7-vc44-83pf), [GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c), [GHSA-xph7-9rjv-w5fr](https://github.com/advisories/GHSA-xph7-9rjv-w5fr), [GHSA-2wm9-hf6c-p5cr](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr) | Removed ChromaDB and langchain-chroma from runtime and every installation profile. Replaced storage with SQLite and exact NumPy cosine search. |
| setuptools | [GHSA-5rjg-fvgr-3xxf](https://github.com/advisories/GHSA-5rjg-fvgr-3xxf), [GHSA-h35f-9h28-mq5c](https://github.com/advisories/GHSA-h35f-9h28-mq5c), [GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w) | Pinned 84.1.0 in the shared base profile and the Windows snapshot; verify the installed environment too. |

The vector store uses SQLite transactions, parameterized file/ID filters and
little-endian float32 vectors. Metadata is JSON; no pickle or executable
embedding-function configuration is deserialized. It has no vector database HTTP
server or administrative reset API. Search computes exact cosine distances on
CPU after applying scope and publication filters. This removes the affected
component rather than suppressing its advisories.

See [migration instructions](docs/VECTOR_MIGRATION.md) before upgrading an existing
library. The explicit one-time migration tool can read a copy through the previous
Chroma environment. The application never imports that dependency. After verified
migration, installers remove both retired packages; failed or missing migrations
block dependency replacement. Preserved database backups are data, not installed
packages or running services.

Repository remediation and GitHub's alert state are different observations. The
dependency graph must finish reprocessing the changed manifests. No alerts are
dismissed as false positives or manually marked fixed to conceal a dependency.
Check the Dependabot API/UI after publication; do not infer closure from test success.

The automatic pip graph job returned an empty manifest set for these profile names.
After successful main-branch tests, CI now submits all exact declared pins and
recursive requirements includes through GitHub's dependency submission API. This
snapshot is explicitly labelled declarative: it does not resolve unpinned transitive
dependencies and is not a full installed-environment audit. It fails on unpinned,
conflicting or unsupported declarations rather than omitting them silently.

## Application controls and boundaries

- Embeddings run with `trust_remote_code=False`; model downloads do not authorize
  execution of remote repository code.
- Ollama requests bypass ambient HTTP proxies and reject redirects. Cloud model
  tags and remote metadata are excluded from runtime choices. The explicitly
  configured model endpoint must still be trusted.
- Optional Scaleway, OVHcloud, xAI and Groq adapters use fixed HTTPS hosts,
  bypass ambient proxies and reject redirects. They send retrieved text context
  only after explicit provider selection. Selecting a cloud vision model also
  sends normalized figures/page images only when answering visual queries.
  The UI discloses this transfer; vision is not selected automatically when
  changing providers. Original files, embeddings and index remain on the host.
  Image-input capability and image counts are checked before sending; remote
  image URLs are not accepted by the adapter. Indexing always uses local Ollama
  and CPU; cloud credentials are never read by ingestion. Changing answer models
  does not alter the index identity or invalidate the local caption cache.
  API credentials are ciphertext in `data/credentials.sqlite3`,
  protected by user-bound Windows DPAPI or Linux AES-GCM/Scrypt. Credential
  mutation is restricted to requests originating on the server host (loopback or
  its configured bind address), and plaintext values are never returned or logged.
  A restart checks existing credentials before stopping the worker and refuses
  if they cannot be decrypted. A fresh start keeps Configuration accessible for
  recovery; unreadable keys are marked unavailable and can be replaced there.
- The web server defaults to 127.0.0.1. Explicit host selection permits only
  loopback or an assigned RFC1918 IPv4 address. Do not expose the personal UI
  through a public proxy.
- Mutations require the session token and matching origin. LAN clients share the
  same library, jobs, settings, console and history. This token is not a login.
  Categories filter retrieval; they are not access permissions.
- Source paths are constrained to the document directory. Query attachments are
  validated PNG/JPEG/WebP, at most four images, 3 MiB each and 16 megapixels, then
  resized and stripped of metadata. Raw query attachments are not persisted.
- Fonts, translations and sanitized Markdown rendering are local. Private
  documents, databases, settings, backups and credentials stay outside Git.
- Retrieved passages are serialized as JSON data before the user's question.
  The system prompt explicitly denies authority to instructions inside sources,
  including claims of system priority. This is defense in depth, not a guarantee
  that a particular model will resist every prompt injection.

## Real security checks

The suite uses unmodified Musa, actual HTTP and browser execution, real E5
embeddings and SQLite, and actual DPAPI/AES-GCM credential encryption. Its private
library is populated through API uploads. No production documents/settings are
used. Model cases call an explicitly chosen installed local Ollama model and
record its digest, answers and usage. No response or model transport is patched.

Retrieved evidence is quoted as JSON data, followed by the actual user question.
The system message distinguishes document content from instructions, including
claims of higher authority inside documents. This framing preserves evidence and
citations without a topic blocklist. It mitigates the tested injections; it cannot
guarantee resistance to every attack on every model.

Mappings follow [OWASP Web Top 10 2025](https://top10.owasp.org/2025/) and the
published [OWASP LLM Top 10 2026](https://github.com/GenAI-Security-Project/GenAI-LLM-Top10/tree/main/2026/final).
They identify exercised controls, not certification or complete risk coverage:

| Risks | Actual checks | Limits |
|---|---|---|
| A01, A02, A07 | Host/origin/token rejection, CSP/CORS, source containment | CSRF token is not authentication; no accounts or tenant isolation. |
| A03, LLM04 | Installed-package/npm advisories and served asset hashes | Advisory availability and model training provenance remain separate concerns. |
| A04 | Credential ciphertext persistence and plaintext absence | No deployment TLS or compromised-OS assessment. |
| A05, LLM10 | SQL-value payloads, unsafe uploads, URL rejection observed with a listening socket, browser XSS payloads | Controlled cases, not exhaustive fuzzing. No destructive or external attack targets. |
| A06, LLM03, LLM06 | Request bounds, actual output counts, model-to-filesystem action boundary | No load/DoS stress test. No model-controlled shell or general action tools exist. |
| A08, A10 | Duplicate uploads, corrupt PDF, preserved published index, malformed requests | Selected parsing failures, not every parser/format combination. |
| A09, LLM02, LLM08 | Synthetic secret canaries absent from API/logs/answers | Public system instructions are not secrets; external alerting is not tested. |
| LLM01, LLM05 | Malicious instructions in a genuinely indexed/retrieved document | Exact model and corpus only; no training-time/weight poisoning assessment. |
| LLM07 | Known factual answer and a question without supporting evidence | Two bounded cases, not a general accuracy benchmark. |
| LLM09 | Actual embedding search and out-of-scope canaries | Categories scope retrieval; they do not provide user access control. |

JSON/Markdown reports include every OWASP category, executed checks and limits.
Missing checks are `not_run`, never passes. Hosted CI runs real web/vector/browser
checks with pinned E5, while the eight LLM cases are explicitly `not_run`. Run the
model command in [tests/README.md](tests/README.md) against the deployment model.
Simulated-model Python and browser suites remain optional local regressions and
are excluded from CI. Model verdicts must not be transferred between models.

Bandit scans app, installer and tool source. HIGH findings block CI. Medium/low
findings remain `review` items in the full report and summary; there are no global
suppressions. pip-audit queries OSV for the actual installed inventory, including
transitives and CPU/CUDA versions. OSV also checks vendored npm versions. Any
known vulnerability, unaudited package or incomplete scan blocks the audit.
File hashes establish correspondence with the checked-in manifest, not upstream
authenticity. These scans do not audit GPU drivers or model weight provenance.
`pip check` only checks dependency consistency. Passing controlled tests does not
establish that the application or model is free of vulnerabilities.

For a private report, use GitHub private vulnerability reporting if available, or
request a private contact without posting credentials/documents in an issue.
