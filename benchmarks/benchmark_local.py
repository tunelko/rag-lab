"""Synthetic CPU diagnostics only: file polling and deterministic model routing."""
import argparse
import json
from pathlib import Path
import statistics
import sys
import tempfile
import time
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from file_fingerprints import FileFingerprints, sha256_file
from model_routing import select_model


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as directory:
        files = [Path(directory) / f'document-{index}.pdf' for index in range(12)]
        payload = b'synthetic data, no real documents\n' * (4 * 1024**2 // 33)
        for path in files:
            path.write_bytes(payload)
        cache = FileFingerprints()
        for path in files:
            cache.digest(path)
        def measure(digest):
            samples = []
            for _ in range(7):
                started = time.perf_counter()
                for path in files:
                    digest(path)
                samples.append(time.perf_counter() - started)
            return statistics.median(samples)
        baseline, cached = measure(sha256_file), measure(cache.digest)
        polling = {'files': len(files), 'bytes_per_file': len(payload), 'repetitions': 7,
                   'baseline_median_ms': round(baseline * 1000, 3), 'cached_median_ms': round(cached * 1000, 3),
                   'speedup': round(baseline / cached, 2), 'cache': cache.snapshot()}
    settings = SimpleNamespace(response_model='text', vision_model='vision', response_mode='automatic', multimodal=True)
    available = {'models': ['text', 'vision'], 'default_model': 'text'}
    hits = [(SimpleNamespace(metadata={'page': 1}), .1)]
    capabilities = lambda model: ['vision'] if model == 'vision' else []
    started = time.perf_counter()
    for _ in range(3000):
        select_model(settings, 'Explica el diagrama y sus conectores.', hits, True, available, capabilities)
    routing_us = (time.perf_counter() - started) * 1_000_000 / 3000
    result = {'scope': 'Synthetic CPU benchmark; temporary bytes and fake model metadata. No GPU, models, embeddings or network.',
              'library_fingerprints': polling, 'routing_mean_us': round(routing_us, 3), 'routing_iterations': 3000}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
