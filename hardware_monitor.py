"""Read-only host/GPU telemetry. Never initializes CUDA or runs a model."""
import csv
from datetime import datetime, timezone
import io
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import psutil

ROOT = Path(__file__).resolve().parent


def number(value):
    try:
        return float(value.strip())
    except ValueError:
        return None


def gpu_snapshot():
    windows_tool = Path(os.environ.get("WINDIR", "C:/Windows")) / "System32" / "nvidia-smi.exe"
    executable = str(windows_tool) if windows_tool.is_file() else shutil.which("nvidia-smi")
    if not executable:
        return {"available": False, "devices": [], "error": "nvidia-smi no está disponible."}
    try:
        result = subprocess.run([executable, "--query-gpu=name,utilization.gpu,utilization.memory,memory.used,memory.total,temperature.gpu,power.draw", "--format=csv,noheader,nounits"],
                                capture_output=True, text=True, timeout=3,
                                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), check=True)
        devices = []
        for row in csv.reader(io.StringIO(result.stdout)):
            if len(row) != 7:
                continue
            used, total = number(row[3]), number(row[4])
            devices.append({"name": row[0].strip(), "utilization_percent": number(row[1]),
                            "memory_utilization_percent": number(row[2]),
                            "memory_used_bytes": int(used * 1024**2) if used is not None else None,
                            "memory_total_bytes": int(total * 1024**2) if total is not None else None,
                            "temperature_c": number(row[5]), "power_w": number(row[6])})
        return {"available": bool(devices), "devices": devices}
    except (OSError, subprocess.SubprocessError) as exc:
        return {"available": False, "devices": [], "error": str(exc)[:300]}


class HardwareMonitor:
    def __init__(self, interval=2, gpu_reader=gpu_snapshot):
        self.interval, self.gpu_reader = interval, gpu_reader
        self.lock = threading.RLock()
        self.previous = {}
        self.last = None
        self.last_at = 0

    def sample(self):
        with self.lock:
            now = time.monotonic()
            if self.last is not None and now - self.last_at < self.interval:
                return self.last
            # FastAPI may dispatch successive requests to different threads.
            # A bounded interval avoids psutil's per-thread first-sample zeros.
            cpu = psutil.cpu_percent(interval=.1)
            ram = psutil.virtual_memory()
            logical = psutil.cpu_count() or 1
            processes, seen = [], set()
            for item in psutil.process_iter(["pid", "name"]):
                try:
                    name = (item.info["name"] or "").lower()
                    role = "ollama" if name in {"ollama.exe", "ollama", "llama-server.exe", "llama-server"} else None
                    if name in {"python.exe", "python", "python3"}:
                        args = item.cmdline()
                        if "web_app:app" in args and "--app-dir" in args and Path(args[args.index("--app-dir") + 1]).resolve() == ROOT:
                            role = "rag"
                    if not role:
                        continue
                    process = self.previous.get(item.pid) or item
                    raw_cpu = process.cpu_percent(None)
                    percentage = round(raw_cpu / logical, 1) if item.pid in self.previous else None
                    self.previous[item.pid] = process
                    seen.add(item.pid)
                    processes.append({"pid": item.pid, "name": item.info["name"], "role": role,
                                      "cpu_percent": percentage, "ram_bytes": process.memory_info().rss})
                except (psutil.Error, IndexError, OSError):
                    continue
            self.previous = {pid: process for pid, process in self.previous.items() if pid in seen}
            self.last = {"time": datetime.now(timezone.utc).isoformat(), "interval_seconds": self.interval,
                         "cpu_percent": cpu, "logical_cpus": logical,
                         "ram_used_bytes": ram.used, "ram_total_bytes": ram.total,
                         "ram_percent": ram.percent, "processes": processes, "gpu": self.gpu_reader()}
            self.last_at = now
            return self.last
