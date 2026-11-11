"""Download public tender documents (pliegos) selected by a profile's filter.

Reads the public syndication feed of the Spanish procurement platform, keeps the tenders
that match profiles/<profile>/pliegos.json (contract type and subject), and downloads their
technical and administrative PDFs into a staging folder with a manifest. For every tender
it also writes a summary sheet (ficha) with what the platform publishes about it: body,
subject, status, submission deadline, budget, CPV and link. The sheet is an ordinary text
document, so the library indexes it next to the PDFs.

    python -m tools.fetch_pliegos --profile ciberseguridad --tenders 30     # download
    python -m tools.fetch_pliegos --profile ciberseguridad --refresh        # update status and sheets

It does not index anything: copy the files to the library afterwards (tools/load_pliegos).
Only this tool touches the network. It identifies itself as RAG-Local/1.0 and nothing else,
verifies TLS with Mozilla's root list, and waits between requests.
"""
from __future__ import annotations

import argparse
from datetime import date
import hashlib
import html
import json
from pathlib import Path
import re
import ssl
import sys
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = 'RAG-Local/1.0'
MAX_DOCUMENT = 40 * 1024**2
SHEET_SUFFIX = '__FICHA.md'     # library_queries.SUMMARY_SUFFIX: one sheet stands for its tender
KINDS = {'technical': ('TechnicalDocumentReference', 'PPT'), 'legal': ('LegalDocumentReference', 'PCAP')}
# https://contrataciondelestado.es/codice/cl/2.04/SyndicationContractFolderStatusCode-2.04.gc
STATUS = {'PRE': 'anuncio previo', 'PUB': 'en plazo de presentación de ofertas', 'EV': 'pendiente de adjudicación',
          'ADJ': 'adjudicada', 'RES': 'resuelta', 'ANUL': 'anulada'}
# https://contrataciondelestado.es/codice/cl/2.08/ContractCode-2.08.gc
CONTRACT_TYPES = {'1': 'suministros', '2': 'servicios', '3': 'obras', '21': 'gestión de servicios públicos',
                  '22': 'concesión de servicios', '31': 'concesión de obras públicas', '32': 'concesión de obras',
                  '40': 'colaboración público-privada', '7': 'administrativo especial', '8': 'privado', '50': 'patrimonial'}
METADATA = ('folder', 'title', 'organ', 'cpv', 'status', 'type', 'updated', 'link', 'amount_without_tax',
            'amount_with_tax', 'estimated_value', 'deadline', 'deadline_time', 'place', 'place_code', 'city')
# Autonomous communities by the first four characters of the NUTS code the platform publishes.
REGIONS = {'ES11': 'Galicia', 'ES12': 'Principado de Asturias', 'ES13': 'Cantabria', 'ES21': 'País Vasco',
           'ES22': 'Comunidad Foral de Navarra', 'ES23': 'La Rioja', 'ES24': 'Aragón', 'ES30': 'Comunidad de Madrid',
           'ES41': 'Castilla y León', 'ES42': 'Castilla-La Mancha', 'ES43': 'Extremadura', 'ES51': 'Cataluña',
           'ES52': 'Comunitat Valenciana', 'ES53': 'Illes Balears', 'ES61': 'Andalucía', 'ES62': 'Región de Murcia',
           'ES63': 'Ceuta', 'ES64': 'Melilla', 'ES70': 'Canarias'}


def region(tender):
    """Autonomous community of the place of performance, or '' when the platform gives no regional code."""
    code = (tender.get('place_code') or '').upper()
    return REGIONS.get(code[:4], 'toda España' if code in ('ES', 'ES0', 'ES00', 'ES000') else '')


def fold(text):
    text = unicodedata.normalize('NFKD', text)
    return ''.join(char for char in text if not unicodedata.combining(char))


def context():
    import certifi
    return ssl.create_default_context(cafile=certifi.where())


def fetch(url, tls, *, limit=None, timeout=120):
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls))
    with opener.open(request, timeout=timeout) as response:
        data = response.read((limit + 1) if limit else -1)
    if limit and len(data) > limit:
        raise ValueError('document larger than the limit')
    return data


