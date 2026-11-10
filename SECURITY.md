# Security and dependency status

RAG Local is a single-user application bound to loopback by default, with an embedded Chroma
database. An explicit private IPv4 bind permits access from a trusted LAN. It is not a hosted Chroma server or a multi-tenant service. Do not expose
the local UI through a public proxy or run `chroma run` against its private index.

## Dependency review — 2026-11-11

GitHub reported 23 open alerts representing seven distinct advisories. Chroma is
included by five requirements profiles, so each Chroma advisory appears five times.
Dependency updates and application-level mitigations have different meanings: a
mitigation does not patch the dependency or close its alert.

| Dependency | Advisory | Action / status |
|---|---|---|
| setuptools | [GHSA-5rjg-fvgr-3xxf](https://github.com/advisories/GHSA-5rjg-fvgr-3xxf) | Fixed by the 84.1.0 pin in the Windows environment snapshot (PR #1). |
| setuptools | [GHSA-h35f-9h28-mq5c](https://github.com/advisories/GHSA-h35f-9h28-mq5c) | Fixed by the same update. |
| setuptools | [GHSA-xmj3-9gf7-h52w](https://github.com/advisories/GHSA-xmj3-9gf7-h52w) | Fixed by the same update. |
| ChromaDB | [GHSA-36p7-vc44-83pf](https://github.com/advisories/GHSA-36p7-vc44-83pf) | Server collection-update code injection; no patched release listed. |
| ChromaDB | [GHSA-f4j7-r4q5-qw2c](https://github.com/advisories/GHSA-f4j7-r4q5-qw2c) | Server collection-creation code injection; no patched release listed. |
| ChromaDB | [GHSA-xph7-9rjv-w5fr](https://github.com/advisories/GHSA-xph7-9rjv-w5fr) | Server RBAC scope checks; no patched release listed. |
| ChromaDB | [GHSA-2wm9-hf6c-p5cr](https://github.com/advisories/GHSA-2wm9-hf6c-p5cr) | Server cross-tenant authorization; no patched release listed. |

The published ChromaDB version remains 1.5.9. Its 20 alerts must remain open until
a fixed version is available and tested, or the dependency is safely replaced.
Changing the requirements layout, suppressing alerts, or lowering the version
does not remediate them.

The latest API inspection still returned all 23 open alerts. GitHub's SBOM reports
setuptools 78.1.0, while the repository pin and inspected local environment are
84.1.0. This is a dependency-graph discrepancy, not evidence that GitHub has
closed those alerts. No alerts were dismissed during this review.

## Controls in this application

- Chroma uses an explicitly selected embedded Rust implementation and a local
  persistence directory. Shell variables cannot switch this client to Chroma HTTP.
  Administrative database reset is disabled.
- The web app does not publish Chroma's `/api/v2/tenants/.../collections` endpoints
  or accept collection embedding-function configuration from browser requests.
- Embeddings are supplied by the application with `trust_remote_code=False`.
  Downloading a model does not authorize execution of remote model code.
- Runtime Ollama HTTP calls use a direct connection without ambient HTTP proxies
  and reject redirects, keeping document context at the explicitly configured
  endpoint. This does not protect against a compromised endpoint/model server.
- The UI server defaults to `127.0.0.1`. `--host` accepts loopback or an assigned
  RFC1918 IPv4 address, never a wildcard/public bind. Mutating requests require a
  session token and a matching origin; Host validation includes only the explicit bind.
- LAN access shares the same library, configuration, jobs, statistics and console.
  The token prevents cross-origin mutations; it is not a login or a per-user role.
  Topics organize retrieval, not access permissions. Use only on a trusted network.
- Query attachments are validated PNG/JPEG/WebP, limited to four images, 3 MiB
  each and 16 megapixels, then resized and stripped of metadata. Raw attachments
  are not saved to chat history or the document library.
- Fonts, icons, translations and Markdown rendering are self-hosted. Private
  documents, indexes, topic assignments and credentials stay outside Git.

These controls keep the server entry points described by the Chroma advisories
outside this application's normal execution path. They are not a fix for ChromaDB
itself or a guarantee against other vulnerabilities.

## Validation and follow-up

CPU regression tests verify persistent retrieval despite hostile Chroma client
environment settings, disabled reset, and absence of the affected HTTP endpoints.
The CI runs on Windows and Linux. Model weights and GPU workloads are not needed
for these checks.

Updating repository pins does not update an existing virtual environment. Apply
the reviewed setuptools update to that environment separately and run `pip check`.
Review Dependabot again when a Chroma fix is published; test compatibility on an
isolated index before changing an existing document library.

For a private security report, use GitHub's private vulnerability-reporting feature
if enabled. Otherwise, request a private contact without posting exploit details,
documents or credentials in a public issue.
