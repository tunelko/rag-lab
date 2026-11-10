"""Bounded, read-only capture of this RAG process' output and Python logs."""
from collections import deque
from datetime import datetime, timezone
import logging
import secrets
import sys
import threading


class ConsoleBuffer:
    def __init__(self, capacity=1000):
        self.entries = deque(maxlen=capacity)
        self.lock = threading.RLock()
        self.instance = secrets.token_hex(8)
        self.sequence = 0

    def append(self, text, stream="stdout", level="INFO"):
        text = str(text).replace("\x00", "").rstrip("\r\n")
        if not text:
            return
        with self.lock:
            for line in text.splitlines():
                self.sequence += 1
                self.entries.append({"id": self.sequence, "time": datetime.now(timezone.utc).isoformat(),
                                     "stream": stream, "level": level, "text": line[:4000]})

    def snapshot(self, after=0, limit=200):
        with self.lock:
            entries = [dict(entry) for entry in self.entries if entry["id"] > after][:limit]
            oldest = self.entries[0]["id"] if self.entries else 0
            return {"instance": self.instance, "entries": entries,
                    "cursor": entries[-1]["id"] if entries else min(after, self.sequence),
                    "newest": self.sequence, "oldest": oldest, "capacity": self.entries.maxlen,
                    "dropped": bool(after and oldest > after + 1)}


class OutputTee:
    def __init__(self, original, buffer, stream):
        self.original, self.buffer, self.stream = original, buffer, stream
        self.partial = ""
        self.lock = threading.RLock()

    def write(self, text):
        with self.lock:
            result = self.original.write(text)
            self.original.flush()
            self.partial += text
            while "\n" in self.partial:
                line, self.partial = self.partial.split("\n", 1)
                self.buffer.append(line, self.stream, "ERROR" if self.stream == "stderr" else "INFO")
            # Bound output without newlines as well as complete log lines.
            if len(self.partial) > 4000:
                self.buffer.append(self.partial[:4000], self.stream)
                self.partial = self.partial[-4000:]
            return result

    def flush(self):
        with self.lock:
            self.original.flush()

    def __getattr__(self, key):
        return getattr(self.original, key)

    def finish(self):
        with self.lock:
            if self.partial:
                self.buffer.append(self.partial, self.stream)
                self.partial = ""


class ConsoleHandler(logging.Handler):
    def __init__(self, buffer):
        super().__init__()
        self.buffer = buffer

    def emit(self, record):
        if getattr(record, "_rag_console_recorded", False):
            return
        record._rag_console_recorded = True
        # If the logger already writes through a tee, its stream will capture it.
        for logger in (logging.getLogger(record.name), logging.getLogger("uvicorn"), logging.getLogger()):
            if any(isinstance(getattr(handler, "stream", None), OutputTee) for handler in logger.handlers):
                return
        self.buffer.append(self.format(record), "log", record.levelname)


class ConsoleCapture:
    def __init__(self, buffer):
        self.buffer = buffer

    def __enter__(self):
        self.stdout, self.stderr = sys.stdout, sys.stderr
        self.out = OutputTee(self.stdout, self.buffer, "stdout")
        self.err = OutputTee(self.stderr, self.buffer, "stderr")
        sys.stdout, sys.stderr = self.out, self.err
        self.handler = ConsoleHandler(self.buffer)
        self.handler.setFormatter(logging.Formatter("%(name)s · %(message)s"))
        self.rag_level = logging.getLogger("rag").level
        logging.getLogger("rag").setLevel(logging.INFO)
        logging.getLogger().addHandler(self.handler)
        logging.getLogger("uvicorn").addHandler(self.handler)
        self.buffer.append("Consola del servidor RAG conectada.", "server")
        return self

    def __exit__(self, *args):
        logging.getLogger("rag").setLevel(self.rag_level)
        logging.getLogger().removeHandler(self.handler)
        logging.getLogger("uvicorn").removeHandler(self.handler)
        self.out.finish()
        self.err.finish()
        if sys.stdout is self.out:
            sys.stdout = self.stdout
        if sys.stderr is self.err:
            sys.stderr = self.stderr
