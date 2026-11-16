"""Lossless CTI observations and explicitly documented relationships, no inference."""
from collections import Counter, defaultdict
import hashlib
import json
from pathlib import Path
import re
import unicodedata

TYPES = {'intrusion-set', 'threat-actor', 'attack-pattern', 'malware', 'tool', 'campaign'}
PUBLIC = re.compile(r'(?:G\d{4}|T\d{4}(?:\.\d{3})?|S\d{4}|C\d{4})')
MITRE_NOTICE = '© 2026 The MITRE Corporation. This work is reproduced and distributed with the permission of The MITRE Corporation.'


def alias(value):
    return re.sub(r'[\W_]+', '', unicodedata.normalize('NFKC', str(value)).casefold())


def public_id(obj):
    for reference in obj.get('external_references', []):
        identifier = reference.get('external_id', '')
        if reference.get('source_name') == 'mitre-attack' and PUBLIC.fullmatch(identifier):
            return identifier
    return None


def references(obj):
    return [dict(row) for row in obj.get('external_references', []) if isinstance(row, dict)]


def stix(record):
    source, revision = record['source'], record['revision']
    name = (f'enterprise-attack/enterprise-attack-{record["attack_version"]}.json' if source == 'mitre'
            else 'data/stix/threat-actors.stix.json')
    artifact = next(row for row in record['artifacts'] if Path(row['path']).as_posix().endswith(name))
    bundle = json.loads(Path(artifact['path']).read_text('utf-8-sig'))
    if bundle.get('type') != 'bundle' or not isinstance(bundle.get('objects'), list):
        raise ValueError(f'{source}: no es un bundle STIX válido.')
    # Newest observation of one STIX id wins within this one source snapshot.
    objects = {}
    for obj in bundle['objects']:
        identifier = obj.get('id')
        if identifier and (identifier not in objects or obj.get('modified', '') > objects[identifier].get('modified', '')):
            objects[identifier] = obj
    rows = []
    for identifier, obj in objects.items():
        kind = obj.get('type')
        if kind not in TYPES | {'relationship'}:
            continue
        row = {'id': source + ':' + identifier, 'source': source, 'revision': revision,
               'stix_id': identifier, 'type': kind, 'name': obj.get('name', ''),
               'public_id': public_id(obj), 'aliases': list(obj.get('aliases', [])),
               'description': obj.get('description', ''), 'references': references(obj),
               'created': obj.get('created'), 'modified': obj.get('modified'),
               'attack_version': record.get('attack_version'),
               'active': not (obj.get('revoked', False) or obj.get('x_mitre_deprecated', False)),
               'original': obj, 'source_url': artifact['url'], 'source_sha256': artifact['sha256']}
        if kind == 'relationship':
            row.update(source_ref=source + ':' + obj.get('source_ref', ''),
                       target_ref=source + ':' + obj.get('target_ref', ''),
                       relationship_type=obj.get('relationship_type', ''))
        rows.append(row)
    if source == 'brandefense':
        apt_file = next(item for item in record['artifacts'] if Path(item['path']).name == 'apt-groups.json')
        supplements = json.loads(Path(apt_file['path']).read_text('utf-8-sig')).get('items', [])
        for row in rows:
            matching = [item for item in supplements if row['public_id'] and item.get('mitre_attack_id') == row['public_id']]
            if matching:
                row['supplement'] = matching
    return rows


