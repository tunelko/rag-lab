"""Evaluate the real focused retrieval path, including its exact context budget."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path, PurePosixPath
import time

import rag
import retrieval_pipeline
from evaluation import aggregate_rankings, score_evidence, score_ranking
from library_queries import query_intent
from model_routing import asks_for_vision

MAX_BYTES = 1024 * 1024
MAX_QUESTIONS = 100
CODE_FILES = ('rag.py', 'retrieval_pipeline.py', 'retrieval_terms.py', 'library_queries.py',
              'vector_store.py', 'model_routing.py', 'evaluation.py',
              'retrieval_evaluation.py', 'retrieval_experiments.py',
              'laboratory_store.py', 'web_app.py')


def code_identity():
    files = {name: hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
             for name in CODE_FILES}
    return {'files': files, 'sha256': hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()}


class LabError(ValueError):
    def __init__(self, code, line=0, **values):
        self.code, self.line, self.values = code, line, values
        super().__init__(f'Line {line}: {code}' if line else code)

    def public(self):
        return {'code': self.code, 'line': self.line, **self.values}

    def user_message(self):
        messages = {
            'size': 'El conjunto supera el límite de 1 MiB.',
            'utf8': 'El archivo debe estar codificado en UTF-8.',
            'json': 'El JSON de esta línea no es válido.',
            'row': 'Cada línea debe contener un objeto JSON.',
            'id': 'Cada pregunta necesita un identificador único, de hasta 100 caracteres.',
            'question': 'Escribe una pregunta de entre 1 y 6000 caracteres.',
            'focused': 'Esta pregunta es de inventario o de conjunto. Esta fase solo evalúa preguntas concretas.',
            'reference': 'Revisa las referencias: documento y página, o identificador de fragmento, con un único nivel de detalle.',
            'page': 'La página debe ser un número entero positivo.',
            'evidence': 'La evidencia debe ser una lista no vacía de textos literales, de hasta 6000 caracteres cada uno.',
            'count': 'El conjunto supera el límite de 100 preguntas.',
            'empty': 'El conjunto no contiene preguntas.',
            'missing_file': 'No está indexado el documento {file}.',
            'missing_id': 'El fragmento de referencia no pertenece al índice publicado.',
            'missing_page': 'La página {page} de {file} no contiene fragmentos indexados.',
            'no_index': 'Sube e indexa documentos en Biblioteca antes de evaluar.',
            'changed': 'La biblioteca ha cambiado. Vuelve a validar el conjunto antes de ejecutar.',
            'reranker': 'El reordenador configurado no está disponible en la caché local. No se ha sustituido ni descargado.',
            'embeddings': 'El modelo de búsqueda no está preparado en la caché local. Prepáralo desde Configuración.',
            'busy': 'Hay una indexación o un experimento en curso o en cola. Termínalo o cancélalo antes de evaluar.',
            'dataset_changed': 'El conjunto guardado no coincide con su huella. Vuelve a cargarlo.',
            'parameters': 'Los parámetros de recuperación no son válidos.',
            'disk': 'No hay espacio suficiente para crear la copia temporal del índice.',
        }
        message = messages.get(self.code, 'No se ha podido completar el experimento.').format(**self.values)
        return f'Línea {self.line}: {message}' if self.line else message


def relative_name(value, line):
    if not isinstance(value, str) or not 1 <= len(value) <= 1000:
        raise LabError('reference', line)
    name = value.replace('\\', '/')
    if ':' in name or name.startswith('/') or any(part in {'', '.', '..'} for part in name.split('/')):
        raise LabError('reference', line)
    return PurePosixPath(name).as_posix()


def parse_dataset(content):
    try:
        length = len(content.encode('utf-8'))
    except UnicodeEncodeError:
        raise LabError('utf8') from None
    if length > MAX_BYTES:
        raise LabError('size')
    rows, identifiers = [], set()
    for line, raw in enumerate(content.lstrip('\ufeff').splitlines(), 1):
        if not raw.strip():
            continue
        try:
            row = json.loads(raw)
        except ValueError:
            raise LabError('json', line) from None
        if not isinstance(row, dict):
            raise LabError('row', line)
        try:
            json.dumps(row, ensure_ascii=False).encode('utf-8')
        except UnicodeEncodeError:
            raise LabError('utf8', line) from None
        if (not isinstance(row.get('id'), str) or not row['id'].strip()
                or len(row['id']) > 100 or row['id'] in identifiers):
            raise LabError('id', line)
        question = row.get('question')
        if not isinstance(question, str) or not question.strip() or len(question) > 6000:
            raise LabError('question', line)
        if query_intent(question).mode != 'focused':
            raise LabError('focused', line)
        qrels = row.get('relevant')
        if not isinstance(qrels, list) or not 1 <= len(qrels) <= 20:
            raise LabError('reference', line)
        for qrel in qrels:
            if not isinstance(qrel, dict):
                raise LabError('reference', line)
            if qrel.get('vector_id'):
                if not isinstance(qrel['vector_id'], str) or len(qrel['vector_id']) > 128:
                    raise LabError('reference', line)
            else:
                qrel['file'] = relative_name(qrel.get('file'), line)
            if 'page' in qrel and (type(qrel['page']) is not int or not 1 <= qrel['page'] <= 100000):
                raise LabError('page', line)
            relevance = qrel.get('relevance', 1)
            if type(relevance) not in {int, float} or not math.isfinite(relevance) or not 0 < relevance <= 10:
                raise LabError('reference', line)
        try:
            score_ranking([], qrels)
        except (ValueError, TypeError):
            raise LabError('reference', line) from None
        try:
            if 'evidence' in row:
                if not isinstance(row['evidence'], list) or len(row['evidence']) > 20:
                    raise ValueError()
                score_evidence([], qrels, row['evidence'])
                if any(len(value) > 6000 for value in row['evidence']):
                    raise ValueError()
        except (ValueError, TypeError):
            raise LabError('evidence', line) from None
        if 'files' in row:
            if not isinstance(row['files'], list) or not 1 <= len(row['files']) <= 100:
                raise LabError('reference', line)
            row['files'] = [relative_name(name, line) for name in row['files']]
        identifiers.add(row['id'])
        # Keep only the documented schema; uploaded text is never an instruction.
        rows.append({key: row[key] for key in ('id', 'question', 'relevant', 'evidence', 'files', 'strata') if key in row})
        rows[-1]['line'] = line
        if len(rows) > MAX_QUESTIONS:
            raise LabError('count', line)
    if not rows:
        raise LabError('empty')
    return rows


def corpus_identity(config, state):
    files = {record['file']: {'sha256': record.get('sha256'), 'chunks': len(record.get('ids', []))}
             for record in state['files'].values() if record.get('ids')}
    fingerprint = hashlib.sha256(json.dumps({'files': files, 'identity': config.identity(),
        'ids': sorted(rag.published_ids(state))}, sort_keys=True).encode()).hexdigest()
    return {'collection': config.collection, 'identity': config.identity(), 'files': files,
            'documents': len(files), 'published_chunks': len(rag.published_ids(state)),
            'fingerprint': fingerprint}


def validate_references(rows, store, published):
    records = store.get(ids=published, include=['metadatas'])
    identifiers = set(records['ids'])
    pages = {}
    for metadata in records['metadatas']:
        pages.setdefault(metadata['file'], set()).add(metadata.get('page', 0))
    for row in rows:
        for name in row.get('files', []):
            if name not in pages:
                raise LabError('missing_file', row['line'], file=name)
        for qrel in row['relevant']:
            if qrel.get('vector_id'):
                if qrel['vector_id'] not in identifiers:
                    raise LabError('missing_id', row['line'])
            elif qrel['file'] not in pages:
                raise LabError('missing_file', row['line'], file=qrel['file'])
            elif 'page' in qrel and qrel['page'] not in pages[qrel['file']]:
                raise LabError('missing_page', row['line'], file=qrel['file'], page=qrel['page'])


def evaluate_question(config, embeddings, row, *, k, reranker=None, rerank_window=50, files=None):
    started = time.perf_counter()
    # This is the chat's implementation, not a parallel ranking algorithm.
    hits, trace = retrieval_pipeline.retrieve(config, row['question'].strip(), embeddings, k,
        files=row.get('files', files), visual=asks_for_vision(row['question']),
        reranker=reranker, rerank_window=rerank_window, include_ids=True)
    raw = [{'id': doc.metadata['vector_id'], 'text': doc.page_content,
            'metadata': dict(doc.metadata), 'score': float(score)} for doc, score in hits]
    context = [{'id': doc.metadata['vector_id'], 'text': doc.page_content,
                'metadata': dict(doc.metadata), 'score': float(score)}
               for doc, score in rag.context_fragments(hits, config.context_chars)]
    metrics = score_ranking(raw, row['relevant'], ks=(k,))
    evidence = None
    if 'evidence' in row:
        evidence = bool(score_evidence(context, row['relevant'], row['evidence'],
                                      ks=(max(1, len(context)),))[f'evidence_recall@{max(1, len(context))}'])
    # Source paths are machine-specific and never part of the web/export format.
    def public(hit):
        meta = hit['metadata']
        return {'id': hit['id'], 'file': meta.get('file'), 'page': meta.get('page'),
                'chunk': meta.get('chunk'), 'item_type': meta.get('item_type', 'text'),
                'text': hit['text'], 'score': hit['score']}
    return {'id': row['id'], 'question': row['question'], 'relevant': row['relevant'],
            'evidence': row.get('evidence'), 'evidence_found': evidence,
            'latency_ms': round((time.perf_counter() - started) * 1000, 3),
            'metrics': metrics, 'trace': trace, 'context': [public(hit) for hit in context],
            'retrieved_ids': [hit['id'] for hit in raw],
            'context_chars': sum(len(hit['text']) for hit in context)}


def summarize(results, total):
    annotated = [row for row in results if row['evidence_found'] is not None]
    found = sum(row['evidence_found'] for row in annotated)
    return {**(aggregate_rankings(results) if results else {}),
            'completed': len(results), 'total': total, 'annotated': len(annotated),
            'found': found, 'without_evidence': len(results) - len(annotated),
            'evidence_recall': found / len(annotated) if annotated else None}


def evaluate_snapshot(config, content, get_embeddings, *, k=4, reranker=None,
                      rerank_window=50, observer=None, cancelled=None,
                      get_reranker=None, on_report=None, files=None):
    """Measure an already frozen collection; the caller owns snapshot lifecycle.

    Validation precedes the embedding factory. No model is downloaded, no LLM is
    called, and no FTS is built. A callback can persist each completed question;
    cancellation retains that partial report rather than pretending it is full.
    """
    if type(k) is not int or not 1 <= k <= 12 or type(rerank_window) is not int or rerank_window < 1:
        raise LabError('parameters')
    rows = parse_dataset(content)
    state = rag.load_state(config)
    published = rag.published_ids(state)
    if not published:
        raise LabError('no_index')
    corpus = corpus_identity(config, state)
    scope = frozenset(corpus['files']) if files is None else frozenset(files)
    eligible = [identifier for record in state['files'].values() if record.get('file') in scope
                for identifier in record.get('ids', [])]
    validate_references(rows, rag.open_store(config, None), eligible)
    report = {'format': 'musa-focused-retrieval-v1', 'scope': 'focused-no-history',
              'dataset_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(),
              'corpus': corpus, 'code': code_identity(), 'status': 'running',
              'generation_evaluated': False, 'k': k, 'context_chars': config.context_chars,
              'scope_files': sorted(scope),
              'reranker': getattr(reranker, 'model_id', None), 'rerank_window': rerank_window,
              'queries': [], 'summary': summarize([], len(rows))}
    if on_report:
        on_report(report)
    if cancelled and cancelled():
        report['status'] = 'cancelled'
        if on_report:
            on_report(report)
        return report
    embeddings = get_embeddings()
    if get_reranker:
        reranker = get_reranker()
        report['reranker'] = getattr(reranker, 'model_id', None)
    report['reranker_device'] = getattr(reranker, 'device', None)
    for row in rows:
        if cancelled and cancelled():
            report['status'] = 'cancelled'
            break
        result = evaluate_question(config, embeddings, row, k=k, reranker=reranker,
                                  rerank_window=rerank_window, files=scope)
        report['queries'].append(result)
        report['summary'] = summarize(report['queries'], len(rows))
        if observer:
            observer(result, report['summary'])
        if on_report:
            on_report(report)
    else:
        report['status'] = 'done'
    if on_report:
        on_report(report)
    return report
