"""Turn record query results into cited evidence for the answer model, for any profile.

The model never computes the result: the conditions applied, the totals, the counts and
the rows shown come from the engine and the executor. Text taken from the sources is data.
"""
from __future__ import annotations

import re

from langchain_core.documents import Document
import record_engine
from record_profiles import localized

DETAIL_CARDS = 5      # full cards after the table of a filter result

TEXT = {
    'es': {
        'record': 'Registro', 'yes': 'sí', 'no': 'no', 'unknown': 'desconocido', 'none': 'ninguna',
        'sources': 'Fuentes y fecha de sus datos', 'missing': 'Identificadores sin ningún dato en esta biblioteca',
        'lookup': 'Consulta por identificador', 'total': 'Coincidencias totales', 'shown': 'Mostradas',
        'count': 'Recuento exacto', 'table': 'Resultado de la consulta', 'groups': 'Grupos',
        'unavailable': 'Todavía no hay datos publicados para esta categoría. Sincroniza sus fuentes antes de consultarla.',
        'words': 'No he entendido {items}, así que no he ejecutado la consulta: el resultado habría ignorado esa parte de la pregunta.',
        'suggestions': ' Nombres parecidos que sí están publicados: {names}.',
        'range': 'El valor {items} está fuera del rango admitido. No he ejecutado la consulta.',
        'combination': ('No puedo combinar esas condiciones con los datos disponibles: {items} proceden de la misma fuente, '
                        'y un registro que no está en ella no tiene esos datos. No he ejecutado la consulta.'),
        'date': 'Indica a qué fecha te refieres ({items}). No he ejecutado la consulta para no elegir una por ti.',
        'system': (' La evidencia son registros estructurados obtenidos con una consulta exacta. Las condiciones aplicadas, el total '
                   'de coincidencias, los recuentos y el orden son los que indica la evidencia: no los recalcules ni los amplíes. '
                   'Si se muestran menos registros que el total, dilo. Da la fecha de la fuente al citar un dato.'),
    },
    'en': {
        'record': 'Record', 'yes': 'yes', 'no': 'no', 'unknown': 'unknown', 'none': 'none',
        'sources': 'Sources and date of their data', 'missing': 'Identifiers with no data in this library',
        'lookup': 'Lookup by identifier', 'total': 'Total matches', 'shown': 'Shown',
        'count': 'Exact count', 'table': 'Query result', 'groups': 'Groups',
        'unavailable': 'No data has been published for this category yet. Synchronise its sources before querying it.',
        'words': 'I did not understand {items}, so I did not run the query: the result would have ignored that part of the question.',
        'suggestions': ' Similar names that are published: {names}.',
        'range': 'The value {items} is outside the accepted range. I did not run the query.',
        'combination': ('I cannot combine those conditions with the available data: {items} come from the same source, and a '
                        'record that is not in it has none of that data. I did not run the query.'),
        'date': 'Say which date you mean ({items}). I did not run the query rather than choose one for you.',
        'system': (' The evidence is structured records returned by an exact query. The conditions applied, the total number of '
                   'matches, the counts and the order are those stated in the evidence: do not recompute or extend them. '
                   'If fewer records are shown than the total, say so. Give the source date when citing a value.'),
    },
}


def text(key, language='es'):
    return TEXT['en' if language == 'en' else 'es'][key]


def system_addition(profile, record_set, language='es'):
    """Extra system instructions for record answers: the generic rules, the set's caveats and the profile's role."""
    parts = [text('system', language), localized(record_set.get('notes', ''), language), localized(profile.get('role', ''), language)]
    return ' '.join(part.strip() for part in parts if part).join((' ', ''))


def notice(kind, record_set=None, language='es', *, items=(), suggestions=()):
    """A fixed answer that needs no model: guidance, missing data, or why a question was not run."""
    if kind == 'guidance':
        return localized(record_set.get('guidance', ''), language) or text('unavailable', language)
    if kind == 'unavailable':
        return text('unavailable', language)
    if kind in ('combination', 'date') and record_set:
        items = [localized(record_set['fields'][name].get('label', name), language) for name in items]
        shown = ' / '.join(items) if kind == 'date' else ', '.join(items)
    else:
        shown = ', '.join(f'«{item}»' for item in items)
    message = text(kind, language).format(items=shown)
    if suggestions:
        message += text('suggestions', language).format(names=', '.join(suggestions))
    return message


def rejection_notice(rejection):
    return {'notice': rejection.reason, 'items': list(rejection.items), 'suggestions': list(rejection.suggestions)}


