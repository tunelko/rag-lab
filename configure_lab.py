"""Shared Windows/Linux setup. Import and --check use only the standard library."""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import importlib.metadata
import ipaddress
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request

import setup_texts

ROOT = Path(__file__).resolve().parent
TEXTUAL_VERSION = '8.2.8'
LANGUAGE = setup_texts.DEFAULT


def set_language(code):
    """Language of every message from here on; an unknown code keeps the default."""
    global LANGUAGE
    LANGUAGE = code if code in setup_texts.LANGUAGES else setup_texts.DEFAULT
    return LANGUAGE


def t(key, **values):
    return setup_texts.text(key, LANGUAGE, **values)


def language_file(root: Path):
    return root / '.cache' / 'setup-language'


def saved_language(root: Path):
    try:
        return setup_texts.normalize(language_file(root).read_text('utf-8').strip())
    except OSError:
        return None


def save_language(root: Path, code):
    """Remember the wizard's language. A preference of the tool, kept with its own files in .cache."""
    try:
        atomic_write(language_file(root), (code + '\n').encode())
    except OSError:
        pass


def venv_python(root: Path, directory='.venv', system=None):
    return root / directory / ('Scripts/python.exe' if (system or os.name) == 'nt' else 'bin/python')


def read_env(path: Path):
    values = {}
    if path.exists():
        for line in path.read_text('utf-8-sig').splitlines():
            match = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s*=\s*(.*?)\s*$', line)
            if match:
                values[match[1]] = match[2].strip('"\'')
    return values


def local_endpoint(value: str):
    parsed = urllib.parse.urlsplit(value.strip())
    try:
        local = parsed.hostname == 'localhost' or ipaddress.ip_address(parsed.hostname or '').is_loopback
    except ValueError:
        local = False
    if (not local or parsed.scheme not in ('http', 'https') or parsed.username or parsed.password
            or parsed.query or parsed.fragment or parsed.path not in ('', '/')):
        raise ValueError(t('endpoint_local'))
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError(t('endpoint_port')) from exc
    return value.strip().rstrip('/')


def local_json(url, payload=None, timeout=3):
    # Loopback traffic must not go through a machine's configured HTTP proxy.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            raise ValueError(t('redirected'))
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
    with opener.open(request, timeout=timeout) as response:
        return json.load(response)


@dataclass
class Plan:
    profile: str = 'ollama'
    endpoint: str = 'http://127.0.0.1:11434'
    response_model: str = ''
    vision_model: str = ''
    multimodal: bool = False
    response_mode: str = 'automatic'
    k: int = 4
    max_images: int = 3
    max_visuals: int = 500
    search_only: bool = False
    collection: str = 'documents'
    chunk_size: int = 500
    chunk_overlap: int = 80
    context_chars: int = 12000
    output_tokens: int = 512
    port: int = 8765


def load_plan(root: Path):
    values = read_env(root / '.env')
    settings_path = root / 'data/settings.json'
    settings = json.loads(settings_path.read_text('utf-8-sig')) if settings_path.exists() else {}
    cuda_installed = any((root / '.venv/Lib/site-packages').glob('torch-*+cu*.dist-info')) or any(
        (root / '.venv/lib').glob('python*/site-packages/torch-*+cu*.dist-info'))
    return Plan(
        profile=values.get('RAG_SETUP_PROFILE', 'cuda' if cuda_installed else 'ollama'),
        endpoint=values.get('RAG_OLLAMA_URL', 'http://127.0.0.1:11434'),
        response_model=settings.get('response_model', values.get('RAG_OLLAMA_MODEL', '')),
        vision_model=settings.get('vision_model', ''), multimodal=settings.get('multimodal', False),
        response_mode=settings.get('response_mode', 'automatic'), k=settings.get('k', 4),
        max_images=settings.get('max_images', 3), max_visuals=settings.get('max_visuals_per_document', 500),
        search_only=settings.get('search_only', False), collection=values.get('RAG_COLLECTION', 'documents'),
        chunk_size=int(values.get('RAG_CHUNK_SIZE', 500)), chunk_overlap=int(values.get('RAG_CHUNK_OVERLAP', 80)),
        context_chars=int(values.get('RAG_CONTEXT_CHARS', 12000)), output_tokens=int(values.get('RAG_MAX_NEW_TOKENS', 512)),
        port=int(values.get('RAG_WEB_PORT', 8765)),
    )


