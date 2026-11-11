"""Experimentally evaluate dense, FTS5 and hybrid retrieval against local judgments."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from evaluation import aggregate_rankings, score_evidence, score_ranking
from model_routing import asks_for_vision
import rag
# One implementation for the measured strategies and the production query path.
from retrieval_pipeline import (
    automatic_scope_spans as _automatic_scope_spans, channel_catalog as _channel_catalog,
    is_native as _is_native, library_catalog as _library_catalog, load_reranker,
    rerank as _rerank, without_identifier as _without_identifier)
from retrieval_terms import FTS_STOP_WORDS, FTS_TOKENIZER


NATIVE_MODES = frozenset({'dense-native', 'dense-native-scoped',
                          'dense-native-scoped-expanded'})
SCOPED_MODES = frozenset({'dense-native-scoped', 'dense-native-scoped-expanded'})
EXPANDED_MODES = frozenset({'dense-native-scoped-expanded'})
# Channel modes search the native channel itself (ids), instead of filtering the
# global candidate window afterwards.
CHANNEL_MODES = frozenset({'dense-channel', 'dense-channel-scoped',
                           'dense-channel-scoped-reranked'})
CHANNEL_SCOPED_MODES = frozenset({'dense-channel-scoped', 'dense-channel-scoped-reranked'})
RERANKED_MODES = frozenset({'dense-channel-scoped-reranked'})
DENSE_MODES = frozenset({'dense', 'dense-routed'}) | NATIVE_MODES | CHANNEL_MODES


def _channel_metrics(hits, cutoffs):
    metrics = {}
    for k in cutoffs:
        selected = hits[:k]
        native = sum(_is_native(hit) for hit in selected)
        metrics[f'native_fragments@{k}'] = native
        metrics[f'visual_fragments@{k}'] = len(selected) - native
    return metrics


def _budget_context(hits, max_chars):
    context = []
    remaining = max_chars
    for hit in hits:
        if remaining <= 0:
            break
        text = str(hit.get('text') or '')[:remaining]
        if not text:
            continue
        context.append({**hit, 'text': text, 'metadata': dict(hit.get('metadata') or {})})
        remaining -= len(text)
    return context


def _expanded_context(seeds, neighbors, max_chars):
    candidates = list(seeds)
    seen = {hit.get('id') for hit in seeds}
    for seed in seeds:
        metadata = seed.get('metadata') or {}
        chunk = metadata.get('chunk')
        if not (isinstance(chunk, int) or str(chunk).isdigit()):
            continue
        base = (str(metadata.get('file') or '').replace('\\', '/').casefold(),
                metadata.get('page'))
        for adjacent in (int(chunk) - 1, int(chunk) + 1):
            neighbor = neighbors.get((*base, adjacent))
            if not neighbor or neighbor.get('id') in seen or not _is_native(neighbor):
                continue
            seen.add(neighbor.get('id'))
            candidates.append(neighbor)
    return _budget_context(candidates, max_chars)


def _context_metrics(contexts):
    metrics = {}
    for k, context in contexts.items():
        native = sum(_is_native(hit) for hit in context)
        metrics[f'context_chars@{k}'] = sum(len(hit.get('text') or '') for hit in context)
        metrics[f'context_fragments@{k}'] = len(context)
        metrics[f'context_native_fragments@{k}'] = native
        metrics[f'context_visual_fragments@{k}'] = len(context) - native
    return metrics


def load_dataset(path):
    rows = []
    identifiers = set()
    for number, line in enumerate(path.read_text(encoding='utf-8-sig').splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f'Invalid JSON on line {number}: {error.msg}') from error
        if not isinstance(row, dict) or not isinstance(row.get('id'), str) or not row['id']:
            raise ValueError(f'Line {number} needs a nonempty string id.')
        if row['id'] in identifiers:
            raise ValueError(f'Duplicate query id: {row["id"]}')
        if not isinstance(row.get('question'), str) or not row['question'].strip():
            raise ValueError(f'Query {row["id"]} needs a question.')
        if not isinstance(row.get('relevant'), list) or not row['relevant']:
            raise ValueError(f'Query {row["id"]} needs relevance judgments.')
        # Validation is deliberately done before loading an embedding model.
        score_ranking([], row['relevant'])
        if 'evidence' in row:
            score_evidence([], row['relevant'], row['evidence'])
        identifiers.add(row['id'])
        rows.append(row)
    if not rows:
        raise ValueError('The evaluation dataset is empty.')
    return rows


def run_mode(store, mode, dataset, published, cutoffs, *, context_chars=12000,
             reranker=None, rerank_window=50):
    per_query = []
    limit = max(cutoffs)
    window = max(512, limit)
    if mode in RERANKED_MODES and reranker is None:
        raise ValueError('A reranker is required for ' + mode)
    setup_started = time.perf_counter()
    aliases, neighbors = ({}, {})
    channel_ids = None
    if mode in SCOPED_MODES | EXPANDED_MODES:
        aliases, neighbors = _library_catalog(store, published)
    if mode in CHANNEL_MODES:
        aliases, channel_ids = _channel_catalog(store, published)
    setup_ms = (time.perf_counter() - setup_started) * 1000
    for row in dataset:
        started = time.perf_counter()
        files = row.get('files')
        if files is not None and (not isinstance(files, list)
                                  or not all(isinstance(name, str) for name in files)):
            raise ValueError(f'Query {row["id"]} has an invalid files scope.')
        scope = None
        query = row['question']
        if mode in SCOPED_MODES | CHANNEL_SCOPED_MODES:
            if files is not None:
                scope = {'applied': False, 'reason': 'explicit-files', 'files': list(files),
                         'matched_aliases': []}
            else:
                scoped, matched, spans = _automatic_scope_spans(row['question'], aliases)
                if scoped:
                    files = scoped
                    scope = {'applied': True, 'reason': 'model-identifier',
                             'files': scoped, 'matched_aliases': matched}
                    if mode in CHANNEL_SCOPED_MODES:
                        # The scope already encodes the model; searching with it only
                        # favours covers and notices that repeat the product name.
                        query = _without_identifier(row['question'], spans)
                        scope['search_query'] = query
                else:
                    scope = {'applied': False, 'reason': 'no-model-identifier',
                             'files': [], 'matched_aliases': []}
        options = {'ids': channel_ids if channel_ids is not None else published}
        if files is not None:
            options['filter'] = {'file': {'$in': files}}
        embedding_ms = 0.0
        rerank_ms = None
        if mode in DENSE_MODES:
            embedding_started = time.perf_counter()
            vector = store.embed_query(query)
            embedding_ms = (time.perf_counter() - embedding_started) * 1000
            search_started = time.perf_counter()
            hits = store.dense_search_vector(vector, window, **options)
        elif mode == 'lexical':
            search_started = time.perf_counter()
            hits = store.lexical_search(row['question'], window, **options)
        else:
            embedding_started = time.perf_counter()
            vector = store.embed_query(row['question'])
            embedding_ms = (time.perf_counter() - embedding_started) * 1000
            search_started = time.perf_counter()
            hits = store.hybrid_search(row['question'], window, candidate_k=window,
                                       vector=vector, **options)
        search_ms = (time.perf_counter() - search_started) * 1000
        hits = [hit for hit in hits if rag.usable_evidence(hit['text'])]
        if mode in NATIVE_MODES:
            hits = [hit for hit in hits if _is_native(hit)]
        elif mode == 'dense-routed' and not asks_for_vision(row['question']):
            native = [hit for hit in hits if _is_native(hit)]
            visual = [hit for hit in hits if not _is_native(hit)]
            hits = native + visual
        candidates = hits
        if mode in RERANKED_MODES:
            rerank_started = time.perf_counter()
            hits = _rerank(hits, query, reranker, rerank_window)
            rerank_ms = (time.perf_counter() - rerank_started) * 1000
        metrics = score_ranking(hits, row['relevant'], ks=cutoffs)
        metrics.update(_channel_metrics(hits, cutoffs))
        contexts = {}
        seed_contexts = {}
        for k in cutoffs:
            seeds = hits[:k]
            seed_contexts[k] = _budget_context(seeds, context_chars)
            contexts[k] = (_expanded_context(seeds, neighbors, context_chars)
                           if mode in EXPANDED_MODES else
                           seed_contexts[k])
        metrics.update(_context_metrics(contexts))
        if 'evidence' in row:
            if mode == 'dense' or mode in CHANNEL_MODES:
                # Measured before reranking: what a reordering stage could still reach.
                ceilings = score_evidence(candidates, row['relevant'], row['evidence'],
                                          ks=(20, 50, 100, 512))
                metrics.update({f'candidate_{name}': value for name, value in ceilings.items()})
            for k, context in contexts.items():
                evidence = score_evidence(context, row['relevant'], row['evidence'],
                                          ks=(max(1, len(context)),))
                metrics[f'evidence_recall@{k}'] = evidence[
                    f'evidence_recall@{max(1, len(context))}']
                if mode in EXPANDED_MODES:
                    seed_context = seed_contexts[k]
                    seed_evidence = score_evidence(
                        seed_context, row['relevant'], row['evidence'],
                        ks=(max(1, len(seed_context)),))
                    metrics[f'seed_evidence_recall@{k}'] = seed_evidence[
                        f'evidence_recall@{max(1, len(seed_context))}']
        latency_ms = (time.perf_counter() - started) * 1000
        strata = row.get('strata', ['unclassified'])
        if isinstance(strata, str):
            strata = [strata]
        if not isinstance(strata, list) or not strata or not all(
                isinstance(item, str) and item for item in strata):
            raise ValueError(f'Query {row["id"]} has invalid strata.')
        per_query.append({
            'id': row['id'], 'strata': list(dict.fromkeys(strata)),
            'latency_ms': round(latency_ms, 3), 'embedding_ms': round(embedding_ms, 3),
            'search_ms': round(search_ms, 3), 'metrics': metrics,
            **({'rerank_ms': round(rerank_ms, 3)} if rerank_ms is not None else {}),
            **({'automatic_scope': scope} if scope is not None else {}),
            'hits': [{'id': hit['id'], 'file': hit['metadata'].get('file'),
                      'page': hit['metadata'].get('page'),
                      'item_type': hit['metadata'].get('item_type'),
                      'score': hit['score']} for hit in hits],
        })
    groups = sorted({stratum for item in per_query for stratum in item['strata']})
    summary = aggregate_rankings(per_query)
    if mode in RERANKED_MODES:
        times = sorted(item['rerank_ms'] for item in per_query)
        summary['rerank_ms_p50'] = statistics.median(times)
        summary['rerank_ms_p95'] = times[min(len(times) - 1, int(0.95 * len(times)))]
    return {'summary': summary, 'mode_setup_ms': round(setup_ms, 3),
            **({'channel_fragments': len(channel_ids)} if channel_ids is not None else {}),
            'by_stratum': {group: aggregate_rankings(
                [item for item in per_query if group in item['strata']]) for group in groups},
            'queries': per_query}


def paired_comparison(results, left, right, metric):
    left_rows = {item['id']: item['metrics'][metric] for item in results[left]['queries']}
    right_rows = {item['id']: item['metrics'][metric] for item in results[right]['queries']}
    deltas = [right_rows[key] - left_rows[key] for key in left_rows]
    tolerance = 1e-12
    generator = random.Random(0)
    bootstrap = sorted(statistics.fmean(deltas[generator.randrange(len(deltas))]
                                        for _ in deltas) for _ in range(5000))
    return {'metric': metric, 'left': left, 'right': right,
            'wins': sum(delta > tolerance for delta in deltas),
            'losses': sum(delta < -tolerance for delta in deltas),
            'ties': sum(abs(delta) <= tolerance for delta in deltas),
            'mean_delta': statistics.fmean(deltas),
            'bootstrap_seed': 0, 'bootstrap_samples': 5000,
            'mean_delta_ci95': [bootstrap[124], bootstrap[4874]]}


def adoption_verdict(results, dataset, cutoffs):
    rules = {
        'decision_k': 4, 'minimum_queries': 50,
        'maximum_overall_fragment_recall_regression': 0.01,
        'minimum_mean_ndcg_improvement': 0.02,
        'ndcg_bootstrap_ci_lower_must_exceed': 0.0,
        'maximum_hybrid_p95_latency_overhead_ms': 100.0,
        'critical_strata': {
            'exact-identifier': {'minimum_queries': 5, 'maximum_fragment_recall_regression': 0.0},
            'cross-language': {'minimum_queries': 5, 'maximum_fragment_recall_regression': 0.02},
            'cjk': {'minimum_queries': 5, 'maximum_fragment_recall_regression': 0.02},
        },
    }
    if not {'dense', 'hybrid'} <= set(results) or 4 not in cutoffs:
        return {'rules': rules, 'verdict': 'not_evaluated',
                'reason': 'Dense and hybrid modes plus cutoff 4 are required.', 'checks': []}
    dense, hybrid = results['dense'], results['hybrid']
    paired = paired_comparison(results, 'dense', 'hybrid', 'ndcg@4')
    checks = []

    def add(name, passed, observed, requirement):
        checks.append({'name': name, 'passed': passed, 'observed': observed,
                       'requirement': requirement})

    add('minimum_queries', True if len(dataset) >= 50 else None, len(dataset), '>= 50')
    recall_delta = (hybrid['summary']['fragment_recall@4'] -
                    dense['summary']['fragment_recall@4'])
    add('overall_fragment_recall@4', recall_delta >= -0.01, recall_delta, 'delta >= -0.01')
    ndcg_delta = hybrid['summary']['ndcg@4'] - dense['summary']['ndcg@4']
    add('mean_ndcg@4', ndcg_delta >= 0.02, ndcg_delta, 'delta >= 0.02')
    lower = paired['mean_delta_ci95'][0]
    add('ndcg@4_bootstrap_lower', lower > 0, lower, '> 0')
    latency_overhead = (hybrid['summary']['latency_ms_p95'] -
                        dense['summary']['latency_ms_p95'])
    add('hybrid_p95_latency_overhead_ms', latency_overhead <= 100,
        latency_overhead, '<= 100 ms')
    for name, rule in rules['critical_strata'].items():
        dense_group = dense['by_stratum'].get(name)
        hybrid_group = hybrid['by_stratum'].get(name)
        count = dense_group['queries'] if dense_group and hybrid_group else 0
        if count < rule['minimum_queries']:
            add('stratum:' + name, None, {'queries': count},
                f'>= {rule["minimum_queries"]} queries')
            continue
        delta = hybrid_group['fragment_recall@4'] - dense_group['fragment_recall@4']
        maximum = rule['maximum_fragment_recall_regression']
        add('stratum:' + name, delta >= -maximum,
            {'queries': count, 'fragment_recall_delta': delta}, f'delta >= -{maximum}')
    if any(check['passed'] is False for check in checks):
        verdict = 'fail'
    elif any(check['passed'] is None for check in checks):
        verdict = 'insufficient_data'
    else:
        verdict = 'pass'
    return {'rules': rules, 'verdict': verdict, 'checks': checks}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dataset', type=Path, required=True,
                        help='JSONL queries with explicit file/page/chunk relevance judgments.')
    parser.add_argument('--mode', nargs='+',
                        choices=('dense', 'dense-native', 'dense-routed',
                                 'dense-native-scoped', 'dense-native-scoped-expanded',
                                 'dense-channel', 'dense-channel-scoped',
                                 'dense-channel-scoped-reranked', 'lexical', 'hybrid'),
                        default=('dense', 'lexical', 'hybrid'))
    parser.add_argument('--cutoffs', nargs='+', type=int, default=(1, 4, 8))
    parser.add_argument('--output', type=Path)
    parser.add_argument('--db', type=Path, help='Override the configured vector directory.')
    parser.add_argument('--collection', help='Override RAG_COLLECTION.')
    parser.add_argument('--allow-download', action='store_true',
                        help='Allow downloading the configured embedding or reranking model if absent.')
    parser.add_argument('--reranker', default='BAAI/bge-reranker-v2-m3',
                        help='Cross-encoder used by dense-channel-scoped-reranked.')
    parser.add_argument('--rerank-window', type=int, default=50,
                        help='Number of top candidates the cross-encoder reorders.')
    parser.add_argument('--rerank-device', choices=('auto', 'cpu', 'cuda'), default='auto',
                        help='auto uses CUDA when available. Embeddings stay on their configured device.')
    args = parser.parse_args()
    modes = list(dict.fromkeys(args.mode))
    cutoffs = tuple(sorted(set(args.cutoffs)))
    if not cutoffs or cutoffs[0] < 1:
        parser.error('--cutoffs must contain positive integers')
    if args.rerank_window < max(cutoffs):
        parser.error('--rerank-window must be at least the largest cutoff')
    dataset_path = args.dataset.resolve()
    dataset = load_dataset(dataset_path)
    config = rag.Config()
    if args.db:
        config = replace(config, db=args.db.resolve())
    if args.collection:
        config = replace(config, collection=args.collection)
    state = rag.load_state(config)
    published = rag.published_ids(state)
    if not published:
        raise SystemExit('The selected collection has no published vectors.')
    store = rag.open_store(config, None)
    if {'lexical', 'hybrid'} & set(modes) and not store.lexical_index_available():
        raise SystemExit('Optional lexical index unavailable. Stop or idle the server, then run: '
                         'python -m tools.prepare_lexical_index')
    embeddings = None
    if (DENSE_MODES | {'hybrid'}) & set(modes):
        embeddings = rag.make_embeddings(config, allow_download=args.allow_download)
        store.embeddings = embeddings
    warm_started = time.perf_counter()
    store.get(ids=published, include=[], cached=True)
    warm_seconds = time.perf_counter() - warm_started
    if embeddings is not None:
        vector = store.embed_query(dataset[0]['question'])
        store.dense_search_vector(vector, 1, ids=published)
    if {'lexical', 'hybrid'} & set(modes):
        store.lexical_search(dataset[0]['question'], 1, ids=published)
    reranker = None
    if RERANKED_MODES & set(modes):
        reranker = load_reranker(args.reranker, args.rerank_device, allow_download=args.allow_download)
        reranker('warm up', ['warm up'])
    results = {mode: run_mode(store, mode, dataset, published, cutoffs,
                              context_chars=config.context_chars, reranker=reranker,
                              rerank_window=args.rerank_window) for mode in modes}
    index_fingerprint = hashlib.sha256(json.dumps({
        'ids': sorted(published), 'config': state.get('config'),
        'files': {key: value.get('sha256') for key, value in sorted(state.get('files', {}).items())},
    }, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    paired = []
    if {'dense', 'hybrid'} <= set(modes):
        for metric in ('ndcg@4', 'fragment_recall@4'):
            if 4 in cutoffs:
                paired.append(paired_comparison(results, 'dense', 'hybrid', metric))
    adoption = adoption_verdict(results, dataset, cutoffs)
    payload = {
        'format': 'rag-local-retrieval-evaluation-v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'dataset': {'path': str(dataset_path), 'sha256': hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
                    'queries': len(dataset)},
        'corpus': {'collection': config.collection, 'published_chunks': len(published),
                   'documents': len(state.get('files', {})), 'config': config.identity(),
                   'fingerprint': index_fingerprint},
        'cutoffs': list(cutoffs), 'cache_warm_seconds': round(warm_seconds, 6),
        'cache': store.cache_info(), 'modes': results, 'paired': paired,
        'retrieval_configuration': {
            'candidate_window': 512, 'rrf_k': 60, 'context_chars': config.context_chars,
            'fts_tokenizer': FTS_TOKENIZER, 'stop_words': sorted(FTS_STOP_WORDS),
            **({'reranker': reranker.model_id, 'rerank_device': reranker.device,
                'rerank_window': args.rerank_window, 'rerank_max_length': 512,
                'rerank_peak_vram_mib': reranker.peak_vram_mib()} if reranker else {}),
        },
        'adoption': adoption,
        'notes': [
            'No LLM generation is called by this command.',
            'Retrieval labels are explicit ground truth; model output is never used as its own judge.',
            'Dense is exact cosine. Hybrid uses reciprocal-rank fusion of exact cosine and SQLite FTS5 BM25.',
            'All modes use the production evidence filter and a 512-candidate safety window.',
            'Channel experiments keep the requested K budget; native means text, table or a legacy missing item_type.',
            'Evidence anchors are normalized literal strings and can span selected chunks only within one relevant page.',
            'Scoped modes derive model aliases from filenames and first-page native titles; explicit files always take precedence.',
            'Expanded mode adds native adjacent chunks from the same page, then applies the configured character budget.',
            'Channel modes rank the native channel directly: text and tables, plus captions only for pages without native text.',
            'Channel-scoped modes also derive chipset and filename-prefix aliases and search without the matched model identifier.',
            'Reranked mode reorders the top candidates with a local cross-encoder; its milliseconds are recorded per query.',
            'Rankings are collapsed to the judgment granularity before metrics are calculated.',
            'fragment_recall@K measures the first K raw fragments exactly as production consumes them.',
            'Embedding and search milliseconds are recorded separately per query.',
        ],
    }
    output = args.output
    if output is None:
        stamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        output = ROOT / 'reports' / 'evaluation' / f'{dataset_path.stem}-{stamp}.json'
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    concise = {mode: result['summary'] for mode, result in results.items()}
    print(json.dumps(concise, ensure_ascii=False, indent=2))
    print(output)


if __name__ == '__main__':
    main()
