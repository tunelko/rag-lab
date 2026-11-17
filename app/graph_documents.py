"""Document batches and truthful, versioned graph checkpoints for the picker."""
from collections import defaultdict
import json

from graph_store import identifier

MAX_BATCH = 100


def check(done, total, *, current=True, known=True, not_required=False):
    if not current:
        state = 'stale'
    elif not known:
        state = 'model_required'
    elif not_required:
        state = 'not_required'
    elif total and done == total:
        state = 'verified'
    else:
        state = 'partial' if done else 'pending'
    return {'state': state, 'checked': state == 'verified', 'done': done, 'total': total}


def documents(service, *, provider='ollama', model='', offset=0, limit=100, eligible_only=False, files=None):
    from graph_jobs import POLICY, ExtractRequest, extraction_configuration, graph_store, selected_paths
    from graph_engine import POLICY as IDENTITY_POLICY, active_decisions, current_rows, identity_candidates

    store = graph_store(service)
    # The picker needs names and graph checkpoints, not vector manifests or a
    # fingerprint of every unprepared file on each poll of a 100-document batch.
    if files is None:
        names = [path.relative_to(service.documents).as_posix() for path in service.documents.rglob('*')
                 if path.suffix.lower() in {'.pdf', '.md', '.markdown', '.txt'} and path.is_file()
                 and path.resolve().is_relative_to(service.documents)]
    else:
        names = [name for name, _path in selected_paths(service, files)]
    inventory = [{'name': name} for name in sorted(names)]
    config = extraction_configuration(ExtractRequest(files=['selection'], provider=provider, model=model)) if model else None
    signature = config['signature'] if config else None
    if store.path.is_file():
        units, batches = store.checkpoint_inventory(signature)
    else:
        units, batches = [], []
    by_file, by_unit = defaultdict(list), defaultdict(list)
    for unit in units:
        by_file[unit['file']].append(unit)
    for batch in batches:
        by_unit[batch['unit']].append(batch)
    busy = set()
    for job in service.list_jobs(active=True)['jobs']:
        if job['kind'] != 'graph':
            continue
        busy.update(job.get('document_names', []))
        if job.get('graph_run'):
            try:
                busy.update(store.run(job['graph_run'])['config'].get('files', []))
            except ValueError:
                pass
    continuation = getattr(service, 'graph_continuation', None)
    for task in continuation.active_tasks() if continuation is not None else ():
        busy.update(task['config']['files'])

    entries = []
    for document in inventory:
        name = document['name']
        prepared = by_file.get(name, [])
        current, source_changed, source_version = False, False, None
        if prepared:
            path = (service.documents / name).resolve()
            try:
                source_version = service.fingerprints.digest(path) if path.is_relative_to(service.documents) else None
                current = all(row['version'] == source_version and json.loads(row['metadata']).get('parser') == POLICY
                              for row in prepared)
            except (OSError, ValueError, TypeError):
                current = False
            source_changed = not current
        extracted = assessed_units = published = claims = assessed = supported = 0
        resumable = set()
        if current and model:
            for unit in prepared:
                records = [row for row in by_unit[unit['id']] if row['batch_status'] == 'done']
                records.sort(key=lambda row: (row['claims'] == row['assessed'] and row['run_status'] == 'done',
                                             row['claims'] == row['assessed'], row['created']), reverse=True)
                if not records:
                    continue
                best = records[0]
                extracted += 1
                assessed_units += best['claims'] == best['assessed']
                published += best['run_status'] == 'done'
                claims += best['claims']
                assessed += best['assessed']
                supported += best['supported']
                resumable.update(row['run'] for row in records if row['run_status'] in {'error', 'cancelled', 'interrupted'})
        total = len(prepared)
        complete = bool(current and model and total and extracted == assessed_units == published == total)
        in_use = name in busy
        checks = {
            'preparation': check(total if current else 0, total, current=not source_changed),
            'extraction': check(extracted, total, current=not source_changed, known=bool(model)),
            'assessment': check(assessed, claims, current=not source_changed, known=bool(model),
                                not_required=bool(total and extracted == total and not claims)),
            'publication': check(published, total, current=not source_changed, known=bool(model)),
        }
        status = ('in_use' if in_use else 'complete' if complete else 'stale' if source_changed else
                  'partial' if extracted else 'prepared' if current else 'available')
        entries.append({'file': name, 'status': status, 'eligible': not in_use and not complete,
            'in_use': in_use, 'complete': complete, 'source_current': current, 'source_changed': source_changed,
            'source_version': source_version, 'units_total': total, 'units_extracted': extracted,
            'units_assessed': assessed_units, 'units_published': published, 'claims': claims,
            'claims_assessed': assessed, 'claims_supported': supported, 'checks': checks,
            'resumable_runs': sorted(resumable)})

    eligible = [row for row in entries if row['eligible']]
    visible = eligible if eligible_only else entries
    reconciliation_batch = []
    pending_connections = False
    connection_checks = 0
    modelled_sources = 0
    if model and store.path.is_file():
        # Suggest both endpoints, including already modelled documents. The
        # explicit reconciliation action can then connect disjoint source batches
        # without re-extraction or silently broadening remote transmission scope.
        allowed = {row['file'] for row in entries}
        source_rows = current_rows(service, allowed)
        modelled_sources = len({row['file'] for row in source_rows})
        decisions = store.identity_decisions()
        entities, pairs = identity_candidates(source_rows, decisions, MAX_BATCH // 2,
                                             identifier(IDENTITY_POLICY, provider, model))
        connection_checks = sum(1 for _ in active_decisions(entities, decisions))
        pending_connections = bool(pairs)
        reconciliation_batch = sorted({entities[key]['file'] for pair in pairs for key in pair})
    selection_checks = {}
    for key in ('preparation', 'extraction', 'assessment', 'publication'):
        stages = [row['checks'][key] for row in entries]
        covered = sum(stage['state'] in {'verified', 'not_required'} for stage in stages)
        checked = bool(stages) and covered == len(stages) and any(stage['checked'] for stage in stages)
        state = ('verified' if checked else 'not_required' if stages and all(stage['state'] == 'not_required' for stage in stages)
                 else 'partial' if covered else 'pending')
        selection_checks[key] = {'state': state, 'checked': checked, 'done': covered, 'total': len(stages)}
    return {'max_batch_size': MAX_BATCH, 'offset': offset, 'limit': limit,
        'checkpoint_scope': 'selection' if files is not None else 'collection',
        'total_documents': len(entries), 'total_eligible': len(eligible), 'total_visible': len(visible),
        'total_complete': sum(row['complete'] for row in entries), 'total_in_use': sum(row['in_use'] for row in entries),
        'documents': visible[offset:offset + limit], 'next_batch': [row['file'] for row in eligible[:MAX_BATCH]],
        'provider': provider, 'model': model, 'model_signature': signature,
        'reconciliation_batch': reconciliation_batch, 'reconciliation_pending': pending_connections,
        'reconciliation_checked': bool(model and connection_checks and not pending_connections),
        'reconciliation_state': ('model_required' if not model else 'pending' if pending_connections or
                                 (not modelled_sources and not any(row['units_extracted'] for row in entries))
                                 else 'verified' if connection_checks else 'not_required'),
        'reconciliation_scope': 'currently_modelled_sources',
        'selection_checks': selection_checks,
        'summary_checks': {key: sum(row['checks'][key]['checked'] for row in entries)
                           for key in ('preparation', 'extraction', 'assessment', 'publication')}}
