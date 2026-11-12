"""Shared experimental retrieval evaluators. No web, CLI or model side effects."""
from __future__ import annotations

import json
import random
import statistics
import time

from evaluation import aggregate_rankings, score_evidence, score_ranking
from model_routing import asks_for_vision
import rag
from retrieval_pipeline import (
    automatic_scope_spans as _automatic_scope_spans, channel_catalog as _channel_catalog,
    is_native as _is_native, library_catalog as _library_catalog,
    rerank as _rerank, without_identifier as _without_identifier)


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
    return parse_dataset_jsonl(path.read_text(encoding='utf-8-sig'))


def parse_dataset_jsonl(content, *, with_lines=False):
    rows = []
    identifiers = set()
    for number, line in enumerate(content.lstrip('\ufeff').splitlines(), 1):
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
        if with_lines:
            row = {**row, 'line': number}
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
