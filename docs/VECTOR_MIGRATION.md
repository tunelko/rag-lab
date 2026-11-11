# Upgrade an existing vector library

Fresh installations use SQLite and exact NumPy cosine search, without ChromaDB
or langchain-chroma. An older library must be migrated **before** removing those
packages from its previous environment. The original documents, captions, vector
IDs, embeddings, metadata, manifest and category assignments are preserved.

## Windows

From the project directory, after obtaining the new source:

```powershell
.\Start-Web.ps1 stop -WhenIdle
.\.venv\Scripts\python.exe -m tools.migrate_vectors
.\.venv\Scripts\python.exe -m pip uninstall --yes langchain-chroma chromadb
.\.venv\Scripts\python.exe -m pip install -r requirements/ollama.txt
.\.venv\Scripts\python.exe -m pip check
.\Start-Web.ps1 start
```

For an existing CUDA/HF environment, use `requirements/cuda.txt` instead of
`ollama.txt`; keep the already selected PyTorch build. For a previously selected
LAN address, pass it again on start, for example `-Host 192.168.1.111`.

## Linux

```bash
bash web.sh stop --when-idle
.venv/bin/python -m tools.migrate_vectors
.venv/bin/python -m pip uninstall --yes langchain-chroma chromadb
.venv/bin/python -m pip install -r requirements/ollama.txt
.venv/bin/python -m pip check
bash web.sh start
```

Pass `--host <assigned-private-ip>` again when starting a LAN server. Ollama can
remain running throughout. The conversion does not load any inference or
embedding model and does not require GPU memory.

## What is verified

The tool takes the collection writer lock, copies the old database into
`data/vector-backups/<timestamp>/`, and reads only that copy through the legacy
client. Every published ID is imported into a temporary SQLite store. Each
float32 vector, text and metadata record is compared with the source; the tool
also verifies total count and an unchanged manifest before publishing the file.
An incomplete conversion is not published. Existing SQLite stores are never
overwritten. The backup retains a migration report and checksums.

Vectors now live at `data/chroma/<collection>-vectors.sqlite3`. The historical
directory name remains deliberately unchanged so source/category/configuration
paths keep working. Back up the entire data directory as before. The retired
database files can remain as inactive recovery data; the application does not
open them or import Chroma. No original file is deleted by migration.

For other collections or index directories:

```text
python -m tools.migrate_vectors --collection another_collection --db /path/to/index
```

Migrate every nonempty legacy collection before updating dependencies. The wizard
and Windows setup helper refuse that update while an unmigrated collection remains.
If the old environment was already removed, recover it from the previous deployment
solely to perform the conversion; do not reinstall the retired dependency as part
of the current runtime. Keep migration backups private and outside Git.

## Search behavior

The replacement searches exact cosine distances on CPU. It applies file scope and
the manifest's published IDs before selecting the nearest fragments. It does not
recalculate vectors, alter chunking or change model configuration. Near-identical
results can change order: exact ranking and tie handling differ from the retired
approximate HNSW index. This is a local laboratory store, not a distributed or
approximate-nearest-neighbor service. Measure memory and latency before scaling
to much larger collections.

## Optional lexical evaluation index

SQLite vector format v1 remains the production format. Opening the store, polling
status and dense queries do not create FTS tables or change `user_version`.
The offline lexical/hybrid benchmark needs a derived FTS5 index and refuses to
create it implicitly.

With the server stopped or idle, prepare it explicitly:

```text
python -m tools.prepare_lexical_index
```

The command takes the same writer lock and first creates a consistent SQLite backup
plus a manifest copy under `data/vector-backups/<timestamp>-<collection>-lexical/`. It
verifies one FTS row per vector. No document, embedding or manifest record changes.
Once prepared, vector writes also update FTS inside their SQLite transaction; remove
the derived index first if the interpreter is replaced by one without FTS5 support.

Rollback is also explicit and reversible:

```text
python -m tools.prepare_lexical_index --remove
```

This makes another backup, then drops only the FTS table/triggers and its marker.
Dense retrieval continues to use the unchanged v1 vectors. Restoring the backup is
therefore unnecessary for normal rollback, but it remains available for forensic
comparison or recovery.

Backups are intentionally not pruned automatically. Review old lexical-index backups
manually after validation. Restore a SQLite backup only while the RAG server is fully
stopped, so no process can retain a cache generation from the replaced file.
Removing FTS releases its pages for SQLite reuse but does not shrink the database
file; run `VACUUM` later only if reclaiming disk space is necessary. Do not run old
and new application versions against the same live database: older writers do not
advance the cache-generation marker used by the new process.