def _value(name, value, record_set, language):
    if value is None:
        return ''
    if name in record_set.get('flags', ()):
        return text('yes' if value else 'no', language)
    if isinstance(value, float):
        return f'{value:.5f}'.rstrip('0').rstrip('.') if value != int(value) else f'{value:.1f}'
    if isinstance(value, str) and re.fullmatch(r'\d{4}-\d{2}-\d{2}T[\d:.]+Z?', value):
        return value[:10]
    if isinstance(value, list):
        return ' | '.join(_item(item) for item in value)
    return str(value)


def _item(item, nested=False):
    """One row of a detail list as plain text: its column values, with nested JSON as 'key value' pairs."""
    if isinstance(item, dict):
        if nested:
            return ', '.join(f'{key} {_item(value, True)}' for key, value in item.items() if value not in (None, '', []))
        return ' '.join(_item(value, True) for value in item.values() if value not in (None, '', []))
    if isinstance(item, list):
        return '(' + '; '.join(_item(value, True) for value in item) + ')'
    return str(item)


def card(record, record_set, language='es'):
    """Every stored fact about one record, as labelled lines declared by the profile."""
    lines = [f"{text('record', language)} {record['id']}"]
    for line in record_set.get('card', []):
        if any(record.get(name) in (None, '', []) for name in line.get('requires', ())):
            continue
        if any(record.get(name) not in (None, '', []) for name in line.get('missing', ())):
            continue
        if any(bool(record.get(name)) != expected for name, expected in line.get('when', {}).items()):
            continue
        template = line['en' if language == 'en' else 'es']
        names = re.findall(r'\{(\w+)\}', template)
        shown = {name: _value(name, record.get(name), record_set, language) or (text('unknown', language) if name in record_set.get('flags', ()) else '')
                 for name in names}
        lines.append(template.format(**shown))
    return '\n'.join(lines)


def _row(record, record_set, language):
    parts = [record['id']]
    for name in record_set.get('row', []):
        value = _value(name, record.get(name), record_set, language)
        if value:
            parts.append(value)
    return ' · '.join(parts)


def _provenance(sources, language):
    dated = [f"{source['name']} ({(source.get('source_updated_at') or source['fetched_at'])[:10]})" for source in sources]
    return f"{text('sources', language)}: {'; '.join(dated) if dated else text('none', language)}"


def _document(label, content, identifier, url=None):
    # `source` is read as a path by the document pipeline: keep it a plain relative name outside the library.
    return Document(page_content=content, metadata={'file': label, 'source': f'records/{identifier}', 'page': 0,
                                                    'item_type': 'record', 'url': url})


def conditions_line(plan, record_set, language='es'):
    """The first line of every record answer: what was actually applied, written by the engine."""
    return text('lookup', language) + ': ' + ', '.join(plan.ids) if plan.mode == 'lookup' else record_engine.describe(plan, record_set, language)


def evidence(plan, result, record_set, language='es'):
    """(hits, metrics): evidence documents in citation order and numeric diagnostics."""
    t = lambda key: text(key, language)
    footer = _provenance(result['sources'], language)
    link = record_set['identifier'].get('url')

    def record_document(record):
        return _document(record['id'], card(record, record_set, language), record['id'], link.format(id=record['id']) if link else None)

    metrics = {'retrieval_mode': 'records', 'record_mode': plan.mode, 'record_total': result.get('total', 0),
               'record_returned': len(result.get('records', []))}
    if plan.mode == 'lookup':
        documents = [record_document(record) for record in result['records']]
        if result.get('missing'):
            documents.append(_document(t('missing'), f"{t('missing')}: {', '.join(result['missing'])}\n{footer}", 'missing'))
        elif documents:
            documents[-1].page_content += '\n' + footer
        return [(document, 0.0) for document in documents], metrics
    head = [conditions_line(plan, record_set, language), f"{t('total')}: {result['total']}"]
    if plan.mode == 'aggregate':
        if plan.aggregate[0] == 'count':
            head.append(f"{t('count')}: {result['total']}")
        else:
            head.append(f"{t('groups')}:")
            head += [f"- {group['label']}: {group['count']}" for group in result['groups']]
        return [(_document(t('table'), '\n'.join([*head, footer]), 'query'), 0.0)], metrics
    head.append(f"{t('shown')}: {len(result['records'])}")
    head += [f'- {_row(record, record_set, language)}' for record in result['records']]
    documents = [_document(t('table'), '\n'.join([*head, footer]), 'query')]
    documents += [record_document(record) for record in result['records'][:DETAIL_CARDS]]
    return [(document, 0.0) for document in documents], metrics
