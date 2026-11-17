"""Graph preparation/extraction are platform jobs and never reindex vectors."""
import json
import math
import time
from typing import Annotated

from pydantic import BaseModel, Field, ValidationError

from file_fingerprints import sha256_file
from graph_store import Extraction, GraphStore, VerificationRequired, identifier, literal_span
from jobs_runtime import JobFailure
import generation_providers
import graph_extraction
import rag

POLICY = 'graph-text-v1'


def graph_store(service):
    return GraphStore(service.config.db.parent / 'graph.sqlite3')


class PrepareRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    files: list[Annotated[str, Field(min_length=1, max_length=1024)]] = Field(min_length=1, max_length=100)


class ExtractRequest(PrepareRequest):
    provider: str = Field(pattern='^(ollama|groq|scaleway|ovh|xai)$')
    model: str = Field(min_length=1, max_length=300)
    max_units: int = Field(default=3, ge=1, le=100)
    remote_content: bool = False


class ReviewRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    state: str = Field(pattern='^(proposed|accepted|rejected)$')


class AuditRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    all_collections: bool = False


class VerificationRequest(BaseModel):
    model_config = {'extra': 'forbid'}
    status: str = Field(pattern='^(supported|unsupported|uncertain)$')
    note: str = Field(min_length=20, max_length=2000)


def selected_paths(service, files):
    result = []
    for file in dict.fromkeys(files):
        path = (service.documents / file).resolve()
        if not path.is_relative_to(service.documents) or not path.is_file() or path.suffix.lower() not in {'.pdf', '.md', '.txt', '.markdown'}:
            raise ValueError('Selecciona documentos PDF, TXT o Markdown de esta colección.')
        result.append((path.relative_to(service.documents).as_posix(), path))
    return result


def prepare(service, job, paths, *, progress_end=95):
    units = []
    for number, (file, path) in enumerate(paths):
        service.check_cancel(job)
        version = sha256_file(path)
        if path.suffix.lower() == '.pdf':
            import pymupdf
            with pymupdf.open(path) as document:
                if len(document) > 2000:
                    raise ValueError('El piloto admite hasta 2.000 páginas por documento.')
                pages = [(index + 1, page.get_text()) for index, page in enumerate(document)]
        else:
            pages = [(None, path.read_text('utf-8-sig'))]
        ordinal = 0
        for page, text in pages:
            # Literal, bounded passages: offsets and quotes remain reproducible.
            for offset in range(0, len(text), 3000):
                content = text[offset:offset + 3000]
                if not content.strip():
                    continue
                ordinal += 1
                units.append({'id': identifier(file, version, POLICY, page, offset), 'file': file, 'version': version,
                    'page': page, 'ordinal': ordinal, 'text': content, 'kind': 'text',
                    'metadata': {'parser': POLICY, 'page_offset': offset}})
                if len(units) > 20000:
                    raise ValueError('Divide la preparación en selecciones de hasta 20.000 unidades.')
        if sha256_file(path) != version:
            raise ValueError('El documento cambió durante la preparación.')
        service.update(job, progress=progress_end * (number + 1) / len(paths),
            phase=f'Preparando evidencias del grafo · {number + 1}/{len(paths)}', current_document=file,
            graph_documents=number + 1, graph_units=len(units))
    if not units:
        raise ValueError('No hay texto extraíble. Este primer piloto no ejecuta OCR ni visión adicional.')
    with service.lock:
        service.check_cancel(job)
        service.update(job, phase='Publicando evidencias del grafo', stage='indexing', cancellable=False)
    graph_store(service).publish_units(units, [file for file, _ in paths])
    return {'graph': graph_store(service).snapshot(), 'message': 'Evidencias preparadas; modelización independiente del índice vectorial.'}


def generate_passage(service, job, messages, config, key, arguments):
    """Bounded rate-limit recovery, with visible progress and cancellation."""
    if config['provider'] == 'ollama':
        return rag.generate_ollama(messages, config['model'], **arguments)
    for attempt in range(3):
        service.check_cancel(job)
        try:
            return generation_providers.generate(messages, config['provider'], config['model'], key, **arguments)
        except generation_providers.ProviderError as error:
            if error.http_status != 429 or attempt == 2:
                raise
            headers = getattr(error.__cause__, 'headers', None)
            value = headers.get('Retry-After', '') if headers else ''
            try:
                delay = float(value) if value else 60
            except ValueError:
                delay = 60
            if not math.isfinite(delay) or delay < 0 or delay > 120:
                raise
            deadline = time.monotonic() + delay
            last = None
            while time.monotonic() < deadline:
                service.check_cancel(job)
                remaining = math.ceil(deadline - time.monotonic())
                if remaining != last:
                    service.update(job, phase=f'Esperando límite temporal del proveedor · {remaining} s', cancellable=True)
                    last = remaining
                time.sleep(min(.25, max(0, deadline - time.monotonic())))


