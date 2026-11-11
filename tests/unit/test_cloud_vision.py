"""Cloud image paths use synthetic images, fake credentials and offline responses."""
import base64
import json
import unittest
from unittest.mock import patch

import generation_providers as providers
import multimodal
import provider_vision
import rag
from settings_store import Preferences
from tests.unit.test_generation_providers import FakeOpener
from tests.unit import test_multimodal
from tests.unit.test_multimodal import png


class AdapterTests(unittest.TestCase):
    def test_provider_payloads_preserve_mime_and_use_explicit_model(self):
        image = base64.b64encode(png()).decode()
        response = json.dumps({'choices': [{'message': {'content': 'Diagram'}, 'finish_reason': 'stop'}]}).encode()
        for provider, model in [('groq', 'qwen/qwen3.8-27b'), ('scaleway', 'gemma-4-26b-a4b-it'),
                                ('ovh', 'Qwen2.5-VL-72B-Instruct'), ('xai', 'grok-4.7')]:
            with self.subTest(provider=provider):
                opener = FakeOpener(response)
                providers.generate([{'role': 'user', 'content': 'Describe', 'images': [image]}],
                                   provider, model, 'test-only-secret', opener=opener)
                body = json.loads(opener.request.data)
                self.assertEqual(body['model'], model)
                self.assertEqual(body['messages'][0]['content'][1]['image_url']['url'], 'data:image/png;base64,' + image)
                self.assertNotIn('test-only-secret', opener.request.data.decode())

    def test_catalog_metadata_overrides_documented_capability_and_unknown_is_not_guessed(self):
        with patch.dict(provider_vision._discovered, clear=True):
            opener = FakeOpener(json.dumps({'data': [
                {'id': 'new-vision', 'input_modalities': ['text', 'image']},
                {'id': 'qwen/qwen3.8-27b', 'input_modalities': ['text']},
                {'id': 'unknown-vision-by-name'},
            ]}).encode())
            models = providers.list_models('groq', 'test-only-secret', opener=opener)
            self.assertEqual([name for name in models if provider_vision.image_limit('groq', name)], ['new-vision'])
            self.assertEqual(provider_vision.image_limit('scaleway', 'new-vision'), 0)

    def test_limits_and_urls_rejected_before_network_and_cancel_has_no_call(self):
        image = base64.b64encode(png()).decode()
        for images in ([image] * 4, ['https://example.com/private.png'], ['aGVsbG8=']):
            opener = FakeOpener(b'')
            with self.assertRaises(providers.ProviderError):
                providers.generate([{'role': 'user', 'content': 'Describe', 'images': images}],
                    'groq', 'qwen/qwen3.8-27b', 'test-only-secret', opener=opener)
            self.assertIsNone(opener.request)
        opener = FakeOpener(b'')
        with self.assertRaises(InterruptedError):
            providers.generate([], 'groq', 'qwen/qwen3.8-27b', 'test-only-secret', cancelled=lambda: True, opener=opener)
        self.assertIsNone(opener.request)

    def test_response_choices_do_not_change_index_identity(self):
        local = Preferences(multimodal=True, vision_model='local-caption-model')
        remote = local.model_copy(update={'response_provider': 'groq', 'external_model': 'text',
            'external_vision_model': 'remote-vision', 'query_vision_model': 'another-local-response'})
        self.assertEqual(multimodal.policy(local), multimodal.policy(remote))
        self.assertEqual(multimodal.policy(remote), 'visual-v1:local-caption-model')