def galaxy(record):
    rows = []
    allowed = {'threat-actor': 'intrusion-set', 'mitre-intrusion-set': 'intrusion-set',
               'mitre-enterprise-attack-intrusion-set': 'intrusion-set',
               'mitre-attack-pattern': 'attack-pattern', 'mitre-enterprise-attack-attack-pattern': 'attack-pattern',
               'mitre-malware': 'malware', 'mitre-enterprise-attack-malware': 'malware',
               'mitre-tool': 'tool', 'mitre-enterprise-attack-tool': 'tool',
               'mitre-campaign': 'campaign', 'mitre-enterprise-attack-campaign': 'campaign',
               'malpedia': 'malware', 'ransomware': 'malware', 'tool': 'tool'}
    for artifact in record['artifacts']:
        path = Path(artifact['path'])
        if path.parent.name != 'clusters' or path.stem not in allowed:
            continue
        cluster = json.loads(path.read_text('utf-8-sig'))
        for obj in cluster.get('values', []):
            identifier = obj.get('uuid')
            if not identifier:
                continue
            meta = obj.get('meta', {})
            external = meta.get('external_id', '')
            if not isinstance(external, str) or not PUBLIC.fullmatch(external):
                external = None
            row = {'id': 'misp:' + identifier, 'source': 'misp', 'revision': record['revision'],
                   'stix_id': None, 'type': allowed[path.stem], 'name': obj.get('value', ''),
                   'public_id': external, 'aliases': list(meta.get('synonyms', [])),
                   'description': obj.get('description', ''),
                   'references': [{'url': url} for url in meta.get('refs', []) if isinstance(url, str)],
                   'created': None, 'modified': obj.get('modified'),
                   'active': not (obj.get('revoked', False) or obj.get('deprecated', False)
                                  or meta.get('revoked', False) or meta.get('x_mitre_deprecated', False)),
                   'original': obj, 'source_url': artifact['url'], 'source_sha256': artifact['sha256']}
            rows.append(row)
            for relation in obj.get('related', []):
                target = relation.get('dest-uuid')
                if not target:
                    continue
                identity = hashlib.sha256((identifier + ':' + relation.get('type', '') + ':' + target).encode()).hexdigest()
                rows.append({**row, 'id': 'misp:relationship:' + identity, 'type': 'relationship',
                             'name': '', 'public_id': None, 'aliases': [], 'original': relation,
                             'description': relation.get('description', ''),
                             'source_ref': row['id'], 'target_ref': 'misp:' + target,
                             'relationship_type': relation.get('type', '')})
    # One UUID may be represented in multiple galaxy exports; do not count it twice.
    return list({row['id']: row for row in rows}.values())


def normalize(snapshots):
    rows = []
    for record in snapshots:
        rows.extend(galaxy(record) if record['source'] == 'misp' else stix(record))
    entities = {row['id']: row for row in rows if row['type'] != 'relationship'}
    unresolved, inactive_edges = 0, 0
    for row in rows:
        if row['type'] == 'relationship':
            endpoints = [entities.get(row['source_ref']), entities.get(row['target_ref'])]
            row['resolved'] = all(endpoints)
            if not row['resolved']:
                unresolved += 1
            row['active'] = bool(row['active'] and all(node and node['active'] for node in endpoints))
            if not row['active']:
                inactive_edges += 1
        # Identity only follows an explicit public identifier, never a matching alias.
        row['canonical_id'] = row['public_id'] or row['id']
        identity = hashlib.sha256(row['id'].encode()).hexdigest()[:10]
        stem = row['public_id'] or row['source'] + '_' + identity
        label = re.sub(r'[^a-zA-Z0-9.-]+', '_', row['name']).strip('_')[:70]
        if row['type'] == 'relationship' and row.get('resolved'):
            left, right = entities[row['source_ref']], entities[row['target_ref']]
            stem = (left['public_id'] or left['id'].split(':')[-1][-12:]) + '_' + (right['public_id'] or right['id'].split(':')[-1][-12:])
        row['document'] = ('relationships/' if row['type'] == 'relationship' else
                           'groups/' if row['type'] in {'intrusion-set', 'threat-actor'} else
                           'techniques/' if row['type'] == 'attack-pattern' else row['type'] + '/') + stem + '_' + label + '_' + identity + '.md'
    return rows, {'observations': len(rows), 'types': dict(Counter(row['type'] for row in rows)),
                  'sources': dict(Counter(row['source'] for row in rows)),
                  'active_entities': sum(row['active'] for row in entities.values()),
                  'active_relationships': sum(row['type'] == 'relationship' and row['active'] for row in rows),
                  'unresolved_relationships': unresolved, 'inactive_relationships': inactive_edges}


