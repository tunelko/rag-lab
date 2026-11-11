"""OpenAI-compatible generation is parsed offline with a fake HTTPS client."""

import io
import json
import unittest

import generation_providers as providers


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


class FakeOpener:
    def __init__(self, body):
        self.body = body
        self.request = None
        self.timeout = None

    def open(self, request, timeout=None):
        self.request, self.timeout = request, timeout
        return FakeResponse(self.body)


class ProviderTests(unittest.TestCase):
    def test_streaming_chat_records_usage_without_returning_secret(self):
        packets = [
            {"choices": [{"delta": {"content": "Respuesta "}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "[1]."}, "finish_reason": "stop"}],
             "usage": {"prompt_tokens": 100, "completion_tokens": 3}},
        ]
        body = b"".join(b"data: " + json.dumps(packet).encode() + b"\n\n" for packet in packets) + b"data: [DONE]\n\n"
        opener = FakeOpener(body)
        pieces = []
        answer, metrics = providers.generate(
            [{"role": "user", "content": "Pregunta"}], "ovh", "Qwen3.8-27B", "top-secret-value",
            on_chunk=pieces.append, opener=opener,
        )
        self.assertEqual(answer, "Respuesta [1].")
        self.assertEqual(pieces, ["Respuesta ", "[1]."])
        self.assertEqual(metrics["input_tokens"], 100)
        self.assertEqual(metrics["output_tokens"], 3)
        self.assertEqual(metrics["provider"], "ovh")
        self.assertNotIn("top-secret-value", repr(metrics))
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer top-secret-value")
        payload = json.loads(opener.request.data)
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["model"], "Qwen3.8-27B")

    def test_nonstreaming_and_json_schema(self):
        response = {"choices": [{"message": {"content": "{\"rows\": []}"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 5}}
        opener = FakeOpener(json.dumps(response).encode())
        schema = {"type": "object", "properties": {"rows": {"type": "array"}}, "required": ["rows"]}
        answer, metrics = providers.generate(
            [{"role": "user", "content": "List"}], "scaleway", "deepseek-v4-flash-0731", "secret-value",
            response_format=schema, opener=opener,
        )
        self.assertEqual(answer, '{"rows": []}')
        self.assertEqual(metrics["done_reason"], "stop")
        payload = json.loads(opener.request.data)
        self.assertEqual(payload["response_format"]["json_schema"]["schema"], schema)

    def test_images_are_never_sent_to_external_provider(self):
        with self.assertRaisesRegex(providers.ProviderError, "solo con Ollama local"):
            providers.generate([{"role": "user", "content": "image", "images": ["base64"]}],
                               "xai", "grok-4.6", "secret-value", opener=FakeOpener(b""))

    def test_truncated_stream_is_not_reported_as_complete(self):
        body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        with self.assertRaisesRegex(providers.ProviderError, "sin confirmar"):
            providers.generate([{"role": "user", "content": "question"}], "ovh", "model", "secret-value",
                               on_chunk=lambda piece: None, opener=FakeOpener(body))


if __name__ == "__main__":
    unittest.main(verbosity=2)
