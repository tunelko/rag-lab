# RAG Local · Windows & Linux

**English** · [Español](../README.es.md)

[![CI](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml/badge.svg)](https://github.com/tunelko/rag-lab/actions/workflows/ci.yml)

Ask questions about your own documents using a local web chat, with citations and
links to the original pages. RAG Local combines CPU embeddings, a persistent
SQLite vector index and local Ollama models. It supports text, PDF tables and document
images, plus a terminal setup wizard inspired by OMP.

**Start here:** install the prerequisites, download the project, run the wizard,
choose a local model, then index a small document and ask a question. The steps
below cover a fresh installation. The web UI supports English and Spanish;
the terminal wizard uses Spanish labels.

## 1. Install the prerequisites

You need **Python 3.13, 64-bit**, Git and Ollama. The pinned installation targets
Windows x64 and Linux x86_64. You also need Internet for the initial package/model
downloads and enough disk space for those downloads and your document collection.
Ollama manages GPU acceleration; choose models that fit your hardware.

If these tools are already installed, check their versions and continue to step 2.

### Windows

Install these three tools:

| Tool | Install |
|---|---|
| Python **3.13 x64** | [Python Windows downloads](https://www.python.org/downloads/windows/). Select a **3.13** release and include the Python launcher. |
| Git | [Git for Windows](https://git-scm.com/install/windows). |
| Ollama | [Ollama for Windows](https://ollama.com/download/windows). Open Ollama after installation. |

Open a **new PowerShell window** after installing them and check:

```powershell
py -3.13 --version
git --version
ollama --version
```

The first command must report Python 3.13.x. A different Python minor version is
not supported by this pinned setup.

### Linux

Install Git, curl and Python 3.13 with virtual-environment support using your
distribution's packages. If your distribution does not provide Python 3.13, you can
install a managed Python using [uv](https://docs.astral.sh/uv/guides/install-python/).

For Ubuntu/Debian, this is one complete prerequisite setup:

```bash
sudo apt update
sudo apt install -y git curl ca-certificates
curl -LsSf https://astral.sh/uv/install.sh | sh
. "$HOME/.local/bin/env"
uv python install 3.13
export RAG_SETUP_PYTHON="$(uv python find 3.13)"
```

The exported variable tells the launcher which Python to use. Keep using this
terminal, or set it again in a new terminal if Python 3.13 is not on your PATH.
If you installed Python through your distribution instead, verify
`python3.13 --version` and make sure `python3.13 -m venv` is available.

Install Ollama using its [official Linux installer](https://docs.ollama.com/linux):

```bash
curl -fsSL https://ollama.com/install.sh | sh
ollama --version
```

These prerequisite commands install tools on your machine. The project wizard
below installs its Python packages inside the project directory.

## 2. Download the project

Run this in PowerShell or your Linux terminal:

```text
git clone https://github.com/tunelko/rag-lab.git
cd rag-lab
```

Keep all project files together: the launchers, Python code, `dist/` and requirements
files are needed. Run the remaining commands from this `rag-lab` directory.

## 3. Download a local model and check Ollama

Check the server:

```text
ollama list
```

If it cannot connect, open the Ollama application or run `ollama serve` in another
terminal. If it is already running, reuse that instance.

Choose a local text model, and a vision-capable model if you want document images.
One example that can serve both roles is
[Qwen 3.5 9B Q4_K_M](https://ollama.com/library/qwen3.5:9b-q4_K_M):

```text
ollama pull qwen3.5:9b-q4_K_M
```

This explicitly downloads several GB of model weights. Its suitability depends on
available memory and context length. You can use a smaller model or another local
model instead; the wizard lists the models installed on your Ollama server. It
excludes cloud models and validates vision capability when Ollama is connected.

## 4. Open the setup wizard

**Windows — PowerShell:**

```powershell
.\Configurar.ps1
```

If PowerShell blocks the script, launch it with a policy that applies only to this
process:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\Configurar.ps1
```

**Linux:**

```bash
bash configurar.sh
```

The first launch installs Textual into **`.cache/wizard-venv`**, a separate setup
environment. The RAG application uses **`.venv`**. You do not need OMP or Node to run
the wizard or the application.

Use **Tab / Shift+Tab**, arrow keys and Enter to navigate controls. **Ctrl+→** and
**Ctrl+←** move between steps; **Ctrl+Q** exits. Narrow terminals hide the help pane.

## 5. Complete the wizard

| Screen | What to do |
|---|---|
| **Bienvenida** | Check Python, Ollama, existing environments and active jobs. |
| **Entorno Python** | Choose **Ollama GPU + embeddings CPU**, then press **Instalar dependencias**. Wait for installation to finish in the bottom log panel. |
| **Modelos Ollama** | Keep `http://127.0.0.1:11434`, press **Actualizar modelos**, and select your text model. Enable multimodal processing and choose a vision model if needed. The same compatible model can fill both roles. |
| **Recuperación RAG** | Start with 4 retrieved fragments, collection `documents`, chunk size 500 and overlap 80. Leave port 8765 unless it is already in use. |
| **Revisar y guardar** | Review the changes, tick the confirmation checkbox and press **Guardar configuración**. |
| **Listo** | Press **Iniciar web**, or use the commands in step 6. |

The recommended profile installs **PyTorch CPU for embeddings**. Ollama still uses
its own GPU runtime for generation. The advanced **PyTorch CUDA 13.0 + HF NF4**
profile is available for direct Transformers/bitsandbytes use and requires a
compatible NVIDIA driver and model. The wizard does not install GPU drivers.

Saving writes `.env` and `data/settings.json`, with backups under
`.cache/configuration-backups/`. It preserves existing documents and indexes.
Dependency installation is blocked while the RAG server is open; saving is blocked
while jobs are active. Close the server after its work finishes to update packages.
Changes to `.env` require a server restart.

### How requirements files work

`requirements/ollama.txt` is a **pip requirements file**. Its line
`-r base.txt` includes the shared RAG dependencies from the adjacent file. That
file includes `-r web.txt` for FastAPI, upload and Uvicorn dependencies.

The wizard installs the selected PyTorch build **first**, then processes the
requirements files with pip and runs `pip check`. It handles this order for you.

See [pip's requirements format](https://pip.pypa.io/en/stable/reference/requirements-file-format/).

## 6. Start the web application

You can start it later with the same launcher:

```powershell
# Windows
.\Configurar.ps1 -Start
```

```bash
# Linux
bash configurar.sh --start
```

Open **http://127.0.0.1:8765** in your browser, or the port chosen in the wizard.
The wizard uses the same background controller as the lifecycle launchers below.
Closing the browser or setup terminal does not stop it. If the server is already
running, the launcher prints its actual address, including an explicit LAN bind.

Manage this server with the lifecycle launcher:

```powershell
.\Start-Web.ps1 start
.\Start-Web.ps1 status
.\Start-Web.ps1 restart
.\Start-Web.ps1 stop
```

Linux equivalents are `bash web.sh start`, `bash web.sh status`,
`bash web.sh restart` and `bash web.sh stop`. With no action, both scripts start
the server. They read `RAG_WEB_PORT` from `.env` (default 8765); override it with
`-Port 8766` on PowerShell or `--port 8766` on Linux.

The background server survives closing the terminal. Output is appended to
`reports/rag-server-runtime.log` and `reports/rag-server-runtime-errors.log`;
the web Console also shows the running server's output. Repeated start does not
create another instance. Stop/restart refuse to interrupt active RAG jobs: cancel
them in Library/Chat or wait until they finish. These commands manage only this
project's web process and preserve documents, indexes and Ollama.

## 7. Index your first document and ask a question

1. Open **Configuración** and press **Preparar modelo de búsqueda**. On first use it
   downloads the multilingual E5 embedding model. Embeddings run on CPU.
2. Create a small TXT or Markdown file containing a fact you can check, such as
   “Backups run every Friday.”
3. Open **Biblioteca**, select the file and press **Subir e indexar**. Wait for its
   status to become **Indexado**.
4. Open **Chat** and ask “When do backups run?”
5. Expand **Fuentes consultadas** and check the citation against the original file.
6. Then try a short PDF. Enable multimodal processing and a vision model before
   indexing scanned pages or figures.

Image descriptions use model inference and can take several seconds per page.
Start with small PDFs before indexing a large batch. **Indexing and chat share one
queue**: questions wait while a batch is being indexed.

## What is included

| Area | Features |
|---|---|
| Chat | Streamed Markdown, topic source selection, copy buttons and a sliding citation panel. |
| Library | Categories/subcategories, contextual upload destinations, bulk moves without reindexing, cancellation and retry. |
| Settings | Text/vision routing, retrieved fragment count and processing limits. |
| Statistics | CPU/GPU/RAM/VRAM charts, queue time, generation throughput and stage timings. |
| Console | Read-only local server output with filtering, copy and download. |

The top bar includes **Ctrl/Cmd+K** to find a document, prepare a chat question or
navigate. The system button opens the real bind address, local/LAN exposure,
Ollama models, GPU/VRAM readings and status refresh. VRAM above 90% is amber;
above 97% or multiple loaded models is red. Missing readings stay unknown.
The pipeline follows active jobs and reduces to a compact row on mobile.

Reasoning models receive up to `RAG_REASONING_MAX_NEW_TOKENS=2048`, independent
of the default 512-token budget for other models. Reasoning and the final answer
share that allowance. This changes neither the selected model nor its context
window. A blank completion reports a specific error and retains diagnostics.

Supported inputs: PDF, UTF-8 TXT, Markdown, PNG/JPEG/WebP and supported MinerU ZIP
exports. PDF parsing preserves text and tables; visual pages are rendered as whole
pages and described by a local vision model. Search uses textual embeddings of
text and descriptions. Retrieved original images can be sent to the vision model.

Defaults: 20 files per upload batch, 20 MB per file, two simultaneous uploads and
sequential processing. Visual processing allows up to the configured limit of
500 views per document; descriptions are cached to avoid repeating identical work.

The web server defaults to **127.0.0.1** and uses local request/origin checks. This is
a personal application with separate views and no authenticated multi-user roles.
The default workflow keeps documents and inference local. Initial dependency,
embedding and model downloads need Internet. If you manually configure a remote
model endpoint, that service will receive the retrieved context.

## Trusted LAN access

Bind to an IPv4 address assigned to this machine. For example:

```powershell
.\Start-Web.ps1 -Restart -Host 192.168.1.111 -WhenIdle
```

```bash
bash web.sh restart --host 192.168.1.111 --when-idle
```

Open `http://192.168.1.111:8765` on the trusted LAN. Use your actual address.
The controller supports `start`, `stop`, `restart`, `status`, `--help` and the
PowerShell `-Help`/`-h` equivalents. A restart without a host retains the running
bind; `--when-idle` waits for active jobs. No firewall rules are opened automatically.
Wildcard and public IP binds are refused. LAN visitors share all views and data;
there are no separate accounts. See [security boundaries](../SECURITY.md).

## Files and persistence

| Path | Contents |
|---|---|
| `.env` | Startup/CLI configuration; created by the wizard. |
| `data/settings.json` | Shared web preferences. |
| `data/credentials.sqlite3` | Encrypted optional cloud-provider API credentials; never contains plaintext keys. |
| `data/documents/` | Original uploaded documents. |
| `data/chroma/` | Persistent vectors and collection manifests. |
| `data/visual/` | Extracted page images and cached descriptions. |
| `.cache/` | Package/model caches, wizard environment and configuration backups. |
| `reports/` | Local diagnostics, test output and screenshots. |

Documents, models, private configuration and runtime data are ignored by Git.
An index update skips unchanged documents. Removing an original file does not
automatically remove its vectors; use a new collection for a clean corpus.
Changing the embedding model or chunking requires a new collection. Changing
visual processing settings can require updating the index from the web UI.

## Troubleshooting

| Problem | Check |
|---|---|
| Python is missing or has the wrong version | Run `py -3.13 --version` on Windows or check the Linux interpreter. Use Python 3.13 x64. |
| PowerShell blocks the launcher | Use the process-only command in step 4. |
| Linux cannot create a virtual environment | Install `venv` support for Python 3.13, or use the managed Python setup in step 1. |
| Ollama cannot connect | Run `ollama list`; open Ollama or start `ollama serve` in another terminal if needed. |
| No models appear | Download a local model, then press **Actualizar modelos**. |
| No vision model appears | The installed model must declare vision capability in Ollama. |
| Installation or saving is blocked | Finish active jobs; close the server before updating its packages. |
| A PDF appears stalled | Check **Estadísticas** and **Consola**. Page descriptions can be long-running inference. |
| A question waits without an answer | Check whether an indexing job is occupying the sequential queue. |
| The web port is occupied | Choose another port; the launcher does not terminate unrelated programs. |

Read-only setup diagnostics: `Configurar.ps1 -Check` on Windows or
`bash configurar.sh --check` on Linux.

## Development and CI

[GitHub Actions](https://github.com/tunelko/rag-lab/actions) runs Python regression
tests and JavaScript checks on Windows/Linux with Python 3.13, Node 24 and CPU
dependencies. Linux also runs browser QA with Edge. Test vectors, models and sensors
are simulated; no real models are downloaded or run during testing.

For development, add `requirements/wizard.txt` and `requirements/dev.txt` to a
separate development environment, then run:

```text
python -m unittest discover -v
node --test tests/frontend/*.test.mjs
python -m playwright install msedge
python -m tests.browser.qa_redesign
```

Browser QA uses its own server and corpus on port 8767. Logs/screenshots are
available as CI artifacts. A synthetic, CPU-only polling/routing benchmark is
available in `benchmarks/benchmark_local.py`; it does not measure LLM tokens/s.

Detailed guides in Spanish: [installation](INSTALACION.md),
[development and diagnostics](DESARROLLO.md),
[repository management](REPOSITORIO.md) and the [full Spanish README](../README.es.md).

## Limitations and licensing

Answers and image descriptions can be wrong. Check the original sources for
critical figures, pinouts or procedures. Retrieval quality needs evaluation against
your own documents and questions with known answers. The direct HF/NF4 backend
requires separate model/memory validation; GGUF models from Ollama are not loaded
with bitsandbytes.

The project's license has not yet been selected. Vendored libraries and fonts keep
their own license notices. Model weights and user documents are not distributed
with this repository.
