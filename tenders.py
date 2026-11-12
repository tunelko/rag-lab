"""Public tender documents chosen by the user: list what a source publishes, then download the selection.

A profile (profiles/<name>/pliegos.json) names a public syndication feed and a subject
filter. The application keeps a local listing of the tenders of that feed that match the
filter. The user searches that listing and chooses; only then are the chosen tenders'
technical and administrative PDFs downloaded into the library. Each tender also gets a
summary sheet (ficha) with what the platform publishes about it: body, subject, status,
submission deadline, place, budget and link. The sheet is an ordinary text document, so the
library indexes it next to the PDFs.

Two files live in the tender folder of the data directory: `catalog-<profile>.json`, the
listing read from the platform, and `manifest.json`, the register of the tenders that are in
the library.

Network use is explicit and narrow: only the hosts the profile names, over HTTPS, without
proxies, identified as RAG-Local/1.0 and verified against Mozilla's root list. Nothing here
runs at import or at start-up.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import html
import json
import os
from pathlib import Path
import re
import ssl
import time
import unicodedata
import urllib.error
import urllib.request
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
USER_AGENT = 'RAG-Local/1.0'
MAX_DOCUMENT = 40 * 1024**2
MAX_PAGE = 64 * 1024**2
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
PROFILE_NAME = re.compile(r'[a-z0-9][a-z0-9_-]{0,40}')
FRAME = re.compile(rb'([0-9a-fA-F]{1,8})\r\n')
EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


class TenderError(ValueError):
    pass


# ---------------------------------------------------------------- sources

def profiles(root=ROOT):
    """Profiles that define a tender source, by name."""
    folder = Path(root) / 'profiles'
    return sorted(path.parent.name for path in folder.glob('*/pliegos.json') if PROFILE_NAME.fullmatch(path.parent.name))


def load_source(profile, root=ROOT):
    """The tender source of one profile. Its feed and document hosts must be HTTPS hosts named in the file."""
    if not isinstance(profile, str) or not PROFILE_NAME.fullmatch(profile):
        raise TenderError('Perfil no válido.')
    path = Path(root) / 'profiles' / profile / 'pliegos.json'
    if not path.is_file():
        raise TenderError('Ese perfil no tiene una fuente de pliegos.')
    config = json.loads(path.read_text(encoding='utf-8'))
    feed = urlsplit(config['source']['feed'])
    if feed.scheme != 'https' or not feed.hostname:
        raise TenderError('La fuente de pliegos debe ser una dirección HTTPS.')
    config['profile'] = profile
    return config


def hosts(config):
    """Every host the source may be read from: the feed's and the ones that serve its documents."""
    return {urlsplit(config['source']['feed']).hostname, *config['source']['document_hosts']}


# ---------------------------------------------------------------- network

class _SameHosts(urllib.request.HTTPRedirectHandler):
    """Follow a redirect only to HTTPS on a host the profile names."""

    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = urlsplit(newurl)
        if target.scheme != 'https' or target.hostname not in self.allowed:
            raise urllib.error.HTTPError(newurl, code, 'redirección a un sitio no permitido', headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def unframe(data):
    """Undo chunk framing left inside a body, or return the data unchanged.

    One of the platform's servers sometimes answers with the transfer framing still in the
    content: `<size in hex>\\r\\n<bytes>\\r\\n` repeated, ended by a zero size.
    """
    position, pieces = 0, []
    while True:
        match = FRAME.match(data, position)
        if not match:
            return data
        size, start = int(match[1], 16), match.end()
        if size == 0:
            return b''.join(pieces) if pieces else data
        end = start + size
        if data[end:end + 2] != b'\r\n':
            return data
        pieces.append(data[start:end])
        position = end + 2


def feed_text(data):
    """A page of the feed as text. The feed has no carriage returns of its own, so any framing left is removed."""
    text = unframe(data).decode('utf-8', 'replace')
    if '\r\n' in text:
        text = re.sub(r'\r\n[0-9a-fA-F]{1,8}\r\n', '', text)
    return text


def reader(config):
    """A function that fetches one address of this source and nothing else."""
    import certifi
    allowed = hosts(config)
    tls = ssl.create_default_context(cafile=certifi.where())
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), urllib.request.HTTPSHandler(context=tls),
                                         _SameHosts(allowed))

    def read(url, *, limit=MAX_PAGE, timeout=180):
        target = urlsplit(url)
        if target.scheme != 'https' or target.hostname not in allowed:
            raise TenderError('Dirección fuera de la fuente del perfil.')
        request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with opener.open(request, timeout=timeout) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise TenderError('La respuesta supera el tamaño máximo.')
        return data

    return read


