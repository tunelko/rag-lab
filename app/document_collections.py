"""Isolated libraries served by one application, with one shared execution queue."""
from dataclasses import replace
import json
from pathlib import Path
import re
from threading import RLock
from urllib.parse import parse_qs

from starlette.responses import JSONResponse

KEY = re.compile(r'[a-z][a-z0-9-]{0,47}')


class CollectionPool:
    def __init__(self, service, factory):
        self.default, self.factory = service, factory
        service.collection_pool = self
        self.root = service.config.db.parent / 'collections'
        self.registry = self.root / 'registry.json'
        self.lock = RLock()
        self.services = {'default': service}
        self.apps = {}

    def records(self):
        return json.loads(self.registry.read_text('utf-8')) if self.registry.is_file() else []

    def snapshot(self, selected='default'):
        with self.lock:
            return {'selected': selected, 'collections': [
                {'id': 'default', 'name': 'Biblioteca', 'adapter': None}, *self.records()]}

    def create(self, key, name, adapter=None):
        if not KEY.fullmatch(key) or key == 'default' or adapter not in (None, 'cti'):
            raise ValueError('Colección no válida.')
        name = name.strip()
        if not name or len(name) > 100:
            raise ValueError('El nombre necesita entre 1 y 100 caracteres.')
        with self.lock:
            rows = self.records()
            existing = next((row for row in rows if row['id'] == key), None)
            if existing:
                if existing['adapter'] != adapter:
                    raise ValueError('La colección existente usa otro adaptador.')
                return existing
            if len(rows) >= 32:
                raise ValueError('Se ha alcanzado el límite de 32 colecciones.')
            record = {'id': key, 'name': name, 'adapter': adapter}
            self.root.mkdir(parents=True, exist_ok=True)
            pending = self.registry.with_suffix('.pending')
            pending.write_text(json.dumps([*rows, record], ensure_ascii=False, indent=2), 'utf-8')
            pending.replace(self.registry)
            return record

    def get(self, key):
        with self.lock:
            if key in self.services:
                return self.services[key]
            record = next((row for row in self.records() if row['id'] == key), None)
            if record is None:
                raise ValueError('La colección no existe.')
            from credential_store import CredentialStore
            from settings_store import SettingsStore
            root = self.root / key
            service = type(self.default)(replace(self.default.config, db=root / 'chroma', collection=key),
                                         root / 'documents')
            service.executor.shutdown(wait=True)
            service.executor = self.default.executor
            service.owns_executor = False
            service.hardware = self.default.hardware
            service.owns_hardware = False
            service.console = self.default.console
            service.settings = SettingsStore(self.default.settings.path)
            service.credentials.close()
            service.credentials = CredentialStore(self.default.credentials.path)
            service.collection_key, service.dataset_adapter = key, record['adapter']
            service.collection_pool = self

            def embeddings(download=False):
                service.embeddings = self.default.get_embeddings(download=download)
                return service.embeddings

            service.get_embeddings = embeddings
            service.statistics_archive.mark_interrupted()
            service.laboratory.recover()
            self.services[key] = service
            return service

    def app(self, key):
        with self.lock:
            if key not in self.apps:
                self.apps[key] = self.factory(self.get(key), self)
            return self.apps[key]

    def active_jobs(self):
        with self.lock:
            services = list(self.services.items())
        result = [{**job, 'collection': key} for key, service in services
                  for job in service.list_jobs(active=True)['jobs']]
        active_tasks = {(job['collection'], job.get('graph_task')) for job in result if job.get('graph_task')}
        for key, service in services:
            continuation = getattr(service, 'graph_continuation', None)
            for task in continuation.active_tasks() if continuation is not None else ():
                if (key, task['id']) not in active_tasks:
                    result.append({'id': 'graph-task-' + task['id'], 'graph_task': task['id'], 'collection': key,
                        'kind': 'graph', 'status': 'queued', 'phase': 'Esperando el siguiente pasaje del procesamiento continuo',
                        'persistent': True, 'progress': None})
        return result

    def close(self):
        with self.lock:
            children = [service for key, service in self.services.items() if key != 'default']
        for service in children:
            with service.lock:
                service.closed = True
                for cancel in service.cancels.values():
                    cancel.set()
        if children:
            self.default.executor.shutdown(wait=True, cancel_futures=True)
        for service in children:
            service.close()


class CollectionDispatch:
    def __init__(self, app, pool):
        self.app, self.pool = app, pool

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http' or not scope['path'].startswith('/api/'):
            return await self.app(scope, receive, send)
        headers = dict(scope['headers'])
        query = parse_qs(scope.get('query_string', b'').decode('ascii', 'replace'))
        key = headers.get(b'x-rag-collection', b'').decode('ascii', 'replace') or query.get('collection', ['default'])[0]
        if key == 'default':
            return await self.app(scope, receive, send)
        if not KEY.fullmatch(key):
            return await JSONResponse({'detail': 'Colección no válida.'}, 400)(scope, receive, send)
        try:
            app = self.pool.app(key)
        except ValueError as error:
            return await JSONResponse({'detail': str(error)}, 404)(scope, receive, send)
        return await app(scope, receive, send)