def first(pattern, text, default=''):
    match = re.search(pattern, text, re.S)
    return html.unescape(match[1]).strip() if match else default


def parse_entry(block):
    """The fields of one tender that the filter, the manifest and the summary sheet need."""
    project = first(r'<cac:ProcurementProject>(.*?)</cac:ProcurementProject>', block)
    budget = first(r'<cac:BudgetAmount>(.*?)</cac:BudgetAmount>', project)
    deadline = first(r'<cac:TenderSubmissionDeadlinePeriod>(.*?)</cac:TenderSubmissionDeadlinePeriod>', block)
    documents = []
    for kind, (tag, _) in KINDS.items():
        for reference in re.findall(rf'<cac:{tag}>(.*?)</cac:{tag}>', block, re.S):
            name, url = first(r'<cbc:ID>([^<]*)', reference), first(r'<cbc:URI>([^<]*)', reference)
            if name and url:
                documents.append({'kind': kind, 'name': name, 'url': url})
    return {
        'id': first(r'<id>([^<]+)', block), 'folder': first(r'<cbc:ContractFolderID>([^<]+)', block),
        'title': ' '.join(first(r'<title>(.*?)</title>', block).split()), 'updated': first(r'<updated>([^<]+)', block),
        'link': first(r'<link href="([^"]+)"', block), 'status': first(r'ContractFolderStatusCode[^>]*>([^<]+)', block),
        'type': first(r'<cbc:TypeCode[^>]*>([^<]+)', project), 'organ': first(r'<cac:PartyName>\s*<cbc:Name>([^<]+)', block),
        'cpv': sorted(set(re.findall(r'ItemClassificationCode[^>]*>(\d+)', block))),
        'amount_without_tax': first(r'<cbc:TaxExclusiveAmount[^>]*>([^<]+)', budget),
        'amount_with_tax': first(r'<cbc:TotalAmount[^>]*>([^<]+)', budget),
        'estimated_value': first(r'<cbc:EstimatedOverallContractAmount[^>]*>([^<]+)', budget),
        'deadline': first(r'<cbc:EndDate>([^<]+)', deadline), 'deadline_time': first(r'<cbc:EndTime>([^<]+)', deadline),
        'place': first(r'<cac:RealizedLocation>.*?<cbc:CountrySubentity>([^<]+)', project),
        'place_code': first(r'<cac:RealizedLocation>.*?<cbc:CountrySubentityCode[^>]*>([^<]+)', project),
        'city': first(r'<cac:RealizedLocation>.*?<cbc:CityName>([^<]+)', project),
        'documents': documents,
    }


def reason(tender, rules):
    """Why a tender is selected, or None. Acronyms are matched as written; words ignore case and accents."""
    if tender['type'] not in rules['contract_types']:
        return None
    title, plain = tender['title'], fold(tender['title']).casefold()
    if any(term in plain for term in rules['excluded_terms']):
        return None
    cpv = tender['cpv']
    if any(code.startswith(tuple(rules['excluded_cpv_prefixes'])) for code in cpv):
        return None

    def has(terms):
        return next((term for term in terms if re.search(rf'(?<![a-z0-9]){re.escape(term)}(?![a-z0-9])', plain)), None)

    acronym = next((term for term in rules['acronyms'] if re.search(rf'(?<![A-Za-z0-9]){term}(?![A-Za-z0-9])', fold(title))), None)
    security = any(code.startswith(tuple(rules['security_cpv_prefixes'])) for code in cpv)
    it = any(code.startswith(tuple(rules['it_cpv_prefixes'])) for code in cpv)
    clear = has(rules['unambiguous_terms']) or acronym
    if clear:
        return f'término: {clear}'
    if it and has(rules['it_only_terms']):
        return f"CPV de TI + término: {has(rules['it_only_terms'])}"
    if security:
        return 'CPV de seguridad'
    return None


def safe_name(text, limit=70):
    text = fold(text)
    text = re.sub(r'[^A-Za-z0-9._-]+', '_', text).strip('._')
    return text[:limit] or 'documento'


