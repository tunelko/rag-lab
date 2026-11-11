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
- Optional Scaleway, OVHcloud and xAI response adapters use fixed HTTPS hosts,
  bypass ambient proxies and reject redirects. They send retrieved text context
  only after explicit provider selection; document files, embeddings and images
  are not uploaded. API credentials are ciphertext in `data/credentials.sqlite3`,
  protected by user-bound Windows DPAPI or Linux AES-GCM/Scrypt. Credential
  mutation is restricted to requests originating on the server host (loopback or
  its configured bind address), and plaintext values are never returned or logged.
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

Tests verify transaction failures, cancellation, uncommitted vectors, scoped
retrieval, migration fidelity, interrupted migration, proxy/redirect refusal and
browser behavior. CI runs on Windows/Linux without inference models. `pip check`
checks dependency consistency; it is not a vulnerability scanner. Passing these
checks does not establish absence of every vulnerability or model error.

For a private report, use GitHub private vulnerability reporting if available, or
request a private contact without posting credentials/documents in an issue.