def validated_passage(service, job, unit, config, key):
    evidence = graph_extraction.passages(unit['text'])
    schema = graph_extraction.schema_for(evidence)
    prompt = graph_extraction.PROMPT + '\nSchema: ' + json.dumps(schema)
    messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content': json.dumps(evidence, ensure_ascii=False)}]
    arguments = {'max_output_tokens': 768, 'response_format': schema,
                 'cancelled': lambda: service.cancelled(job)}
    for attempt in range(2):
        try:
            answer, metrics = generate_passage(service, job, messages, config, key, arguments)
        except generation_providers.ProviderError as error:
            if not attempt and error.http_status == 400 and error.error_code == 'json_validate_failed':
                arguments['response_format'] = None
                service.update(job, phase='Reparando el formato de la extracción')
                continue
            raise JobFailure('GRAPH_PROVIDER', 'El proveedor no pudo completar la extracción. Puedes reanudar el trabajo.', str(error)) from error
        text = answer.strip()
        if text.startswith('```json\n') and text.endswith('\n```'):
            text = text[8:-4]
        try:
            result = graph_extraction.materialize(text, unit['text'], evidence)
            return result, {**metrics, 'validation_attempts': attempt + 1,
                            'validation_policy': graph_extraction.POLICY, 'prompt_hash': identifier(prompt)}
        except ValueError as error:
            if isinstance(error, ValidationError):
                detail = '; '.join(str(row['msg']) for row in error.errors(include_url=False, include_input=False)[:3])[:1000]
            else:
                detail = str(error)[:1000]
            if attempt:
                raise JobFailure('GRAPH_EXTRACTION_INVALID',
                    'El modelo no ha podido corregir las relaciones de este pasaje. Lo validado se conserva; puedes reanudar.', detail) from None
            service.update(job, phase='Reparando referencias o citas de la extracción', cancellable=True)
            messages.extend([{'role': 'assistant', 'content': answer}, {'role': 'user', 'content':
                'Repair the JSON you just returned. Validation failed: ' + detail +
                '\nReturn only an object with claims, matching the schema. Each claim must have subject, subject_kind, '
                'predicate, object, object_kind, evidence_id and basis. Use only the evidence_id values supplied above. '
                'Do not return entities, entity keys or quotes. Do not invent missing evidence. Empty claims is valid.'}])


