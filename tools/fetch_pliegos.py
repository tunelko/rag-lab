"""Download public tender documents (pliegos) selected by a profile's filter.

Reads the public syndication feed of the Spanish procurement platform, keeps the tenders
that match profiles/<profile>/pliegos.json (contract type and subject), and downloads their
technical and administrative PDFs into a staging folder with a manifest. It does not index
anything: copy or upload the files to the library afterwards.

    python -m tools.fetch_pliegos --profile ciberseguridad --tenders 30

Only this tool touches the network. It identifies itself as RAG-Local/1.0 and nothing else,
verifies TLS with Mozilla's root list, and waits between requests.
"""
from __future__ import annotations

import argparse
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
KINDS = {'technical': ('TechnicalDocumentReference', 'PPT'), 'legal': ('LegalDocumentReference', 'PCAP')}


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
    """The fields of one tender that the filter and the manifest need."""
    project = first(r'<cac:ProcurementProject>(.*?)</cac:ProcurementProject>', block)
    documents = []
    for kind, (tag, _) in KINDS.items():
        for reference in re.findall(rf'<cac:{tag}>(.*?)</cac:{tag}>', block, re.S):
            name, url = first(r'<cbc:ID>([^<]*)', reference), first(r'<cbc:URI>([^<]*)', reference)
            if name and url:
                documents.append({'kind': kind, 'name': name, 'url': url})
    return {
        'id': first(r'<id>([^<]+)', block), 'folder': first(r'<cbc:ContractFolderID>([^<]+)', block),
        'title': first(r'<title>(.*?)</title>', block), 'updated': first(r'<updated>([^<]+)', block),
        'link': first(r'<link href="([^"]+)"', block), 'status': first(r'ContractFolderStatusCode[^>]*>([^<]+)', block),
        'type': first(r'<cbc:TypeCode[^>]*>([^<]+)', project), 'organ': first(r'<cac:PartyName>\s*<cbc:Name>([^<]+)', block),
        'cpv': sorted(set(re.findall(r'ItemClassificationCode[^>]*>(\d+)', block))),
        'amount_without_tax': first(r'<cbc:TaxExclusiveAmount[^>]*>([^<]+)', block),
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


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--profile', required=True)
    parser.add_argument('--tenders', type=int, default=30, help='stop after this many selected tenders with documents')
    parser.add_argument('--max-pages', type=int, default=80)
    parser.add_argument('--output', type=Path, default=ROOT / 'data' / 'pliegos')
    parser.add_argument('--delay', type=float, default=1.0)
    args = parser.parse_args(argv)
    config = json.loads((ROOT / 'profiles' / args.profile / 'pliegos.json').read_text(encoding='utf-8'))
    rules, hosts = config['filter'], set(config['source']['document_hosts'])
    args.output.mkdir(parents=True, exist_ok=True)
    manifest_path = args.output / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.exists() else {'tenders': {}}
    tls = context()
    url, pages, seen, scanned = config['source']['feed'], 0, set(), 0
    while url and pages < args.max_pages and len(manifest['tenders']) < args.tenders:
        data = fetch(url, tls, timeout=180).decode('utf-8', 'replace')
        pages += 1
        for block in data.split('<entry>')[1:]:
            tender = parse_entry(block.split('</entry>')[0])
            scanned += 1
            key = tender['folder'] or tender['id']
            if key in seen or key in manifest['tenders']:
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
                digest = hashlib.sha256(content).hexdigest()
                name = f"{safe_name(key, 30)}__{KINDS[item['kind']][1]}__{safe_name(Path(item['name']).stem, 60)}.pdf"
                (args.output / name).write_bytes(content)
                saved.append({'kind': item['kind'], 'original_name': item['name'], 'file': name, 'bytes': len(content),
                              'sha256': digest, 'url': item['url']})
                time.sleep(args.delay)
            if any(item['kind'] == 'technical' for item in saved):
                manifest['tenders'][key] = {**{name: tender[name] for name in
                    ('folder', 'title', 'organ', 'cpv', 'status', 'type', 'updated', 'link', 'amount_without_tax')},
                    'selected_by': why, 'documents': saved}
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding='utf-8')
                print(f"[{len(manifest['tenders'])}] {tender['title'][:95]}  <- {why}", flush=True)
                if len(manifest['tenders']) >= args.tenders:
                    break
        following = re.search(r'<link href="([^"]+)" rel="next"', data)
        url = html.unescape(following[1]) if following else None
        print(f'página {pages}: {scanned} licitaciones leídas, {len(manifest["tenders"])} seleccionadas', flush=True)
        time.sleep(args.delay)
    files = sum(len(item['documents']) for item in manifest['tenders'].values())
    print(f'FIN: {len(manifest["tenders"])} licitaciones, {files} PDF en {args.output}', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
