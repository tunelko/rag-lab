"""Deterministic planner for questions about structured vulnerability records.

`plan()` reads only the question text: no database, no model and no network. It returns
None when the question has no identifier and nothing exact to run.

The caller decides when to consult it: inside the record category for any plan, and for
the whole library only for a lookup by identifier. A document topic never calls it, which
is what keeps "how many vulnerabilities does this BIOS fix?" a document question.

Rules that keep the plan honest:
- a valid identifier always wins over filter words in the same question;
- "recent", "most likely" and "top N" are an ordering and a limit, never an invented
  date window or threshold;
- a weakness described in words is not turned into a CWE: only a written CWE-n counts;
- relative dates are resolved against the `today` captured when the question is accepted.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date, timedelta
import re
import unicodedata

HYPHENS = dict.fromkeys(map(ord, '‐‑‒–—−'), '-')
NUMBER = r'(\d+(?:[.,]\d+)?)\s*(%)?'
ABOVE = (r'(?:superior(?:es)? a|mayor(?:es)? (?:que|de|a)|por encima de|mas de|al menos|como minimo|'
         r'above|over|greater than|higher than|more than|at least|>=?)')
BELOW = r'(?:inferior(?:es)? a|menor(?:es)? (?:que|de|a)|por debajo de|menos de|como maximo|below|under|less than|lower than|at most|<=?)'
ARTICLE = r'(?:l|el|la|los|las|the)?\s*'
CVE = re.compile(r'(?<![a-z0-9])cve[-\s_]?((?:19|20)\d{2})[-\s_](\d{4,7})(?!\d)')
CWE = re.compile(r'(?<![a-z0-9])cwe[-\s]?(\d{1,4})(?![a-z0-9-])')
SUBJECT = re.compile(r'\bvulnerabilidad(?:es)?\b|\bvulnerabilit(?:y|ies)\b|\bcves?\b(?![-\s]?\d)|\bkev entries\b'
                     r'|\bentradas (?:de|en|del) (?:catalogo )?kev\b|\baltas (?:en|de|del|al?) (?:catalogo )?kev\b')
KEV = re.compile(r'\bkev\b|explotacion conocida|\bexplotad[ao]s?\b|known[- ]exploited|actively exploited'
                 r'|exploited in the wild|evidencias? (?:publicas? )?de explotacion')
SEVERITY = (('critical', r'\bcritic[ao]s?\b|\bcritical\b'), ('high', r'high[- ]severity|(?:severidad|gravedad) alta'),
            ('medium', r'medium[- ]severity|(?:severidad|gravedad) media'), ('low', r'low[- ]severity|(?:severidad|gravedad) baja'))
ECOSYSTEMS = {'ubuntu': 'Ubuntu', 'debian': 'Debian', 'alpine': 'Alpine', 'pypi': 'PyPI', 'npm': 'npm',
              'maven': 'Maven', 'rubygems': 'RubyGems', 'nuget': 'NuGet', 'packagist': 'Packagist', 'crates.io': 'crates.io'}
MONTHS = {name: number for number, names in enumerate((
    'enero january', 'febrero february', 'marzo march', 'abril april', 'mayo may', 'junio june', 'julio july',
    'agosto august', 'septiembre setiembre september', 'octubre october', 'noviembre november',
    'diciembre december'), 1) for name in names.split()}
MONTH = '(' + '|'.join(MONTHS) + ')'
ADDED = re.compile(r'\banadi\w+|\badded\b|\baltas?\b|\bincorporad\w+|\badditions?\b')
PUBLISHED = re.compile(r'\bpublicad\w+|\bpublished\b|\bpublicacion\b')
WORD = r'[a-z0-9.+#-]+'  # product names keep their hyphens ("pan-os")
GENERIC_PRODUCT = {'products', 'product', 'productos', 'producto', 'software', 'devices', 'dispositivos'}


@dataclass(frozen=True)
class RecordPlan:
    mode: str
    ids: tuple = ()
    filter: dict = field(default_factory=dict)
    aggregate: dict | None = None
    # Names the question uses as a condition that the published vocabulary does not contain.
    # A plan with any of these must not be run: the result would silently ignore them.
    unresolved: tuple = ()

    def as_dict(self):
        result = {'mode': self.mode}
        if self.mode == 'record_lookup':
            result['ids'] = list(self.ids)
        else:
            result['filter'] = dict(self.filter)
        if self.aggregate:
            result['aggregate'] = dict(self.aggregate)
        if self.unresolved:
            result['unresolved'] = list(self.unresolved)
        return result


def normalize(text):
    text = unicodedata.normalize('NFKD', str(text).translate(HYPHENS))
    text = ''.join(char for char in text if not unicodedata.combining(char)).casefold()
    return re.sub(r'\s+', ' ', text).strip()


def identifiers(question):
    """Canonical CVE identifiers in order of first appearance, without repeats."""
    found = [f'CVE-{year}-{number}' for year, number in CVE.findall(normalize(question))]
    return tuple(dict.fromkeys(found))


def weaknesses(question):
    return tuple(dict.fromkeys(f'CWE-{int(number)}' for number in CWE.findall(normalize(question))))


def _number(value, percent):
    number = float(value.replace(',', '.'))
    return number / 100 if percent or number > 1 else number


def _thresholds(text, metric, scale=True):
    """min/max for one metric from 'between a and b', 'above a' or 'below a' written after its name."""
    convert = _number if scale else (lambda value, percent: float(value.replace(',', '.')))
    between = re.search(rf'{metric}.{{0,30}}?(?:entre|between)\s+{NUMBER}\s+(?:y|and)\s+{NUMBER}', text)
    if between:
        return convert(between[1], between[2]), convert(between[3], between[4])
    low = re.search(rf'{metric}.{{0,30}}?{ABOVE}\s*{ARTICLE}{NUMBER}', text)
    high = re.search(rf'{metric}.{{0,30}}?{BELOW}\s*{ARTICLE}{NUMBER}', text)
    return (convert(low[1], low[2]) if low else None), (convert(high[1], high[2]) if high else None)


def _dates(text, today):
    """Inclusive (from, to) written in the question; either side may be None."""
    def iso(year, month=1, day=1):
        return date(year, month, day).isoformat()
    span = re.search(rf'(?:entre|between|from|de)\s+{MONTH}\s+(?:y|and|to|a)\s+{MONTH}\s+(?:de |del |of )?((?:19|20)\d{{2}})', text)
    if span:
        year, first, last = int(span[3]), MONTHS[span[1]], MONTHS[span[2]]
        return iso(year, first), iso(year, last, calendar.monthrange(year, last)[1])
    days = re.search(r'(?:ultimos|last|past)\s+(\d{1,4})\s+(?:dias|days)', text)
    if days:
        return (today - timedelta(days=int(days[1]))).isoformat(), today.isoformat()
    if re.search(r'\beste ano\b|\bthis year\b|\ben lo que va de ano\b', text):
        return iso(today.year), today.isoformat()
    if re.search(r'\beste mes\b|\bthis month\b', text):
        return iso(today.year, today.month), today.isoformat()
    years = re.search(r'(?:entre|between)\s+((?:19|20)\d{2})\s+(?:y|and)\s+((?:19|20)\d{2})', text)
    if years:
        return iso(int(years[1])), iso(int(years[2]), 12, 31)
    since = re.search(r'(?:desde|since|a partir de|from)\s+((?:19|20)\d{2})\b', text)
    before = re.search(r'(?:antes de|before|anteriores a|prior to)\s+((?:19|20)\d{2})\b', text)
    until = re.search(r'(?:hasta|until|through)\s+((?:19|20)\d{2})\b', text)
    if since or before or until:
        return (iso(int(since[1])) if since else None,
                iso(int(before[1]) - 1, 12, 31) if before else iso(int(until[1]), 12, 31) if until else None)
    year = re.search(r'(?:\ben|\bdurante|\bin|\bduring|\bdel?|\bof)\s+(?:el ano |the year )?((?:19|20)\d{2})\b', text)
    if year:
        return iso(int(year[1])), iso(int(year[1]), 12, 31)
    return None, None


def _vendor(text, catalog):
    """Longest known vendor written as whole words, and the product text next to it, if any."""
    best = None
    for vendor in catalog:
        match = re.search(rf'(?<![a-z0-9]){re.escape(vendor)}(?![a-z0-9])', text)
        if match and (best is None or len(vendor) > len(best[0])):
            best = (vendor, match)
    if best is None:
        return None, None
    vendor, match = best
    products = [normalize(item) for item in (catalog[vendor] if isinstance(catalog, dict) else ())]
    after = re.findall(WORD, text[match.end():])[:4]
    # The product may sit right before the vendor ("kernel Linux") or before a preposition ("kernel de Linux").
    before = re.search(rf'((?:{WORD} ){{1,3}})(?:(?:de|del|of|from|by|en|in)\s+)?$', text[:match.start()])
    candidates = [' '.join(after[:size]) for size in range(len(after), 0, -1)]
    if before:
        words = [word for word in before[1].split() if word not in {'el', 'la', 'los', 'las', 'del', 'the', 'de'}]
        candidates += [' '.join(words[-size:]) for size in range(len(words), 0, -1)]
    for candidate in sorted(candidates, key=len, reverse=True):
        if candidate and candidate not in GENERIC_PRODUCT and any(_contains(product, candidate) for product in products):
            return vendor, candidate
    return vendor, None


def _contains(text, phrase):
    return re.search(rf'(?<![a-z0-9-]){re.escape(phrase)}(?![a-z0-9-])', text) is not None


def _product_only(text, catalog):
    """A product written without its vendor: (vendor, product) when only one vendor makes it."""
    owners = {}
    for vendor, products in catalog.items():
        for product in products:
            product = normalize(product)
            if len(product) >= 4 and _contains(text, product):
                owners.setdefault(product, set()).add(vendor)
    if not owners:
        return None, None
    product = max(owners, key=len)
    return (next(iter(owners[product])) if len(owners[product]) == 1 else None), product


def _aggregate(text, added):
    if re.search(r'\bmensual(?:es|mente)?\b|\bpor mes\b|\bper month\b|\bby month\b|\bmonthly\b', text):
        return {'op': 'group', 'by': 'kev_added_month' if added else 'published_month'}
    if re.search(r'\bpor ano\b|\bcada ano\b|\banual(?:es|mente)?\b|\bper year\b|\bby year\b|\byearly\b|\bannual\b', text):
        return {'op': 'group', 'by': 'kev_added_year' if added else 'published_year'}
    if re.search(r'\bque fabricantes\b|\bpor fabricante\b|\bwhich vendors\b|\bby vendor\b|\bper vendor\b|\bfabricantes con mas\b', text):
        return {'op': 'group', 'by': 'vendor'}
    if re.search(r'\bpor cwe\b|\bby cwe\b|\bcwes? mas (?:comunes|frecuentes|habituales)\b|\bmost (?:common|frequent) cwes?\b'
                 r'|\bque cwes?\b|\bwhich cwes?\b', text):
        return {'op': 'group', 'by': 'cwe'}
    if re.search(r'\bcuant[ao]s\b|\bhow many\b|\bnumero de\b|\bnumber of\b|\brecuento\b|\bcount of\b', text):
        return {'op': 'count'}
    return None


def plan(question, *, today=None, vendors=()):
    """RecordPlan for a structured vulnerability question, or None for the existing paths.

    `vendors` is the vocabulary of the published snapshot: an iterable of normalized vendor
    names, or a mapping from each vendor to its normalized product names.
    """
    text = normalize(question)
    found = identifiers(text)
    if found:
        return RecordPlan('record_lookup', ids=found)
    if not SUBJECT.search(text):
        return None
    today = today or date.today()
    if isinstance(today, str):
        today = date.fromisoformat(today)
    catalog = vendors if isinstance(vendors, dict) else {normalize(name): () for name in vendors}
    catalog = {normalize(name): products for name, products in catalog.items()}
    filters = {}
    if KEV.search(text):
        filters['kev'] = True
    if 'ransomware' in text:
        filters['ransomware'] = True
    if 'percentil' in text:
        # A threshold written on the percentile is not a threshold on the score.
        low, high = _thresholds(text, r'percentil(?:es?)?')
        direct = re.search(rf'({BELOW})?\s*{ARTICLE}percentil(?:es?)?\s+{NUMBER}', text)
        if direct and low is None and high is None:
            value = _number(direct[2], direct[3])
            low, high = (None, value) if direct[1] else (value, None)
        names = ('percentile_min', 'percentile_max')
    else:
        low, high = _thresholds(text, 'epss')
        names = ('epss_min', 'epss_max')
    if low is not None:
        filters[names[0]] = low
    if high is not None:
        filters[names[1]] = high
    low, high = _thresholds(text, 'cvss', scale=False)
    more = re.search(rf'cvss\s+(?:de |of |score of )?{NUMBER}\s*(?:o mas|o superior|or (?:more|higher|above|greater)|\+)', text)
    if more:
        low = float(more[1].replace(',', '.'))
    if low is not None:
        filters['cvss_min'] = low
    if high is not None:
        filters['cvss_max'] = high
    for name, pattern in SEVERITY:
        if re.search(pattern, text):
            filters['severity'] = name
            break
    added = bool(ADDED.search(text)) and not PUBLISHED.search(text)
    start, end = _dates(text, today)
    prefix = 'kev_added' if added else 'published'
    if start:
        filters[f'{prefix}_from'] = start
    if end:
        filters[f'{prefix}_to'] = end
    listed = weaknesses(text)
    if listed:
        filters['cwe'] = list(listed)
    # An ecosystem name is always the ecosystem: it is not also read as a vendor's product,
    # which would silently restrict the result to catalogued entries of that vendor.
    named = text
    for word, ecosystem in ECOSYSTEMS.items():
        pattern = rf'(?<![a-z0-9]){re.escape(word)}(?![a-z0-9])'
        if re.search(pattern, text):
            filters['ecosystem'] = ecosystem
            named = re.sub(pattern, ' ', text)
            break
    vendor, product = _vendor(named, catalog)
    if vendor is None:
        vendor, product = _product_only(named, catalog)
    if vendor:
        filters['vendor'] = vendor
    if product:
        filters['product'] = product
    aggregate = _aggregate(text, added)
    if aggregate is None:
        if re.search(r'\bmayor(?:es)? epss\b|\bepss mas alto\b|\bhighest epss\b|\bby epss\b|\bpor epss\b'
                     r'|\bmayor probabilidad de explotacion\b|\bmost likely to be exploited\b', text):
            filters.update(sort='epss', order='desc')
        elif re.search(r'\bby cvss\b|\bpor cvss\b|\bmayor(?:es)? cvss\b|\bhighest cvss\b|\bcvss mas alto\b|\bmas graves\b|\bmost severe\b', text):
            filters.update(sort='cvss', order='desc')
        elif re.search(r'\brecientes?\b|\brecent\b|\blatest\b|\bnewest\b|\bmas nuev[ao]s\b', text):
            filters.update(sort='kev_added' if filters.get('kev') else 'published', order='desc')
        limit = re.search(r'\btop\s+(\d{1,3})\b|\b(?:las|los|the|first|primer[ao]s)\s+(\d{1,3})\s+(?:\w+\s+)?(?:vulnerabilidades|vulnerabilities|cves?)\b'
                          r'|\b(?:las|los|the)\s+(\d{1,3})\s+(?:primer[ao]s|first)\b', text)
        if limit:
            filters['limit'] = int(limit[1] or limit[2] or limit[3])
    unresolved = _unresolved(question, catalog)
    if not filters and aggregate is None and not unresolved:
        return None  # nothing exact to run; a count or grouping over everything is exact
    return RecordPlan('record_aggregate' if aggregate else 'record_filter', filter=filters, aggregate=aggregate,
                      unresolved=unresolved)


KNOWN_NAMES = {'kev', 'epss', 'cvss', 'cwe', 'cwes', 'cve', 'cves', 'cisa', 'nvd', 'first', 'osv', 'mitre', *MONTHS}


def _unresolved(question, catalog):
    """Capitalised names used as a condition ("de Cisco", "in Acme Router") that nothing recognises."""
    known = set(KNOWN_NAMES) | set(ECOSYSTEMS)
    for vendor, products in catalog.items():
        known.update(re.findall(WORD, vendor))
        for product in products:
            known.update(re.findall(WORD, normalize(product)))
    original = unicodedata.normalize('NFKC', str(question).translate(HYPHENS))
    found = []
    for match in re.finditer(r'\b(?:de|del|of|in|from|by)\s+((?:[A-ZÀ-Ý][\w.+#-]*)(?:\s+[A-ZÀ-Ý0-9][\w.+#-]*){0,2})', original):
        words = [word for word in re.findall(WORD, normalize(match[1])) if word not in known]
        # Identifiers such as CWE-787 are conditions of their own, not names.
        if words and not CWE.search(normalize(match[1])):
            found.append(match[1].strip())
    return tuple(dict.fromkeys(found))