def validate_plan(plan: Plan, root: Path):
    local_endpoint(plan.endpoint)
    if plan.profile not in ('ollama', 'cuda') or plan.response_mode not in ('automatic', 'manual'):
        raise ValueError(t('bad_profile_or_mode'))
    for model in (plan.response_model, plan.vision_model):
        if model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,299}', model):
            raise ValueError(t('bad_model_name'))
        if model.lower().endswith(':cloud'):
            raise ValueError(t('cloud_model'))
    if not plan.search_only and not plan.response_model:
        raise ValueError(t('need_response_model'))
    if plan.multimodal and not plan.vision_model:
        raise ValueError(t('need_vision_model'))
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,62}', plan.collection):
        raise ValueError(t('bad_collection'))
    if not 0 <= plan.chunk_overlap < plan.chunk_size <= 4000 or plan.chunk_size < 100:
        raise ValueError(t('bad_chunks'))
    if (plan.k not in (2, 4, 6, 8) or not 1 <= plan.max_images <= 4 or not 1 <= plan.max_visuals <= 500
            or not 200 <= plan.context_chars <= 64000 or not 64 <= plan.output_tokens <= 4096
            or not 1024 <= plan.port <= 65535):
        raise ValueError(t('bad_limits'))
    old = read_env(root / '.env')
    manifest = root / 'data/chroma' / f'{plan.collection}-manifest.json'
    if manifest.exists():
        identity = json.loads(manifest.read_text('utf-8'))['config']
        if (plan.chunk_size != identity.get('chunk_size') or plan.chunk_overlap != identity.get('chunk_overlap')
                or old.get('RAG_EMBEDDING_MODEL', 'intfloat/multilingual-e5-small') != identity.get('embedding_model')):
            raise ValueError(t('index_differs'))


def probe(root: Path, plan: Plan):
    result = {'python': platform.python_version(), 'system': platform.system(), 'machine': platform.machine(),
              'project': str(root), 'environment': venv_python(root).exists(), 'models': [], 'vision_models': [],
              'ollama': False, 'servers': [], 'notes': []}
    try:
        endpoint = local_endpoint(plan.endpoint)
        data = local_json(endpoint + '/api/tags')
        result['ollama'] = True
        for item in data.get('models', []):
            model = item['name']
            if model.lower().endswith(':cloud') or item.get('remote_host') or item.get('remote_model'):
                continue
            # /api/show reads model metadata; it does not load or run the model.
            try:
                details = local_json(endpoint + '/api/show', {'model': model})
                if details.get('remote_host') or details.get('remote_model'):
                    continue
                result['models'].append(model)
                if 'vision' in details.get('capabilities', []):
                    result['vision_models'].append(model)
            except (OSError, ValueError):
                result['notes'].append(t('metadata_failed', model=model))
    except (OSError, ValueError) as exc:
        result['notes'].append(t('ollama_unavailable', error=exc))
    old = read_env(root / '.env')
    for port in sorted({8765, plan.port, int(old.get('RAG_WEB_PORT', 8765))}):
        hosts=['127.0.0.1']
        # Managed servers may bind only to a LAN address. Discovery must stay
        # standard-library-only, including before the project environment exists.
        try:
            state=json.loads((root/'.cache/servers'/f'{port}.json').read_text('utf-8'))
            if not isinstance(state,dict):
                raise ValueError(t('bad_server_state'))
            address=ipaddress.IPv4Address(state.get('host','127.0.0.1'))
            if address.is_loopback or any(address in ipaddress.IPv4Network(net) for net in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):
                hosts.append(str(address))
        except (OSError, ValueError, TypeError):
            pass
        for host in dict.fromkeys(hosts):
            try:
                address = f'http://{host}:{port}'
                session = local_json(address + '/api/session', timeout=.5)
                if not session.get('token'):
                    continue
                jobs = local_json(address + '/api/jobs?active=1', timeout=.5).get('jobs', [])
                result['servers'].append({'port': port, 'host':host, 'active_jobs': len(jobs)})
                break
            except (OSError, ValueError):
                pass
    return result