# ---------------------------------------------------------------- entries and filter

def fold(text):
    text = unicodedata.normalize('NFKD', text)
    return ''.join(char for char in text if not unicodedata.combining(char))


def first(pattern, text, default=''):
    match = re.search(pattern, text, re.S)
    return html.unescape(match[1]).strip() if match else default


def parse_entry(block):
    """The fields of one tender that the filter, the listing and the summary sheet need."""
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


def entries(text):
    return [parse_entry(block.split('</entry>')[0]) for block in text.split('<entry>')[1:]]


def reason(tender, rules):
    """Why a tender is selected, or None. Acronyms are matched as written; words ignore case and accents."""
    if tender['type'] not in rules['contract_types']:
        return None
    if rules.get('statuses') and tender.get('status') not in rules['statuses']:
        return None
    # Tenders of the current period: by their submission deadline, or by the publication date when none is given.
    if rules.get('deadline_from') and (tender.get('deadline') or (tender.get('updated') or '')[:10]) < rules['deadline_from']:
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


def subject_rules(rules):
    """The filter without status or period: what a tender is about does not change, its state does."""
    return {**rules, 'statuses': None, 'deadline_from': None}


def wanted_documents(tender, config):
    """The PDFs of a tender that the profile asks for and that its hosts serve."""
    allowed = set(config['source']['document_hosts'])
    return [item for item in tender.get('documents', []) if item['kind'] in config['documents']
            and item['name'].lower().endswith('.pdf') and urlsplit(item['url']).scheme == 'https'
            and urlsplit(item['url']).hostname in allowed]


def region(tender):
    """Autonomous community of the place of performance, or '' when the platform gives no regional code."""
    code = (tender.get('place_code') or '').upper()
    return REGIONS.get(code[:4], 'toda España' if code in ('ES', 'ES0', 'ES00', 'ES000') else '')


def stamp(value):
    """A date and time of the feed, with its zone, or None."""
    try:
        moment = datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def is_open(tender, today):
    """In its submission period on `today` (a date): what the search shows as open."""
    return tender.get('status') == 'PUB' and (not tender.get('deadline') or tender['deadline'][:10] >= today.isoformat())


# ---------------------------------------------------------------- summary sheets

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


def sheet_name(key):
    return f'{safe_name(key, 30)}{SHEET_SUFFIX}'


def write_sheet(key, tender, output):
    """Write or renew one tender's sheet in `output`; True when the file changed."""
    name = sheet_name(key)
    content = sheet(tender).encode('utf-8')
    path = Path(output) / name
    tender['sheet'] = name
    if path.exists() and path.read_bytes() == content:
        return False
    path.write_bytes(content)
    return True


def tender_files(tender):
    """Library file names of a registered tender: its PDFs and its sheet."""
    return [item['file'] for item in tender.get('documents', [])] + ([tender['sheet']] if tender.get('sheet') else [])


# ---------------------------------------------------------------- files of the tender folder

