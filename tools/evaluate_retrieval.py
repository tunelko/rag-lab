"""Experimentally evaluate dense, FTS5 and hybrid retrieval against local judgments."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rag
from retrieval_pipeline import load_reranker
from retrieval_terms import FTS_STOP_WORDS, FTS_TOKENIZER

from retrieval_experiments import (
    DENSE_MODES, RERANKED_MODES, adoption_verdict, load_dataset,
    paired_comparison, run_mode)


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
