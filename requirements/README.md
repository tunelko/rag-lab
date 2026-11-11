# Dependency profiles

The setup wizard installs the chosen profile in a project virtual environment.
Model weights are downloaded separately through Ollama.

| File | Environment |
|---|---|
| `base.txt` | Shared parsing, embeddings, vector store and web dependencies. |
| `web.txt` | FastAPI, uploads and Uvicorn, included by the base profile. |
| `ollama.txt` | Recommended runtime: CPU embeddings and local Ollama generation. |
| `cuda.txt` | Advanced runtime: base profile plus Accelerate/bitsandbytes NF4. |
| `torch-cpu.txt` | Pinned PyTorch CPU build from the official CPU index. |
| `torch-cuda.txt` | Pinned PyTorch CUDA 13.0 build from its official index. |
| `wizard.txt` | Terminal wizard, isolated in `.cache/wizard-venv`. |
| `dev.txt` | Ollama runtime, wizard and browser test dependencies. |
| `lock-windows-cu130.txt` | Windows CUDA environment snapshot with security updates applied. |

For manual installation, create/activate a **Python 3.13 x64** virtual environment
and run from the project root:

```text
python -m pip install -r requirements/torch-cpu.txt
python -m pip install -r requirements/ollama.txt
python -m pip check
```

For development, use `dev.txt` in the second command. Direct HF/NF4 uses
`torch-cuda.txt` first and `cuda.txt` second, and requires a compatible GPU/driver.
The recommended runtime lets Ollama manage its own GPU support independently.
