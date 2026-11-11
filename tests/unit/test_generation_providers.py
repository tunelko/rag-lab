"""OpenAI-compatible generation is parsed offline with a fake HTTPS client."""

import io
import json
import unittest
import urllib.error

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
    def test_provider_registration_agrees_with_credentials_and_settings(self):
        from credential_store import PROVIDERS as credential_providers
        from settings_store import Preferences
        self.assertEqual(set(providers.PROVIDERS), credential_providers)
        for provider in credential_providers:
            self.assertEqual(Preferences(response_provider=provider).response_provider, provider)

    def test_groq_live_catalog_excludes_inactive_audio_classifiers_and_agents(self):
        opener = FakeOpener(json.dumps({"data": [
            {"id": "openai/gpt-oss-20b", "active": True},
            {"id": "llama-3.3-70b-versatile", "active": True},
            {"id": "unavailable-model", "active": False},
            *({"id": model, "active": True} for model in (
                "whisper-large-v3", "playai-tts", "canopylabs/orpheus-v1-english",
                "meta-llama/llama-prompt-guard-2-22m", "openai/gpt-oss-safeguard-20b", "groq/compound")),
        ]}).encode())
        self.assertEqual(providers.list_models("groq", "fake-groq-secret", opener=opener),
                         ["llama-3.3-70b-versatile", "openai/gpt-oss-20b"])
        self.assertEqual(opener.request.full_url, "https://api.groq.com/openai/v1/models")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer fake-groq-secret")
        self.assertEqual(opener.request.get_header("User-agent"), providers.CLIENT_USER_AGENT)

    def test_groq_stream_accepts_native_and_standard_token_usage(self):
        usage = {"prompt_tokens": 83, "completion_tokens": 11, "total_tokens": 94}
        for final in ({"x_groq": {"usage": usage}}, {"usage": usage}):
            with self.subTest(final=final):
                packets = [
                    {"choices": [{"delta": {"content": "Friday [1]."}}]},
                    {"choices": [{"delta": {}, "finish_reason": "stop"}], **final},
                ]
                data = b"".join(b"data: " + json.dumps(item).encode() + b"\n\n" for item in packets) + b"data: [DONE]\n\n"
                opener, pieces = FakeOpener(data), []
                answer, metrics = providers.generate([{"role": "user", "content": "When?"}],
                    "groq", "openai/gpt-oss-20b", "fake-groq-secret", on_chunk=pieces.append,
                    max_output_tokens=1024, opener=opener)
                self.assertEqual(answer, "Friday [1].")
                self.assertEqual(pieces, [answer])
                self.assertEqual((metrics['input_tokens'], metrics['output_tokens'], metrics['total_tokens']), (83, 11, 94))
                self.assertEqual(metrics['provider'], 'groq')
                payload = json.loads(opener.request.data)
                self.assertEqual(payload['max_completion_tokens'], 1024)
                self.assertNotIn('max_tokens', payload)
                self.assertEqual(opener.request.full_url, 'https://api.groq.com/openai/v1/chat/completions')
                self.assertEqual(opener.request.get_header('User-agent'), providers.CLIENT_USER_AGENT)

    def test_groq_cloudflare_problem_response_explains_rejection_without_dumping_json(self):
        class Rejected:
            def open(self, request, timeout=None):
                detail = {'title': 'Error 1010: Access denied', 'detail': 'Client signature blocked: fake-groq-secret',
                          'ray_id': 'test-ray', 'cloudflare_error': True}
                raise urllib.error.HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO(json.dumps(detail).encode()))
        for operation in ('models', 'generate'):
            with self.subTest(operation=operation), self.assertRaises(providers.ProviderError) as error:
                if operation == 'models':
                    providers.list_models('groq', 'fake-groq-secret', opener=Rejected())
                else:
                    providers.generate([{'role': 'user', 'content': 'Test'}], 'groq', 'chat-model',
                                       'fake-groq-secret', opener=Rejected())
            self.assertIn('Client signature blocked', str(error.exception))
            self.assertNotIn('fake-groq-secret', str(error.exception))
            self.assertNotIn('ray_id', str(error.exception))

    def test_groq_global_answers_stream_json_and_keep_document_validation(self):
        from global_answers import GlobalAnswer
        for response, complete in (('{"1":"Friday","2":"Sunday"}', True), ('{"1":"Friday"}', False)):
            with self.subTest(complete=complete):
                validator = GlobalAnswer([{'citation': 1, 'file': 'first.txt'}, {'citation': 2, 'file': 'second.txt'}])
                packet = {'choices': [{'delta': {'content': response}, 'finish_reason': 'stop'}]}
                opener = FakeOpener(b'data: ' + json.dumps(packet).encode() + b'\n\ndata: [DONE]\n\n')
                messages = [{'role': 'user', 'content': 'Compare both documents.'}]
                answer, _ = providers.generate(messages, 'groq', 'llama-3.3-70b-versatile',
                    'fake-groq-secret', response_format=validator.schema, on_chunk=validator.feed, opener=opener)
                payload = json.loads(opener.request.data)
                self.assertEqual(payload['response_format'], {'type': 'json_object'})
                self.assertTrue(payload['stream'])
                self.assertIn(json.dumps(validator.schema), payload['messages'][0]['content'])
                self.assertEqual(len(messages), 1)
                if complete:
                    rendered = validator.finish(answer)
                    self.assertIn(r'first\.txt [1]', rendered)
                    self.assertIn(r'second\.txt [2]', rendered)
                else:
                    with self.assertRaises(ValueError):
                        validator.finish(answer)

    def test_groq_native_stream_error_is_not_a_success_and_redacts_secret(self):
        packet = {'x_groq': {'error': 'Rejected fake-groq-secret'}}
        opener = FakeOpener(b'data: ' + json.dumps(packet).encode() + b'\n\ndata: [DONE]\n\n')
        with self.assertRaises(providers.ProviderError) as error:
            providers.generate([{'role': 'user', 'content': 'Test'}], 'groq', 'openai/gpt-oss-20b',
                               'fake-groq-secret', on_chunk=lambda piece: None, opener=opener)
        self.assertNotIn('fake-groq-secret', str(error.exception))
        self.assertIn('Rejected', str(error.exception))

    def test_groq_rejects_non_chat_models_before_contacting_provider(self):
        opener = FakeOpener(b'')
        with self.assertRaises(providers.ProviderError):
            providers.generate([{'role': 'user', 'content': 'Test'}], 'groq', 'groq/compound',
                               'fake-groq-secret', opener=opener)
        self.assertIsNone(opener.request)

    def test_model_catalog_uses_encrypted_key_server_side_and_returns_only_ids(self):
        opener = FakeOpener(json.dumps({"data": [
            {"id": "llama-3.3-70b-instruct", "object": "model"},
            {"id": "deepseek-v4.1-flash"}, {"id": "deepseek-v4.1-flash"},
            {"id": "bad\nmodel"}, {"missing": "id"},
        ]}).encode())
        models = providers.list_models("scaleway", "secret-value", opener=opener)
        self.assertEqual(models, ["deepseek-v4.1-flash", "llama-3.3-70b-instruct"])
        self.assertEqual(opener.request.full_url, "https://api.scaleway.ai/v1/models")
        self.assertEqual(opener.request.method, "GET")
        self.assertEqual(opener.request.get_header("Authorization"), "Bearer secret-value")
        self.assertNotIn("secret-value", repr(models))

    def test_scaleway_permission_error_explains_the_required_iam_policy(self):
        class DeniedOpener:
            def open(self, request, timeout=None):
                payload = io.BytesIO(b'{"error":{"message":"insufficient permissions to access the resource"}}')
                raise urllib.error.HTTPError(request.full_url, 403, "Forbidden", {}, payload)

        with self.assertRaises(providers.ProviderError) as raised:
            providers.list_models("scaleway", "secret-value", opener=DeniedOpener())
        message = str(raised.exception)
        self.assertIn("GenerativeApisModelAccess", message)
        self.assertIn("proyecto u organización correctos", message)
        self.assertNotIn("secret-value", message)

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

    def test_images_are_never_sent_to_unconfirmed_models(self):
        for provider in providers.PROVIDERS:
            with self.subTest(provider=provider), self.assertRaisesRegex(providers.ProviderError, "no está confirmado"):
                providers.generate([{"role": "user", "content": "image", "images": ["base64"]}],
                                   provider, "chat-model", "secret-value", opener=FakeOpener(b""))

    def test_truncated_stream_is_not_reported_as_complete(self):
        body = b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
        with self.assertRaisesRegex(providers.ProviderError, "sin confirmar"):
            providers.generate([{"role": "user", "content": "question"}], "ovh", "model", "secret-value",
                               on_chunk=lambda piece: None, opener=FakeOpener(body))


if __name__ == "__main__":
    unittest.main(verbosity=2)
