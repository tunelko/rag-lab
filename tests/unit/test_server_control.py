"""Exercise server lifecycle with a real, isolated Uvicorn app; no RAG models."""
from contextlib import redirect_stdout, redirect_stderr
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import server_control as control


FIXTURE = '''from contextlib import asynccontextmanager
from pathlib import Path
from fastapi import FastAPI
ROOT = Path(__file__).resolve().parent
@asynccontextmanager
async def lifespan(app):
    yield
    (ROOT / 'shutdown.txt').write_text('clean')
app = FastAPI(lifespan=lifespan)
@app.get('/api/jobs')
def jobs():
    return {'jobs': [{'id': 'busy'}] if (ROOT / 'busy.txt').exists() else []}
'''


class ServerControlTests(unittest.TestCase):
    def test_disappearing_windows_launcher_does_not_change_requested_host(self):
        gone=SimpleNamespace(cmdline=lambda:(_ for _ in ()).throw(control.psutil.NoSuchProcess(123)))
        fresh=SimpleNamespace(pid=456)
        with patch.object(control,'matching_processes',side_effect=[[(gone,True)],[(fresh,True)]]), \
             patch.object(control,'port_in_use',side_effect=[False,True]), \
             patch.object(control,'python_executable',return_value=Path(sys.executable)), \
             patch.object(control.subprocess,'Popen') as spawn:
            control.start(self.root,self.port,host='127.0.0.2')
        command=spawn.call_args.args[0]
        self.assertEqual(command[command.index('--host')+1],'127.0.0.2')

    def test_host_accepts_only_loopback_or_private_ipv4(self):
        self.assertEqual(control.validate_host('192.168.1.111'),'192.168.1.111')
        for address in ('0.0.0.0','8.8.8.8','example.com','169.254.1.2'):
            with self.assertRaises(ValueError):control.validate_host(address)

    def test_managed_server_on_another_local_address_can_be_stopped_without_host_argument(self):
        control.start(self.root,self.port,timeout=10,host='127.0.0.2')
        self.assertEqual(control.running_host(self.root,self.port),'127.0.0.2')
        self.assertEqual(control.active_jobs(self.port,'127.0.0.2'),[])
        self.assertTrue(control.port_in_use(self.port,'127.0.0.2'))
        import json
        state,_=control.paths(self.root,self.port)
        self.assertEqual(json.loads(state.read_text('utf-8'))['host'],'127.0.0.2')
        control.stop(self.root,self.port,timeout=10)
        self.assertFalse(control.port_in_use(self.port,'127.0.0.2'))

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='rag-control-')
        self.root = Path(self.directory.name).resolve()
        (self.root / 'web_app.py').write_text(FIXTURE, encoding='utf-8')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            self.port = probe.getsockname()[1]
        self.python = patch.object(control, 'python_executable', return_value=Path(sys.executable))
        self.python.start()
        self.output = io.StringIO()
        self.stdout = redirect_stdout(self.output)
        self.stderr = redirect_stderr(self.output)
        self.stdout.__enter__()
        self.stderr.__enter__()

    def tearDown(self):
        try:
            (self.root / 'busy.txt').unlink(missing_ok=True)
            control.stop(self.root, self.port, timeout=10)
        finally:
            self.stdout.__exit__(None, None, None)
            self.stderr.__exit__(None, None, None)
            self.python.stop()
            self.directory.cleanup()

    def test_start_is_idempotent_restart_changes_worker_and_stop_is_clean(self):
        control.start(self.root, self.port, timeout=10)
        original = control.matching_processes(self.root, self.port)
        self.assertEqual(len(original), 1)
        control.start(self.root, self.port, timeout=10)
        self.assertEqual(control.matching_processes(self.root, self.port)[0][0].pid, original[0][0].pid)
        self.assertEqual(control.main(['status', '--root', str(self.root), '--port', str(self.port)]), 0)
        self.assertEqual(control.main(['--restart', '--root', str(self.root), '--port', str(self.port)]), 0)
        restarted = control.matching_processes(self.root, self.port)
        self.assertNotEqual(restarted[0][0].pid, original[0][0].pid)
        self.assertEqual((self.root / 'shutdown.txt').read_text(), 'clean')
        control.stop(self.root, self.port, timeout=10)
        self.assertFalse(control.matching_processes(self.root, self.port))
        self.assertFalse(control.port_in_use(self.port))
        self.assertFalse(control.paths(self.root, self.port)[0].exists())
        self.assertFalse((self.root / 'data').exists())

    def test_busy_restart_is_refused_without_stopping_worker(self):
        control.start(self.root, self.port, timeout=10)
        original = control.matching_processes(self.root, self.port)[0][0].pid
        (self.root / 'busy.txt').write_text('working')
        self.assertEqual(control.main(['restart', '--root', str(self.root), '--port', str(self.port)]), 1)
        self.assertEqual(control.matching_processes(self.root, self.port)[0][0].pid, original)
        self.assertFalse((self.root / 'shutdown.txt').exists())

    def test_foreign_listener_is_preserved_and_pid_files_are_not_trusted(self):
        with socket.socket() as foreign:
            foreign.bind(('127.0.0.1', self.port))
            foreign.listen()
            state, _ = control.paths(self.root, self.port)
            control.atomic_json(state, {'pid': os.getpid(), 'created': 0})
            with self.assertRaisesRegex(RuntimeError, 'otro proceso'):
                control.start(self.root, self.port)
            control.stop(self.root, self.port)
            self.assertTrue(control.port_in_use(self.port))
            self.assertEqual(foreign.getsockname()[1], self.port)

    def test_configured_port_and_explicit_port_validation(self):
        (self.root / '.env').write_text('RAG_WEB_PORT="9876"\n', encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(control.default_port(self.root), 9876)
        self.assertEqual(control.main(['status', '--root', str(self.root), '--port', '80']), 1)

    def test_relative_app_directory_uses_the_servers_working_directory(self):
        def process(pid, directory):
            return SimpleNamespace(pid=pid, info={'name':'python'}, ppid=lambda:999,
                cwd=lambda:str(directory), cmdline=lambda:[sys.executable,'-m','uvicorn',
                    'web_app:app','--app-dir','.','--port',str(self.port)])
        other=process(123, self.root/'another-checkout')
        own=process(456, self.root)
        with patch.object(control.psutil,'process_iter',return_value=[other,own]):
            found=control.matching_processes(self.root,self.port)
        self.assertEqual([item.pid for item,_ in found],[456])

    def test_deferred_restart_waits_until_the_current_work_is_finished(self):
        control.start(self.root,self.port,timeout=10)
        original=control.matching_processes(self.root,self.port)[0][0].pid
        (self.root/'busy.txt').write_text('working')
        result=[]
        waiter=threading.Thread(target=lambda:result.append(control.main(['restart','--when-idle','--root',str(self.root),'--port',str(self.port)])))
        waiter.start()
        try:
            time.sleep(.15)
            self.assertEqual(control.matching_processes(self.root,self.port)[0][0].pid,original)
            (self.root/'busy.txt').unlink()
            waiter.join(timeout=12)
            self.assertFalse(waiter.is_alive())
            self.assertEqual(result,[0])
            self.assertNotEqual(control.matching_processes(self.root,self.port)[0][0].pid,original)
        finally:
            (self.root/'busy.txt').unlink(missing_ok=True)
            waiter.join(timeout=12)
