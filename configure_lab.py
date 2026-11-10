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

ROOT = Path(__file__).resolve().parent
TEXTUAL_VERSION = '8.2.8'


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
        raise ValueError('Usa una URL local: http://127.0.0.1:11434 (sin credenciales ni rutas).')
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError('Puerto de Ollama no válido.') from exc
    return value.strip().rstrip('/')


def local_json(url, payload=None, timeout=3):
    # Loopback traffic must not go through a machine's configured HTTP proxy.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args):
            raise ValueError('El servicio local intentó redirigir la petición.')
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
        raise ValueError('Perfil o modo de respuesta no válido.')
    for model in (plan.response_model, plan.vision_model):
        if model and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/+@-]{0,299}', model):
            raise ValueError('Nombre de modelo no válido.')
        if model.lower().endswith(':cloud'):
            raise ValueError('Este asistente configura modelos locales; elige uno sin :cloud.')
    if not plan.search_only and not plan.response_model:
        raise ValueError('Elige un modelo de respuesta o activa solo búsqueda.')
    if plan.multimodal and not plan.vision_model:
        raise ValueError('El modo multimodal necesita un modelo visual.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{2,62}', plan.collection):
        raise ValueError('La colección necesita entre 3 y 63 letras, números, guiones o guiones bajos.')
    if not 0 <= plan.chunk_overlap < plan.chunk_size <= 4000 or plan.chunk_size < 100:
        raise ValueError('Fragmentos: 100–4000 caracteres; solapamiento menor que el tamaño.')
    if (plan.k not in (2, 4, 6, 8) or not 1 <= plan.max_images <= 4 or not 1 <= plan.max_visuals <= 500
            or not 200 <= plan.context_chars <= 64000 or not 64 <= plan.output_tokens <= 4096
            or not 1024 <= plan.port <= 65535):
        raise ValueError('Revisa los límites de recuperación, imágenes, contexto, salida y puerto.')
    old = read_env(root / '.env')
    manifest = root / 'data/chroma' / f'{plan.collection}-manifest.json'
    if manifest.exists():
        identity = json.loads(manifest.read_text('utf-8'))['config']
        if (plan.chunk_size != identity.get('chunk_size') or plan.chunk_overlap != identity.get('chunk_overlap')
                or old.get('RAG_EMBEDDING_MODEL', 'intfloat/multilingual-e5-small') != identity.get('embedding_model')):
            raise ValueError('El índice existente usa otra división o embeddings. Elige una colección nueva.')


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
                result['notes'].append('No se han podido validar los metadatos de ' + model)
    except (OSError, ValueError) as exc:
        result['notes'].append('Ollama no disponible: ' + str(exc))
    old = read_env(root / '.env')
    for port in sorted({8765, plan.port, int(old.get('RAG_WEB_PORT', 8765))}):
        try:
            address = f'http://127.0.0.1:{port}'
            session = local_json(address + '/api/session', timeout=.5)
            if not session.get('token'):
                continue
            jobs = local_json(address + '/api/jobs?active=1', timeout=.5).get('jobs', [])
            result['servers'].append({'port': port, 'active_jobs': len(jobs)})
        except (OSError, ValueError):
            pass
    return result


