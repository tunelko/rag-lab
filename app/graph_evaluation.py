"""Paired retrieval evaluation of the actual chat path, with source judgments.

This measures retrieval and literal evidence coverage, not semantic answer truth.
Neither a judge LLM nor a generation model is called by this evaluator.
"""
import hashlib
import json
import time

from evaluation import aggregate_rankings, score_evidence, score_ranking
from graph_engine import augment, current_rows
from graph_store import identifier, now
import rag
import retrieval_evaluation as evaluation
import retrieval_pipeline


def fingerprint(service):
    from graph_jobs import graph_store
    store = graph_store(service)
    return identifier(rag.load_state(service.config), current_rows(service), store.identity_decisions())


def evaluate(service, job, content, *, parameters, hops=2):
    from graph_jobs import graph_store
    rows = evaluation.parse_dataset(content)
    if any('vector_id' in qrel for row in rows for qrel in row['relevant']):
        raise ValueError('Compara el grafo por archivo o página; una relación no es un vector del índice.')
    state = rag.load_state(service.config)
    published = rag.published_ids(state)
    if not published:
        raise ValueError('La comparación necesita un índice vectorial real, además del grafo.')
    embeddings = service.get_embeddings()
    vector_store = rag.open_store(service.config, embeddings)
    evaluation.validate_references(rows, vector_store, published)
    documents = frozenset(row['name'] for row in service.list_documents() if row.get('indexed'))
    for row in rows:
        scope = set(row.get('files', documents))
        if not scope <= documents or any(qrel.get('file') not in scope for qrel in row['relevant']):
            raise ValueError('Las referencias deben pertenecer al ámbito indexado de esta evaluación.')
    before = fingerprint(service)
    results = {'baseline': [], 'graph': []}
    scorer = parameters.scorer()
    for index, row in enumerate(rows):
        service.check_cancel(job)
        service.update(job, phase=f'Comparando recuperación con y sin grafo · {index + 1}/{len(rows)}',
                       progress=100 * index / len(rows), stage='evaluation')
        scope = frozenset(row.get('files', documents))
        # Both arms receive exactly this same baseline, query, index, scorer and
        # context budget. The graph alone adds separately supported source spans.
        started = time.perf_counter()
        baseline, baseline_trace = retrieval_pipeline.retrieve(service.config, row['question'], embeddings,
            parameters.k, files=scope, reranker=scorer, rerank_window=parameters.rerank_window, include_ids=True)
        baseline_seconds = time.perf_counter() - started
        started = time.perf_counter()
        expanded, trace = augment(service, row['question'], baseline, files=scope, k=parameters.k, hops=hops)
        graph_seconds = time.perf_counter() - started
        for mode, hits in (('baseline', baseline), ('graph', expanded)):
            context = [{'id': doc.metadata.get('graph_assertion') or doc.metadata.get('vector_id'),
                        'text': doc.page_content, 'metadata': dict(doc.metadata)}
                       for doc, _ in rag.context_fragments(hits, parameters.context_chars)]
            metrics = score_ranking(context, row['relevant'], ks=(parameters.k,))
            if row.get('evidence'):
                metrics.update(score_evidence(context, row['relevant'], row['evidence'], ks=(parameters.k,)))
            results[mode].append({'id': row['id'], 'question': row['question'], 'metrics': metrics,
                'latency_ms': round(1000 * (baseline_seconds + (graph_seconds if mode == 'graph' else 0)), 3),
                'trace': baseline_trace if mode == 'baseline' else trace,
                'context': [{**{key: hit['metadata'].get(key) for key in
                    ('file', 'page', 'graph_assertion', 'graph_unit', 'graph_path')}, 'text': hit['text']} for hit in context]})
    if fingerprint(service) != before:
        raise ValueError('El índice, las fuentes o el grafo cambiaron durante la comparación. Repite con una colección estable.')
    report = {'format': 'rag-local-graph-evaluation-v1', 'created_at': now(),
        'dataset_sha256': hashlib.sha256(content.encode('utf-8')).hexdigest(), 'corpus_fingerprint': before,
        'collection': service.config.collection, 'code_identity': evaluation.code_identity(),
        'graph_code_sha256': {name: hashlib.sha256((rag.ROOT / 'app' / name).read_bytes()).hexdigest()
                              for name in ('graph_engine.py', 'graph_store.py', 'graph_evaluation.py')},
        'parameters': {**parameters.public(), 'hops': hops,
                       'reranker_loaded': scorer is not None, 'reranker_actual_device': getattr(scorer, 'device', None)},
        'latency_design': 'paired shared baseline plus measured incremental graph retrieval; not end-to-end generation',
        'generation_evaluated': False, 'semantic_faithfulness_evaluated': False,
        'summaries': {mode: aggregate_rankings(values) for mode, values in results.items()}, 'results': results,
        'graph_assertions': len(current_rows(service)), 'identity_decisions': len(graph_store(service).identity_decisions())}
    report['delta'] = {key: report['summaries']['graph'][key] - report['summaries']['baseline'][key]
                       for key in report['summaries']['baseline'] if isinstance(report['summaries']['baseline'][key], (int, float))}
    root = service.config.db.parent / 'lab' / 'graph-evaluations'
    root.mkdir(parents=True, exist_ok=True)
    path = root / (identifier(before, report['created_at']) + '.json')
    pending = path.with_suffix('.pending')
    pending.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    pending.replace(path)
    return {'evaluation': report, 'evaluation_id': path.stem}
