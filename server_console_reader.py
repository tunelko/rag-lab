"""Read the running server's fixed log files while an older process finishes work.

The current server already serves /assets. Publish a bounded JSON snapshot there,
then exit when its processes end or the native console endpoint becomes available.
No model calls, settings changes, or writes to the document/index directories.
"""
import argparse
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request

import psutil
from server_console import ConsoleBuffer, LineClassifier

ROOT = Path(__file__).resolve().parent


def running_server_processes():
    found = []
    for process in psutil.process_iter(["pid", "name"]):
        if "python" not in (process.info["name"] or "").lower():
            continue
        try:
            args = process.cmdline()
            if "web_app:app" not in args or "--port" not in args or args[args.index("--port") + 1] != "8765":
                continue
            if "--app-dir" not in args or Path(args[args.index("--app-dir") + 1]).resolve() != ROOT:
                continue
            found.append((process.pid, process.create_time()))
        except (psutil.Error, ValueError, IndexError):
            continue
    return found


def read_new_lines(path, position, partial):
    if not path.is_file():
        return position, partial, []
    if path.stat().st_size < position:
        position, partial = 0, ""
    with path.open("rb") as stream:
        stream.seek(position)
        raw = stream.read(65536)
        position = stream.tell()
    text = partial + raw.decode("utf-8", errors="replace")
    pieces = text.split("\n")
    return position, pieces[-1][-4000:], [line.rstrip("\r") for line in pieces[:-1]]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=float, default=1)
    args = parser.parse_args()
    processes = running_server_processes()
    if not processes:
        return
    buffer = ConsoleBuffer()
    buffer.append("Lectura del registro del servidor actual conectada.", "server")
    files = [(ROOT / "reports" / "rag-server-runtime.log", "stdout"),
             (ROOT / "reports" / "rag-server-runtime-errors.log", "stderr")]
    states = {str(path): (0, "") for path, _ in files}
    classifiers = {origin: LineClassifier() for _, origin in files}
    output = ROOT / "dist" / "runtime-console.json"
    previous_jobs = {}
    next_probe = 0
    try:
        while True:
            alive = False
            for pid, created in processes:
                try:
                    alive |= psutil.Process(pid).create_time() == created
                except psutil.Error:
                    pass
            if not alive:
                break
            if time.monotonic() >= next_probe:
                next_probe = time.monotonic() + 10
                try:
                    with urllib.request.urlopen("http://127.0.0.1:8765/openapi.json", timeout=2) as response:
                        if "/api/console" in json.load(response).get("paths", {}):
                            break
                except (OSError, ValueError):
                    pass
            for path, origin in files:
                position, partial, lines = read_new_lines(path, *states[str(path)])
                states[str(path)] = (position, partial)
                for line in lines:
                    buffer.append(line, origin, classifiers[origin].classify(line), time_source="read")
            try:
                with urllib.request.urlopen("http://127.0.0.1:8765/api/jobs?active=1", timeout=2) as response:
                    result = json.load(response)
                for job in [*result.get("jobs", []), *result.get("recent", [])]:
                    state = (job["status"], job.get("phase"), job.get("current_document"))
                    if previous_jobs.get(job["id"]) != state:
                        previous_jobs[job["id"]] = state
                        name = job.get("current_document") or job.get("document_name") or job.get("kind", "tarea")
                        buffer.append(f"{name} · {job.get('phase', job['status'])}", "job", "ERROR" if job["status"] == "error" else "INFO", time_source="read")
                previous_jobs = dict(list(previous_jobs.items())[-200:])
            except (OSError, ValueError):
                pass
            pending = output.with_suffix(".pending")
            pending.write_text(json.dumps(buffer.snapshot(limit=1000), ensure_ascii=False), encoding="utf-8")
            try:
                pending.replace(output)
            except PermissionError:
                pass  # A Windows reader may briefly hold the previous snapshot open.
            time.sleep(max(.25, args.interval))
    finally:
        try:
            output.unlink(missing_ok=True)
        except PermissionError:
            pass


if __name__ == "__main__":
    main()
