"""Console capture and read-only API. No real models, downloads, or GPU calls."""
import io
import logging
import sys
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from server_console import ConsoleBuffer, ConsoleCapture, OutputTee
from server_console_reader import read_new_lines
import test_web


class ConsoleTests(unittest.TestCase):
    def test_compatibility_reader_handles_appends_and_truncated_logs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "server.log"
            path.write_text("primera\npar", encoding="utf-8")
            position, partial, lines = read_new_lines(path, 0, "")
            self.assertEqual(lines, ["primera"])
            with path.open("a", encoding="utf-8") as stream:
                stream.write("cial\n")
            position, partial, lines = read_new_lines(path, position, partial)
            self.assertEqual(lines, ["parcial"])
            path.write_text("nuevo\n", encoding="utf-8")
            _, _, lines = read_new_lines(path, position, partial)
            self.assertEqual(lines, ["nuevo"])

    def test_bounded_history_incremental_cursor_and_gaps(self):
        buffer = ConsoleBuffer(capacity=3)
        for i in range(5):
            buffer.append(f"line {i}")
        result = buffer.snapshot(after=1, limit=2)
        self.assertEqual([entry["id"] for entry in result["entries"]], [3, 4])
        self.assertTrue(result["dropped"])
        self.assertEqual(buffer.snapshot(after=4)["entries"][0]["id"], 5)
        self.assertEqual(buffer.snapshot(after=999)["cursor"], 5)

    def test_tee_preserves_original_output_and_partial_lines(self):
        original = io.StringIO()
        buffer = ConsoleBuffer()
        tee = OutputTee(original, buffer, "stdout")
        tee.write("primera ")
        self.assertFalse(buffer.snapshot()["entries"])
        tee.write("línea\nsegunda")
        tee.finish()
        self.assertEqual(original.getvalue(), "primera línea\nsegunda")
        self.assertEqual([entry["text"] for entry in buffer.snapshot()["entries"]], ["primera línea", "segunda"])

    def test_capture_restores_streams_and_records_structured_logs(self):
        buffer = ConsoleBuffer()
        stdout, stderr = io.StringIO(), io.StringIO()
        with patch("sys.stdout", stdout), patch("sys.stderr", stderr):
            with ConsoleCapture(buffer):
                print("archivo.pdf: 12 chunks")
                print("fallo de prueba", file=sys.stderr)
                logging.getLogger("rag.vision").info("Caché de página %s", 4)
            self.assertIs(sys.stdout, stdout)
            self.assertIs(sys.stderr, stderr)
        entries = buffer.snapshot()["entries"]
        self.assertTrue(any(entry["text"] == "archivo.pdf: 12 chunks" for entry in entries))
        self.assertTrue(any(entry["level"] == "ERROR" and entry["text"] == "fallo de prueba" for entry in entries))
        self.assertEqual(sum("Caché de página 4" in entry["text"] for entry in entries), 1)
        self.assertLessEqual(len(buffer.snapshot()["entries"]), 1000)


class ConsoleApiTests(unittest.TestCase):
    setUp = test_web.WebTests.setUp
    tearDown = test_web.WebTests.tearDown

    def test_console_is_read_only_bounded_and_does_not_load_embeddings(self):
        service = self.client.app.state.service
        service.console.append('<script>alert(1)</script>', "stderr", "ERROR")
        result = self.client.get("/api/console?after=0&limit=2")
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.json()["entries"][-1]["text"], '<script>alert(1)</script>')
        self.assertIsNone(service.embeddings)
        self.assertEqual(self.client.get("/consola").status_code, 200)
        self.assertEqual(self.client.get("/api/console?after=-1").status_code, 422)
        self.assertEqual(self.client.get("/api/console?limit=100000").status_code, 422)
        self.assertEqual(self.client.post("/api/console", headers=self.headers, json={"command": "anything"}).status_code, 405)


if __name__ == "__main__":
    unittest.main()
