"""Run a record plan against the published snapshots, for any profile.

SQL is assembled only from names declared in a validated profile (views, columns, aliases);
every value from a question travels as a parameter. The views are the store's `current_*`
views, so staged or failed snapshots are never visible. Nothing here knows a domain.
"""
from __future__ import annotations

import json

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
OPERATORS = {'eq': '=', 'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<='}


def _joins(record_set):
    parts = []
    for alias, table in record_set['tables'].items():
        source = f"(SELECT * FROM {table['view']} GROUP BY {table['key']})" if table.get('one_per_key') else table['view']
        parts.append(f" LEFT JOIN {source} {alias} ON {alias}.{table['key']} = ids.id")
    return ''.join(parts)


def _select(record_set):
    columns = [f'{column} AS {name}' for name, column in record_set.get('columns', {}).items()]
    columns += [f"{alias}.{record_set['tables'][alias]['key']} IS NOT NULL AS {name}" for name, alias in record_set.get('presence', {}).items()]
    return 'ids.id AS id' + ''.join(', ' + column for column in columns)


def _visible(record_set):
    rule = record_set.get('visible_when')
    if not rule:
        return [], []
    clause = f"{rule['column']} = ?"
    return [f"({rule['column']} IS NULL OR {clause})" if rule.get('or_missing') else clause], [rule['equals']]


def _in_view(spec, values, prefix=False):
    view = spec['in']
    if prefix:
        match = ' OR '.join('(x.{0} = ? OR x.{0} LIKE ?)'.format(view['column']) for _ in values)
        parameters = [item for value in values for item in (value, str(value).replace('%', '').replace('_', '') + ':%')]
    else:
        match = f"x.{view['column']} IN ({','.join('?' * len(values))})"
        parameters = list(values)
    return f"EXISTS (SELECT 1 FROM {view['view']} x WHERE x.{view['key']} = ids.id AND ({match}))", parameters


def _conditions(plan, record_set):
    """(clauses, parameters, tables that must have a row) for the plan's conditions and ordering."""
    fields = record_set['fields']
    clauses, values = _visible(record_set)
    required = []

    def table(column):
        required.append(column.split('.')[0])

    for item in plan.conditions:
        spec = fields[item.field]
        kind = spec['type']
        if kind == 'flag' and 'present_in' in spec:
            alias = spec['present_in']
            clauses.append(f"{alias}.{record_set['tables'][alias]['key']} IS {'NOT ' if item.value else ''}NULL")
            if item.value:
                required.append(alias)
        elif kind == 'flag':
            clauses.append(f"{spec['column']} = 1" if item.value else f"({spec['column']} = 0 OR {spec['column']} IS NULL)")
            if item.value:
                table(spec['column'])
        elif kind == 'number':
            clauses.append(f"{spec['column']} {OPERATORS[item.op]} ?")
            values.append(float(item.value))
            table(spec['column'])
        elif kind == 'date':
            clauses.append(f"substr({spec['column']}, 1, 10) {OPERATORS[item.op]} ?")
            values.append(str(item.value))
            table(spec['column'])
        elif kind == 'identifiers':
            clause, parameters = _in_view(spec, list(item.value))
            clauses.append(clause)
            values += parameters
        elif kind == 'vocabulary':
            clauses.append(f"instr({spec['column']}, ?) > 0" if item.op == 'contains' else f"{spec['column']} = ?")
            values.append(str(item.value))
            table(spec['column'])
        elif kind == 'enum' and 'in' in spec:
            clause, parameters = _in_view(spec, [item.value], prefix=spec['in'].get('prefix', False))
            clauses.append(clause)
            values += parameters
        elif kind == 'enum':
            for constraint in spec['values'][item.value]['where']:
                column = constraint['column']
                table(column)
                for op in ('ge', 'le', 'gt', 'lt', 'eq'):
                    if op in constraint:
                        clauses.append(f'{column} {OPERATORS[op]} ?')
                        values.append(constraint[op])
                if 'prefix' in constraint:
                    clauses.append('(' + ' OR '.join(f'{column} LIKE ?' for _ in constraint['prefix']) + ')')
                    values += [prefix.replace('%', '').replace('_', '') + '%' for prefix in constraint['prefix']]
        else:
            raise ValueError(f'Unsupported condition on {item.field}')
    order = ' ORDER BY ids.id DESC'
    if plan.order:
        column = fields[plan.order[0]]['column']
        clauses.append(f'{column} IS NOT NULL')     # ranking only considers records that have the value
        table(column)
        order = f" ORDER BY {'substr(' + column + ', 1, 10)' if fields[plan.order[0]]['type'] == 'date' else column}" \
                f" {'ASC' if plan.order[1] == 'asc' else 'DESC'}, ids.id DESC"
    return clauses, values, required, order


def _universe(record_set, required):
    """Identifiers to consider: one required table when there is one, otherwise all of them."""
    tables = record_set['tables']
    if required:
        alias = required[0]
        return f"(SELECT {tables[alias]['key']} AS id FROM {tables[alias]['view']})"
    return '(' + ' UNION '.join(f"SELECT {table['key']} AS id FROM {table['view']}" for table in tables.values()) + ')'


def provenance(db):
    """Published snapshot of every source, to cite the data and its date."""
    return [dict(row) for row in db.execute(
        '''SELECT sources.id AS source_id, sources.name, sources.canonical_url, sources.license_name,
                  snapshots.id AS snapshot_id, snapshots.fetched_at, snapshots.source_updated_at
           FROM sources JOIN snapshots ON snapshots.id = sources.published_snapshot_id ORDER BY sources.id''')]