def installation_commands(root: Path, profile: str):
    if profile not in ('ollama', 'cuda'):
        raise ValueError('Perfil no válido.')
    python = str(venv_python(root))
    requirements = root / 'requirements'
    torch_profile = 'torch-cpu.txt' if profile == 'ollama' else 'torch-cuda.txt'
    packages = [python, '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(requirements / (profile + '.txt'))]
    return [
        [sys.executable, '-m', 'venv', str(root / '.venv')],
        [python, '-m', 'pip', 'install', '--only-binary=:all:', '-r', str(requirements / torch_profile)],
        packages,
        [python, '-m', 'pip', 'check'],
    ]


def install_environment(root: Path, plan: Plan, report=print):
    if sys.version_info[:2] != (3, 13) or platform.machine().lower() not in ('amd64', 'x86_64'):
        raise ValueError('La instalación fijada requiere Python 3.13 x64 / x86_64.')
    if probe(root, plan)['servers']:
        raise ValueError('El RAG está abierto. Cierra su servidor antes de instalar dependencias; puedes seguir revisando el asistente.')
    environment = dict(os.environ, PIP_CACHE_DIR=str(root / '.cache/pip'), PIP_DISABLE_PIP_VERSION_CHECK='1',
                       PYTHONUTF8='1', PYTHONIOENCODING='utf-8')
    for index, command in enumerate(installation_commands(root, plan.profile)):
        if index == 0 and venv_python(root).exists():
            report('Entorno existente: se conserva .venv.')
            continue
        report(subprocess.list2cmdline(command))
        with subprocess.Popen(command, cwd=root, env=environment, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding='utf-8', errors='replace',
                              creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)) as process:
            for line in process.stdout:
                report(line.rstrip())
            if process.wait():
                raise RuntimeError('La instalación falló; revisa el registro. No se ha guardado la configuración.')


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
        raise ValueError('Hay tareas activas. Espera a que terminen antes de aplicar cambios; no se ha escrito ningún archivo.')
    if state['ollama']:
        if not plan.search_only and plan.response_model not in state['models']:
            raise ValueError('El modelo de respuesta no está instalado en Ollama.')
        if plan.multimodal and plan.vision_model not in state['vision_models']:
            raise ValueError('El modelo visual debe estar instalado y declarar capacidad vision.')
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
            print(f'El RAG ya está abierto: http://127.0.0.1:{plan.port}')
            return 0
    python = venv_python(root)
    if not python.exists():
        raise ValueError('Primero instala el entorno desde el asistente.')
    print(f'RAG local: http://127.0.0.1:{plan.port} · Ctrl+C para cerrar.', flush=True)
    environment = dict(os.environ, PYTHONUTF8='1', OMP_NUM_THREADS='4')
    try:
        return subprocess.call([str(python), '-m', 'uvicorn', 'web_app:app', '--app-dir', str(root),
                                '--host', '127.0.0.1', '--port', str(plan.port), '--no-access-log'], env=environment, cwd=root)
    except KeyboardInterrupt:
        return 0


