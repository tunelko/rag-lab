"""Exercise Ollama response parsing without contacting Ollama or running a model."""
import io
import json
import unittest
from unittest.mock import patch

import rag


class OllamaStreamTests(unittest.TestCase):
    def test_stream_text_and_native_metrics(self):
        packets = [
            {"message": {"content": "Cada "}, "done": False},
            {"message": {"content": "viernes [1]."}, "done": False},
            {"message": {"content": ""}, "done": True, "eval_count": 10,
             "eval_duration": 1_000_000_000, "prompt_eval_count": 100, "done_reason": "stop"},
        ]
        body = "\n".join(json.dumps(packet) for packet in packets).encode()
        pieces = []
        with patch("rag.urllib.request.urlopen", return_value=io.BytesIO(body)) as request:
            answer, metrics = rag.generate_ollama([], "test", on_chunk=pieces.append)
        self.assertEqual(answer, "Cada viernes [1].")
        self.assertEqual(pieces, ["Cada ", "viernes [1]."])
        self.assertEqual(metrics["generation_tok_s"], 10)
        self.assertEqual(metrics["input_tokens"], 100)
        self.assertTrue(json.loads(request.call_args.args[0].data)["stream"])

    def test_nonstream_cli_compatibility(self):
        body = json.dumps({"message": {"content": "Respuesta"}, "eval_count": 12, "eval_duration": 2_000_000_000}).encode()
        with patch("rag.urllib.request.urlopen", return_value=io.BytesIO(body)):
            answer, metrics = rag.generate_ollama([], "test")
        self.assertEqual(answer, "Respuesta")
        self.assertEqual(metrics["generation_tok_s"], 6)

    def test_truncated_stream_is_not_reported_as_complete(self):
        with patch("rag.urllib.request.urlopen", return_value=io.BytesIO(b'{"message":{"content":"partial"}}\n')):
            with self.assertRaises(RuntimeError):
                rag.generate_ollama([], "test", on_chunk=lambda text: None)

    def test_error_packet_is_reported(self):
        with patch("rag.urllib.request.urlopen", return_value=io.BytesIO(b'{"error":"model unavailable"}\n')):
            with self.assertRaisesRegex(RuntimeError, "model unavailable"):
                rag.generate_ollama([], "test", on_chunk=lambda text: None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
