"""Configurator regression checks: temporary files, fake services, no inference."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import configure_lab as setup


def fake_probe(root, plan):
    return {'python': '3.13.0', 'system': 'Linux', 'machine': 'x86_64', 'environment': False,
            'models': ['local:latest'], 'vision_models': ['vision:latest'], 'servers': [], 'ollama': True}


class ConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / '.env.example').write_text('RAG_EMBEDDING_MODEL=intfloat/multilingual-e5-small\n', encoding='utf-8')
        self.plan = setup.Plan(response_model='local:latest')

    def tearDown(self):
        self.temp.cleanup()

    def test_platform_paths_and_install_profiles(self):
        self.assertEqual(setup.venv_python(self.root, system='nt'), self.root / '.venv/Scripts/python.exe')
        self.assertEqual(setup.venv_python(self.root, system='posix'), self.root / '.venv/bin/python')
        cpu = setup.installation_commands(self.root, 'ollama')
        cuda = setup.installation_commands(self.root, 'cuda')
        self.assertEqual(Path(cpu[1][-1]).name, 'torch-cpu.txt')
        self.assertEqual(Path(cuda[1][-1]).name, 'torch-cuda.txt')
        self.assertEqual(Path(cpu[2][-1]).name, 'ollama.txt')
        self.assertEqual(Path(cuda[2][-1]).name, 'cuda.txt')
        self.assertEqual(cpu[-2][-2:],['langchain-chroma','chromadb'])
        self.assertFalse(any('ollama' == argument or 'sudo' == argument for command in cpu for argument in command))

    def test_only_loopback_urls_and_valid_ranges(self):
        for value in ['http://127.0.0.1:11434', 'http://localhost:11434', 'http://[::1]:11434/']:
            self.assertTrue(setup.local_endpoint(value))
        for value in ['https://example.com', 'http://user:secret@localhost', 'http://localhost/a',
                      'http://localhost?token=x', 'file:///etc/passwd', 'http://127.0.0.1:99999']:
            with self.subTest(value=value), self.assertRaises(ValueError):
                setup.local_endpoint(value)
        for change in [dict(response_model='other:cloud'), dict(chunk_size=80), dict(chunk_overlap=500),
                       dict(port=80), dict(collection='../other'), dict(max_visuals=501), dict(k=3)]:
            with self.subTest(change=change), self.assertRaises(ValueError):
                setup.validate_plan(replace(self.plan, **change), self.root)
        setup.validate_plan(replace(self.plan, response_model='', search_only=True), self.root)

    def test_save_preserves_extra_settings_and_exact_backup(self):
        original = '# comment\nHF_TOKEN=private-example\nRAG_OLLAMA_MODEL=old:latest\n'
        (self.root / '.env').write_text(original, encoding='utf-8')
        (self.root / 'data').mkdir()
        old_settings = {'vision_model': 'vision:latest', 'response_mode': 'manual'}
        (self.root / 'data/settings.json').write_text(json.dumps(old_settings), encoding='utf-8')
        original_settings = (self.root / 'data/settings.json').read_bytes()
        backup = setup.save_configuration(self.root, self.plan, fake_probe)
        self.assertEqual((backup / '.env').read_text('utf-8'), original)
        self.assertEqual((backup / 'settings.json').read_bytes(), original_settings)
        self.assertIn('HF_TOKEN=private-example', (self.root / '.env').read_text('utf-8'))
        self.assertEqual(setup.load_plan(self.root).response_model, 'local:latest')
        self.assertFalse((self.root / 'data/chroma').exists())
        self.assertFalse((self.root / 'data/documents').exists())

    def test_no_mutation_while_jobs_are_active(self):
        def busy(root, plan):
            return {**fake_probe(root, plan), 'servers': [{'port': 8765, 'active_jobs': 1}]}
        with self.assertRaisesRegex(ValueError, 'tareas activas'):
            setup.save_configuration(self.root, self.plan, busy)
        self.assertFalse((self.root / '.env').exists())
        self.assertFalse((self.root / 'data').exists())
        self.assertFalse((self.root / '.cache').exists())

    def test_uninstalled_or_nonvision_models_cannot_be_saved_when_connected(self):
        for plan in [replace(self.plan, response_model='missing:latest'),
                     replace(self.plan, multimodal=True, vision_model='local:latest')]:
            with self.assertRaises(ValueError):
                setup.save_configuration(self.root, plan, fake_probe)
        self.assertFalse((self.root / '.env').exists())

    def test_second_write_failure_restores_original_files(self):
        (self.root / '.env').write_bytes(b'RAG_OLLAMA_MODEL=old:latest\n')
        (self.root / 'data').mkdir()
        settings = self.root / 'data/settings.json'
        settings.write_bytes(b'{"k": 8}')
        actual_write = setup.atomic_write
        def failing_write(path, content):
            if path == settings and b'response_model' in content:
                raise OSError('simulated disk error')
            return actual_write(path, content)
        with patch.object(setup, 'atomic_write', side_effect=failing_write), self.assertRaises(OSError):
            setup.save_configuration(self.root, self.plan, fake_probe)
        self.assertEqual((self.root / '.env').read_bytes(), b'RAG_OLLAMA_MODEL=old:latest\n')
        self.assertEqual(settings.read_bytes(), b'{"k": 8}')

    def test_existing_index_policy_requires_new_collection(self):
        index = self.root / 'data/chroma'
        index.mkdir(parents=True)
        (index / 'documents-manifest.json').write_text(json.dumps({'config': {
            'embedding_model': 'intfloat/multilingual-e5-small', 'chunk_size': 500, 'chunk_overlap': 80}}), encoding='utf-8')
        setup.validate_plan(self.plan, self.root)
        with self.assertRaisesRegex(ValueError, 'colección nueva'):
            setup.validate_plan(replace(self.plan, chunk_size=800), self.root)
        setup.validate_plan(replace(self.plan, chunk_size=800, collection='new_documents'), self.root)

    def test_probe_uses_only_metadata_and_excludes_cloud(self):
        calls = []
        def service(url, payload=None, **kwargs):
            calls.append((url, payload))
            if url.endswith('/api/tags'):
                return {'models': [{'name': 'local:latest'}, {'name': 'remote:cloud'}, {'name': 'remote-hidden:latest'}]}
            if url.endswith('/api/show'):
                return {'capabilities': ['vision']} if payload['model'] == 'local:latest' else {'remote_host': 'remote'}
            raise OSError('RAG not running')
        with patch.object(setup, 'local_json', side_effect=service):
            state = setup.probe(self.root, self.plan)
        self.assertEqual(state['models'], ['local:latest'])
        self.assertEqual(state['vision_models'], ['local:latest'])
        self.assertFalse(any('/api/chat' in url or '/api/generate' in url or '/api/pull' in url for url, _ in calls))

    def test_existing_cuda_environment_is_selected_without_importing_torch(self):
        (self.root / '.venv/Lib/site-packages/torch-2.14.1+cu130.dist-info').mkdir(parents=True)
        self.assertEqual(setup.load_plan(self.root).profile, 'cuda')

    def test_dependency_update_requires_all_legacy_collections_to_be_migrated(self):
        db=self.root/'data/chroma';db.mkdir(parents=True)
        (db/'example-manifest.json').write_text(json.dumps({'files':{'old':{'ids':['published']}}}),encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'Migra primero'):
            setup.require_migrated_indexes(self.root)
        (db/'example-vectors.sqlite3').touch()
        with self.assertRaisesRegex(ValueError,'Migra primero'):
            setup.require_migrated_indexes(self.root)
        (db/'example-vectors.sqlite3').unlink()
        from vector_store import VectorStore
        VectorStore(db/'example-vectors.sqlite3').upsert_vectors(['published'],['text'],[{}],[[1,0]])
        setup.require_migrated_indexes(self.root)

    def test_probe_finds_managed_lan_server_and_reports_its_busy_queue(self):
        directory=self.root/'.cache/servers';directory.mkdir(parents=True)
        (directory/'8765.json').write_text(json.dumps({'host':'192.168.1.111','pid':123,'created':1}),encoding='utf-8')
        def service(url,payload=None,**kwargs):
            if url=='http://192.168.1.111:8765/api/session':return {'token':'test'}
            if url=='http://192.168.1.111:8765/api/jobs?active=1':return {'jobs':[{'id':'busy'}]}
            raise OSError('not listening')
        with patch.object(setup,'local_json',side_effect=service):
            state=setup.probe(self.root,self.plan)
        self.assertEqual(state['servers'],[{'host':'192.168.1.111','port':8765,'active_jobs':1}])

    def test_start_reports_existing_bind_and_uses_shared_controller(self):
        from contextlib import redirect_stdout
        import io
        state={**fake_probe(self.root,self.plan),'servers':[{'host':'192.168.1.111','port':8765,'active_jobs':0}]}
        output=io.StringIO()
        with patch.object(setup,'probe',return_value=state),patch.object(setup.subprocess,'call') as execute,redirect_stdout(output):
            self.assertEqual(setup.start_web(self.root),0)
        execute.assert_not_called()
        self.assertIn('http://192.168.1.111:8765',output.getvalue())
        python=setup.venv_python(self.root);python.parent.mkdir(parents=True);python.touch()
        with patch.object(setup,'probe',return_value=fake_probe(self.root,self.plan)),patch.object(setup.subprocess,'call',return_value=0) as execute:
            self.assertEqual(setup.start_web(self.root),0)
        self.assertEqual(execute.call_args.args[0],[str(python),str(self.root/'server_control.py'),'start','--root',str(self.root),'--port','8765'])

    def test_install_does_not_touch_running_servers(self):
        state = {**fake_probe(self.root, self.plan), 'servers': [{'port': 8765, 'active_jobs': 0}]}
        # platform.machine() may spawn Windows `ver` when its cache is cold;
        # this assertion concerns installer commands, not host identification.
        with patch.object(setup, 'probe', return_value=state), patch.object(setup.platform, 'machine', return_value='x86_64'), patch.object(setup.subprocess, 'Popen') as execute:
            with self.assertRaisesRegex(ValueError, 'RAG está abierto'):
                setup.install_environment(self.root, self.plan)
        execute.assert_not_called()
        self.assertFalse((self.root / '.venv').exists())


class TerminalTests(unittest.IsolatedAsyncioTestCase):
    async def test_wizard_navigation_review_confirmation_and_save(self):
        from textual.widgets import Checkbox, Input, Select
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / '.env.example').write_text('RAG_EMBEDDING_MODEL=intfloat/multilingual-e5-small\n', encoding='utf-8')
            app = setup.make_app(root, fake_probe)
            async with app.run_test(size=(130, 42)) as pilot:
                await pilot.pause()
                self.assertFalse((root / '.env').exists())
                for target in (1, 2):
                    await pilot.press('ctrl+right')
                    await pilot.pause()
                    self.assertEqual(app.step, target)
                app.query_one('#models', Select).value = 'local:latest'
                await pilot.pause()
                self.assertEqual(app.query_one('#response-model', Input).value, 'local:latest')
                await pilot.press('ctrl+right')
                await pilot.pause()
                self.assertEqual(app.step, 3)
                await pilot.press('ctrl+right')
                await pilot.pause()
                self.assertEqual(app.step, 4)
                app.query_one('#apply').focus()
                await pilot.press('enter')
                await pilot.pause()
                self.assertFalse((root / '.env').exists())
                app.query_one('#confirm', Checkbox).value = True
                app.query_one('#apply').focus()
                await pilot.press('enter')
                for _ in range(40):
                    if app.step == 5:
                        break
                    await pilot.pause(.05)
                self.assertEqual(app.step, 5)
                self.assertEqual(setup.load_plan(root).response_model, 'local:latest')
                self.assertFalse((root / '.venv').exists())

    async def test_narrow_terminal_and_exit_do_not_mutate_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            app = setup.make_app(root, fake_probe)
            async with app.run_test(size=(80, 28)) as pilot:
                await pilot.pause()
                self.assertTrue(app.screen.has_class('narrow'))
                self.assertFalse(app.query_one('#help').display)
                await pilot.press('ctrl+q')
            self.assertFalse((root / '.env').exists())
            self.assertFalse((root / '.venv').exists())


if __name__ == '__main__':
    unittest.main()
