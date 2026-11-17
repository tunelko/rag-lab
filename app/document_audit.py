"""Read-only, per-document integrity audit of local sources, vectors and graphs."""
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3

import numpy as np

from file_fingerprints import sha256_file
from graph_store import GraphStore
import rag


def audit(service, job, services):
    report = {'started': datetime.now(timezone.utc).isoformat(), 'collections': [], 'files': [], 'issues': [],
              'checked_documents': 0, 'total_documents': 0, 'checked_vectors': 0, 'checked_units': 0,
              'checked_assertions': 0, 'duplicate_groups': [],
              'source_assessments': {'supported': 0, 'unsupported': 0, 'uncertain': 0, 'pending': 0, 'accepted': 0, 'rejected': 0},
              'scope': 'Integridad técnica y trazabilidad. No certifica la veracidad de todo el contenido ni la inferencia semántica.'}
    selections = []
    for selected in services:
        paths = sorted(path for path in selected.documents.rglob('*') if path.is_file() and path.name != '.gitkeep')
        selections.append((selected, paths))
        report['total_documents'] += len(paths)
    for selected, paths in selections:
        key = getattr(selected, 'collection_key', 'default')
        state = rag.load_state(selected.config)
        documents, duplicate = {}, defaultdict(list)
        # Both sides resolved: a folder reached through a link or a Windows short name is still the collection.
        base = selected.documents.resolve()
        pdf_texts = {}
        def issue(file, message):
            report['issues'].append({'collection': key, 'file': file, 'message': message})
            if file in documents:
                documents[file]['status'] = 'requires_review'
                if message not in documents[file]['issues']:
                    documents[file]['issues'].append(message)
        for path in paths:
            service.check_cancel(job)
            name = path.relative_to(selected.documents).as_posix()
            item = {'collection': key, 'file': name, 'status': 'valid', 'issues': [], 'pages': None}
            try:
                if not path.resolve().is_relative_to(base):
                    raise ValueError('La ruta sale de la colección.')
                digest = sha256_file(path)
                item['sha256'] = digest
                duplicate[digest].append(name)
                if path.stat().st_size == 0:
                    raise ValueError('Archivo vacío.')
                if path.suffix.lower() not in {'.pdf', '.txt', '.md', '.markdown'}:
                    raise ValueError('Formato sin verificador documental; requiere revisión: ' + path.suffix)
                if path.suffix.lower() == '.pdf':
                    import pymupdf
                    with pymupdf.open(path) as pdf:
                        if pdf.needs_pass or len(pdf) == 0:
                            raise ValueError('PDF cifrado o sin páginas accesibles.')
                        texts = [page.get_text() for page in pdf]
                    item['pages'] = len(texts)
                    item['text_pages'] = sum(bool(text.strip()) for text in texts)
                    if not item['text_pages']:
                        item['issues'].append('Sin texto extraíble; requiere OCR o revisión visual.')
                    pdf_texts[name] = texts
                else:
                    text = path.read_text('utf-8-sig')
                    if not text.strip() or '\0' in text:
                        raise ValueError('Texto vacío o con caracteres NUL.')
                indexed = state['files'].get(str(path.resolve()))
                item['index'] = 'published' if indexed else 'not_indexed'
                if indexed and (indexed['sha256'] != digest or not indexed.get('ids')):
                    item['issues'].append('Índice desactualizado o sin fragmentos publicados.')
                if sha256_file(path) != digest:
                    item['issues'].append('El archivo cambió durante la revisión.')
            except (OSError, RuntimeError, ValueError) as error:
                item['issues'].append(str(error))
            if item['issues']:
                item['status'] = 'requires_review'
                for message in item['issues']:
                    issue(name, message)
            documents[name] = item
            report['files'].append(item)
            report['checked_documents'] += 1
            if report['checked_documents'] % 100 == 0 or len(paths) < 100:
                service.update(job, phase=f'Verificando documentos · {report["checked_documents"]}/{report["total_documents"]}',
                    progress=80 * report['checked_documents'] / max(1, report['total_documents']))
        for group in duplicate.values():
            if len(group) > 1:
                report['duplicate_groups'].append({'collection': key, 'files': group})
        expected = {}
        for record in state['files'].values():
            name = record.get('file', '')
            if name not in documents:
                issue(name, 'Documento publicado ausente del corpus revisado.')
            expected.update((identity, (name, record['sha256'])) for identity in record.get('ids', []))
        vector_path = selected.config.db / f'{selected.config.collection}-vectors.sqlite3'
        if vector_path.is_file():
            with closing(sqlite3.connect(vector_path.absolute().as_uri() + '?mode=ro', uri=True)) as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                    issue('', 'Fallo de integridad SQLite del índice vectorial.')
                dimension = db.execute("SELECT value FROM info WHERE key='dimensions'").fetchone()
                seen = set()
                for identity, name, text, metadata, vector in db.execute('SELECT id,file,text,metadata,vector FROM vectors'):
                    service.check_cancel(job)
                    if identity not in expected:
                        continue  # Unpublished staged/obsolete vectors are not query evidence.
                    seen.add(identity)
                    try:
                        meta = json.loads(metadata)
                        if (name, meta.get('sha256')) != expected[identity] or meta.get('file') != name or not text.strip():
                            raise ValueError('Fragmento inconsistente con su documento o versión.')
                        numbers = np.frombuffer(vector, dtype=np.float32)
                        if not dimension or len(numbers) != int(dimension[0]) or not np.isfinite(numbers).all():
                            raise ValueError('Vector con dimensión incorrecta o valores no finitos.')
                        if name in pdf_texts and not 1 <= int(meta.get('page', 0)) <= len(pdf_texts[name]):
                            raise ValueError('Página de fragmento fuera del PDF.')
                    except (ValueError, TypeError) as error:
                        issue(name, str(error))
                    report['checked_vectors'] += 1
                if expected.keys() - seen:
                    issue('', f'{len(expected.keys() - seen)} fragmentos publicados no existen en SQLite.')
        elif expected:
            issue('', 'Falta el índice vectorial publicado.')
        store = GraphStore(selected.config.db.parent / 'graph.sqlite3')
        if store.path.is_file():
            with store.connection() as db:
                if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok' or db.execute('PRAGMA foreign_key_check').fetchone():
                    issue('', 'Fallo de integridad o referencias en el grafo.')
                units = {row['id']: dict(row) for row in db.execute('SELECT * FROM graph_units WHERE active=1')}
                source_texts = {}
                for unit in units.values():
                    service.check_cancel(job)
                    name = unit['file']
                    try:
                        document = documents[name]
                        if unit['version'] != document.get('sha256'):
                            raise ValueError('La evidencia del grafo pertenece a otra versión del documento.')
                        if name in pdf_texts:
                            if not isinstance(unit['page'], int) or not 1 <= unit['page'] <= len(pdf_texts[name]):
                                raise ValueError('Página de evidencia fuera del documento.')
                            text = pdf_texts[name][unit['page'] - 1]
                        else:
                            if name not in source_texts:
                                source_texts[name] = (selected.documents / name).read_text('utf-8-sig')
                            text = source_texts[name]
                        offset = json.loads(unit['metadata'])['page_offset']
                        if not isinstance(offset, int) or offset < 0:
                            raise ValueError('Posición de evidencia no válida.')
                        if text[offset:offset + len(unit['text'])] != unit['text']:
                            raise ValueError('El pasaje no coincide con la posición registrada en la fuente.')
                    except (KeyError, IndexError, TypeError, ValueError, OSError) as error:
                        issue(name, str(error))
                    report['checked_units'] += 1
                rows = db.execute('''SELECT a.*,s.unit AS subject_unit,o.unit AS object_unit,COALESCE(v.status,'pending') AS verification,r.status AS run_status
                    FROM graph_assertions a LEFT JOIN graph_entities s ON s.id=a.subject LEFT JOIN graph_entities o ON o.id=a.object
                    JOIN graph_runs r ON r.id=a.run
                    LEFT JOIN graph_verifications v ON v.id=(SELECT MAX(id) FROM graph_verifications WHERE assertion=a.id)''')
                for row in rows:
                    if row['unit'] not in units:
                        continue
                    unit = units[row['unit']]
                    if row['subject_unit'] != row['unit'] or row['object_unit'] != row['unit'] or unit['text'][row['start']:row['end']] != row['quote']:
                        issue(unit['file'], 'Relación con entidades o cita fuera de su unidad de evidencia.')
                    if row['run_status'] == 'done' and (row['run'] == unit['published_run'] or (row['review'] == 'accepted' and row['verification'] == 'supported')):
                        report['source_assessments'][row['verification']] += 1
                        if row['review'] in {'accepted', 'rejected'}:
                            report['source_assessments'][row['review']] += 1
                        if row['review'] == 'accepted' and row['verification'] != 'supported':
                            issue(unit['file'], 'Relación aceptada sin contraste favorable de la fuente.')
                    report['checked_assertions'] += 1
        current = {path.relative_to(selected.documents).as_posix() for path in selected.documents.rglob('*')
                   if path.is_file() and path.name != '.gitkeep'}
        if current != set(documents):
            issue('', 'El corpus cambió durante la revisión; repite la comprobación.')
        report['collections'].append({'id': key, 'documents': len(documents)})
    report['completed'] = datetime.now(timezone.utc).isoformat()
    report['status'] = 'requires_review' if report['issues'] or report['duplicate_groups'] else 'passed'
    root = service.collection_pool.default.config.db.parent / 'audits'
    root.mkdir(parents=True, exist_ok=True)
    path = root / ('documents-' + report['completed'].replace(':', '-') + '.json')
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), 'utf-8')
    pending = root / 'latest.json.pending'
    pending.write_text(json.dumps(report, ensure_ascii=False), 'utf-8')
    pending.replace(root / 'latest.json')
    return {'audit': {key: value for key, value in report.items() if key != 'files'}, 'message': 'Verificación documental terminada.'}