def installation_commands(root: Path, profile: str):
    if profile not in ('ollama', 'cuda'):
        raise ValueError(t('bad_profile'))
    python = str(venv_python(root))
    requirements = root / 'requirements'
    torch_profile = 'torch-cpu.txt' if profile == 'ollama' else 'torch-cuda.txt'
    packages = [python, '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(requirements / (profile + '.txt'))]
    return [
        [sys.executable, '-m', 'venv', str(root / '.venv')],
        [python, '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(requirements / torch_profile)],
        packages,
        [python, '-m', 'pip', 'uninstall', '--yes', 'langchain-chroma', 'chromadb'],
        [python, '-m', 'pip', 'check'],
    ]


def require_migrated_indexes(root: Path):
    import sqlite3
    for manifest in (root/'data/chroma').glob('*-manifest.json'):
        state=json.loads(manifest.read_text('utf-8'))
        name=manifest.name.removesuffix('-manifest.json')
        published={identifier for record in state.get('files',{}).values() for identifier in record.get('ids',[])}
        if not published:continue
        migrated=False
        target=manifest.parent/f'{name}-vectors.sqlite3'
        if target.is_file():
            try:
                db=sqlite3.connect(target.resolve().as_uri()+'?mode=ro',uri=True)
                try:
                    migrated=(db.execute('PRAGMA user_version').fetchone()[0]==1
                              and published<={row[0] for row in db.execute('SELECT id FROM vectors')})
                finally:db.close()
            except sqlite3.Error:pass
        if not migrated:
            raise ValueError(t('migrate_first', name=name))


def install_environment(root: Path, plan: Plan, report=print):
    if sys.version_info[:2] != (3, 13) or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError(t('need_python'))
    if probe(root, plan)['servers']:
        raise ValueError(t('server_open'))
    require_migrated_indexes(root)
    environment = dict(os.environ, PIP_CACHE_DIR=str(root / '.cache/pip'), PIP_DISABLE_PIP_VERSION_CHECK='1',
                       PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for index, command in enumerate(installation_commands(root, plan.profile)):
        if index == 0 and venv_python(root).exists():
            report(t('environment_kept'))
            continue
        report(subprocess.list2cmdline(command))
        with subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            for line in process.stdout:
                report(line.rstrip())
            if process.wait():
                raise RuntimeError(t('install_failed'))


def update_env(text: str, values: dict):
    remaining = dict(values)
    lines = []
    for line in text.splitlines():
        match = re.match(r'^\s*([A-Z][A-Z0-9_]*)\s*=', line)
        key = match[1] if match else None
        if key in values:
            if key in remaining:
                lines.append(f'{key}={remaining.pop(key)}')
        else:
            lines.append(line)
    lines.extend(f'{key}={value}' for key, value in remaining.items())
    return '\n'.join(lines) + '\n'


def atomic_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=path.name + '.', suffix='.pending', dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def save_configuration(root: Path, plan: Plan, inspect=probe):
    validate_plan(plan, root)
    state = inspect(root, plan)
    if any(server['active_jobs'] for server in state['servers']):
        raise ValueError(t('jobs_active'))
    if state['ollama']:
        if not plan.search_only and plan.response_model not in state['models']:
            raise ValueError(t('model_not_installed'))
        if plan.multimodal and plan.vision_model not in state['vision_models']:
            raise ValueError(t('vision_not_installed'))
    env_path, settings_path = root / '.env', root / 'data/settings.json'
    paths = (env_path, settings_path)
    originals = {path: path.read_bytes() if path.exists() else None for path in paths}
    previous = env_path.read_text('utf-8-sig') if env_path.exists() else (root / '.env.example').read_text('utf-8-sig')
    values = {'RAG_SETUP_PROFILE': plan.profile, 'RAG_EMBEDDING_DEVICE': 'cpu', 'RAG_BACKEND': 'ollama', 'RAG_OLLAMA_URL': local_endpoint(plan.endpoint),
              'RAG_OLLAMA_MODEL': plan.response_model, 'RAG_COLLECTION': plan.collection,
              'RAG_CHUNK_SIZE': plan.chunk_size, 'RAG_CHUNK_OVERLAP': plan.chunk_overlap, 'RAG_TOP_K': plan.k,
              'RAG_CONTEXT_CHARS': plan.context_chars, 'RAG_MAX_NEW_TOKENS': plan.output_tokens, 'RAG_WEB_PORT': plan.port}
    settings = json.loads(originals[settings_path].decode('utf-8-sig')) if originals[settings_path] else {}
    settings.update(response_model=plan.response_model, response_mode=plan.response_mode, k=plan.k,
                    search_only=plan.search_only, multimodal=plan.multimodal, vision_model=plan.vision_model,
                    max_images=plan.max_images, max_visuals_per_document=plan.max_visuals)
    backup = root / '.cache/configuration-backups' / datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    backup.mkdir(parents=True, exist_ok=False)
    for path, data in originals.items():
        if data is not None:
            atomic_write(backup / path.name, data)
    try:
        atomic_write(env_path, update_env(previous, values).encode())
        atomic_write(settings_path, (json.dumps(settings, ensure_ascii=False, indent=2) + '\n').encode())
    except Exception:
        for path, data in originals.items():
            if data is None:
                path.unlink(missing_ok=True)
            else:
                atomic_write(path, data)
        raise
    return backup


def start_web(root: Path):
    plan = load_plan(root)
    for server in probe(root, plan)['servers']:
        if server['port'] == plan.port:
            print(t('already_open', url=f"http://{server.get('host','127.0.0.1')}:{plan.port}"))
            return 0
    python = venv_python(root)
    if not python.exists():
        raise ValueError(t('install_first'))
    environment = dict(os.environ, PYTHONUTF8='1', OMP_NUM_THREADS='4')
    try:
        return subprocess.call([str(python), str(root/'server_control.py'), 'start', '--root', str(root),
                                '--port', str(plan.port)], env=environment, cwd=root)
    except KeyboardInterrupt:
        return 0


def make_app(root=ROOT, inspect=probe, installer=install_environment):
    from rich.text import Text
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.binding import Binding
    from textual.widgets import Button, Checkbox, ContentSwitcher, Input, Label, RichLog, Select, Static

    # Steps, in order. The language comes first so that everything after it is read in it.
    LANGUAGE_STEP, WELCOME, ENVIRONMENT, MODELS, RETRIEVAL, REVIEW, DONE = range(7)
    STEP_NAMES = ('language', 'welcome', 'environment', 'models', 'retrieval', 'review', 'done')
    FIELDS = [('collection', 'collection'), ('chunk_size', 'chunk-size'), ('overlap', 'overlap'), ('context', 'context'),
              ('output', 'output'), ('images', 'images'), ('visuals', 'visuals'), ('port', 'port')]

    class ConfigurationApp(App):
        # The key hints are a line written by compose, so they follow the language like everything else.
        BINDINGS = [Binding('ctrl+right', 'next_step', show=False), Binding('ctrl+left', 'previous_step', show=False),
                    Binding('ctrl+q', 'quit', show=False)]
        CSS = """
        Screen { background: #11151d; color: #d8dee9; }
        #heading { height: 3; padding: 1 2 0 2; color: #9ed6ff; text-style: bold; }
        #location { height: 1; padding: 0 2; color: #8593a8; }
        #body { height: 1fr; margin: 1 2 0 2; }
        #steps { width: 25; border: round #38445c; padding: 1; }
        #center { width: 1fr; border: round #7aa2f7; margin: 0 1; }
        #pages { height: 1fr; }
        .page { padding: 1 2; }
        .page-title { text-style: bold; color: #a8d6ff; margin-bottom: 1; height: auto; }
        .description { height: auto; margin-bottom: 1; color: #b2bdce; }
        Label { margin-top: 1; height: auto; }
        Input, Select { margin-bottom: 0; }
        Checkbox { height: auto; margin-top: 1; }
        #help { width: 32; border: round #38445c; padding: 1; }
        #navigation { height: 3; align: right middle; margin: 0 1; }
        Button { margin-left: 1; min-width: 12; }
        #log { height: 7; border: round #38445c; margin: 0 2; }
        #status { height: 1; margin: 0 2; color: #9ec9a7; }
        .narrow #help { display: none; }
        .small #steps { display: none; }
        #keys { height: 1; padding: 0 2; background: #1b2230; color: #8593a8; }
        """

        def __init__(self):
            super().__init__(ansi_color=False)
            self.plan = load_plan(root)
            self.step = LANGUAGE_STEP
            self.busy = False
            self.state = {'models': [], 'vision_models': [], 'servers': [], 'ollama': False}
            self.status = 'status_review'
            self.backup = None
            self.log_lines = []

        def compose(self) -> ComposeResult:
            plan = self.plan
            yield Static(t('heading'), id='heading')
            yield Static(str(root), id='location', markup=False)
            with Horizontal(id='body'):
                yield Static('', id='steps')
                with Vertical(id='center'):
                    with ContentSwitcher(initial=f'page-{self.step}', id='pages'):
                        with VerticalScroll(id='page-0', classes='page'):
                            yield Static(t('language_title'), classes='page-title')
                            yield Static(t('language_description'), classes='description')
                            yield Label(t('language_label'))
                            yield Select([(name, code) for code, name in setup_texts.LANGUAGES.items()],
                                         value=LANGUAGE, allow_blank=False, id='language')
                        with VerticalScroll(id='page-1', classes='page'):
                            yield Static(t('welcome_title'), classes='page-title')
                            yield Static(t('welcome_description'), classes='description')
                            yield Static('', id='detection', markup=False)
                            yield Button(t('check_machine'), id='refresh', variant='primary')
                        with VerticalScroll(id='page-2', classes='page'):
                            yield Static(t('environment_title'), classes='page-title')
                            yield Select([(t('profile_ollama'), 'ollama'), (t('profile_cuda'), 'cuda')],
                                         value=plan.profile, allow_blank=False, id='profile')
                            yield Static(t('environment_description'), classes='description')
                            yield Static('', id='install-plan', markup=False)
                            yield Button(t('install_dependencies'), id='install', variant='primary')
                        with VerticalScroll(id='page-3', classes='page'):
                            yield Static(t('models_title'), classes='page-title')
                            yield Label(t('ollama_server'))
                            yield Input(plan.endpoint, id='endpoint')
                            yield Button(t('refresh_models'), id='models-refresh')
                            yield Label(t('text_model'))
                            yield Select([(t('type_name_below'), '')], value='', allow_blank=False, id='models')
                            yield Input(plan.response_model, placeholder=t('model_placeholder'), id='response-model')
                            yield Checkbox(t('enable_multimodal'), plan.multimodal, id='multimodal')
                            yield Label(t('vision_model'))
                            yield Select([(t('type_name_below'), '')], value='', allow_blank=False, id='vision-models')
                            yield Input(plan.vision_model, id='vision-model')
                            yield Label(t('model_choice'))
                            yield Select([(t('mode_automatic'), 'automatic'), (t('mode_manual'), 'manual')],
                                         value=plan.response_mode, allow_blank=False, id='response-mode')
                        with VerticalScroll(id='page-4', classes='page'):
                            yield Static(t('retrieval_title'), classes='page-title')
                            yield Label(t('fragments'))
                            yield Select([(str(n), n) for n in (2, 4, 6, 8)], value=plan.k, allow_blank=False, id='fragments')
                            yield Checkbox(t('search_only'), plan.search_only, id='search-only')
                            values = {'collection': plan.collection, 'chunk-size': plan.chunk_size, 'overlap': plan.chunk_overlap,
                                      'context': plan.context_chars, 'output': plan.output_tokens, 'images': plan.max_images,
                                      'visuals': plan.max_visuals, 'port': plan.port}
                            for name, identifier in FIELDS:
                                yield Label(t('field_' + name.replace('-', '_')))
                                yield Input(str(values[identifier]), id=identifier)
                        with VerticalScroll(id='page-5', classes='page'):
                            yield Static(t('review_title'), classes='page-title')
                            yield Static('', id='review', markup=False)
                            yield Checkbox(t('confirm_changes'), id='confirm')
                            yield Button(t('save_configuration'), id='apply', variant='success')
                        with VerticalScroll(id='page-6', classes='page'):
                            yield Static(t('done_title'), classes='page-title')
                            yield Static(t('done_description'), classes='description')
                            yield Static('', id='finished', markup=False)
                            yield Button(t('start_web'), id='start-web', variant='success')
                    with Horizontal(id='navigation'):
                        yield Button(t('previous'), id='back')
                        yield Button(t('next'), id='next', variant='primary')
                yield Static('', id='help', markup=False)
            yield RichLog(id='log', markup=False, highlight=False, max_lines=300, wrap=True)
            yield Static('', id='status')
            yield Static(t('key_hints'), id='keys')

        def on_mount(self):
            self.apply_language()
            self.refresh_metadata()

        def on_resize(self, event):
            self.screen.set_class(event.size.width < 110, 'narrow')
            self.screen.set_class(event.size.width < 75, 'small')

        def log_line(self, message):
            self.log_lines = (self.log_lines + [str(message)])[-300:]
            self.query_one('#log', RichLog).write(Text(str(message)))

        def apply_language(self):
            """Everything that is not written by compose: titles, key hints, and what the session has shown so far."""
            self.title, self.sub_title = t('title'), t('subtitle')
            log = self.query_one('#log', RichLog)
            for line in self.log_lines:
                log.write(Text(line))
            if self.state.get('system'):
                self.show_detection()
            if self.backup is not None:
                self.show_finished()
            self.query_one('#status', Static).update(t(self.status))
            self.show_step()

        def show_step(self):
            self.query_one('#pages', ContentSwitcher).current = f'page-{self.step}'
            self.query_one('#steps', Static).update(Text('\n\n'.join(
                ('▸ ' if n == self.step else '✓ ' if n < self.step else '  ') + t('step_' + name)
                for n, name in enumerate(STEP_NAMES))))
            self.query_one('#help', Static).update(t('help_' + STEP_NAMES[self.step]))
            self.query_one('#back', Button).disabled = self.step == LANGUAGE_STEP or self.busy
            self.query_one('#next', Button).disabled = self.step >= REVIEW or self.busy
            self.query_one('#language', Select).disabled = self.busy
            self.update_install_plan()
            if self.step == REVIEW:
                self.query_one('#review', Static).update(self.review_text())

        def collect(self):
            p = self.plan
            p.profile = self.query_one('#profile', Select).value
            p.endpoint = local_endpoint(self.query_one('#endpoint', Input).value)
            p.response_model = self.query_one('#response-model', Input).value.strip()
            p.vision_model = self.query_one('#vision-model', Input).value.strip()
            p.multimodal = self.query_one('#multimodal', Checkbox).value
            p.search_only = self.query_one('#search-only', Checkbox).value
            p.response_mode = self.query_one('#response-mode', Select).value
            p.k = self.query_one('#fragments', Select).value
            p.collection = self.query_one('#collection', Input).value.strip()
            for field, identifier, label in [
                    ('chunk_size', 'chunk-size', 'field_chunk_size'), ('chunk_overlap', 'overlap', 'field_overlap'),
                    ('context_chars', 'context', 'field_context'), ('output_tokens', 'output', 'field_output'),
                    ('max_images', 'images', 'field_images'), ('max_visuals', 'visuals', 'field_visuals'), ('port', 'port', 'field_port')]:
                try:
                    setattr(p, field, int(self.query_one('#' + identifier, Input).value))
                except ValueError as exc:
                    raise ValueError(t('need_integer', field=t(label))) from exc
            return p

        def review_text(self):
            p = self.plan
            old = load_plan(root)
            changes = [f'{key}: {getattr(old, key)} → {value}' for key, value in asdict(p).items()
                       if getattr(old, key) != value and key != 'profile']
            return (t('review_files') + '\n\n'
                    + (t('review_changes') + '\n' + '\n'.join(changes) if changes else t('review_unchanged')) + '\n\n'
                    + t('review_summary', response=p.response_model or t('only_search'), vision=p.vision_model or t('not_chosen'),
                        url=f'http://127.0.0.1:{p.port}'))

        def update_install_plan(self):
            profile = self.query_one('#profile', Select).value
            self.query_one('#install-plan', Static).update(t('commands_to_run') + '\n' + '\n'.join(
                subprocess.list2cmdline(command) for command in installation_commands(root, profile)[1:]))

        def action_next_step(self):
            if self.busy or self.step >= REVIEW:
                return
            try:
                if self.step >= MODELS:
                    self.collect()
                if self.step == RETRIEVAL:
                    validate_plan(self.plan, root)
                self.step += 1
                self.show_step()
            except ValueError as exc:
                self.notify(str(exc), severity='error', timeout=8)

        def action_previous_step(self):
            if not self.busy and self.step > LANGUAGE_STEP:
                self.step -= 1
                self.query_one('#confirm', Checkbox).value = False
                self.show_step()

        def action_quit(self):
            if self.busy:
                self.notify(t('wait_operation'), severity='warning')
            else:
                self.exit()

        @on(Select.Changed, '#language')
        def language_changed(self, event):
            if event.value == LANGUAGE or event.value not in setup_texts.LANGUAGES or self.busy:
                return
            try:
                self.collect()      # keep what has been typed so far
            except ValueError:
                pass
            set_language(event.value)
            save_language(root, LANGUAGE)
            self.log_lines.append(t('language_changed', name=setup_texts.LANGUAGES[LANGUAGE]))
            # Outside this handler: the selector that sent the event is one of the widgets being replaced.
            self.run_worker(self.rebuild(), exclusive=True, group='language')

        async def rebuild(self):
            """Every label is written by compose: building the screen again is the whole translation."""
            await self.recompose()
            if not self.query('#language'):     # the wizard was closed meanwhile
                return
            self.apply_language()
            self.query_one('#language', Select).focus()

        @on(Select.Changed, '#profile')
        def profile_changed(self):
            self.update_install_plan()

        @on(Select.Changed, '#models, #vision-models')
        def selected_model(self, event):
            if event.value:
                identifier = '#response-model' if event.select.id == 'models' else '#vision-model'
                self.query_one(identifier, Input).value = str(event.value)

        def set_busy(self, value, status):
            self.busy = value
            self.status = status
            self.query_one('#status', Static).update(t(status))
            for identifier in ('install', 'apply', 'refresh', 'models-refresh', 'start-web'):
                self.query_one('#' + identifier, Button).disabled = value
            self.show_step()

        def show_detection(self):
            state = self.state
            self.query_one('#detection', Static).update(t(
                'detection', system=f'{state["system"]} {state["machine"]}', python=state['python'],
                environment=t('environment_present' if state['environment'] else 'environment_missing'),
                ollama=t('ollama_connected' if state['ollama'] else 'ollama_missing'),
                models=len(state['models']), visuals=len(state['vision_models']), servers=len(state['servers']),
                jobs=sum(server['active_jobs'] for server in state['servers'])))
            for identifier, names in (('models', state['models']), ('vision-models', state['vision_models'])):
                self.query_one('#' + identifier, Select).set_options([(t('type_name_below'), '')] + [(name, name) for name in names])

        def metadata_ready(self, state):
            self.state = state
            self.show_detection()
            self.log_line(t('detection_done'))

        @work(thread=True, exclusive=True, group='metadata')
        def refresh_metadata(self):
            try:
                state = inspect(root, self.plan)
                self.call_from_thread(self.metadata_ready, state)
            except Exception as exc:
                self.call_from_thread(self.log_line, str(exc))

        @work(thread=True, exclusive=True, group='operation')
        def perform_operation(self, operation):
            try:
                if operation == 'install':
                    installer(root, self.plan, lambda line: self.call_from_thread(self.log_line, line))
                    self.call_from_thread(self.log_line, t('dependencies_ready'))
                    self.call_from_thread(self.set_busy, False, 'status_installed')
                    self.call_from_thread(self.refresh_metadata)
                else:
                    backup = save_configuration(root, self.plan, inspect)
                    self.call_from_thread(self.saved, backup)
            except Exception as exc:
                self.call_from_thread(self.log_line, 'ERROR: ' + str(exc))
                self.call_from_thread(self.set_busy, False, 'status_stopped')

        def show_finished(self):
            self.query_one('#finished', Static).update(t('finished', url=f'http://127.0.0.1:{self.plan.port}', backup=self.backup))

        def saved(self, backup):
            self.step = DONE
            self.backup = backup
            self.show_finished()
            self.log_line(t('configuration_saved'))
            self.set_busy(False, 'status_done')

        @on(Button.Pressed)
        def button_pressed(self, event):
            identifier = event.button.id
            if identifier == 'next':
                self.action_next_step()
            elif identifier == 'back':
                self.action_previous_step()
            elif identifier in ('refresh', 'models-refresh'):
                try:
                    self.collect()
                    self.refresh_metadata()
                except ValueError as exc:
                    self.notify(str(exc), severity='error')
            elif identifier in ('install', 'apply'):
                try:
                    self.collect()
                    if identifier == 'apply':
                        validate_plan(self.plan, root)
                        if not self.query_one('#confirm', Checkbox).value:
                            raise ValueError(t('need_confirmation'))
                    self.set_busy(True, 'status_installing' if identifier == 'install' else 'status_saving')
                    self.perform_operation('install' if identifier == 'install' else 'save')
                except ValueError as exc:
                    self.notify(str(exc), severity='error', timeout=8)
            elif identifier == 'start-web' and not self.busy:
                self.exit('start')

    return ConfigurationApp()


def bootstrap():
    directory = '.cache/wizard-venv'
    python = venv_python(ROOT, directory)
    if not python.exists():
        print(t('preparing_interface'), flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / directory)], check=True)
    check = subprocess.run([str(python), '-c', 'import importlib.metadata; print(importlib.metadata.version("textual"))'],
                           capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != TEXTUAL_VERSION:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:',
                        '-r', str(ROOT / 'requirements/wizard.txt')], check=True,
                       env=dict(os.environ, PIP_CACHE_DIR=str(ROOT / '.cache/pip'), PYTHONUTF8='1'))
    # The wizard runs in its own interpreter: hand it the language already resolved here.
    return subprocess.call([str(python), str(Path(__file__).resolve()), '--lang', LANGUAGE], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--bootstrap', action='store_true', help='Prepare the isolated TUI environment and open the wizard.')
    group.add_argument('--check', action='store_true', help='Read-only local detection, without installing anything.')
    group.add_argument('--start', action='store_true', help='Start the configured local web server in the foreground.')
    parser.add_argument('--lang', choices=sorted(setup_texts.LANGUAGES),
                        help='Language of the wizard and its messages. Default: the saved choice, then the system language.')
    args = parser.parse_args()
    # An explicit --lang, then RAG_SETUP_LANGUAGE, then what the wizard remembered, then the session's language.
    set_language(setup_texts.resolve(args.lang, saved_language(ROOT)))
    if platform.system() not in ('Windows', 'Linux'):
        parser.error(t('unsupported_system'))
    if sys.version_info[:2] != (3, 13):
        parser.error(t('use_python'))
    try:
        if args.check:
            print(json.dumps(probe(ROOT, load_plan(ROOT)), ensure_ascii=False, indent=2))
            return 0
        if args.start:
            return start_web(ROOT)
        if args.bootstrap:
            return bootstrap()
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error(t('need_terminal'))
        try:
            app = make_app()
        except ImportError as exc:
            raise ValueError(t('need_launcher')) from exc
        return start_web(ROOT) if app.run() == 'start' else 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