def euros(value):
    try:
        return f'{float(value):,.2f}'.replace(',', ' ').replace('.', ',').replace(' ', '.') + ' EUR'
    except (TypeError, ValueError):
        return ''


def day(value):
    """A date of the feed as DD/MM/YYYY, or '' when it is missing or malformed."""
    match = re.match(r'(\d{4})-(\d{2})-(\d{2})', value or '')
    return f'{match[3]}/{match[2]}/{match[1]}' if match else ''


def sheet(tender):
    """The summary sheet of one tender. The first lines carry what questions about the whole set need."""
    data_day = (tender.get('updated') or '')[:10]
    status = tender.get('status', '')
    deadline = tender.get('deadline', '')
    if status == 'PUB' and deadline and deadline >= data_day:
        open_line = f"Abierta para presentar ofertas: sí, hasta el {day(deadline)}" + (f" a las {tender['deadline_time'][:5]}" if tender.get('deadline_time') else '')
    elif status == 'PUB':
        open_line = 'Abierta para presentar ofertas: en plazo según la plataforma' + (f', con fecha límite {day(deadline)}' if deadline else ', sin fecha límite publicada')
    else:
        open_line = f"Abierta para presentar ofertas: no ({STATUS.get(status, status or 'estado no publicado')})" + (f'; el plazo terminó el {day(deadline)}' if deadline else '')
    community = region(tender)
    where = ' · '.join(item for item in (
        f'Comunidad autónoma: {community}' if community else '',
        f"Provincia o zona: {tender['place']}" if tender.get('place') and tender['place'] != community else '') if item)
    # The opening block is what a question about every tender receives, possibly cut short when there
    # are many tenders. Every fact goes there, shortest first; the subject, the longest, goes last.
    lines = [
        f"Licitación {tender['folder']} · {tender.get('organ', '')[:60]}",
        f"Estado a {day(data_day)}: {STATUS.get(status, status or 'no publicado')}. {open_line}.",
        where or 'Comunidad autónoma: no publicada',
        'Presupuesto sin impuestos: ' + (euros(tender.get('amount_without_tax')) or 'no publicado')
        + (f" · Valor estimado: {euros(tender['estimated_value'])}" if euros(tender.get('estimated_value')) else ''),
        f"Objeto: {tender['title'][:90]}",
        '',
        f"Tipo de contrato: {CONTRACT_TYPES.get(tender.get('type', ''), tender.get('type', ''))}"
        + (f" · Localidad: {tender['city']}" if tender.get('city') else ''),
        'CPV: ' + ', '.join(tender.get('cpv', [])),
        'Presupuesto con impuestos: ' + (euros(tender.get('amount_with_tax')) or 'no publicado'),
        f"Objeto completo: {tender['title']}",
        'Documentos en la biblioteca: ' + ', '.join(item['file'] for item in tender.get('documents', [])),
        f"Enlace: {tender.get('link', '')}",
        f"Fuente: Plataforma de Contratación del Sector Público. Dato publicado el {day(data_day)}; el estado y los plazos cambian.",
    ]
    return '\n'.join(lines) + '\n'


def write_sheets(manifest, output):
    """Write or renew every tender's sheet; returns how many changed."""
    changed = 0
    for key, tender in manifest['tenders'].items():
        name = f"{safe_name(key, 30)}{SHEET_SUFFIX}"
        content = sheet(tender).encode('utf-8')
        path = output / name
        if not path.exists() or path.read_bytes() != content:
            path.write_bytes(content)
            changed += 1
        tender['sheet'] = name
    return changed


