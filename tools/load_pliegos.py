"""Put downloaded tender documents and their summary sheets into a running library and index them.

Copies the PDFs and sheets listed in data/pliegos/manifest.json into the library folder,
files them under a category and starts indexing through the server's own API (the calls the
web page makes). Unchanged files are skipped by the index; a sheet whose status changed is
replaced and indexed again. Run it on the computer that runs the server.

    python -m tools.load_pliegos --url http://127.0.0.1:8765 --category Pliegos --subcategory Ciberseguridad
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import sys
import time
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--url', required=True, help='address of the running server')
    parser.add_argument('--staging', type=Path, default=ROOT / 'data' / 'pliegos')
    parser.add_argument('--library', type=Path, default=ROOT / 'data' / 'documents')
    parser.add_argument('--category', default='Pliegos')
    parser.add_argument('--subcategory', default='')
    args = parser.parse_args(argv)
    base = args.url.rstrip('/')
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def call(path, method='GET', body=None, token=None):
        headers = {'Origin': base}
        if token:
            headers['X-RAG-Token'] = token
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers['Content-Type'] = 'application/json'
        request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
        try:
            with opener.open(request, timeout=120) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            raise SystemExit(f"{method} {path} -> {error.code} {error.read().decode('utf-8', 'replace')[:300]}")

    token = call('/api/session')['token']
    manifest = json.loads((args.staging / 'manifest.json').read_text(encoding='utf-8'))
    names = []
    for tender in manifest['tenders'].values():
        names += [document['file'] for document in tender['documents']]
        if tender.get('sheet'):
            names.append(tender['sheet'])
    copied = 0
    for name in names:
        source, target = args.staging / name, args.library / name
        if not target.exists() or target.read_bytes() != source.read_bytes():
            shutil.copyfile(source, target)
            copied += 1
    print(f"licitaciones: {len(manifest['tenders'])} · archivos: {len(names)} · copiados o renovados: {copied}", flush=True)

    topics = {(item['name'], item['parent_id']): item['id'] for item in call('/api/library')['topics']}
    topic = topics.get((args.category, None)) or call('/api/admin/topics', 'POST', {'name': args.category}, token)['id']
    label = args.category
    if args.subcategory:
        topic = topics.get((args.subcategory, topic)) or call('/api/admin/topics', 'POST', {'name': args.subcategory, 'parent_id': topic}, token)['id']
        label += ' / ' + args.subcategory
    for start in range(0, len(names), 200):
        call('/api/admin/library/assign', 'POST', {'names': names[start:start + 200], 'topic_id': topic}, token)
    print(f'categoría: {label} · documentos asignados: {len(names)}', flush=True)

    job = call('/api/admin/index', 'POST', {}, token)['job_id']
    started, last = time.time(), ''
    while True:
        state = call(f'/api/jobs/{job}')
        line = f"{state['status']} · {round(state.get('progress') or 0)} %"
        if line != last:
            print(f'  {round(time.time() - started):>4}s  {line}', flush=True)
            last = line
        if state['status'] in ('done', 'error', 'cancelled'):
            break
        time.sleep(3)
    print('indexación:', state['status'], json.dumps(state.get('summary'), ensure_ascii=False), state.get('error', ''), flush=True)
    for item in [item for item in state.get('documents', []) if item.get('error_info')][:10]:
        print('  fallo:', item['name'][:70], '->', item['error_info'].get('message'))
    status = call('/api/status')
    print(f"biblioteca: {status['document_count']} documentos, {sum(item['indexed'] for item in status['documents'])} indexados, "
          f"{status['chunk_count']} fragmentos", flush=True)
    return 0 if state['status'] == 'done' else 1


if __name__ == '__main__':
    sys.exit(main())