def markdown(row, entities, outgoing, incoming):
    front = {key: row.get(key) for key in ('id', 'stix_id', 'public_id', 'type', 'name', 'source', 'revision', 'attack_version', 'modified')}
    # JSON is valid YAML and quotes every source-provided frontmatter value.
    lines = ['---', json.dumps(front, ensure_ascii=False), '---', '', '# ' + (row['name'] or row.get('relationship_type', '')), '']
    if row['aliases']:
        lines += ['Aliases: ' + ', '.join(map(str, row['aliases'])), '']
    if row.get('public_id'):
        lines += ['MITRE ATT&CK: ' + row['public_id'], '']
    lines += [row['description'], '']
    if row['type'] == 'relationship':
        left, right = entities[row['source_ref']], entities[row['target_ref']]
        lines[4] = f'# {left["public_id"] or left["name"]} → {row["relationship_type"]} → {right["public_id"] or right["name"]}'
        lines += ['## Relación documentada',
                  f'{left["public_id"] or left["id"]} · {left["name"]} → {row["relationship_type"]} → {right["public_id"] or right["id"]} · {right["name"]}', '']
    else:
        for title, edges, field in [('Relaciones documentadas salientes', outgoing.get(row['id'], []), 'target_ref'),
                                     ('Relaciones documentadas entrantes', incoming.get(row['id'], []), 'source_ref')]:
            if edges:
                lines += ['## ' + title]
            for edge in edges:
                other = entities[edge[field]]
                lines += [f'- {edge["relationship_type"]}: {other["public_id"] or other["id"]} · {other["name"]}']
                if edge['description']:
                    lines += ['  ' + edge['description']]
    lines += ['', '## Referencias', '- Dataset original: ' + row['source_url']]
    if row.get('supplement'):
        lines += ['## Datos adicionales de Brandefense', json.dumps(row['supplement'], ensure_ascii=False), '']
    for reference in row['references']:
        if reference.get('url'):
            lines += ['- ' + reference['url']]
        if reference.get('description'):
            lines += ['  ' + reference['description']]
    lines += ['', '## Procedencia y licencia', f'Fuente: {row["source"]}. Commit: {row["revision"]}. Modificado: {row["modified"] or "no indicado"}.']
    if row['source'] == 'mitre':
        lines += [MITRE_NOTICE, 'Licencia y atribuciones completas: LICENSES.md.']
    elif row['source'] == 'brandefense':
        lines += ['Brandefense Research Team — CC BY 4.0. Documento derivado del dataset original; véase LICENSES.md.']
    else:
        lines += ['MISP Galaxy contributors — CC0 1.0 (JSON). Véase LICENSES.md.']
    return '\n'.join(lines) + '\n'


def export(rows, folder, snapshots, *, cancelled=lambda: False, observer=lambda **kw: None):
    folder.mkdir(parents=True, exist_ok=True)
    entities = {row['id']: row for row in rows if row['type'] != 'relationship'}
    outgoing, incoming = defaultdict(list), defaultdict(list)
    for edge in rows:
        if edge['type'] == 'relationship' and edge['active']:
            outgoing[edge['source_ref']].append(edge)
            incoming[edge['target_ref']].append(edge)
    expected, changed = set(), 0
    completed = 0
    total = sum(bool(row['active']) for row in rows)
    for row in rows:
        if not row['active']:
            continue
        if cancelled():
            raise InterruptedError('Preparación de documentos cancelada.')
        target = folder / row['document']
        target.parent.mkdir(parents=True, exist_ok=True)
        content = markdown(row, entities, outgoing, incoming)
        expected.add(row['document'])
        if not target.is_file() or target.read_text('utf-8') != content:
            temporary = target.with_suffix('.pending')
            temporary.write_text(content, 'utf-8')
            temporary.replace(target)
            changed += 1
        completed += 1
        if completed % 250 == 0 or completed == total:
            observer(completed=completed, total=total)
    licenses = ['# Fuentes y licencias CTI', '', 'Los registros normalizados mantienen la procedencia de cada observación.', '']
    for snapshot in snapshots:
        for artifact in snapshot['artifacts']:
            if Path(artifact['path']).name in {'LICENSE', 'LICENSE.txt', 'LICENSE.md'}:
                licenses += ['## ' + snapshot['source'], artifact['url'], '', Path(artifact['path']).read_text('utf-8'), '']
    (folder / 'LICENSES.md').write_text('\n'.join(licenses), 'utf-8')
    return {'documents': len(expected) + 1, 'changed_documents': changed, 'expected': sorted(expected | {'LICENSES.md'})}


def sample_documents(rows, maximum):
    """Stable, bounded semantic corpus; the complete graph remains structured."""
    priority = {'intrusion-set': 0, 'threat-actor': 0, 'attack-pattern': 1, 'campaign': 2,
                'malware': 3, 'tool': 3, 'relationship': 4}
    sources = {'mitre': 0, 'brandefense': 1, 'misp': 2}
    active = [row for row in rows if row['active']]
    return sorted(active, key=lambda row: (priority[row['type']], sources[row['source']],
                                         row['public_id'] or row['name'], row['id']))[:maximum]
