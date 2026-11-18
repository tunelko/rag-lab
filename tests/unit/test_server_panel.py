"""Operate the terminal panel against a real Uvicorn worker in a temporary project."""
import asyncio
from pathlib import Path
import site
import socket
import sys
import tempfile
import unittest
import venv

from textual.widgets import Button

import server_control
from server_panel import ServerPanel
from tests.unit.test_server_control import FIXTURE


class ServerPanelTests(unittest.IsolatedAsyncioTestCase):
    async def test_buttons_follow_the_worker_and_its_queue(self):
        with tempfile.TemporaryDirectory(prefix='rag-panel-') as directory:
            root = Path(directory).resolve()
            app_dir = root / server_control.CODE
            app_dir.mkdir()
            (app_dir / 'web_app.py').write_text(FIXTURE, encoding='utf-8')
            # Use the real worker dependencies without installing packages or touching the lab.
            env = venv.EnvBuilder(with_pip=False)
            env.create(root / '.venv')
            env_site = Path(env.ensure_directories(root / '.venv').lib_path)
            (env_site / 'test-runtime.pth').write_text('\n'.join(site.getsitepackages()), encoding='utf-8')
            with socket.socket() as listener:
                listener.bind(('127.0.0.1', 0))
                port = listener.getsockname()[1]
                listener.listen()
                panel = ServerPanel(root, port, None, 'es', sys.executable)
                async with panel.run_test(size=(80, 28)) as pilot:
                    async def settle():
                        await pilot.pause()
                        await panel.workers.wait_for_complete()
                        await pilot.pause()

                    try:
                        await settle()
                        self.assertEqual(panel.status['state'], 'occupied')
                        self.assertTrue(panel.query_one('#start', Button).disabled)
                        listener.close()
                        await pilot.click('#refresh')
                        await settle()
                        self.assertEqual(panel.status['state'], 'stopped')
                        await pilot.click('#start')
                        await settle()
                        original = panel.status['pid']
                        self.assertIsNotNone(original)
                        (root / 'busy.txt').touch()
                        await pilot.press('r')
                        await settle()
                        self.assertEqual(panel.status['jobs'], 1)
                        self.assertTrue(panel.query_one('#stop', Button).disabled)
                        self.assertTrue(panel.query_one('#restart', Button).disabled)
                        (root / 'busy.txt').unlink()
                        await pilot.press('r')
                        await settle()
                        await pilot.click('#restart')
                        await settle()
                        self.assertEqual(panel.status['state'], 'running')
                        self.assertNotEqual(panel.status['pid'], original)
                        await pilot.click('#stop')
                        await settle()
                        self.assertEqual(panel.status['state'], 'stopped')
                        self.assertEqual((root / 'shutdown.txt').read_text(), 'clean')
                        await pilot.press('q')
                    finally:
                        (root / 'busy.txt').unlink(missing_ok=True)
                        await asyncio.to_thread(server_control.stop, root, port, 10)
