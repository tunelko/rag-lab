"""Verify the lab without running a model or allocating GPU tensors by default."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from importlib.metadata import distribution, version
import json
import os
from pathlib import Path
import subprocess
import sys

# Use NVML for availability checking, avoiding CUDA context initialization.
os.environ.setdefault("PYTORCH_NVML_BASED_CUDA_CHECK", "1")
ROOT = Path(__file__).resolve().parent


def cpu_imports():
    import torch
    import accelerate
    import bitsandbytes
    import chromadb
    import sentence_transformers
    from langchain_chroma import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    from transformers import AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig

    config = BitsAndBytesConfig(load_in_4bit=True, bnb_4bit_quant_type="nf4")
    result = torch.ones((2, 2), device="cpu") @ torch.ones((2, 2), device="cpu")
    assert result.tolist() == [[2.0, 2.0], [2.0, 2.0]]
    assert config.load_in_4bit
    assert "query_encode_kwargs" in HuggingFaceEmbeddings.model_fields
    assert not torch.cuda.is_initialized(), "CPU imports unexpectedly initialized CUDA."
    return {"imports": "OK", "cpu_tensor_operation": "OK", "nf4_configuration": "OK",
            "cuda_context_initialized": torch.cuda.is_initialized()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cpu-imports", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--cuda-smoke", action="store_true", help="Explicitly run a small CUDA/NF4 test when the GPU is available")
    parser.add_argument("--write-report", action="store_true")
    args = parser.parse_args()
    if args.cpu_imports:
        print(json.dumps(cpu_imports()))
        return 0

    import torch

    packages = ["torch", "transformers", "accelerate", "bitsandbytes", "sentence-transformers",
                "langchain", "langchain-chroma", "langchain-huggingface", "chromadb", "pypdf"]
    report = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0], "executable": sys.executable,
        "virtual_environment": sys.prefix != sys.base_prefix,
        "packages": {package: version(package) for package in packages},
        "torch_cuda_build": torch.version.cuda,
        "cuda_available": torch.cuda.is_available(),
        "compiled_gpu_architectures": torch.cuda.get_arch_list(),
        "gpu_tensor_test": "NOT RUN (GPU workload left untouched)",
    }
    report["cuda_context_initialized"] = torch.cuda.is_initialized()
    report["bitsandbytes_cuda_dlls"] = [
        str(file) for file in (distribution("bitsandbytes").files or [])
        if "cuda" in str(file).lower() and str(file).lower().endswith(".dll")
    ]
    child_env = dict(os.environ, CUDA_VISIBLE_DEVICES="", HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
                     HF_HUB_DISABLE_TELEMETRY="1", ANONYMIZED_TELEMETRY="False", OMP_NUM_THREADS="2",
                     PYTHONIOENCODING="utf-8")
    child = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--cpu-imports"],
                           env=child_env, capture_output=True, text=True, encoding="utf-8", timeout=180)
    if child.returncode:
        report["cpu_libraries"] = {"error": child.stderr[-6000:], "stdout": child.stdout[-1000:]}
    else:
        report["cpu_libraries"] = json.loads(child.stdout.strip().splitlines()[-1])
    if args.cuda_smoke:
        if not report["cuda_available"]:
            raise RuntimeError("CUDA is unavailable.")
        free, _ = torch.cuda.mem_get_info()
        if free < 512 * 1024**2:
            raise RuntimeError("Less than 512 MiB free; CUDA smoke test cancelled.")
        import bitsandbytes as bnb
        layer = bnb.nn.Linear4bit(16, 16, bias=False, compute_dtype=torch.float16, quant_type="nf4").to("cuda")
        with torch.inference_mode():
            result = layer(torch.ones((2, 16), device="cuda", dtype=torch.float16))
        torch.cuda.synchronize()
        assert result.shape == (2, 16) and torch.isfinite(result).all()
        report["gpu_tensor_test"] = "OK: CUDA + bitsandbytes NF4"
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if args.write_report:
        directory = ROOT / "reports"
        directory.mkdir(exist_ok=True)
        (directory / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if child.returncode == 0 and report["cuda_available"] and torch.version.cuda else 1


if __name__ == "__main__":
    raise SystemExit(main())
