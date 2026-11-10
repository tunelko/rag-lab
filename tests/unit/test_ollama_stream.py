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
        self.assertFalse(json.loads(request.call_args.args[0].data)["think"])

    def test_images_use_a_separate_explicit_context_budget(self):
        body = b'{"message":{"content":"Respuesta"},"eval_count":1}'
        with patch("rag.urllib.request.urlopen", return_value=io.BytesIO(body)) as request:
            rag.generate_ollama([{"role": "user", "content": "Figura", "images": ["base64"]}], "test")
        self.assertEqual(json.loads(request.call_args.args[0].data)["options"]["num_ctx"], 8192)

    def test_nonstream_cli_compatibility(self):
        body = json.dumps({"message": {"content": "Respuesta"}, "eval_count": 12, "eval_duration": 2_000_000_000}).encode()
        with patch("rag.urllib.request.urlopen", return_value=io.BytesIO(body)):
            answer, metrics = rag.generate_ollama([], "test")
        self.assertEqual(answer, "Respuesta")
        self.assertEqual(metrics["generation_tok_s"], 6)

    def test_global_generation_has_an_independent_output_and_context_budget(self):
        body = b'{"message":{"content":"Tabla"},"eval_count":1}'
        with patch('rag.urllib.request.urlopen', return_value=io.BytesIO(body)) as request:
            rag.generate_ollama([], 'test', context_length=8192, max_output_tokens=1536)
        options = json.loads(request.call_args.args[0].data)['options']
        self.assertEqual(options['num_ctx'], 8192)
        self.assertEqual(options['num_predict'], 1536)
        with self.assertRaises(ValueError):
            rag.generate_ollama([], 'test', max_output_tokens=100000)

    def test_structured_schema_is_sent_to_the_local_api(self):
        body=b'{"message":{"content":"{}"},"eval_count":1}'
        schema={'type':'object','properties':{'1':{'type':'string'}},'required':['1']}
        with patch('rag.urllib.request.urlopen',return_value=io.BytesIO(body)) as request:
            rag.generate_ollama([], 'test', response_format=schema)
        self.assertEqual(json.loads(request.call_args.args[0].data)['format'],schema)

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
