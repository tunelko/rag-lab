"""Persistent, opt-in graph processing, yielding the shared queue after each unit."""
import threading
import time

from pydantic import Field

from graph_jobs import ExtractRequest, extraction_configuration, extract, graph_store, prepare, selected_paths


class ContinuousRequest(ExtractRequest):
    max_total_units: int = Field(default=30, ge=1, le=1000)
    max_seconds: int = Field(default=600, ge=10, le=7200)
    pause_seconds: float = Field(default=2, ge=.1, le=60)
    max_rss_mb: int = Field(default=8192, ge=512, le=65536)


class GraphContinuation:
    def __init__(self, service):
        self.service = service
        self.lock = threading.RLock()
        self.timers = {}
        self.jobs = {}
        self.closed = False

    def active_tasks(self):
        store = graph_store(self.service)
        if not store.path.exists():
            return []
        return [record for record in store.tasks() if record['status'] in {'queued', 'running'}]

    def start(self, body):
        task = graph_store(self.service).task_create(body.model_dump())
        self.schedule(task, 0)
        return {'task_id': task, 'task': graph_store(self.service).tasks(task)}

    def schedule(self, task, delay):
        with self.lock:
            if self.closed or task in self.timers:
                return
            timer = threading.Timer(delay, self.enqueue, args=(task,))
            timer.daemon = True
            self.timers[task] = timer
            timer.start()

    def enqueue(self, task):
        service, store = self.service, graph_store(self.service)
        with self.lock:
            self.timers.pop(task, None)
            if self.closed or service.closed:
                return
            record = store.tasks(task)
            if record['status'] not in {'queued', 'running'}:
                return
            try:
                queued = service.start('graph', lambda job: self.step(task, job), graph_task=task,
                                       document_names=record['config']['files'])
                self.jobs[task] = queued['job_id']
            except Exception as error:
                store.task_update(task, status='paused_error', error=str(error))

    def step(self, task, job):
        from graph_engine import reconcile
        import psutil

        service, store = self.service, graph_store(self.service)
        record = store.tasks(task)
        if record['status'] not in {'queued', 'running'}:
            self.forget_job(task, job)
            return {'graph_task': task, 'task': record}
        body = ContinuousRequest.model_validate(record['config'])
        if record['processed'] >= body.max_total_units or record['elapsed'] >= body.max_seconds:
            store.task_update(task, status='paused_budget')
            self.forget_job(task, job)
            return {'graph_task': task, 'task': store.tasks(task)}
        try:
            over_memory = psutil.Process().memory_info().rss > body.max_rss_mb * 2 ** 20
        except (OSError, psutil.Error) as error:
            store.task_update(task, status='paused_resources', error='No se puede comprobar el límite de memoria: ' + str(error))
            self.forget_job(task, job)
            return {'graph_task': task, 'task': store.tasks(task)}
        if over_memory:
            store.task_update(task, status='paused_resources', error='Se ha alcanzado el límite de memoria del proceso.')
            self.forget_job(task, job)
            return {'graph_task': task, 'task': store.tasks(task)}
        started = time.monotonic()
        service.update(job, graph_deadline=started + body.max_seconds - record['elapsed'])
        store.task_update(task, status='running')
        try:
            # Reuse prepared units. A source modification requires a fresh process,
            # not silent continuation on a new version with the old consent.
            if not record['current_run']:
                prepare(service, job, selected_paths(service, body.files), progress_end=10)
            config = {**extraction_configuration(body), 'continuation_task': task}
            units = store.units(set(body.files))
            versions = {row['file']: row['version'] for row in units}
            for file, path in selected_paths(service, body.files):
                if versions.get(file) != service.fingerprints.digest(path):
                    raise ValueError('Una fuente cambió; inicia una preparación nueva antes de continuar.')
            cached = store.cached_units(config)
            pending = [row for row in units if row['id'] not in cached]
            pending.sort(key=lambda row: (-min(1500, len(row['text'])), row['ordinal'], row['file']))
            if not pending:
                result = reconcile(service, job, config, self.key(body.provider))
                if not result['pairs']:
                    store.task_update(task, status='done')
                return {'graph_task': task, 'reconciliation': result, 'task': store.tasks(task)}
            if body.provider == 'ollama':
                loaded = service.ollama_status().get('loaded', [])
                requested = body.model if ':' in body.model else body.model + ':latest'
                if any(row.get('name') != requested for row in loaded):
                    store.task_update(task, status='paused_resources',
                        error='Hay otro modelo local cargado. No se descarga ni se sustituye automáticamente; libera recursos o selecciona un proveedor remoto.')
                    return {'graph_task': task, 'task': store.tasks(task)}
            run = store.resumable_run(config, set(body.files), 1) or store.start_run(config, [pending[0]['id']])
            store.task_update(task, run=run)
            self.check_active(task, job)
            result = extract(service, job, run, self.key(body.provider))
            # Cancel/pause is possible again once the unit's atomic publication ends.
            service.update(job, stage='graph', cancellable=True)
            self.check_active(task, job)
            result['reconciliation'] = reconcile(service, job, config, self.key(body.provider))
            return {**result, 'graph_task': task, 'task': store.tasks(task)}
        except InterruptedError:
            if store.tasks(task)['status'] in {'queued', 'running'}:
                status = 'paused_budget' if time.monotonic() >= service.snapshot(job)['graph_deadline'] else 'paused'
                store.task_update(task, status=status)
            raise
        except Exception as error:
            store.task_update(task, status='paused_error', error=str(error))
            raise
        finally:
            store.task_update(task, elapsed=time.monotonic() - started)
            with self.lock:
                self.forget_job(task, job)
            latest = store.tasks(task)
            if latest['status'] in {'queued', 'running'}:
                if self.finished(task):
                    store.task_update(task, status='done')
                elif latest['processed'] >= body.max_total_units or latest['elapsed'] >= body.max_seconds:
                    store.task_update(task, status='paused_budget')
                else:
                    self.schedule(task, body.pause_seconds)

    def key(self, provider):
        if provider == 'ollama':
            return None
        key = self.service.credentials.get(provider)
        if not key:
            raise ValueError('Configura o desbloquea la credencial del proveedor para continuar.')
        return key  # Never persisted in tasks or job metadata.

    def check_active(self, task, job):
        self.service.check_cancel(job)
        if self.closed or graph_store(self.service).tasks(task)['status'] not in {'queued', 'running'}:
            raise InterruptedError('Procesamiento continuo pausado.')

    def forget_job(self, task, job):
        with self.lock:
            if self.jobs.get(task) == job:
                self.jobs.pop(task, None)

    def finished(self, task):
        from graph_engine import POLICY, current_rows, identity_candidates
        from graph_store import identifier
        store = graph_store(self.service)
        record = store.tasks(task)
        if not record['current_run']:
            return False
        body = ContinuousRequest.model_validate(record['config'])
        config = extraction_configuration(body)
        units = store.units(set(body.files))
        cached = store.cached_units(config)
        if not units or any(unit['id'] not in cached for unit in units):
            return False
        versions = {row['file']: row['version'] for row in units}
        try:
            if any(versions.get(file) != self.service.fingerprints.digest(path)
                   for file, path in selected_paths(self.service, body.files)):
                return False
        except (OSError, ValueError):
            return False
        _, pairs = identity_candidates(current_rows(self.service, set(body.files)), store.identity_decisions(), 1,
                                      identifier(POLICY, body.provider, body.model))
        return not pairs

    def cancelled_queued(self, job):
        with self.lock:
            task = next((task for task, current in self.jobs.items() if current == job), None)
            if task is not None:
                self.jobs.pop(task, None)
                store = graph_store(self.service)
                if store.tasks(task)['status'] in {'queued', 'running'}:
                    store.task_update(task, status='paused')

    def pause(self, task):
        store = graph_store(self.service)
        with self.lock:
            record = store.tasks(task)
            if record['status'] == 'done':
                return record
            job = self.jobs.get(task)
            if job:
                # Refuse cancellation while the atomic publication is running.
                self.service.cancel(job)
            store.task_update(task, status='paused')
            timer = self.timers.pop(task, None)
            if timer:
                timer.cancel()
            return store.tasks(task)

    def resume(self, task):
        store = graph_store(self.service)
        with self.lock:
            record = store.tasks(task)
            if record['status'] in {'queued', 'running', 'done'} or task in self.jobs:
                raise ValueError('El procesamiento ya está activo, completado o terminando su unidad actual.')
            if any(row['id'] != task and row['status'] in {'queued', 'running'} for row in store.tasks()):
                raise ValueError('Ya hay otro procesamiento continuo activo en esta colección.')
            if self.finished(task):
                store.task_update(task, status='done', error='')
                return store.tasks(task)
            if record['processed'] >= record['config']['max_total_units'] or record['elapsed'] >= record['config']['max_seconds']:
                raise ValueError('Se agotó el presupuesto autorizado. Inicia otra selección con límites explícitos.')
            self.key(record['config']['provider'])
            store.task_update(task, status='queued', error='')
            self.schedule(task, 0)
            return store.tasks(task)

    def close(self):
        with self.lock:
            self.closed = True
            for timer in self.timers.values():
                timer.cancel()
            self.timers.clear()
            store = graph_store(self.service)
            if not store.path.exists():
                return
            for record in store.tasks():
                if record['status'] in {'queued', 'running'}:
                    store.task_update(record['id'], status='interrupted')
