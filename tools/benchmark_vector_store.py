"""Measure current-index vector mechanics without loading a model or calling Ollama."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import statistics
import sys
import tempfile
import time

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import rag
from library_queries import best_evidence, normalized, token_counts, tokens
from vector_store import VectorStore


class FixedEmbeddings:
    def __init__(self, vector):
        self.vector = vector

    def embed_query(self, _text):
        return self.vector


def measure(action, repetitions):
    values = []
    for _ in range(repetitions):
        started = time.perf_counter()
        action()
        values.append((time.perf_counter() - started) * 1000)
    ordered = sorted(values)
    return {'samples': repetitions, 'p50_ms': round(statistics.median(ordered), 3),
            'p95_ms': round(float(np.percentile(ordered, 95)), 3),
            'min_ms': round(min(ordered), 3), 'max_ms': round(max(ordered), 3)}


def lexical_copy_benchmark(config, published):
    """Exercise derived-index writes on an automatically discarded SQLite copy."""
    source = config.db / f'{config.collection}-vectors.sqlite3'
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / source.name
        source_db = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
        target_db = sqlite3.connect(target)
        try:
            source_db.backup(target_db)
        finally:
            target_db.close();source_db.close()
        store = VectorStore(target)
        started = time.perf_counter();store.ensure_lexical_index()
        build_ms = (time.perf_counter() - started) * 1000
        sample_ids = published[:min(500, len(published))]
        sample = store.get(ids=sample_ids, include=['documents', 'metadatas', 'embeddings'])
        searches = measure(lambda: store.lexical_search('cpu support', 512), 20)
        updates = measure(lambda: store.upsert_vectors(sample['ids'], sample['documents'],
                          sample['metadatas'], sample['embeddings']), 3)
        deletes = measure(lambda: store.delete(sample['ids']), 1)
        with store.connect() as db:
            vectors = db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]
            mappings = db.execute('SELECT COUNT(*) FROM fts_rows').fetchone()[0]
            fts = db.execute('SELECT COUNT(*) FROM vectors_fts').fetchone()[0]
        if not vectors == mappings == fts:
            raise RuntimeError('Lexical-copy benchmark detected inconsistent row counts.')
        return {'temporary_copy': True, 'sample_writes': len(sample_ids),
                'build_ms': round(build_ms, 3), 'search': searches,
                'upsert_existing': updates, 'delete_existing': deletes,
                'remaining_rows': vectors, 'verified': True}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repetitions', type=int, default=20)
    parser.add_argument('--baseline-repetitions', type=int, default=3)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--lexical-copy', action='store_true',
                        help='Also benchmark FTS on an automatically discarded SQLite copy.')
    args = parser.parse_args()
    if not 3 <= args.repetitions <= 1000 or not 1 <= args.baseline_repetitions <= 100:
        parser.error('invalid repetition count')
    config = rag.Config()
    state = rag.load_state(config)
    published = rag.published_ids(state)
    if not published:
        raise SystemExit('The current collection has no published vectors.')
    raw = rag.open_store(config, None).get(ids=[published[0]], include=['embeddings'])
    if not len(raw['embeddings']):
        raise SystemExit('The first published vector is missing.')
    embeddings = FixedEmbeddings(raw['embeddings'][0])
    store = rag.open_store(config, embeddings)

    VectorStore.clear_cache(store.path)
    started = time.perf_counter()
    store.dense_search('fixed-vector-probe', 10, ids=published)
    cold_ms = (time.perf_counter() - started) * 1000
    warm = measure(lambda: store.dense_search('fixed-vector-probe', 10, ids=published),
                   args.repetitions)

    def legacy_reconstruction():
        rows = store.get(ids=published, include=['documents', 'metadatas', 'embeddings'])
        matrix = np.asarray(rows['embeddings'], dtype=np.float32)
        vector = np.asarray(embeddings.vector, dtype=np.float32)
        distances = 1 - np.clip(matrix @ vector /
            (np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector)), -1, 1)
        np.argsort(distances, kind='stable')[:10]

    baseline = measure(legacy_reconstruction, args.baseline_repetitions)
    rows = store.get(ids=published, include=['documents', 'metadatas', 'embeddings'])
    matrix = np.asarray(rows['embeddings'], dtype=np.float32)
    vector = np.asarray(embeddings.vector, dtype=np.float32)
    scores = matrix @ vector / (np.linalg.norm(matrix, axis=1) * np.linalg.norm(vector))
    groups = {}
    for index, metadata in enumerate(rows['metadatas']):
        groups.setdefault(metadata.get('file', ''), []).append(index)

    def rank_every_document():
        for indices in groups.values():
            best_evidence([rows['documents'][index] for index in indices], scores[indices],
                          'cpu support', [rows['metadatas'][index] for index in indices])

    tokens.cache_clear();normalized.cache_clear();token_counts.cache_clear()
    ranking_cold = measure(rank_every_document, 1)
    ranking_warm = measure(rank_every_document, min(args.repetitions, 30))
    payload = {
        'format': 'rag-local-vector-benchmark-v1',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'collection': config.collection, 'published_vectors': len(published),
        'dimensions': len(embeddings.vector), 'cold_ms': round(cold_ms, 3),
        'warm_exact_top_k': warm, 'legacy_reconstruct_and_full_sort': baseline,
        'warm_speedup_p50': round(baseline['p50_ms'] / warm['p50_ms'], 2) if warm['p50_ms'] else None,
        'global_ranking_cold': ranking_cold, 'global_ranking_warm': ranking_warm,
        'global_documents': len(groups),
        'text_feature_cache': {'tokens': tokens.cache_info()._asdict(),
                               'normalized': normalized.cache_info()._asdict(),
                               'token_counts': token_counts.cache_info()._asdict()},
        'cache': store.cache_info(), 'embedding_calls': 0, 'ollama_calls': 0,
        'scope': 'Storage/search mechanics only; this does not measure retrieval quality.',
    }
    if args.lexical_copy:
        payload['lexical_copy'] = lexical_copy_benchmark(config, published)
    encoded = json.dumps(payload, ensure_ascii=False, indent=2)
    print(encoded)
    if args.output:
        output = args.output.resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(encoded, encoding='utf-8')
        print(output)


if __name__ == '__main__':
    main()