def assess_passage(service, job, unit, extraction, config, key):
    if not extraction.assertions:
        return [], {'claims': 0, 'automatic': True}
    evidence = graph_extraction.passages(unit['text'])
    schema = graph_extraction.ReviewPlan.model_json_schema()
    schema['$defs']['Assessment']['properties']['evidence_id']['enum'] = list(evidence)
    schema['$defs']['Assessment']['properties']['index']['enum'] = list(range(len(extraction.assertions)))
    entities = {row.key: row for row in extraction.entities}
    claims = [{'index': index, 'subject': entities[row.subject].label, 'predicate': row.predicate,
        'object': entities[row.object].label, 'basis': row.basis, 'polarity': row.polarity,
        'qualifiers': row.qualifiers, 'original_evidence': row.quote} for index, row in enumerate(extraction.assertions)]
    prompt = graph_extraction.REVIEW_PROMPT + '\nSchema: ' + json.dumps(schema)
    messages = [{'role': 'system', 'content': prompt}, {'role': 'user', 'content':
        json.dumps({'passages': evidence, 'claims': claims}, ensure_ascii=False)}]
    arguments = {'max_output_tokens': 768, 'response_format': schema, 'cancelled': lambda: service.cancelled(job)}
    service.update(job, phase='Contrastando automáticamente las relaciones con sus fuentes', cancellable=True)
    for attempt in range(2):
        try:
            answer, metrics = generate_passage(service, job, messages, config, key, arguments)
        except generation_providers.ProviderError as error:
            if not attempt and error.http_status == 400 and error.error_code == 'json_validate_failed':
                arguments['response_format'] = None
                continue
            raise JobFailure('GRAPH_ASSESSMENT_PROVIDER', 'El contraste automático no pudo completarse. Las propuestas no se aceptan sin comprobar.', str(error)) from error
        text = answer.strip()
        if text.startswith('```json\n') and text.endswith('\n```'):
            text = text[8:-4]
        try:
            result = graph_extraction.ReviewPlan.model_validate_json(text)
            checked = GraphStore._assessments(result.assessments, len(extraction.assertions))
            for index, assessment in checked.items():
                if assessment.evidence_id not in evidence:
                    raise ValueError('El contraste debe elegir una evidencia existente.')
                if assessment.status == 'supported':
                    segment = evidence[assessment.evidence_id]
                    claim = extraction.assertions[index]
                    claim.quote, claim.source_start, claim.source_end = segment['text'], segment['start'], segment['end']
            return [checked[index] for index in range(len(checked))], {**metrics, 'automatic': True,
                'assessment_policy': 'graph-source-review-v1', 'prompt_hash': identifier(prompt),
                'assessments': [row.model_dump() for row in result.assessments]}
        except ValueError:
            if attempt:
                raise JobFailure('GRAPH_ASSESSMENT_INVALID', 'El modelo no completó un contraste válido de todas las relaciones. No se ha aceptado ninguna propuesta sin comprobar.') from None
            messages.extend([{'role': 'assistant', 'content': answer}, {'role': 'user', 'content':
                'Return a complete assessments array matching the schema. Cover each supplied claim index exactly once, '
                'use an existing evidence_id, a supported/unsupported/uncertain status, and a reason of at least 20 characters. '
                'Do not omit any claim or invent source identifiers.'}])


def extract(service, job, run, key, *, progress_start=0):
    store = graph_store(service)
    record = store.run(run)
    config = record['config']
    pending = {batch['unit'] for batch in record['batches'] if batch['status'] != 'done'}
    total = len(record['batches'])
    by_id = {unit['id']: unit for unit in store.units()}
    store.state(run, 'running')
    try:
        # Legacy checkpoints are reviewed by the model too; users do not inherit
        # a manual acceptance task when resuming an older extraction.
        for batch in record['batches']:
            if batch['status'] != 'done':
                continue
            legacy = store.batch_for_assessment(run, batch['unit'])
            if legacy is not None:
                unit = by_id.get(batch['unit'])
                if unit is None or sha256_file(selected_paths(service, [unit['file']])[0][1]) != unit['version']:
                    raise ValueError('La fuente del lote confirmado cambió; prepara su nueva versión.')
                assessments, metrics = assess_passage(service, job, unit, legacy, config, key)
                if sha256_file(selected_paths(service, [unit['file']])[0][1]) != unit['version']:
                    raise ValueError('La fuente cambió durante el contraste automático del lote confirmado.')
                store.assess_existing_batch(run, batch['unit'], assessments, metrics)
        for unit_id in sorted(pending):
            service.check_cancel(job)
            unit = by_id.get(unit_id)
            if unit is None:
                raise ValueError('Una fuente ha cambiado; prepara un nuevo trabajo.')
            path = selected_paths(service, [unit['file']])[0][1]
            if sha256_file(path) != unit['version']:
                raise ValueError('El documento cambió; prepara sus evidencias antes de modelizar.')
            result, metrics = validated_passage(service, job, unit, config, key)
            assessments, assessment_metrics = assess_passage(service, job, unit, result, config, key)
            metrics['automatic_assessment'] = assessment_metrics
            if sha256_file(path) != unit['version']:
                raise ValueError('El documento cambió durante la modelización; prepara sus evidencias de nuevo.')
            store.publish_extraction(run, unit_id, result, metrics, assessments=assessments)
            pending.remove(unit_id)
            service.update(job, progress=progress_start + (100 - progress_start) * (total - len(pending)) / total,
                phase=f'Modelizando relaciones · {total - len(pending)}/{total}',
                current_document=unit['file'], graph_run=run, graph_units_done=total - len(pending), graph_units_total=total)
        for batch in record['batches']:
            unit = by_id.get(batch['unit'])
            if unit is None or sha256_file(selected_paths(service, [unit['file']])[0][1]) != unit['version']:
                raise ValueError('La fuente cambió antes de publicar el grafo; conserva su versión y vuelve a preparar.')
        with service.lock:
            service.check_cancel(job)
            service.update(job, phase='Publicando relaciones contrastadas automáticamente', stage='indexing', cancellable=False)
        store.complete(run)
        return {'graph_run': run, 'graph': store.snapshot()}
    except BaseException as error:
        store.state(run, 'cancelled' if isinstance(error, InterruptedError) else 'error', str(error))
        raise


