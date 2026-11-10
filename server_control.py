"""Project-scoped, cross-platform start/stop/restart/status for the local server.

Managed servers accept a local stop file so Uvicorn can shut down cleanly on
Windows as well as Linux. Legacy foreground Uvicorn processes are recognized
only by this project's exact directory, app and port. Ollama is never managed.
"""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request

import psutil


ROOT = Path(__file__).resolve().parent
CONTROLLER = Path(__file__).resolve()


def default_port(root):
    values = {}
    path = root / '.env'
    if path.is_file():
        for line in path.read_text(encoding='utf-8-sig').splitlines():
            key, separator, value = line.partition('=')
            if separator:
                values[key.strip()] = value.strip().strip('\"\'')
    return int(os.environ.get('RAG_WEB_PORT', values.get('RAG_WEB_PORT', '8765')))


def paths(root, port):
    directory = root / '.cache' / 'servers'
    return directory / f'{port}.json', directory / f'{port}.stop'


def argument(command, name):
    try:
        return command[command.index(name) + 1]
    except (ValueError, IndexError):
        return None


def process_path(process, value):
    path = Path(value)
    return (path if path.is_absolute() else Path(process.cwd()) / path).resolve()


def matching_processes(root, port):
    """Ignore reused PIDs, unrelated Python apps and servers from other checkouts."""
    matched = []
    parents = set()
    for process in psutil.process_iter(['pid', 'name']):
        try:
            if 'python' not in (process.info['name'] or '').lower():
                continue
            command = process.cmdline()
            if argument(command, '--port') != str(port):
                continue
            managed = (len(command) > 2 and process_path(process, command[1]) == CONTROLLER
                       and command[2] == '_run' and argument(command, '--root')
                       and process_path(process, argument(command, '--root')) == root)
            legacy = ('web_app:app' in command and argument(command, '--app-dir')
                      and process_path(process, argument(command, '--app-dir')) == root)
            if managed or legacy:
                matched.append((process, bool(managed)))
                parents.add(process.ppid())
        except (psutil.Error, OSError, ValueError):
            continue
    # On Windows a venv launcher and its child share the same command. Manage the
    # actual worker; the launcher exits with it and must not be killed first.
    return [(process, managed) for process, managed in matched if process.pid not in parents]


def active_jobs(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f'http://127.0.0.1:{port}/api/jobs?active=1', timeout=3) as response:
            payload = json.load(response)
        if not isinstance(payload.get('jobs'), list):
            raise RuntimeError('No se puede verificar la cola del servidor; no se detendra.')
        return payload['jobs']
    except urllib.error.URLError as error:
        if isinstance(error.reason, (ConnectionRefusedError, ConnectionResetError)):
            return []
        raise RuntimeError(f'No se puede comprobar la cola: {error}') from error


def port_in_use(port):
    with socket.socket() as connection:
        connection.settimeout(.25)
        return connection.connect_ex(('127.0.0.1', port)) == 0


def atomic_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix(path.suffix + f'.{os.getpid()}.pending')
    pending.write_text(json.dumps(value), encoding='utf-8')
    pending.replace(path)


def alive(process):
    try:
        return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


def stop(root, port, timeout=30):
    processes = matching_processes(root, port)
    if not processes:
        print(f'Servidor detenido (puerto {port}).')
        return
    jobs = active_jobs(port)
    if jobs:
        raise RuntimeError(f'Hay {len(jobs)} tareas activas. Cancela o espera su finalizacion antes de stop/restart.')
    # Capture old in-memory metrics once when upgrading a server without durable history.
    # Fixture/other app roots are not migrated into this project's private database.
    if root==ROOT:
        from statistics_store import StatisticsStore
        from tools.history_bridge import capture
        archive=StatisticsStore(root/'data/statistics.sqlite3')
        try:
            capture(f'http://127.0.0.1:{port}',archive,seed=True)
        finally:
            archive.close()
        if active_jobs(port):
            raise RuntimeError('Hay tareas activas nuevas; no se ha detenido el servidor.')
    state_path, stop_path = paths(root, port)
    for process, managed in processes:
        if managed:
            atomic_json(stop_path, {'pid': process.pid, 'created': process.create_time()})
        else:
            # An older, foreground-only launcher has no cooperative stop channel.
            # It has already been identified by exact project/app/port and idle queue.
            try:
                process.terminate()
            except psutil.NoSuchProcess:
                pass
    deadline = time.monotonic() + timeout
    while any(alive(process) for process, _ in processes):
        if time.monotonic() >= deadline:
            raise RuntimeError('El servidor no ha terminado a tiempo; no se ha forzado su cierre ni arrancado otro.')
        time.sleep(.1)
    # A stale request/state must never be inherited by the next start.
    stop_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    print(f'Servidor detenido (puerto {port}).')