def make_app(root=ROOT, inspect=probe, installer=install_environment):
    from rich.text import Text
    from textual import on, work
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.widgets import Button, Checkbox, ContentSwitcher, Footer, Input, Label, RichLog, Select, Static

    class ConfigurationApp(App):
        TITLE = 'RAG Local · Configuración'
        SUB_TITLE = 'Laboratorio local / Windows + Linux'
        BINDINGS = [('ctrl+right', 'next_step', 'Siguiente'), ('ctrl+left', 'previous_step', 'Anterior'),
                    ('ctrl+q', 'quit', 'Salir')]
        CSS = '''
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
        Footer { background: #1b2230; }
        '''
        steps = ['Bienvenida', 'Entorno Python', 'Modelos Ollama', 'Recuperación RAG', 'Revisar y guardar', 'Listo']
        help_text = [
            'TAB cambia el foco. Las flechas recorren las opciones. Ctrl+→ avanza y Ctrl+← retrocede.\n\n'
            'Los cambios se guardan únicamente en Revisar y guardar. Salir antes conserva la configuración actual.',
            'Ollama usa su propio soporte GPU. El perfil recomendado instala PyTorch CPU para los embeddings.\n\n'
            'CUDA añade el backend Transformers/NF4. No se actualizan drivers ni se ejecutan modelos. '
            'La instalación necesita Internet y espacio para los paquetes.',
            'Actualizar consulta /api/tags y /api/show: solo metadatos. No descarga, carga ni ejecuta modelos.\n\n'
            'Si Ollama está apagado puedes escribir los nombres y guardar. Para descargar un modelo, usa ollama pull por separado.',
            'Un fragmento es un trozo de texto recuperado como evidencia. Más fragmentos amplían el contexto y el trabajo.\n\n'
            'La ingesta visual describe páginas completas. Un límite alto permite más trabajo, no garantiza que sea rápido. '
            'Si cambias la división de un índice existente, elige otra colección.',
            'Guardar escribe .env y data/settings.json, con copia de seguridad privada en .cache.\n\n'
            'No reindexa ni arranca servicios. Con tareas activas el guardado se bloquea. '
            'Un servidor ya abierto debe reiniciarse después para leer cambios de .env.',
            'La instalación y la configuración están separadas del arranque. Iniciar web vuelve a la consola y ejecuta '
            'el servidor en primer plano, donde Ctrl+C lo cierra. No se detienen instancias existentes.',
        ]

        def __init__(self):
            super().__init__(ansi_color=False)
            self.plan = load_plan(root)
            self.step = 0
            self.busy = False
            self.state = {'models': [], 'vision_models': [], 'servers': [], 'ollama': False}

        def compose(self) -> ComposeResult:
            yield Static('π > RAG LOCAL  /  CONFIGURACIÓN DEL LABORATORIO', id='heading')
            yield Static(str(root), id='location', markup=False)
            with Horizontal(id='body'):
                yield Static('', id='steps')
                with Vertical(id='center'):
                    with ContentSwitcher(initial='page-0', id='pages'):
                        with VerticalScroll(id='page-0', classes='page'):
                            yield Static('Tu laboratorio, paso a paso', classes='page-title')
                            yield Static('Configura un RAG local con Ollama, documentos y fuentes.\n\n'
                                         'Este asistente conserva los documentos, el índice y los modelos existentes. '
                                         'Puedes recorrerlo sin aplicar cambios.\n\n'
                                         'Dos lanzadores; el mismo asistente en Windows y Linux.', classes='description')
                            yield Static('', id='detection', markup=False)
                            yield Button('Comprobar equipo', id='refresh', variant='primary')
                        with VerticalScroll(id='page-1', classes='page'):
                            yield Static('01 / Entorno Python', classes='page-title')
                            yield Select([('Ollama GPU + embeddings CPU (recomendado)', 'ollama'),
                                          ('PyTorch CUDA 13.0 + HF NF4 (avanzado)', 'cuda')],
                                         value=self.plan.profile, allow_blank=False, id='profile')
                            yield Static('Requiere Python 3.13 x64 / Linux x86_64. Los paquetes se instalan en .venv; '
                                         'el asistente usa otro entorno en .cache.', classes='description')
                            yield Static('', id='install-plan', markup=False)
                            yield Button('Instalar dependencias', id='install', variant='primary')
                        with VerticalScroll(id='page-2', classes='page'):
                            yield Static('02 / Modelos de respuesta', classes='page-title')
                            yield Label('Servidor Ollama local')
                            yield Input(self.plan.endpoint, id='endpoint')
                            yield Button('Actualizar modelos', id='models-refresh')
                            yield Label('Modelo de texto instalado / nombre manual')
                            yield Select([('Escribir nombre debajo', '')], value='', allow_blank=False, id='models')
                            yield Input(self.plan.response_model, placeholder='Nombre exacto de ollama list', id='response-model')
                            yield Checkbox('Activar documentos e imágenes multimodales', self.plan.multimodal, id='multimodal')
                            yield Label('Modelo visual instalado / nombre manual')
                            yield Select([('Escribir nombre debajo', '')], value='', allow_blank=False, id='vision-models')
                            yield Input(self.plan.vision_model, id='vision-model')
                            yield Label('Elección del modelo por consulta')
                            yield Select([('Automática según pregunta y fuentes', 'automatic'), ('Manual', 'manual')],
                                         value=self.plan.response_mode, allow_blank=False, id='response-mode')
                        with VerticalScroll(id='page-3', classes='page'):
                            yield Static('03 / Recuperación y documentos', classes='page-title')
                            yield Label('Fragmentos recuperados por pregunta')
                            yield Select([(str(n), n) for n in (2, 4, 6, 8)], value=self.plan.k, allow_blank=False, id='fragments')
                            yield Checkbox('Solo buscar fragmentos, sin generar respuesta', self.plan.search_only, id='search-only')
                            for label, identifier, value in [
                                ('Colección del índice', 'collection', self.plan.collection),
                                ('Tamaño del fragmento (caracteres)', 'chunk-size', self.plan.chunk_size),
                                ('Solapamiento (caracteres)', 'overlap', self.plan.chunk_overlap),
                                ('Contexto documental máximo (caracteres)', 'context', self.plan.context_chars),
                                ('Tokens máximos de salida', 'output', self.plan.output_tokens),
                                ('Imágenes por respuesta (1–4)', 'images', self.plan.max_images),
                                ('Vistas por documento (1–500)', 'visuals', self.plan.max_visuals),
                                ('Puerto de la web local', 'port', self.plan.port),
                            ]:
                                yield Label(label)
                                yield Input(str(value), id=identifier)
                        with VerticalScroll(id='page-4', classes='page'):
                            yield Static('04 / Revisión de cambios', classes='page-title')
                            yield Static('', id='review', markup=False)
                            yield Checkbox('He revisado los cambios y quiero guardarlos', id='confirm')
                            yield Button('Guardar configuración', id='apply', variant='success')
                        with VerticalScroll(id='page-5', classes='page'):
                            yield Static('Configuración guardada', classes='page-title')
                            yield Static('Siguiente: inicia la web, prepara los embeddings desde Configuración y añade '
                                         'documentos en Biblioteca. La primera preparación descarga el modelo de búsqueda. '
                                         'Las consultas y la indexación comparten una cola.', classes='description')
                            yield Static('', id='finished', markup=False)
                            yield Button('Iniciar web', id='start-web', variant='success')
                    with Horizontal(id='navigation'):
                        yield Button('Anterior', id='back')
                        yield Button('Siguiente →', id='next', variant='primary')
                yield Static('', id='help', markup=False)
            yield RichLog(id='log', markup=False, highlight=False, max_lines=300, wrap=True)
            yield Static('Revisión · Sin cambios aplicados', id='status')
            yield Footer()

        def on_mount(self):
            self.show_step()
            self.refresh_metadata()

        def on_resize(self, event):
            self.screen.set_class(event.size.width < 110, 'narrow')
            self.screen.set_class(event.size.width < 75, 'small')

        def log_line(self, message):
            self.query_one('#log', RichLog).write(Text(str(message)))

        def show_step(self):
            self.query_one('#pages', ContentSwitcher).current = f'page-{self.step}'
            self.query_one('#steps', Static).update(Text('\n\n'.join(
                ('▸ ' if n == self.step else '✓ ' if n < self.step else '  ') + label
                for n, label in enumerate(self.steps))))
            self.query_one('#help', Static).update(self.help_text[self.step])
            self.query_one('#back', Button).disabled = self.step == 0 or self.busy
            self.query_one('#next', Button).disabled = self.step >= 4 or self.busy
            self.update_install_plan()
            if self.step == 4:
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
            for field, identifier in [('chunk_size', 'chunk-size'), ('chunk_overlap', 'overlap'), ('context_chars', 'context'),
                                      ('output_tokens', 'output'), ('max_images', 'images'), ('max_visuals', 'visuals'), ('port', 'port')]:
                try:
                    setattr(p, field, int(self.query_one('#' + identifier, Input).value))
                except ValueError as exc:
                    raise ValueError(f'El campo {identifier} necesita un número entero.') from exc
            return p

        def review_text(self):
            p = self.plan
            old = load_plan(root)
            changes = [f'{key}: {getattr(old, key)} → {value}' for key, value in asdict(p).items()
                       if getattr(old, key) != value and key != 'profile']
            return ('ARCHIVOS\n.env\ndata/settings.json\nCopia privada: .cache/configuration-backups/\n\n'
                    + ('CAMBIOS\n' + '\n'.join(changes) if changes else 'Los valores elegidos coinciden con los existentes.')
                    + f'\n\nRespuesta: {p.response_model or "solo búsqueda"}\nVisual: {p.vision_model or "sin elegir"}'
                    + f'\nWeb: http://127.0.0.1:{p.port}\n\nGuardar no instala paquetes ni ejecuta modelos.')

        def update_install_plan(self):
            profile = self.query_one('#profile', Select).value
            self.query_one('#install-plan', Static).update('COMANDOS A EJECUTAR\n' + '\n'.join(
                subprocess.list2cmdline(command) for command in installation_commands(root, profile)[1:]))

        def action_next_step(self):
            if self.busy or self.step >= 4:
                return
            try:
                if self.step >= 2:
                    self.collect()
                if self.step == 3:
                    validate_plan(self.plan, root)
                self.step += 1
                self.show_step()
            except ValueError as exc:
                self.notify(str(exc), severity='error', timeout=8)

        def action_previous_step(self):
            if not self.busy and self.step > 0:
                self.step -= 1
                self.query_one('#confirm', Checkbox).value = False
                self.show_step()

        def action_quit(self):
            if self.busy:
                self.notify('Espera a que termine la operación antes de cerrar.', severity='warning')
            else:
                self.exit()

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
            self.query_one('#status', Static).update(status)
            for identifier in ('install', 'apply', 'refresh', 'models-refresh', 'start-web'):
                self.query_one('#' + identifier, Button).disabled = value
            self.show_step()

        def metadata_ready(self, state):
            self.state = state
            text = (f'Sistema: {state["system"]} {state["machine"]}\nPython: {state["python"]}\n'
                    f'Entorno RAG: {"existente" if state["environment"] else "pendiente de instalar"}\n'
                    f'Ollama: {"conectado" if state["ollama"] else "no disponible"}\n'
                    f'Modelos: {len(state["models"])} · Visuales: {len(state["vision_models"])}\n'
                    f'Servidores RAG abiertos: {len(state["servers"])}\n'
                    f'Tareas activas: {sum(server["active_jobs"] for server in state["servers"])}')
            self.query_one('#detection', Static).update(text)
            for identifier, names in (('models', state['models']), ('vision-models', state['vision_models'])):
                self.query_one('#' + identifier, Select).set_options([('Escribir nombre debajo', '')] + [(name, name) for name in names])
            self.log_line('Detección completada. Solo se han consultado metadatos locales.')

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
                    self.call_from_thread(self.log_line, 'Dependencias listas. No se ha ejecutado ningún modelo.')
                    self.call_from_thread(self.set_busy, False, 'Instalación terminada · Continúa la configuración')
                    self.call_from_thread(self.refresh_metadata)
                else:
                    backup = save_configuration(root, self.plan, inspect)
                    self.call_from_thread(self.saved, backup)
            except Exception as exc:
                self.call_from_thread(self.log_line, 'ERROR: ' + str(exc))
                self.call_from_thread(self.set_busy, False, 'Operación detenida · Revisa el registro')

        def saved(self, backup):
            self.step = 5
            self.query_one('#finished', Static).update(f'Web: http://127.0.0.1:{self.plan.port}\nCopia: {backup}\n\n'
                                                       'Windows: .\\Configurar.ps1 -Start\nLinux: bash configurar.sh --start')
            self.log_line('Configuración guardada. Documentos, índice y modelos conservados.')
            self.set_busy(False, 'Listo · Arranque pendiente')

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
                            raise ValueError('Revisa los cambios y marca la confirmación antes de guardar.')
                    self.set_busy(True, 'Instalando dependencias…' if identifier == 'install' else 'Guardando configuración…')
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
        print('Preparando interfaz de terminal en .cache/wizard-venv (sin tocar .venv)…', flush=True)
        subprocess.run([sys.executable, '-m', 'venv', str(ROOT / directory)], check=True)
    check = subprocess.run([str(python), '-c', 'import importlib.metadata; print(importlib.metadata.version("textual"))'],
                           capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != TEXTUAL_VERSION:
        subprocess.run([str(python), '-m', 'pip', 'install', '--disable-pip-version-check', '--only-binary=:all:',
                        '-r', str(ROOT / 'requirements/wizard.txt')], check=True,
                       env=dict(os.environ, PIP_CACHE_DIR=str(ROOT / '.cache/pip'), PYTHONUTF8='1'))
    return subprocess.call([str(python), str(Path(__file__).resolve())], cwd=ROOT)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--bootstrap', action='store_true', help='Prepare the isolated TUI environment and open the wizard.')
    group.add_argument('--check', action='store_true', help='Read-only local detection, without installing anything.')
    group.add_argument('--start', action='store_true', help='Start the configured local web server in the foreground.')
    args = parser.parse_args()
    if platform.system() not in ('Windows', 'Linux'):
        parser.error('Sistemas admitidos: Windows y Linux.')
    if sys.version_info[:2] != (3, 13):
        parser.error('Usa Python 3.13 x64. Linux puede necesitar el paquete python3.13-venv.')
    try:
        if args.check:
            print(json.dumps(probe(ROOT, load_plan(ROOT)), ensure_ascii=False, indent=2))
            return 0
        if args.start:
            return start_web(ROOT)
        if args.bootstrap:
            return bootstrap()
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            parser.error('Abre una terminal interactiva o usa --check para la detección de solo lectura.')
        try:
            app = make_app()
        except ImportError as exc:
            raise ValueError('Abre Configurar.ps1 o configurar.sh para instalar la interfaz aislada.') from exc
        return start_web(ROOT) if app.run() == 'start' else 0
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print('ERROR:', exc, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