def read_json(path, default):
    path = Path(path)
    if not path.is_file():
        return default
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    """Replace a JSON file in one step, so a reader never sees half of it."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_suffix('.pending')
    with pending.open('w', encoding='utf-8') as output:
        json.dump(value, output, ensure_ascii=False, indent=1)
        output.flush()
        os.fsync(output.fileno())
    pending.replace(path)


def catalog_path(folder, profile):
    return Path(folder) / f'catalog-{profile}.json'


def register_path(folder):
    return Path(folder) / 'manifest.json'


def load_catalog(folder, profile):
    catalog = read_json(catalog_path(folder, profile), {})
    catalog.setdefault('tenders', {})
    return catalog


def load_register(folder):
    register = read_json(register_path(folder), {})
    register.setdefault('tenders', {})
    return register


# ---------------------------------------------------------------- listing

def refresh(config, catalog, registered, read, *, days, now, max_pages=400, delay=1.0, progress=None,
            cancelled=None, sleep=time.sleep):
    """Bring the listing up to date with the feed, newest first, back to `days` days before `now`.

    `catalog` is changed in place. `registered` maps the tenders already in the library to the
    date of the data held about them; the newer data found for them is returned, not applied.
    Reading stops at the period asked for, or sooner when it reaches what an earlier complete
    reading already listed. The marks of what the listing covers move only when the reading
    is complete, so a cancelled one is simply repeated.
    """
    rules, tenders = subject_rules(config['filter']), catalog.setdefault('tenders', {})
    allowed = hosts(config)
    cutoff = now - timedelta(days=days)
    known_newest, known_oldest = stamp(catalog.get('newest')), stamp(catalog.get('oldest'))
    url, pages, scanned, found, changes = config['source']['feed'], 0, 0, 0, {}
    head = reached = None
    complete = False
    while url and pages < max_pages:
        if cancelled and cancelled():
            break
        text = feed_text(read(url))
        pages += 1
        stamps = []
        for tender in entries(text):
            scanned += 1
            when, key = stamp(tender['updated']), tender['folder'] or tender['id']
            if not when or not key:
                continue
            stamps.append(when)
            if key in registered and when > (stamp(registered[key]) or EPOCH) and when > (stamp(changes.get(key, {}).get('updated')) or EPOCH):
                changes[key] = {name: tender[name] for name in METADATA}
            previous = tenders.get(key)
            if previous and (stamp(previous.get('updated')) or EPOCH) >= when:
                continue
            why = reason(tender, rules)
            if not why:
                continue
            found += previous is None
            tenders[key] = {**{name: tender[name] for name in METADATA}, 'selected_by': why,
                            'documents': wanted_documents(tender, config)}
        if stamps:
            head = max(head, max(stamps)) if head else max(stamps)
            reached = min(reached, min(stamps)) if reached else min(stamps)
            # The feed runs from new to old: a page that crosses a limit has everything newer than it.
            if min(stamps) <= cutoff:
                complete = True
            elif known_newest and known_oldest and min(stamps) <= known_newest and known_oldest <= cutoff:
                complete = True
        following = re.search(r'<link href="([^"]+)" rel="next"', text)
        url = html.unescape(following[1]) if following else None
        if url and (urlsplit(url).scheme != 'https' or urlsplit(url).hostname not in allowed):
            url = None
        if progress:
            progress(pages=pages, scanned=scanned, listed=len(tenders), found=found, reached=reached)
        if complete or not url:
            complete = True
            break
        sleep(delay)
    if complete and head:
        joined = known_newest and reached and reached <= known_newest
        catalog['newest'] = max(head, known_newest).isoformat() if known_newest else head.isoformat()
        catalog['oldest'] = (min(known_oldest, reached) if joined and known_oldest else reached).isoformat()
        catalog['read_at'] = now.isoformat()
    return {'pages': pages, 'scanned': scanned, 'found': found, 'listed': len(tenders), 'complete': complete,
            'changes': changes}


def row(key, tender, registered, today):
    """One tender as the search shows it."""
    documents = tender.get('documents', [])
    return {'key': key, 'folder': tender.get('folder') or key, 'title': tender.get('title', ''), 'organ': tender.get('organ', ''),
            'status': tender.get('status', ''), 'open': is_open(tender, today), 'deadline': (tender.get('deadline') or '')[:10],
            'deadline_time': (tender.get('deadline_time') or '')[:5], 'region': region(tender), 'place': tender.get('place', ''),
            'amount': tender.get('amount_without_tax', ''), 'estimated': tender.get('estimated_value', ''),
            'cpv': tender.get('cpv', []), 'updated': tender.get('updated', ''), 'link': tender.get('link', ''),
            'selected_by': tender.get('selected_by', ''), 'in_library': key in registered,
            'technical': sum(item['kind'] == 'technical' for item in documents),
            'legal': sum(item['kind'] == 'legal' for item in documents)}


def view(catalog, register, today):
    """The listing and the register as the page needs them: every tender once, newest data first."""
    registered = register['tenders']
    rows = [row(key, tender, registered, today) for key, tender in catalog['tenders'].items()]
    listed = set(catalog['tenders'])
    for key, tender in registered.items():
        if key not in listed:
            rows.append(row(key, tender, registered, today))
    for item in rows:
        if item['in_library']:
            # What the library holds is what its sheet says; a newer state waits in the listing until it is applied.
            item['library_status'] = registered[item['key']].get('status', '')
            item['files'] = len(tender_files(registered[item['key']]))
    rows.sort(key=lambda item: item['updated'], reverse=True)
    return {'tenders': rows, 'read_at': catalog.get('read_at'), 'newest': catalog.get('newest'), 'oldest': catalog.get('oldest'),
            'regions': sorted({item['region'] for item in rows if item['region']})}


# ---------------------------------------------------------------- download

def download(config, key, tender, output, read, *, delay=1.0, cancelled=None, sleep=time.sleep):
    """Download one tender's PDFs into `output`. Returns its register record, or None with the reason.

    A tender without its technical document is not kept: nothing is left in `output` for it.
    """
    saved, written, used = [], [], set()
    for item in wanted_documents(tender, config):
        if cancelled and cancelled():
            break
        try:
            content = unframe(read(item['url'], limit=MAX_DOCUMENT, timeout=120))
        except (OSError, ValueError) as error:
            saved.append({'kind': item['kind'], 'original_name': item['name'], 'error': type(error).__name__})
            continue
        if not content.startswith(b'%PDF'):
            saved.append({'kind': item['kind'], 'original_name': item['name'], 'error': 'not a PDF'})
            continue
        stem = f"{safe_name(key, 30)}__{KINDS[item['kind']][1]}__{safe_name(item['name'].rsplit('.', 1)[0], 60)}"
        name, number = f'{stem}.pdf', 1
        while name.casefold() in used:      # two documents of one tender published under the same name
            number += 1
            name = f'{stem}_{number}.pdf'
        used.add(name.casefold())
        path = Path(output) / name
        fresh = not path.exists()
        if fresh or path.read_bytes() != content:
            path.write_bytes(content)
        if fresh:
            written.append(path)
        saved.append({'kind': item['kind'], 'original_name': item['name'], 'file': name, 'bytes': len(content),
                      'sha256': hashlib.sha256(content).hexdigest(), 'url': item['url']})
        sleep(delay)
    stored = [item for item in saved if item.get('file')]
    if (cancelled and cancelled()) or not any(item['kind'] == 'technical' for item in stored):
        for path in written:
            path.unlink(missing_ok=True)
        failed = next((item['error'] for item in saved if item.get('error') and item['kind'] == 'technical'), None)
        return None, 'cancelled' if cancelled and cancelled() else failed or 'no technical document'
    return {**{name: tender.get(name, '') for name in METADATA}, 'selected_by': tender.get('selected_by', ''),
            'profile': config.get('profile', ''), 'documents': stored}, None