class CloudVisionWebTests(unittest.TestCase):
    setUp = test_multimodal.IntegrationTests.setUp
    tearDown = test_multimodal.IntegrationTests.tearDown
    wait = test_multimodal.IntegrationTests.wait
    upload = test_multimodal.IntegrationTests.upload

    def cloud(self, **values):
        service = self.client.app.state.service
        preferences = service.settings.get().model_copy(update={
            'response_provider': 'groq', 'external_model': 'openai/gpt-oss-20b',
            'external_vision_model': 'qwen/qwen3.8-27b', 'vision_model': 'test-local:latest', 'multimodal': True, **values})
        with patch.object(service.credentials, 'get', return_value='test-only-secret'), patch.object(service, 'model_capabilities', return_value=['vision']):
            response = self.client.put('/api/settings', headers=self.headers, json=preferences.model_dump())
        self.assertEqual(response.status_code, 200, response.text)
        return service

    def test_indexing_stays_local_and_switching_response_provider_preserves_index(self):
        service = self.cloud()
        with patch.object(service, 'model_capabilities', return_value=['vision']), \
             patch('generation_providers.generate', side_effect=AssertionError('Index must not contact cloud')), \
             patch('multimodal.describe_image', return_value='Four USB ports') as local:
            self.upload('cloud.png', png())
            self.upload('cloud.png', png())
            self.assertEqual(local.call_count, 1)
            self.assertEqual(local.call_args.args[1], 'test-local:latest')
            state = rag.load_state(service.config)
            self.assertIn('visual-v1:test-local:latest', repr(state))
            self.assertNotIn('test-only-secret', repr(state))
            self.cloud(response_provider='scaleway', external_vision_model='gemma-4-26b-a4b-it')
            doc, = service.list_documents()
            self.assertTrue(doc['indexed'])
            self.assertFalse(doc['needs_reindex'])
            self.wait(self.client.post('/api/admin/index', headers=self.headers).json()['job_id'])
            self.assertEqual(local.call_count, 1)
        self.assertEqual(len(list(service.visual_root.glob('*.json'))), 1)
        self.assertEqual(rag.load_state(service.config), state)

    def test_cloud_images_use_visual_selection_and_record_actual_route(self):
        service = self.cloud()
        self.upload('facts.txt', b'The maintenance window starts on Sunday.')
        captured = []
        def answer(messages, provider, model, api_key, on_chunk=None, **kwargs):
            captured.append((provider, model, messages[-1].get('images', [])))
            on_chunk('Diagram [1].')
            return 'Diagram [1].', {'done_reason': 'stop', 'output_tokens': 3}
        with patch.object(service.credentials, 'get', return_value='test-only-secret'), \
             patch('generation_providers.generate', side_effect=answer), \
             patch('rag.generate_ollama', side_effect=AssertionError('No local fallback')):
            image = base64.b64encode(png()).decode()
            request = self.client.post('/api/query', headers=self.headers,
                json={'question': 'What is in this diagram?', 'images': [image]})
            self.assertEqual(request.status_code, 202, request.text)
            job = self.wait(request.json()['job_id'])
            self.assertEqual(captured[-1][:2], ('groq', 'qwen/qwen3.8-27b'))
            self.assertEqual(len(captured[-1][2]), 1)
            self.assertEqual(job['metrics']['images_used'], 1)
            self.assertEqual(job['metrics']['routing_mode'], 'external_vision')
            self.assertTrue(job['metrics']['cloud'])
            request = self.client.post('/api/query', headers=self.headers, json={'question': 'When is maintenance?'})
            self.wait(request.json()['job_id'])
            self.assertEqual(captured[-1], ('groq', 'openai/gpt-oss-20b', []))

    def test_missing_cloud_vision_does_not_block_local_indexing(self):
        service = self.cloud(external_vision_model='')
        with patch.object(service.credentials, 'get', return_value='test-only-secret'), \
             patch('generation_providers.generate') as generate, \
             patch.object(service, 'model_capabilities', return_value=['vision']), \
             patch('multimodal.describe_image', return_value='Local caption'):
            self.upload('saved-locally.png', png())
            self.assertTrue((service.documents / 'saved-locally.png').exists())
            self.upload('facts.txt', b'Maintenance is Sunday.')
            response = self.client.post('/api/query', headers=self.headers,
                json={'question': 'Read image', 'images': [base64.b64encode(png()).decode()]})
            self.assertEqual(response.status_code, 422)
            generate.assert_not_called()

    def test_catalog_filters_visual_models_without_inference(self):
        service = self.client.app.state.service
        with patch.object(service.credentials, 'get', return_value='test-only-secret'), \
             patch('generation_providers.list_models', return_value=['openai/gpt-oss-20b', 'qwen/qwen3.8-27b']), \
             patch('generation_providers.generate') as generate:
            result = self.client.get('/api/providers/groq/models').json()
        self.assertEqual(result['vision_models'], ['qwen/qwen3.8-27b'])
        self.assertEqual(result['image_limits'], {'qwen/qwen3.8-27b': 3})
        generate.assert_not_called()

    def test_queued_index_captures_local_model_without_accessing_cloud_credentials(self):
        service = self.cloud()
        with patch.object(service.credentials, 'get', side_effect=AssertionError('Index must not access cloud credentials')), \
             patch.object(service, 'start', return_value={'job_id': 'held'}) as start:
            response = self.client.post('/api/admin/upload', headers=self.headers,
                files=[('files', ('held.png', png()))], data={'auto_index': 'true'})
        self.assertEqual(response.status_code, 200, response.text)
        callback = start.call_args.args[1]
        self.cloud(response_provider='scaleway', external_vision_model='gemma-4-26b-a4b-it')
        with patch.object(service, 'index') as index:
            callback('held')
        self.assertEqual(index.call_args.kwargs['preferences'].response_provider, 'groq')
        self.assertEqual(index.call_args.kwargs['preferences'].vision_model, 'test-local:latest')
        self.assertNotIn('provider_key', index.call_args.kwargs)

    def test_retrieved_figure_uses_cloud_vision_but_manual_mode_stays_textual(self):
        service = self.cloud()
        with patch.object(service, 'model_capabilities', return_value=['vision']), \
             patch('multimodal.describe_image', return_value='Four USB ports'):
            self.upload('diagram.png', png())
        from langchain_core.documents import Document
        text = Document(page_content='Four USB ports.', metadata={
            'file': 'diagram.png', 'source': str(service.documents / 'diagram.png'), 'page': 1})
        captured = []
        def answer(messages, provider, model, api_key, on_chunk=None, **kwargs):
            captured.append((model, len(messages[-1].get('images', []))))
            on_chunk('Four [1].')
            return 'Four [1].', {'done_reason': 'stop'}
        with patch.object(service.credentials, 'get', return_value='test-only-secret'), \
             patch('retrieval_pipeline.retrieve', return_value=([(text, .1)], {})), \
             patch('generation_providers.generate', side_effect=answer):
            for mode in ('automatic', 'manual'):
                self.cloud(response_mode=mode)
                request = self.client.post('/api/query', headers=self.headers, json={'question': 'Describe the diagram'})
                self.wait(request.json()['job_id'])
        self.assertEqual(captured, [('qwen/qwen3.8-27b', 1), ('openai/gpt-oss-20b', 0)])