def wait_for_idle(root, port, interval=2):
    original={(process.pid,process.create_time()) for process,_ in matching_processes(root,port)}
    last=None
    while original:
        current={(process.pid,process.create_time()) for process,_ in matching_processes(root,port)}
        if current!=original:
            print('El servidor original ha cambiado; se cancela el reinicio diferido.',flush=True)
            return False
        jobs=active_jobs(port)
        if not jobs: return True
        state=tuple((job['id'],job.get('status')) for job in jobs)
        if state!=last:
            print(f'Esperando a que terminen {len(jobs)} tareas; el indexado continua.',flush=True)
            last=state
        time.sleep(interval)
    return True


def python_executable(root):
    python = root / '.venv' / ('Scripts/python.exe' if os.name == 'nt' else 'bin/python')
    if not python.is_file():
        raise RuntimeError('Instala primero el entorno con Configurar.ps1 o bash configurar.sh.')
    return python


def start(root, port, timeout=30):
    processes = matching_processes(root, port)
    if processes:
        print(f'El servidor ya esta iniciado (PID {processes[0][0].pid}): http://127.0.0.1:{port}')
        return
    if port_in_use(port):
        raise RuntimeError(f'El puerto {port} esta ocupado por otro proceso; no se ha detenido ni sustituido.')
    python = python_executable(root)
    state_path, stop_path = paths(root, port)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    stop_path.unlink(missing_ok=True)
    state_path.unlink(missing_ok=True)
    reports = root / 'reports'
    reports.mkdir(exist_ok=True)
    output = reports / 'rag-server-runtime.log'
    errors = reports / 'rag-server-runtime-errors.log'
    environment = dict(os.environ, PYTHONUTF8='1')
    environment.setdefault('OMP_NUM_THREADS', '4')
    kwargs = ({'creationflags': subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP}
              if os.name == 'nt' else {'start_new_session': True})
    with output.open('ab') as stdout, errors.open('ab') as stderr:
        subprocess.Popen([str(python), str(CONTROLLER), '_run', '--root', str(root), '--port', str(port)],
                         cwd=root, env=environment, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **kwargs)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        processes = matching_processes(root, port)
        if processes and port_in_use(port):
            print(f'Servidor iniciado (PID {processes[0][0].pid}): http://127.0.0.1:{port}')
            print(f'Registros: {output}\nErrores:   {errors}')
            return
        time.sleep(.2)
    raise RuntimeError(f'No se ha confirmado el arranque. Consulta {errors} y ejecuta status antes de reintentar.')


def run_server(root, port):
    import uvicorn
    sys.path.insert(0, str(root))
    state_path, stop_path = paths(root, port)
    identity = {'pid': os.getpid(), 'created': psutil.Process().create_time()}
    atomic_json(state_path, identity)
    server = uvicorn.Server(uvicorn.Config('web_app:app', host='127.0.0.1', port=port, access_log=False))
    finished = threading.Event()

    def watch_stop():
        while not finished.wait(.2):
            try:
                request = json.loads(stop_path.read_text(encoding='utf-8'))
                if request == identity:
                    server.should_exit = True
                    return
            except (OSError, ValueError):
                pass

    watcher = threading.Thread(target=watch_stop, daemon=True)
    watcher.start()
    try:
        server.run()
    finally:
        finished.set()
        watcher.join(timeout=1)
        if state_path.is_file() and json.loads(state_path.read_text(encoding='utf-8')) == identity:
            state_path.unlink(missing_ok=True)
        stop_path.unlink(missing_ok=True)


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ('--start', '--stop', '--restart', '--status'):
        argv[0] = argv[0][2:]
    parser = argparse.ArgumentParser(description='Control del servidor RAG local; no modifica Ollama.')
    parser.add_argument('action', nargs='?', default='start', choices=('start', 'stop', 'restart', 'status', '_run'))
    parser.add_argument('--port', type=int, help='Puerto; por defecto RAG_WEB_PORT en .env o 8765.')
    parser.add_argument('--when-idle',action='store_true',help='Esperar a que termine la cola antes de stop/restart.')
    parser.add_argument('--root', type=Path, default=ROOT, help=argparse.SUPPRESS)
    arguments = parser.parse_args(argv)
    root = arguments.root.resolve()
    try:
        port = arguments.port if arguments.port is not None else default_port(root)
        if not 1024 <= port <= 65535:
            raise ValueError('El puerto debe estar entre 1024 y 65535.')
        if arguments.when_idle and arguments.action not in ('stop','restart'):
            raise ValueError('--when-idle solo se usa con stop/restart.')
        if arguments.when_idle and not wait_for_idle(root,port):
            return 0
        if arguments.action == '_run':
            run_server(root, port)
        elif arguments.action == 'status':
            processes = matching_processes(root, port)
            if processes:
                print(f'Servidor iniciado (PID {processes[0][0].pid}): http://127.0.0.1:{port}')
            elif port_in_use(port):
                print(f'Servidor RAG detenido; el puerto {port} esta ocupado por otro proceso.')
            else:
                print(f'Servidor detenido (puerto {port}).')
        else:
            if arguments.action in ('stop', 'restart'):
                stop(root, port)
            if arguments.action in ('start', 'restart'):
                start(root, port)
        return 0
    except (OSError, ValueError, RuntimeError, psutil.Error) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
