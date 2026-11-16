"""Dataset imports are platform jobs; adapters feed the regular document index."""
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil

import cti_records
import cti_sources
import rag
from index_lock import index_writer
from file_fingerprints import signature
from knowledge_store import KnowledgeStore

ADAPTER_POLICY = 'cti-v1'


def synchronize_cti(service, job_id, version, *, raw_root=None, preferences=None, max_documents=5000):
    root = Path(raw_root) if raw_root else Path(__file__).resolve().parents[1] / 'datasets' / 'cti'
    raw = root
    key = getattr(service, 'collection_key', 'cti')
    if key != 'cti':
        root = root / 'collections' / key
    receipt_path = service.config.db.parent / 'dataset-status.json'
    previous = json.loads(receipt_path.read_text('utf-8')) if receipt_path.is_file() else {}
    receipt = {**previous, 'status': 'running', 'attack_version': version, 'max_documents': max_documents,
               'started_at': datetime.now(timezone.utc).isoformat()}
    receipt.pop('error', None)
    cti_sources.write_json(receipt_path, receipt)
    cancelled = lambda: service.cancelled(job_id)

    def observe(phase, **counts):
        service.check_cancel(job_id)
        service.update(job_id, phase=phase, stage='chunking', cancellable=True,
                       download_bytes=counts.get('bytes'), download_total=counts.get('total_bytes'))

    try:
        snapshots = []
        for number, source in enumerate(cti_sources.REPOSITORIES):
            service.update(job_id, progress=number * 5)
            snapshots.append(cti_sources.snapshot(raw, source, version, cancelled, observe))
        observe('Resolviendo objetos y relaciones documentadas')
        previous_rows = {}
        export_path = root / 'exports' / 'observations.jsonl'
        if export_path.is_file():
            with export_path.open(encoding='utf-8') as stream:
                previous_rows = {row['id']: row for line in stream if (row := json.loads(line))}
        same_sources = ({row['source']: row['revision'] for row in snapshots}
                        == {row['source']: row['revision'] for row in previous.get('snapshots', [])})
        reused = bool(previous.get('status') == 'done' and previous_rows and same_sources
                      and previous.get('attack_version') == version
                      and previous.get('adapter_policy', ADAPTER_POLICY) == ADAPTER_POLICY
                      and (root / 'exports' / 'markdown' / 'LICENSES.md').is_file())
        if reused:
            rows, statistics = list(previous_rows.values()), previous['statistics']
        else:
            rows, statistics = cti_records.normalize(snapshots)
        for row in rows:
            old = previous_rows.get(row['id'])
            if (old and old['original'] == row['original'] and old.get('supplement') == row.get('supplement')
                    and old.get('attack_version') == row.get('attack_version')):
                for field in ('revision', 'source_url', 'source_sha256'):
                    row[field] = old[field]
        service.update(job_id, progress=20, phase='Preparando documentos CTI y referencias')
        document_root = service.documents / 'cti'
        evidence_root = root / 'exports' / 'markdown'
        if document_root.is_dir() and previous.get('layout') != 'sample-v1':
            if not document_root.resolve().is_relative_to(service.documents.resolve()):
                raise ValueError('Ruta de documentos CTI no válida.')
            evidence_root.parent.mkdir(parents=True, exist_ok=True)
            if not evidence_root.exists():
                # The cancelled first preparation created evidence files before
                # indexing. Keep them as exports, outside the semantic library.
                shutil.move(str(document_root), str(evidence_root))
        if reused:
            exported = {**previous['export'], 'changed_documents': 0}
        else:
            exported = cti_records.export(rows, evidence_root, snapshots, cancelled=cancelled,
                observer=lambda **counts: service.update(job_id,
                    phase=f'Preparando evidencias · {counts["completed"]}/{counts["total"]}',
                    progress=20 + 10 * counts['completed'] / max(1, counts['total'])))
            exported.pop('expected')
        selected = cti_records.sample_documents(rows, max_documents)
        expected = {row['document'] for row in selected}
        inventory = {}
        for number, row in enumerate(selected):
            service.check_cancel(job_id)
            source, target = evidence_root / row['document'], document_root / row['document']
            target.parent.mkdir(parents=True, exist_ok=True)
            if not target.is_file() or cti_sources.digest(source) != cti_sources.digest(target):
                shutil.copyfile(source, target)
            stat = target.stat()
            inventory['cti/' + row['document']] = {'source': str(target.resolve()),
                'sha256': cti_sources.digest(target), 'bytes': stat.st_size,
                'signature': list(signature(stat)),
                'updated_at': datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()}
            if number % 250 == 0:
                service.update(job_id, phase=f'Preparando muestra semántica · {number}/{len(selected)}',
                               progress=30 + 5 * number / max(1, len(selected)))
        license_root = service.config.db.parent / 'licenses'
        license_root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(evidence_root / 'LICENSES.md', license_root / 'LICENSES.md')
        cti_sources.write_json(service.config.db.parent / 'managed-documents.json', inventory)
        # Removed/revoked observations leave the main library, with their original
        # files preserved in a dated archive and raw/JSONL historical snapshots.
        archive = root / 'history' / receipt['started_at'].replace(':', '-')
        for path in document_root.rglob('*.md'):
            relative = path.relative_to(document_root).as_posix()
            if relative not in expected:
                target = archive / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(path), str(target))
        export_path.parent.mkdir(parents=True, exist_ok=True)
        if not reused:
            temporary = export_path.with_suffix('.jsonl.pending')
            with temporary.open('w', encoding='utf-8', newline='\n') as output:
                for row in rows:
                    output.write(json.dumps(row, ensure_ascii=False) + '\n')
            # Keep a versioned export only when observations actually change.
            history = root / 'history' / receipt['started_at'].replace(':', '-')
            history.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(temporary, history / 'observations.jsonl')
            temporary.replace(export_path)
        topics = service.topics.snapshot([])['topics']
        parent = next((row['id'] for row in topics if row['name'] == 'CTI' and not row['parent_id']), None)
        if parent is None:
            parent = service.topics.create('CTI')
        children = {}
        for folder in {Path(name).parts[0] for name in expected if '/' in name}:
            child = next((row['id'] for row in topics if row['name'] == folder and row['parent_id'] == parent), None)
            children[folder] = child or service.topics.create(folder, parent)
        groups = defaultdict(list)
        for name in expected:
            groups[Path(name).parts[0]].append('cti/' + name)
        for folder, names in groups.items():
            service.topics.assign(names, children.get(folder, parent))
        observe('Indexando CTI con los embeddings locales')
        current = rag.load_state(service.config)
        pending = frozenset(name for name, item in inventory.items()
                            if current['files'].get(item['source'], {}).get('sha256') != item['sha256']
                            or not current['files'].get(item['source'], {}).get('ids'))
        if pending:
            result = service.index(job_id, files=pending, preferences=preferences, progress_range=(35, 99))
            result['summary']['skipped_files'] += len(inventory) - len(pending)
        else:
            result = {'summary': {'indexed_files': 0, 'skipped_files': len(inventory), 'chunks': 0, 'errors': 0},
                      'documents': [], 'message': 'El índice ya contiene esta muestra.'}
            service.update(job_id, progress=99, summary=result['summary'])
        service.check_cancel(job_id)
        with index_writer(service.config):
            state = rag.load_state(service.config)
            removed = [path for path in state['files'] if Path(path).is_relative_to(document_root)
                       and Path(path).relative_to(document_root).as_posix() not in expected]
            stale = [identifier for path in removed for identifier in state['files'][path].get('ids', [])]
            if removed:
                for path in removed:
                    del state['files'][path]
                rag.save_state(service.config, state)
                if stale:
                    rag.open_store(service.config, None).delete(ids=stale)
        receipt.update(status='done', layout='sample-v1', adapter_policy=ADAPTER_POLICY,
                       completed_at=datetime.now(timezone.utc).isoformat(),
                       statistics=statistics, export=exported, snapshots=snapshots,
                       semantic_documents=len(selected), evidence_root=str(evidence_root),
                       indexing=result.get('summary', {}))
        receipt.pop('error', None)
        service.update(job_id, phase='Publicando las relaciones estructuradas', progress=99,
                       stage='indexing', cancellable=False)
        KnowledgeStore(service.config.db.parent / 'knowledge.sqlite3').publish(rows, receipt)
        cti_sources.write_json(receipt_path, receipt)
        service.update(job_id, phase='CTI sincronizado e indexado', progress=100, dataset=receipt)
        return {**result, 'dataset': receipt}
    except BaseException as error:
        receipt.update(status='cancelled' if isinstance(error, InterruptedError) else 'error', error=str(error))
        cti_sources.write_json(receipt_path, receipt)
        raise
