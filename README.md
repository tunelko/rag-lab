# RAG Local

**English** · [Español](README.es.md)

[![CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml)

A local, multimodal document assistant for Windows and Linux. Chat with your PDF,
text and image collection, inspect citations, and monitor processing in a web UI.
Setup uses an OMP-style terminal wizard. The web interface supports English and Spanish.

## Install

**Upgrading an existing Chroma library?** Follow [vector migration](docs/VECTOR_MIGRATION.md)
before updating dependencies. Fresh installations need no migration.

**Requirements:** Python **3.13 x64**, Git and Ollama; Windows x64 or Linux x86_64.
Install [Python](https://www.python.org/downloads/windows/),
[Git](https://git-scm.com/install/) and [Ollama](https://ollama.com/download).
For Linux Python installation, see the [complete setup guide](docs/INSTALLATION.md#linux).

**1. Download the project:**

```text
git clone https://github.com/tunelko/rag-lab.git
cd rag-lab
```

**2. Check Ollama and download a model:**

```text
ollama list
ollama pull qwen3.5:9b-q4_K_M
```

This example downloads several GB and supports text and vision. Select a model
suited to your hardware. If `ollama list` cannot connect, open Ollama or run
`ollama serve` in another terminal.

**3. Open the wizard from the project directory:**

```powershell
# Windows — PowerShell, process-only execution policy
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Configurar.ps1
```

```bash
# Linux — Python 3.13 with venv support must be available
bash configurar.sh
```

**4. Follow these screens:**

1. **Entorno Python:** choose **Ollama GPU + embeddings CPU**, click **Instalar dependencias**, and wait for completion.
2. **Modelos Ollama:** refresh models, select text and enable multimodal processing with a vision model. One compatible model can serve both roles.
3. **Recuperación RAG:** start with 4 fragments and the defaults.
4. **Revisar y guardar:** review, confirm and save.
5. **Listo:** click **Iniciar web**, then open **http://127.0.0.1:8765**.

The wizard installs packages in `.venv`, uses its own environment in `.cache`,
preserves existing data and creates configuration backups. The server runs in the
background. To start later: `Configurar.ps1 -Start` on Windows or
`bash configurar.sh --start` on Linux. [Detailed instructions](docs/INSTALLATION.md).

To manage the server in the background, use the project directory:

```powershell
.\Start-Web.ps1 start
.\Start-Web.ps1 status
.\Start-Web.ps1 restart
.\Start-Web.ps1 stop
```

On Linux: `bash web.sh start|status|restart|stop` (choose one action).
Both launchers also accept `--start`, `--stop`, `--restart`, `--status` and `--help`.
Windows also accepts PowerShell switches such as `-Restart` and `-h` for help.
The port comes from `.env`; override with `-Port 8766` on Windows or `--port 8766`
on Linux. Logs are in `reports/rag-server-runtime*.log`. Stop/restart refuse active
jobs; cancel or finish them first. These commands leave Ollama and documents alone.

## First query

In **Configuración**, click **Preparar modelo de búsqueda** to download the CPU
embeddings. Upload a small TXT through **Biblioteca → Subir e indexar**. Wait for
**Indexado**, ask a question in **Chat**, and open **Contexto recuperado** to
verify the original source. Start with a short PDF before indexing large batches.

## Multimodal RAG

The chat uses the full workspace width. Citations open a sliding source panel;
Escape closes it. Configure the response provider/model, automatic/manual mode,
fragment counts and retrieval-only mode in Settings (the top-right gear). Settings
save automatically and apply to chat; the **+** button attaches images. The system
status icon opens connection and hardware details. The top-right flag selector
switches Spanish/English; fonts and translations are served locally.

The response provider can remain **Ollama local** or be changed to Scaleway,
OVHcloud, xAI/Grok or **Groq**. External providers receive the question, conversation
history and retrieved text chunks. **Vision model for answers** may also send images
needed for that answer. **Local indexing** always uses Ollama for image descriptions
and CPU for extraction/embeddings; it never calls a cloud provider. Its vision model
is configured separately. Changing answer providers/models does not require reindexing.
See [cloud answers and local indexing](docs/CLOUD_VISION.md).
API keys are entered from Settings on the server host
(through loopback or its configured bind address) and are never returned to the browser: Windows encrypts them with
user-bound DPAPI, while Linux uses AES-GCM with an in-memory master password.
The **External model** dropdown loads the provider catalog automatically when a
credential is saved or a configured provider is opened. **Refresh models** reloads
it from `/v1/models` server-side. Select a model from the list; the choice is saved
automatically and restored after reloading the page. For Scaleway, store the **Secret
Key** used as the Generative APIs Bearer credential; the Access Key is not used for
inference. Its IAM bearer must have `GenerativeApisModelAccess` (least privilege),
`GenerativeApisFullAccess` or `AllProductsFullAccess` for the intended Project or
Organization.

For Groq, select **Groq** in Settings, save an API key from the
[Groq console](https://console.groq.com/keys), then choose a model from the live
dropdown. Audio models, classifiers and Compound systems are excluded. Streaming
usage counters support Groq's native format. Global document tables use JSON
object mode with the application's existing per-document validation. Groq pricing
is not integrated; the UI shows it as unavailable instead of guessing a rate.

For Scaleway, the selected model also shows live input/output rates (and cached
input where published), fetched from the public Product Catalog API on every
catalog refresh. Token rates are displayed in EUR per million tokens; audio uses
its own minute unit. The panel shows the fetch time and source, excludes batch
prices, and does not apply taxes, free tiers or account discounts. Missing rates
remain unavailable rather than being treated as free. Other providers do not yet
have an API pricing integration.

In **Biblioteca**, organize **categories and subcategories** in the sidebar. Use
**+** to create a category and its menu to add subcategories or rename it. Select
documents and move them in bulk; the upload destination follows the chosen category.
The chat's **Sources** selector searches the entire library, one category including
its subcategories, one subcategory, or uncategorized documents. Organization is stored under `data/` and preserves
existing PDFs, vectors and captions. **Empty library** starts over: after a typed
confirmation it moves documents, index and categories to a dated folder under
`data/library-backups/` instead of deleting them. See [library organization](docs/LIBRARY.md).

- **Ingest:** extract PDF text/tables and render visual or scanned pages. A local Ollama
  vision model describes the images; descriptions and text enter the local SQLite vector store.
- **Retrieve:** search text and image descriptions using multilingual embeddings.
- **Rank:** focused questions use a native-text channel, automatic document scope
  and an optional local cross-encoder. See the [focused retrieval pipeline](docs/RETRIEVAL.md#focused-retrieval-pipeline).
- **Answer:** use local Ollama or an explicitly selected external provider. The selected
  vision model can inspect original images. Answers include citations.
- **Inspect:** Library shows jobs, Statistics offers resource graphs and timings,
  and Console shows server output. Chat renders Markdown and code.

Statistics keeps [seven days of local history](docs/STATISTICS.md), including day/week
charts and query/indexing records. A [CPU-only visual audit](docs/VISUAL_AUDIT.md)
evaluates page selection and human labels before changing multimodal ingestion.
An [experimental offline retrieval evaluation](docs/EVALUATION.md) compares exact dense, FTS5 and
hybrid ranking with Recall@K, MRR, nDCG and P50/P95 over human-labeled questions.

The [Laboratory](docs/LABORATORY.md) validates local JSONL questions and measures
the chat's focused retrieval on a temporary index snapshot. It shows literal
evidence coverage and exact context per question, with cancellation and reload
recovery. It does not generate answers or change chat settings.

Supports PDF, TXT, Markdown, PNG/JPEG/WebP and supported MinerU exports. Descriptions
are cached. Visual indexing can take seconds per page. Jobs share a sequential
queue; questions wait during indexing. Check original sources for critical details.

Library counts/listings read the actual index register. General list requests,
such as “Dame la lista de cpus admitidas”, and questions about all documents retrieve
evidence from every indexed file with a shared context budget;
the 2/4/6/8 fragment setting applies to focused questions. [Retrieval details](docs/RETRIEVAL.md).

## Layout and environments

```text
requirements/     Runtime, CPU/CUDA, wizard and development profiles
tests/unit/       Offline Python regression tests
tests/browser/    Isolated browser QA and read-only smoke checks
tests/frontend/   JavaScript tests
dist/             Web UI, local fonts and libraries
docs/             Installation, reference and development guides
docs/archive/     Historical design material
benchmarks/       Synthetic diagnostics and private evaluation sets
data/             Private runtime data, ignored by Git
```

Use `requirements/ollama.txt` for the recommended runtime or `requirements/cuda.txt`
for advanced HF/NF4. PyTorch has separate CPU/CUDA files; the wizard handles order.
See [dependency profiles](requirements/README.md) and [test commands](tests/README.md).

```text
python -m unittest discover -s tests -t . -v
node --test tests/frontend/*.test.mjs
python -m tests.browser.qa_redesign
```

[CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml) runs CPU tests on
Windows/Linux, with browser QA on both, using fake models and an isolated corpus.
Ollama handles GPU generation independently of CPU embeddings.

The server defaults to loopback. An explicit `-Host`/`--host` enables trusted LAN
access; see [installation](docs/INSTALLATION.md#trusted-lan-access). Documents, models, indexes and private settings
stay outside Git. Initial downloads need Internet. More: [installation](docs/INSTALLATION.md),
[Spanish reference](docs/REFERENCE.es.md), [diagnostics](docs/DESARROLLO.md).
The project's license is pending; third-party notices remain intact.
Dependency advisories and local deployment boundaries: [security status](SECURITY.md).
Implemented architecture, reviewed defects and remaining limits: [implementation audit](docs/AUDIT.md).

The interface supports English and Spanish, automatic browser detection, a
persistent language selector and a persistent light/dark appearance toggle. New
answers use the selected language through local Ollama. See
[languages](docs/INTERNATIONALIZATION.md) and the [scaling roadmap](docs/SCALING.md).