def extraction_configuration(body):
    return {**body.model_dump(), 'policy': POLICY, 'schema': 1, 'automatic_assessment': True,
        'extraction_policy': graph_extraction.POLICY,
        'signature': identifier(body.provider, body.model, POLICY, graph_extraction.POLICY, 'source-review-v1', schema_for_signature())}


def process_graph(service, job, body, key):
    paths = selected_paths(service, body.files)
    prepare(service, job, paths, progress_end=30)
    service.update(job, stage='graph', progress=30, cancellable=True, phase='Seleccionando evidencias pendientes')
    service.check_cancel(job)
    store = graph_store(service)
    config = extraction_configuration(body)
    cached = store.cached_units(config)
    units = [unit for unit in store.units(set(body.files)) if unit['id'] not in cached]
    units.sort(key=lambda unit: (-min(1500, len(unit['text'])), unit['ordinal'], unit['file']))
    if not units:
        return {'graph': store.snapshot(), 'message': 'La selección ya está modelizada y contrastada con esta configuración.'}
    run = store.start_run(config, [unit['id'] for unit in units[:body.max_units]])
    service.update(job, graph_run=run)
    return extract(service, job, run, key, progress_start=30)


def install_routes(app, service, require_local):
    from fastapi import HTTPException, Request, Query
    from fastapi.responses import Response

    graph_store(service).recover()

    @app.get('/api/graph')
    def snapshot(limit: int = Query(default=100, ge=1, le=500)):
        return graph_store(service).snapshot(limit)

    @app.get('/api/graph/evidence/{unit}')
    def evidence(unit: str):
        try:
            return graph_store(service).evidence(unit)
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.post('/api/graph/assertions/{assertion}/review')
    def review(assertion: str, body: ReviewRequest, request: Request):
        require_local(request)
        try:
            if body.state == 'accepted':
                store = graph_store(service)
                with store.connection() as db:
                    row = db.execute('SELECT unit FROM graph_assertions WHERE id=?', (assertion,)).fetchone()
                if row is None:
                    raise ValueError('Relación no disponible.')
                unit = store.evidence(row['unit'])
                path = selected_paths(service, [unit['file']])[0][1]
                if sha256_file(path) != unit['version']:
                    raise VerificationRequired('El documento cambió; vuelve a preparar y contrastar la relación.')
            graph_store(service).review(assertion, body.state)
            return {'ok': True}
        except VerificationRequired as error:
            raise HTTPException(422, str(error)) from error
        except ValueError as error:
            raise HTTPException(404, str(error)) from error

    @app.post('/api/graph/assertions/{assertion}/verification')
    def verify(assertion: str, body: VerificationRequest, request: Request):
        require_local(request)
        store = graph_store(service)
        try:
            with store.connection() as db:
                row = db.execute('SELECT unit FROM graph_assertions WHERE id=?', (assertion,)).fetchone()
            if row is None:
                raise ValueError('Relación no disponible.')
            unit = store.evidence(row['unit'])
            path = selected_paths(service, [unit['file']])[0][1]
            if sha256_file(path) != unit['version']:
                raise ValueError('El documento cambió. Prepara y modeliza su nueva versión antes de contrastar.')
            store.verify(assertion, body.status, body.note)
            return {'ok': True}
        except ValueError as error:
            raise HTTPException(422, str(error)) from error

    def guard():
        if service.closed:
            raise HTTPException(503, 'El servidor se está cerrando.')
        if service.list_jobs(active=True)['jobs']:
            raise HTTPException(409, 'Espera a que termine la tarea activa de esta colección.')

    @app.post('/api/graph/audit', status_code=202)
    def start_audit(body: AuditRequest, request: Request):
        require_local(request)
        from document_audit import audit
        with service.lock:
            guard()
            pool = service.collection_pool
            services = [pool.default, *(pool.get(row['id']) for row in pool.records())] if body.all_collections else [service]
            return service.start('audit', lambda job: audit(service, job, services))

    def audit_path():
        return service.collection_pool.default.config.db.parent / 'audits' / 'latest.json'

    @app.get('/api/graph/audit')
    def latest_audit():
        path = audit_path()
        if not path.is_file():
            return {'audit': None}
        report = json.loads(path.read_text('utf-8'))
        report.pop('files', None)
        return {'audit': report}

    @app.get('/api/graph/audit/report')
    def audit_report():
        from fastapi.responses import FileResponse
        path = audit_path()
        if not path.is_file():
            raise HTTPException(404, 'Todavía no hay una verificación documental.')
        return FileResponse(path, media_type='application/json', filename='document-audit.json')

    @app.get('/api/graph/source')
    def source(unit: str):
        row = evidence(unit)
        return Response(row['text'], media_type='text/plain; charset=utf-8')

    @app.get('/api/graph/preview')
    def preview(unit: str):
        row = evidence(unit)
        return {'kind': 'text', 'file': row['file'], 'text': row['text'], 'truncated': False}

    @app.post('/api/graph/prepare', status_code=202)
    def prepare_graph(body: PrepareRequest, request: Request):
        require_local(request)
        with service.lock:
            guard()
            try:
                paths = selected_paths(service, body.files)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            return service.start('graph', lambda job: prepare(service, job, paths), document_names=body.files)

    def credentials(provider):
        if provider == 'ollama':
            return None
        try:
            key = service.credentials.get(provider)
        except Exception as error:
            raise HTTPException(409, 'Desbloquea la credencial del proveedor en Configuración.') from error
        if not key:
            raise HTTPException(409, 'Configura la credencial del proveedor antes de modelizar.')
        return key

    def submit(run):
        record = graph_store(service).run(run)
        if record['status'] in {'queued', 'running', 'done'}:
            raise HTTPException(409, 'El trabajo ya está activo o completado.')
        key = credentials(record['config']['provider'])
        graph_store(service).state(run, 'queued')
        queued = service.start('graph', lambda job: extract(service, job, run, key), graph_run=run)
        return {**queued, 'graph_run': run}

    @app.post('/api/graph/process', status_code=202)
    def automatic_graph(body: ExtractRequest, request: Request):
        require_local(request)
        with service.lock:
            guard()
            if body.provider != 'ollama' and not body.remote_content:
                raise HTTPException(422, 'Selecciona expresamente el proveedor remoto en las opciones del grafo.')
            try:
                selected_paths(service, body.files)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            key = credentials(body.provider)
            return service.start('graph', lambda job: process_graph(service, job, body, key), document_names=body.files)

    @app.post('/api/graph/extract', status_code=202)
    def extract_graph(body: ExtractRequest, request: Request):
        require_local(request)
        with service.lock:
            guard()
            if body.provider != 'ollama' and not body.remote_content:
                raise HTTPException(422, 'La extracción remota debe seleccionarse explícitamente.')
            try:
                selected_paths(service, body.files)
            except ValueError as error:
                raise HTTPException(422, str(error)) from error
            key = credentials(body.provider)
            store = graph_store(service)
            units = store.units(set(body.files))
            # Prefer substantive passages to short cover pages, deterministically.
            units.sort(key=lambda unit: (-min(1500, len(unit['text'])), unit['ordinal'], unit['file']))
            if not units:
                raise HTTPException(409, 'Prepara primero las evidencias de esos documentos.')
            config = extraction_configuration(body)
            cached = store.cached_units(config)
            units = [unit for unit in units if unit['id'] not in cached]
            if not units:
                raise HTTPException(409, 'Las unidades ya están modelizadas con este modelo y versión.')
            run = store.start_run(config, [unit['id'] for unit in units[:body.max_units]])
            queued = service.start('graph', lambda job: extract(service, job, run, key), graph_run=run)
            return {**queued, 'graph_run': run}

    @app.post('/api/graph/runs/{run}/resume', status_code=202)
    def resume(run: str, request: Request):
        require_local(request)
        with service.lock:
            guard()
            try:
                return submit(run)
            except ValueError as error:
                raise HTTPException(404, str(error)) from error


def schema_for_signature():
    return graph_extraction.PassagePlan.model_json_schema()
