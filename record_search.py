"""Run a RecordPlan against the published snapshots of the knowledge store.

Every query is parameterised SQL over the `current_*` views, so staged or failed
snapshots are never visible. The model never writes or alters these queries: it
only receives their rows. Thresholds and dates are inclusive on both sides.

Where each condition comes from (first vertical): vendor, product and ransomware
use come from CISA KEV, so filtering by them returns only catalogued entries;
scores come from EPSS; publication date, CVSS and CWE from the CVE record.
Severity bands apply to CVSS 3.x and 4.0 only.
"""
from __future__ import annotations

import json

DEFAULT_LIMIT = 50
MAX_LIMIT = 200
SEVERITY = {'critical': (9.0, 10.0), 'high': (7.0, 8.9), 'medium': (4.0, 6.9), 'low': (0.1, 3.9)}
SORT = {'epss': 'e.score', 'cvss': 'v.cvss_base', 'published': 'v.published_at', 'kev_added': 'k.date_added'}
GROUPS = {
    'vendor': ('k.vendor_key', 'MIN(k.vendor)', ''),
    'cwe': ('w.cwe_id', 'w.cwe_id', ' JOIN current_vulnerability_weaknesses w ON w.cve_id = ids.cve_id'),
    'kev_added_year': ('substr(k.date_added, 1, 4)', 'substr(k.date_added, 1, 4)', ''),
    'kev_added_month': ('substr(k.date_added, 1, 7)', 'substr(k.date_added, 1, 7)', ''),
    'published_year': ('substr(v.published_at, 1, 4)', 'substr(v.published_at, 1, 4)', ''),
    'published_month': ('substr(v.published_at, 1, 7)', 'substr(v.published_at, 1, 7)', ''),
}
# One row per CVE even if several sources publish the same record.
VULNERABILITIES = '(SELECT * FROM current_vulnerabilities GROUP BY cve_id)'
JOINS = (f' LEFT JOIN {VULNERABILITIES} v ON v.cve_id = ids.cve_id'
         ' LEFT JOIN current_kev k ON k.cve_id = ids.cve_id'
         ' LEFT JOIN current_epss e ON e.cve_id = ids.cve_id')
COLUMNS = ('ids.cve_id AS cve_id, v.published_at, v.modified_at, v.status, v.description, v.cvss_base, v.cvss_version,'
           ' v.cvss_vector, v.cvss_origin, k.cve_id IS NOT NULL AS in_kev, k.date_added AS kev_date_added,'
           ' k.vendor AS kev_vendor, k.product AS kev_product, k.ransomware_known AS kev_ransomware,'
           ' k.required_action AS kev_required_action, k.due_date AS kev_due_date,'
           ' e.score AS epss_score, e.percentile AS epss_percentile, e.score_date AS epss_date')
UNIVERSE = ('(SELECT cve_id FROM current_kev UNION SELECT cve_id FROM current_vulnerabilities'
            ' UNION SELECT cve_id FROM current_epss)')


def _conditions(filters):
    """WHERE clauses and parameters for a filter; unknown keys are an error, never ignored."""
    known = {'kev', 'ransomware', 'epss_min', 'epss_max', 'percentile_min', 'percentile_max', 'cvss_min', 'cvss_max',
             'severity', 'published_from', 'published_to', 'kev_added_from', 'kev_added_to', 'cwe', 'vendor', 'product',
             'ecosystem', 'sort', 'order', 'limit'}
    unknown = set(filters) - known
    if unknown:
        raise ValueError(f'Unsupported filter: {sorted(unknown)}')
    where, values = ["(v.status IS NULL OR v.status = 'published')"], []

    def add(clause, *parameters):
        where.append(clause)
        values.extend(parameters)

    if filters.get('kev'):
        add('k.cve_id IS NOT NULL')
    if filters.get('ransomware'):
        add('k.ransomware_known = 1')
    for key, column in (('epss', 'e.score'), ('percentile', 'e.percentile'), ('cvss', 'v.cvss_base')):
        if filters.get(f'{key}_min') is not None:
            add(f'{column} >= ?', float(filters[f'{key}_min']))
        if filters.get(f'{key}_max') is not None:
            add(f'{column} <= ?', float(filters[f'{key}_max']))
    if filters.get('severity'):
        low, high = SEVERITY[filters['severity']]
        add("v.cvss_base BETWEEN ? AND ? AND (v.cvss_version LIKE '3%' OR v.cvss_version LIKE '4%')", low, high)
    for key, column in (('published', 'substr(v.published_at, 1, 10)'), ('kev_added', 'k.date_added')):
        if filters.get(f'{key}_from'):
            add(f'{column} >= ?', filters[f'{key}_from'])
        if filters.get(f'{key}_to'):
            add(f'{column} <= ?', filters[f'{key}_to'])
    if filters.get('cwe'):
        marks = ','.join('?' * len(filters['cwe']))
        add(f'EXISTS (SELECT 1 FROM current_vulnerability_weaknesses w WHERE w.cve_id = ids.cve_id AND w.cwe_id IN ({marks}))',
            *filters['cwe'])
    if filters.get('vendor'):
        add('k.vendor_key = ?', filters['vendor'])
    if filters.get('product'):
        add('instr(k.product_key, ?) > 0', filters['product'])
    if filters.get('ecosystem'):
        add('EXISTS (SELECT 1 FROM current_affected a WHERE a.cve_id = ids.cve_id AND (a.ecosystem = ? OR a.ecosystem LIKE ?))',
            filters['ecosystem'], filters['ecosystem'].replace('%', '').replace('_', '') + ':%')
    return ' WHERE ' + ' AND '.join(where), values