def vocabulary(store, record_set):
    """Published values of the vocabulary fields: parent value -> child values (for example vendor -> products)."""
    fields = record_set['fields']
    parent = next((spec for spec in fields.values() if spec['type'] == 'vocabulary' and not spec.get('parent')), None)
    if parent is None:
        return {}
    child = next((spec for spec in fields.values() if spec['type'] == 'vocabulary' and spec.get('parent')), None)
    alias, column = parent['column'].split('.')
    second = child['column'].split('.')[1] if child and child['column'].split('.')[0] == alias else None
    result = {}
    with store.connect() as db:
        query = f"SELECT DISTINCT {column}{', ' + second if second else ''} FROM {record_set['tables'][alias]['view']} ORDER BY 1"
        for row in db.execute(query):
            result.setdefault(row[0], [])
            if second and row[1] is not None:
                result[row[0]].append(row[1])
    return result


def summary(store, record_set):
    """What is published, for the status card: sources with the date of their data, and row counts per view."""
    with store.connect() as db:
        sources = provenance(db)
        counts = {table['view'].removeprefix('current_'): db.execute(f"SELECT COUNT(*) FROM {table['view']}").fetchone()[0]
                  for table in record_set['tables'].values()} if sources else {}
    return {'available': bool(sources), 'counts': counts,
            'sources': [{'id': source['source_id'], 'name': source['name'], 'license': source['license_name'],
                         'data_date': (source.get('source_updated_at') or source['fetched_at'])[:10],
                         'fetched_at': source['fetched_at']} for source in sources]}


def _details(db, record_set, identifiers):
    """Rows of the detail views (lists that belong to a record) for a few identifiers."""
    extra = {identifier: {name: [] for name in record_set.get('details', {})} for identifier in identifiers}
    if not identifiers:
        return extra
    marks = ','.join('?' * len(identifiers))
    for name, detail in record_set.get('details', {}).items():
        columns = ', '.join(detail['columns'])
        for row in db.execute(f"SELECT DISTINCT {detail['key']}, {columns} FROM {detail['view']} WHERE {detail['key']} IN ({marks}) ORDER BY 1, 2",
                              identifiers):
            items = extra[row[0]][name]
            if len(items) < detail.get('limit', 10):
                values = [json.loads(value or 'null') if column.endswith('_json') else value for column, value in zip(detail['columns'], row[1:])]
                items.append(values[0] if len(values) == 1 else dict(zip(detail['columns'], values)))
    return extra


def search(store, plan, record_set, *, default_limit=DEFAULT_LIMIT):
    """Rows for a plan. The result always states how many matched, not only how many are returned."""
    joins, select = _joins(record_set), _select(record_set)
    with store.connect() as db:
        sources = provenance(db)
        if plan.mode == 'lookup':
            identifiers = list(plan.ids)
            listed = ' UNION ALL '.join('SELECT ? AS id' for _ in identifiers)
            present = ' OR '.join(f"{alias}.{table['key']} IS NOT NULL" for alias, table in record_set['tables'].items())
            rows = {row['id']: dict(row) for row in db.execute(f'SELECT {select} FROM ({listed}) ids{joins} WHERE {present}', identifiers)}
            extra = _details(db, record_set, list(rows))
            records = [{**rows[identifier], **extra[identifier]} for identifier in identifiers if identifier in rows]
            return {'mode': 'lookup', 'records': records, 'total': len(records),
                    'missing': [identifier for identifier in identifiers if identifier not in rows], 'sources': sources}
        clauses, values, required, order = _conditions(plan, record_set)
        base = f' FROM {_universe(record_set, required)} ids{joins}'
        where = (' WHERE ' + ' AND '.join(clauses)) if clauses else ''
        total = db.execute(f'SELECT COUNT(*){base}{where}', values).fetchone()[0]
        if plan.aggregate:
            if plan.aggregate[0] == 'count':
                return {'mode': 'aggregate', 'total': total, 'groups': [], 'sources': sources}
            name, _, unit = plan.aggregate[1].partition(':')
            spec = record_set['fields'][name]
            join = ''
            if unit:
                key = label = f"substr({spec['column']}, 1, {4 if unit == 'year' else 7})"
            elif 'in' in spec:
                join = f" JOIN {spec['in']['view']} g ON g.{spec['in']['key']} = ids.id"
                key = label = f"g.{spec['in']['column']}"
            else:
                key, label = spec['column'], f"MIN({spec.get('label_column', spec['column'])})"
            filtered = where + (' AND ' if where else ' WHERE ') + f'{key} IS NOT NULL'
            groups = [dict(row) for row in db.execute(
                f'SELECT {key} AS key, {label} AS label, COUNT(DISTINCT ids.id) AS count{base}{join}{filtered} '
                f'GROUP BY {key} ORDER BY count DESC, key ASC', values)]
            return {'mode': 'aggregate', 'total': total, 'groups': groups, 'sources': sources}
        limit = max(1, min(int(plan.limit or default_limit), MAX_LIMIT))
        rows = [dict(row) for row in db.execute(f'SELECT {select}{base}{where}{order} LIMIT ?', [*values, limit])]
        extra = _details(db, record_set, [row['id'] for row in rows])
        return {'mode': 'filter', 'records': [{**row, **extra[row['id']]} for row in rows], 'total': total,
                'limit': limit, 'sources': sources}