def pages(config, tls, limit, delay):
    """Entries of the feed, newest first, page after page."""
    url, count = config['source']['feed'], 0
    while url and count < limit:
        data = fetch(url, tls, timeout=180).decode('utf-8', 'replace')
        count += 1
        yield count, [parse_entry(block.split('</entry>')[0]) for block in data.split('<entry>')[1:]]
        following = re.search(r'<link href="([^"]+)" rel="next"', data)
        url = html.unescape(following[1]) if following else None
        time.sleep(delay)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--profile', required=True)
    parser.add_argument('--tenders', type=int, default=30, help='stop after this many selected tenders with documents')
    parser.add_argument('--refresh', action='store_true', help='update status, deadline and sheets of the tenders already downloaded')
    parser.add_argument('--sheets-only', action='store_true', help='rewrite the sheets from the manifest without reading the platform')
    parser.add_argument('--max-pages', type=int, default=80)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'pliegos')
    parser.add_argument('--delay', type=float, default=1.0)
    args = parser.parse_args(argv)
    config = json.loads((ROOT / 'profiles' / args.profile / 'pliegos.json').read_text(encoding='utf-8'))
    rules, hosts = config['filter'], set(config['source']['document_hosts'])
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'tenders': {}}
    tenders = manifest['tenders']
    tls = context()

    def save():
        manifest['read_on'] = date.today().isoformat()
        write_sheets(manifest, args.output)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')

    seen, scanned = set(), 0
    if args.sheets_only:
        changed = write_sheets(manifest, args.output)
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
        print(f'{changed} fichas escritas de {len(tenders)}, sin leer la plataforma', flush=True)
        return 0
    if args.refresh:
        for number, entries in pages(config, tls, args.max_pages, args.delay):
            for tender in entries:
                scanned += 1
                key = tender['folder'] or tender['id']
                # The feed is newest first: the first appearance of a tender is its current state.
                if key in tenders and key not in seen:
                    seen.add(key)
                    before = tenders[key].get('status'), tenders[key].get('deadline')
                    tenders[key].update({name: tender[name] for name in METADATA})
                    if before != (tender['status'], tender['deadline']):
                        print(f"  {key}: {before[0]} -> {tender['status']}, plazo {tender['deadline'] or 'sin fecha'}", flush=True)
            print(f'página {number}: {scanned} licitaciones leídas, {len(seen)} de {len(tenders)} actualizadas', flush=True)
            if len(seen) == len(tenders):
                break
        save()
        print(f'FIN: {len(seen)} de {len(tenders)} licitaciones actualizadas; fichas en {args.output}', flush=True)
        return 0

    for number, entries in pages(config, tls, args.max_pages, args.delay):
        for tender in entries:
            scanned += 1
            key = tender['folder'] or tender['id']
            if key in seen or key in tenders:
                continue
            seen.add(key)
            why = reason(tender, rules)
            wanted = [item for item in tender['documents'] if item['kind'] in config['documents']
                      and item['name'].lower().endswith('.pdf') and urlsplit(item['url']).hostname in hosts]
            if not why or not any(item['kind'] == 'technical' for item in wanted):
                continue
            saved = []
            for item in wanted:
                try:
                    content = fetch(item['url'], tls, limit=MAX_DOCUMENT)
                except (OSError, ValueError, urllib.error.URLError) as error:
                    print(f'  no descargado ({type(error).__name__}): {item["name"][:60]}', flush=True)
                    continue
                if not content.startswith(b'%PDF'):
                    continue
                name = f"{safe_name(key, 30)}__{KINDS[item['kind']][1]}__{safe_name(Path(item['name']).stem, 60)}.pdf"
                (args.output / name).write_bytes(content)
                saved.append({'kind': item['kind'], 'original_name': item['name'], 'file': name, 'bytes': len(content),
                              'sha256': hashlib.sha256(content).hexdigest(), 'url': item['url']})
                time.sleep(args.delay)
            if any(item['kind'] == 'technical' for item in saved):
                tenders[key] = {**{name: tender[name] for name in METADATA}, 'selected_by': why, 'documents': saved}
                save()
                print(f"[{len(tenders)}] {tender['title'][:95]}  <- {why}", flush=True)
                if len(tenders) >= args.tenders:
                    break
        print(f'página {number}: {scanned} licitaciones leídas, {len(tenders)} seleccionadas', flush=True)
        if len(tenders) >= args.tenders:
            break
    save()
    files = sum(len(item['documents']) for item in tenders.values())
    print(f'FIN: {len(tenders)} licitaciones, {files} PDF y {len(tenders)} fichas en {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
