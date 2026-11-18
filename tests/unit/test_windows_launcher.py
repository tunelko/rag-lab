"""Check PowerShell parsing with an isolated Python stub, never a real server."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import venv


@unittest.skipUnless(os.name == 'nt', 'PowerShell Windows entry point')
class WindowsLauncherTests(unittest.TestCase):
    def test_help_and_subcommand_switch_aliases_from_another_directory(self):
        shell = shutil.which('powershell.exe') or shutil.which('pwsh')
        self.assertIsNotNone(shell)
        with tempfile.TemporaryDirectory(prefix='rag-launcher-') as directory:
            root=Path(directory)
            launcher=root/'install/windows/Start-Web.ps1'
            launcher.parent.mkdir(parents=True)
            shutil.copyfile(Path(__file__).resolve().parents[2]/'install/windows/Start-Web.ps1',launcher)
            # Help must work before installing the virtual environment.
            def invoke(*arguments):
                return subprocess.run([shell,'-NoProfile','-ExecutionPolicy','Bypass','-File',str(launcher),*arguments],
                    cwd=root.parent, text=True, capture_output=True, check=True)
            for option in ('-h','--help','-Help'):
                self.assertIn('Uso:',invoke(option).stdout)
            venv.EnvBuilder(with_pip=False).create(root/'.venv')
            (root/'app').mkdir()
            (root/'app/server_control.py').write_text('import json, sys\nprint(json.dumps(sys.argv[1:]))\n',encoding='utf-8')
            self.assertEqual(json.loads(invoke().stdout), [])
            for action in ('start','stop','restart','status','panel'):
                for option in (action,'--'+action,'-'+action):
                    with self.subTest(option=option):
                        result=invoke(option,'-Port','9876')
                        self.assertEqual(json.loads(result.stdout),[action,'--port','9876'])
            self.assertEqual(json.loads(invoke('--restart','--when-idle').stdout),['restart','--when-idle'])