def _order(filters):
    column = SORT.get(filters.get('sort'))
    if column is None:
        return ' ORDER BY ids.cve_id DESC'
    direction = 'ASC' if filters.get('order') == 'asc' else 'DESC'
    # Rows without the value go last in either direction; ties break on the identifier.
    return f' ORDER BY {column} IS NULL, {column} {direction}, ids.cve_id DESC'


def provenance(db):
    """Published snapshot of every source, to cite the data and its date."""
    return [dict(row) for row in db.execute(
        '''SELECT sources.id AS source_id, sources.name, sources.canonical_url, sources.license_name,
                  snapshots.id AS snapshot_id, snapshots.fetched_at, snapshots.source_updated_at
           FROM sources JOIN snapshots ON snapshots.id = sources.published_snapshot_id ORDER BY sources.id''')]


def vocabulary(store):
    """Vendor -> products of the published KEV snapshot, for record_queries.plan()."""
    result = {}
    with store.connect() as db:
        for row in db.execute('SELECT DISTINCT vendor_key, product_key FROM current_kev ORDER BY 1, 2'):
            result.setdefault(row['vendor_key'], []).append(row['product_key'])
    return result


def summary(store):
    """What is published, for the status card: sources with the date of their data, and record counts."""
    with store.connect() as db:
        sources = provenance(db)
        counts = {name: db.execute(f'SELECT COUNT(*) FROM current_{name}').fetchone()[0]
                  for name in ('kev', 'vulnerabilities', 'epss')} if sources else {}
    return {'available': bool(sources), 'counts': counts,
            'sources': [{'id': source['source_id'], 'name': source['name'], 'license': source['license_name'],
                         'data_date': (source.get('source_updated_at') or source['fetched_at'])[:10],
                         'fetched_at': source['fetched_at']} for source in sources]}


def _details(db, identifiers):
    """Weaknesses, references and affected packages for a few records."""
    extra = {identifier: {'cwe': [], 'references': [], 'affected': []} for identifier in identifiers}
    if not identifiers:
        return extra
    marks = ','.join('?' * len(identifiers))
    for row in db.execute(f'SELECT DISTINCT cve_id, cwe_id FROM current_vulnerability_weaknesses WHERE cve_id IN ({marks}) ORDER BY 1, 2', identifiers):
        extra[row['cve_id']]['cwe'].append(row['cwe_id'])
    for row in db.execute(f'SELECT DISTINCT cve_id, url, tags_json FROM current_vulnerability_references WHERE cve_id IN ({marks}) ORDER BY 1, 2', identifiers):
        extra[row['cve_id']]['references'].append({'url': row['url'], 'tags': json.loads(row['tags_json'] or '[]')})
    for row in db.execute(f'''SELECT cve_id, ecosystem, package_name, range_type, events_json FROM current_affected
                              WHERE cve_id IN ({marks}) ORDER BY 1, 2, 3''', identifiers):
        extra[row['cve_id']]['affected'].append({'ecosystem': row['ecosystem'], 'package': row['package_name'],
                                                 'range_type': row['range_type'], 'events': json.loads(row['events_json'] or '[]')})
    return extra


def search(store, plan, *, default_limit=DEFAULT_LIMIT):
    """Rows for a plan. The result always states how many matched, not only how many are returned."""
    with store.connect() as db:
        sources = provenance(db)
        if plan.mode == 'record_lookup':
            identifiers = list(plan.ids)
            marks = ','.join('?' * len(identifiers))
            rows = {row['cve_id']: dict(row) for row in db.execute(
                f'SELECT {COLUMNS} FROM {UNIVERSE} ids{JOINS} WHERE ids.cve_id IN ({marks})', identifiers)}
            extra = _details(db, list(rows))
            records = [{**rows[identifier], **extra[identifier]} for identifier in identifiers if identifier in rows]
            return {'mode': plan.mode, 'records': records, 'total': len(records),
                    'missing': [identifier for identifier in identifiers if identifier not in rows], 'sources': sources}
        where, values = _conditions(plan.filter)
        # Catalogued entries are a small set: start from them whenever the filter requires KEV.
        needs_kev = any(plan.filter.get(key) for key in ('kev', 'ransomware', 'vendor', 'product', 'kev_added_from', 'kev_added_to'))
        universe = '(SELECT cve_id FROM current_kev)' if needs_kev else UNIVERSE
        base = f' FROM {universe} ids{JOINS}'
        total = db.execute(f'SELECT COUNT(*){base}{where}', values).fetchone()[0]
        if plan.mode == 'record_aggregate':
            operation = plan.aggregate or {}
            if operation.get('op') == 'count':
                return {'mode': plan.mode, 'aggregate': operation, 'total': total, 'groups': [], 'sources': sources}
            key, label, join = GROUPS[operation['by']]
            groups = [dict(row) for row in db.execute(
                f'''SELECT {key} AS key, {label} AS label, COUNT(DISTINCT ids.cve_id) AS count{base}{join}{where}
                    AND {key} IS NOT NULL GROUP BY {key} ORDER BY count DESC, key ASC''', values)]
            return {'mode': plan.mode, 'aggregate': operation, 'total': total, 'groups': groups, 'sources': sources}
        limit = max(1, min(int(plan.filter.get('limit') or default_limit), MAX_LIMIT))
        rows = [dict(row) for row in db.execute(
            f'SELECT {COLUMNS}{base}{where}{_order(plan.filter)} LIMIT ?', [*values, limit])]
        extra = _details(db, [row['cve_id'] for row in rows])
        return {'mode': plan.mode, 'records': [{**row, **extra[row['cve_id']]} for row in rows], 'total': total,
                'limit': limit, 'sources': sources}
