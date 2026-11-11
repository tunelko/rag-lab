"""Generic planner for questions about structured records, driven by a profile.

The engine knows field *types* (flag, number, date, identifiers, vocabulary, enum) and the
ways people write comparisons, dates, negations, rankings, counts and groupings. What a
field is called, which words name it and where it is stored come from the profile.

It never guesses. Every rule marks the part of the question it understood; if any word
with meaning is left over, there is no plan but a Rejection naming those words. Negation
words are never filler. A plan is therefore either a faithful reading of the whole
question or it does not exist. No database, model or network is used here.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
import re
import unicodedata

HYPHENS = dict.fromkeys(map(ord, '‐‑‒–—−'), '-')
MAX_LIMIT = 200
NUMBER = r'(\d+(?:[.,]\d+)?)\s*(%|por ciento|percent)?'
MONTHS = {name: number for number, names in enumerate((
    'enero january', 'febrero february', 'marzo march', 'abril april', 'mayo may', 'junio june', 'julio july',
    'agosto august', 'septiembre setiembre september', 'octubre october', 'noviembre november',
    'diciembre december'), 1) for name in names.split()}
MONTH = r'\b(?:' + '|'.join(sorted(MONTHS, key=len, reverse=True)) + r')\b'
YEAR = r'(?:19|20)\d{2}'
ARTICLE = r'(?:\s+(?:el|la|los|las|the|dia|day))*'

# Words that carry no condition. Negations are deliberately absent: an unhandled "no" must reject.
FILLER = set('''
a al an and any are as at be been by cada con cual cuales cuyo cuya dame de del dime display do does el en ensename es esta
este for from get give ha han has have hay i in is it la las lista listame listar list lo los me muestra muestrame mi my of
on or o u para please por favor que quiero se sea sean show son su sus tell that the their them these this those tiene tienen
to todas todos todo all total totales un una unas unos ver was were what which with y e ya also tambien ademas solo solamente
unicamente only just busca buscar buscame find search encuentra obten consulta saber necesito want need algunas algunos alguna
some hayan haya esos esas estos estas aquellas aquellos mas menos tipo tipos type types sobre about cuales segun le les nos
actualmente currently ahora now como among ser estar existen existe exist exists there donde where cuyo whose into dentro durante during
'''.split())
NEGATION_BEFORE = r'(?:\b(?:no|not|nunca|never)\b(?:\s+[a-z]+){0,3}?\s+|\b(?:sin|without|excluyendo|excluding|excepto|except|salvo|fuera del?|outside)\b(?:\s+[a-z]+){0,2}?\s+)'

COMPARATORS = (
    ('ge', r'mayor(?:es)? o igual(?:es)? (?:a|que|de|al)|igual(?:es)? o (?:superior(?:es)?|mayor(?:es)?) (?:a|que|al)|superior(?:es)? o igual(?:es)? (?:a|al)'
           r'|al menos|como minimo|minimo(?: de)?|no (?:menos|inferior(?:es)?) (?:de|a|que)|>=|at least|greater than or equal to'
           r'|equal to or (?:greater|higher|more) than|no less than|minimum(?: of)?'),
    ('le', r'menor(?:es)? o igual(?:es)? (?:a|que|de|al)|igual(?:es)? o (?:inferior(?:es)?|menor(?:es)?) (?:a|que|al)|inferior(?:es)? o igual(?:es)? (?:a|al)'
           r'|como maximo|maximo(?: de)?|no (?:mas|superior(?:es)?) (?:de|a|que)|<=|at most|less than or equal to|no more than|maximum(?: of)?'),
    ('gt', r'mayor(?:es)? (?:que|de|a|al)|superior(?:es)? (?:a|al)|por encima (?:de|del)|mas de|>|above|over|greater than|higher than|more than|exceeding'),
    ('lt', r'menor(?:es)? (?:que|de|a|al)|inferior(?:es)? (?:a|al)|por debajo (?:de|del)|menos de|<|below|under|less than|lower than'),
    ('eq', r'igual(?:es)? a|exactamente|equal to|exactly|='),
)
COMPARATOR = '(?:' + '|'.join(f'(?P<{name}>{pattern})' for name, pattern in COMPARATORS) + ')'
BETWEEN_WORDS = r'(?:entre|between)'
GAP = r'(?:\s+(?:de|del|score|puntuacion|base|of|con|una?|is|es|sea|que sea|que es|el|la|the))*'
OR_MORE = r'(?:o mas|o superior(?:es)?|o mayor(?:es)?|or more|or higher|or above|or greater|\+)'
OR_LESS = r'(?:o menos|o inferior(?:es)?|o menor(?:es)?|or less|or lower|or below)'
COUNT = r'\b(?:cuant[ao]s|cuenta|contar|recuento(?: de)?|numero de|how many|count(?: of)?|number of|total de)\b'
GROUP_VERBS = r'\b(?:agrup\w*|distribu\w*|desglos\w*|group\w*|breakdown|evolucion|tendencia|trend|ranking)\b'
RECENT = (r'(?:primero\s+)?(?:(?:las|los|the)\s+)?(?:mas\s+recientes|recientes?|most\s+recent|recent|latest|newest|mas\s+nuev[ao]s|ultimas)'
          r'(?:\s+(?:primero|first))?')
LIMITS = (r'\btop\s+(\d{1,3})(?:\s+de)?\b', r'\b(?:(?:las|los|the)\s+)?(\d{1,3})\s+(?:primer[ao]s|first)\b',
          r'\b(?:las|los|the|first|primer[ao]s)\s+(\d{1,3})\b(?!\s*(?:dias|days|semanas|weeks|%|,\d|\.\d))')


@dataclass(frozen=True)
class Condition:
    field: str
    op: str          # is, eq, gt, ge, lt, le, in, contains
    value: object


@dataclass(frozen=True)
class Plan:
    record_set: str
    mode: str                      # lookup, filter, aggregate
    ids: tuple = ()
    conditions: tuple = ()
    order: tuple | None = None     # (field, 'desc' | 'asc')
    limit: int | None = None
    aggregate: tuple | None = None  # ('count',) or ('group', key)

    def as_dict(self):
        result = {'record_set': self.record_set, 'mode': self.mode}
        if self.ids:
            result['ids'] = list(self.ids)
        if self.conditions:
            result['conditions'] = [[item.field, item.op, list(item.value) if isinstance(item.value, tuple) else item.value]
                                    for item in self.conditions]
        if self.order:
            result['order'] = list(self.order)
        if self.limit:
            result['limit'] = self.limit
        if self.aggregate:
            result['aggregate'] = list(self.aggregate)
        return result


@dataclass(frozen=True)
class Rejection:
    """The question is about these records but part of it was not understood, so nothing is run."""
    reason: str                    # words, range, combination, date
    items: tuple = ()
    suggestions: tuple = ()


def normalize(text):
    text = unicodedata.normalize('NFKD', str(text).translate(HYPHENS))
    text = ''.join(char for char in text if not unicodedata.combining(char)).casefold()
    return re.sub(r'\s+', ' ', text).strip()


def _terms(terms):
    """Regex for a list of words or phrases; a trailing * makes a word a stem."""
    parts = []
    for term in sorted({normalize(term) for term in terms}, key=len, reverse=True):
        stem = term.endswith('*')
        body = r'\s+'.join(re.escape(word) for word in term.rstrip('*').split())
        parts.append(body + (r'[a-z]*' if stem else ''))
    return r'(?<![a-z0-9])(?:' + '|'.join(parts) + r')(?![a-z0-9])' if parts else r'(?!x)x'


class _Scan:
    """The question plus a record of which characters some rule has understood."""

    def __init__(self, text):
        self.text = text
        self.used = bytearray(len(text))

    def free(self, start, end):
        return not any(self.used[start:end])

    def mark(self, start, end):
        self.used[start:end] = b'\x01' * (end - start)

    def matches(self, pattern):
        return [match for match in re.finditer(pattern, self.text) if self.free(*match.span())]

    def take(self, pattern):
        for match in self.matches(pattern):
            self.mark(*match.span())
            return match
        return None

    def take_all(self, pattern):
        found = self.matches(pattern)
        for match in found:
            self.mark(*match.span())
        return found

    def leftover(self, filler):
        words = []
        for match in re.finditer(r'[a-z0-9]+(?:[.+#_-][a-z0-9]+)*', self.text):
            if not all(self.used[match.start():match.end()]) and match[0] not in filler:
                words.append(match)
        return words


def _number(value, percent, spec):
    number = float(value.replace(',', '.'))
    if spec.get('percent') and (percent or number > spec['range'][1]):
        number /= 100
    return number


def _atom(tag):
    return (rf'(?:(?P<{tag}iso>\d{{4}}-\d{{2}}-\d{{2}})'
            rf'|(?P<{tag}d>\d{{1,2}})(?:\s+de)?\s+(?P<{tag}m>{MONTH})(?:\s+de|\s+del)?\s+(?P<{tag}y>{YEAR})'
            rf'|(?P<{tag}m2>{MONTH})\s+(?P<{tag}d2>\d{{1,2}}),?\s+(?P<{tag}y2>{YEAR})'
            rf'|(?P<{tag}m3>{MONTH})(?:\s+de|\s+del)?\s+(?P<{tag}y3>{YEAR})'
            rf'|(?P<{tag}y4>{YEAR})(?![-\d]))')


def _span(match, tag):
    """Inclusive (first day, last day) of a date written as a day, a month or a year."""
    group = match.groupdict()
    if group.get(f'{tag}iso'):
        day = date.fromisoformat(group[f'{tag}iso'])
        return day, day
    for day_key, month_key, year_key in ((f'{tag}d', f'{tag}m', f'{tag}y'), (f'{tag}d2', f'{tag}m2', f'{tag}y2')):
        if group.get(year_key):
            day = date(int(group[year_key]), MONTHS[group[month_key]], int(group[day_key]))
            return day, day
    if group.get(f'{tag}y3'):
        year, month = int(group[f'{tag}y3']), MONTHS[group[f'{tag}m3']]
        return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])
    year = int(group[f'{tag}y4'])
    return date(year, 1, 1), date(year, 12, 31)


def _dates(scan, today):
    """[(position, first, last)] for every date expression; either bound may be None."""
    found = []

    def add(match, first, last):
        found.append((match.start(), first, last))

    for match in scan.take_all(rf'(?:entre|between|from|de)\s+(?P<ma>{MONTH})\s+(?:y|and|to|a)\s+(?P<mb>{MONTH})\s+(?:de\s+|del\s+|of\s+)?(?P<my>{YEAR})'):
        year = int(match['my'])
        add(match, date(year, MONTHS[match['ma']], 1), date(year, MONTHS[match['mb']], calendar.monthrange(year, MONTHS[match['mb']])[1]))
    for match in scan.take_all(rf'(?:entre|between){ARTICLE}\s+{_atom("a")}\s+(?:y|and){ARTICLE}\s+{_atom("b")}'):
        add(match, _span(match, 'a')[0], _span(match, 'b')[1])
    for match in scan.take_all(r'(?:en\s+)?(?:los\s+|las\s+|the\s+)?(?:ultim[oa]s|last|past)\s+(\d{1,4})\s+(dias|days|semanas|weeks)'):
        days = int(match[1]) * (7 if match[2] in ('semanas', 'weeks') else 1)
        add(match, today - timedelta(days=days), today)
    for match in scan.take_all(r'(?:en\s+)?(?:este ano|this year|lo que va de ano)'):
        add(match, date(today.year, 1, 1), today)
    for match in scan.take_all(r'(?:en\s+)?(?:este mes|this month)'):
        add(match, date(today.year, today.month, 1), today)
    for match in scan.take_all(r'(?:el\s+)?(?:ano pasado|last year)'):
        add(match, date(today.year - 1, 1, 1), date(today.year - 1, 12, 31))
    for match in scan.take_all(rf'(?:desde|a partir del?|since|from|starting){ARTICLE}\s+{_atom("a")}(?:\s+(?:onwards|en adelante))?'):
        add(match, _span(match, 'a')[0], None)
    for match in scan.take_all(rf'(?:despues del?|posteriores? al?|after|later than){ARTICLE}\s+{_atom("a")}'):
        add(match, _span(match, 'a')[1] + timedelta(days=1), None)
    for match in scan.take_all(rf'(?:antes del?|anteriores? al?|before|prior to|earlier than){ARTICLE}\s+{_atom("a")}'):
        add(match, None, _span(match, 'a')[0] - timedelta(days=1))
    for match in scan.take_all(rf'(?:hasta|until|through|up to){ARTICLE}\s+{_atom("a")}'):
        add(match, None, _span(match, 'a')[1])
    for match in scan.take_all(rf'(?:en|durante|in|during|del?|of){ARTICLE}(?:\s+(?:ano|year))?\s+{_atom("a")}'):
        add(match, *_span(match, 'a'))
    return sorted(found)


def _numbers(scan, name, spec, conditions):
    """Comparisons written around one numeric field. Returns an out-of-range value, if any."""
    term = _terms(spec['terms'])
    article = r'(?:\s+(?:el|la|los|las|the|un|una))?'
    forms = (
        (rf'{term}{GAP}\s+{BETWEEN_WORDS}\s+{NUMBER}\s+(?:y|and)\s+{NUMBER}', 'between'),
        (rf'{term}{GAP}\s+(?:de\s+)?{NUMBER}\s+(?:a|to)\s+{NUMBER}', 'between'),
        (rf'{term}{GAP}\s+{COMPARATOR}{article}\s*{NUMBER}', 'compare'),
        (rf'{COMPARATOR}{article}\s*(?:de\s+|del\s+|of\s+)?{term}{GAP}\s+{NUMBER}', 'compare'),
        (rf'{term}{GAP}\s+{NUMBER}\s*{OR_MORE}', 'ge'),
        (rf'{term}{GAP}\s+{NUMBER}\s*{OR_LESS}', 'le'),
    )
    for pattern, kind in forms:
        for match in scan.take_all(pattern):
            numbers = [(value, percent) for value, percent in re.findall(NUMBER, match[0]) if value]
            # The numbers of the expression are its last one or two captures; a digit inside the term is not one.
            values = [_number(value, percent, spec) for value, percent in numbers[-(2 if kind == 'between' else 1):]]
            low, high = spec.get('range', (float('-inf'), float('inf')))
            if any(not low <= value <= high for value in values):
                return match[0]
            if kind == 'between':
                conditions += [Condition(name, 'ge', min(values)), Condition(name, 'le', max(values))]
            else:
                op = kind if kind != 'compare' else next(key for key, _ in COMPARATORS if match.group(key))
                conditions.append(Condition(name, op, values[0]))
    return None


def _vocabulary(words, parents, children, scan):
    """Resolve leftover names against the published vocabulary: (parent, child, unresolved matches).

    `parents` maps each parent value (a vendor) to its child values (products). A parent may be
    written in full or as an unambiguous beginning; a child must be contained in a child value.
    """
    runs, current = [], []
    for match in words:
        if current and scan.text[current[-1].end():match.start()].strip():
            runs.append(current)
            current = []
        current.append(match)
    if current:
        runs.append(current)
    parent = child = None
    resolved = set()
    # First the parent: the longest stretch that is a parent's full name or its unique beginning.
    best = None
    for run in runs:
        for start in range(len(run)):
            for end in range(len(run), start, -1):
                phrase = scan.text[run[start].start():run[end - 1].end()]
                exact = phrase in parents
                beginnings = [value for value in parents if value.startswith(phrase + ' ')] if not exact and len(phrase) >= 4 else []
                if (exact or len(beginnings) == 1) and (best is None or len(phrase) > len(best[0])):
                    best = (phrase, phrase if exact else beginnings[0], run[start:end])
    if best:
        parent = best[1]
        resolved.update(id(match) for match in best[2])
        # Other words of that parent's name elsewhere in the question are the same mention.
        names = set(re.findall(r'[a-z0-9]+', parent))
        resolved.update(id(match) for run in runs for match in run if match[0] in names)
    for run in runs:
        group = [match for match in run if id(match) not in resolved]
        if not group:
            continue
        phrase = scan.text[group[0].start():group[-1].end()]
        pattern = rf'(?<![a-z0-9-]){re.escape(phrase)}(?![a-z0-9-])'
        owners = {owner for owner, values in parents.items() if (parent is None or owner == parent)
                  and any(re.search(pattern, value) for value in values)}
        contiguous = all(not scan.text[left.end():right.start()].strip() for left, right in zip(group, group[1:]))
        if owners and child is None and contiguous and len(phrase) >= 3 and children:
            child = phrase
            resolved.update(id(match) for match in group)
            if parent is None and len(owners) == 1:
                parent = next(iter(owners))
    return parent, child, [match for match in words if id(match) not in resolved]


def plan(question, record_set, *, today=None, vocabulary=None, lookup_only=False):
    """Plan, Rejection, or None when the question states nothing these records can answer.

    `vocabulary` maps each parent value of the vocabulary fields to its child values, as
    published (for example vendor -> products). `lookup_only` is for scopes where only a
    question carrying an identifier may use the records.
    """
    text = normalize(question)
    identifier = record_set['identifier']
    found = [identifier['canonical'].format(None, *groups) if isinstance(groups, tuple) else identifier['canonical'].format(None, groups)
             for groups in re.findall(identifier['pattern'], text)]
    if found:
        return Plan(record_set['id'], 'lookup', ids=tuple(dict.fromkeys(found)))
    if lookup_only:
        return None
    today = date.fromisoformat(today) if isinstance(today, str) else (today or date.today())
    fields = record_set['fields']
    scan = _Scan(text)
    conditions = []

    for name, spec in fields.items():
        if spec['type'] == 'identifiers':
            values = [spec['canonical'].format(None, int(match[1]) if match[1].isdigit() else match[1]) for match in scan.take_all(spec['pattern'])]
            if values:
                conditions.append(Condition(name, 'in', tuple(dict.fromkeys(values))))

    for name, spec in fields.items():
        if spec['type'] != 'enum':
            continue
        for value, definition in spec['values'].items():
            if scan.take_all(_terms(definition['terms'])):
                conditions.append(Condition(name, 'eq', value))
                scan.take_all(_terms(spec.get('terms', [])))   # "del ecosistema Ubuntu": the label goes with the value
                break

    # Dates: each expression belongs to the date field named closest before it.
    date_fields = {name: spec for name, spec in fields.items() if spec['type'] == 'date'}
    expressions = _dates(scan, today)
    mentions = sorted((match.start(), name) for name, spec in date_fields.items() for match in scan.take_all(_terms(spec['terms'])))
    named = [name for _, name in mentions]

    def date_field(position):
        # With several date fields and none named, a date is ambiguous: say so instead of choosing one.
        before = [name for start, name in mentions if start < position]
        if before or named:
            return before[-1] if before else named[0]
        return next(iter(date_fields)) if len(date_fields) == 1 else None

    undated = False   # reported at the end, after any word that was not understood at all
    for position, first, last in expressions:
        name = date_field(position)
        if name is None:
            undated = True
            continue
        if first:
            conditions.append(Condition(name, 'ge', first.isoformat()))
        if last:
            conditions.append(Condition(name, 'le', last.isoformat()))

    for name, spec in fields.items():
        if spec['type'] == 'number':
            wrong = _numbers(scan, name, spec, conditions)
            if wrong:
                return Rejection('range', (wrong,))

    order = None
    for name, spec in fields.items():
        if spec['type'] not in ('number', 'date') or order:
            continue
        term = _terms(spec['terms'])
        padding = r'(?:probabilidad\s+|puntuacion\s+|score\s+|valor\s+)?(?:de\s+)?'
        if spec.get('sort_terms') and scan.take(_terms(spec['sort_terms'])):
            order = (name, 'desc')
        elif scan.take(rf'(?:con\s+)?(?:(?:el|la|los|las|the)\s+)?(?:mayor(?:es)?|mas\s+alt[oa]s?|maxim[oa]s?|highest|greatest|largest)\s+{padding}{term}(?:\s+score)?'):
            order = (name, 'desc')
        elif scan.take(rf'(?:con\s+)?(?:(?:el|la|los|las|the)\s+)?(?:menor(?:es)?|mas\s+baj[oa]s?|minim[oa]s?|lowest|smallest)\s+{padding}{term}(?:\s+score)?'):
            order = (name, 'asc')
        else:
            match = scan.take(rf'(?:ordenad[ao]s?\s+por|orden\s+por|sorted\s+by|ordered\s+by|order\s+by|por|by|segun)\s+{padding}{term}(?:\s+score)?'
                              r'(?:\s+(?P<direction>ascendente|descendente|ascending|descending|asc|desc))?')
            if match:
                order = (name, 'asc' if (match['direction'] or '').startswith('asc') else 'desc')

    for name, spec in fields.items():
        if spec['type'] != 'flag':
            continue
        term = _terms(spec['terms'])
        if scan.take_all(NEGATION_BEFORE + term):
            conditions.append(Condition(name, 'is', False))
        elif scan.take_all(term):
            conditions.append(Condition(name, 'is', True))

    # Counts and groupings. A grouping wins over a count written alongside it.
    aggregate = None
    counted = bool(scan.take_all(COUNT))
    connector = r'(?:por|by|per|segun)\s+(?:(?:el|la|los|las|the|cada|each)\s+)?'
    for unit, words in (('month', r'(?:(?:por|by|per|cada|each)\s+(?:mes|month)|mensual\w*|monthly)'),
                        ('year', r'(?:(?:por|by|per|cada|each)\s+(?:ano|year)|anual\w*|yearly|annual\w*)')):
        if aggregate is None and scan.take(words):
            name = named[-1] if named else (next(iter(date_fields)) if len(date_fields) == 1 else None)
            if name is None:
                undated = True
                continue
            aggregate = ('group', f'{name}:{unit}')
    for name, spec in fields.items():
        if aggregate or not spec.get('group_terms'):
            continue
        term = _terms(spec['group_terms'])
        if (scan.take(rf'{connector}{term}') or scan.take(rf'{term}\s+(?:mas|most)\s+(?:comunes|frecuentes|habituales|common|frequent)')
                or scan.take(rf'(?:most|mas)\s+(?:common|frequent|comunes|frecuentes)\s+{term}')
                or scan.take(rf'(?:que|which|what|cuales)\s+{term}\s+(?:tienen|tiene|have|has|acumulan|concentran)\s+(?:the\s+)?(?:mas|most|more)')):
            aggregate = ('group', name)
    if aggregate:
        scan.take_all(GROUP_VERBS)
    elif counted:
        aggregate = ('count',)

    if order is None and scan.take(RECENT):
        truths = {item.field: item.value for item in conditions if item.op == 'is'}
        for rule in record_set.get('recency', []):
            if all(truths.get(key) == value for key, value in rule.get('when', {}).items()):
                order = (rule['field'], 'desc')
                break
    limit = None
    for pattern in LIMITS:
        match = scan.take(pattern)
        if match:
            limit = int(match[1])
            break

    scan.take_all(_terms(record_set.get('subject', [])))
    for spec in fields.values():
        # Words that only accompany a field are understood once that field has produced something.
        if spec.get('also') and any(item.field == name for item in conditions for name, other in fields.items() if other is spec):
            scan.take_all(_terms(spec['also']))
    filler = FILLER | {normalize(word) for word in record_set.get('filler', [])}
    words = scan.leftover(filler)

    parents = {normalize(key): [normalize(value) for value in values] for key, values in (vocabulary or {}).items()}
    parent_field = next((name for name, spec in fields.items() if spec['type'] == 'vocabulary' and not spec.get('parent')), None)
    child_field = next((name for name, spec in fields.items() if spec['type'] == 'vocabulary' and spec.get('parent')), None)
    if words and parent_field:
        labels = _terms([term for name in (parent_field, child_field) if name for term in fields[name].get('terms', [])])
        names = [match for match in words if not re.fullmatch(labels, match[0])]
        parent, child, unresolved = _vocabulary(names, parents, child_field, scan)
        if parent:
            conditions.append(Condition(parent_field, 'eq', parent))
        if child:
            conditions.append(Condition(child_field, 'contains', child))
        if parent or child:
            # "del proveedor X", "producto Y": the label words belong to the resolved name.
            words = unresolved
        else:
            words = [match for match in words]

    recognised = bool(conditions or order or aggregate or undated)
    if not recognised:
        return None
    if words:
        import difflib
        candidates = list(parents) + [value for values in parents.values() for value in values]
        close = [name for match in words for name in difflib.get_close_matches(match[0], candidates, n=2, cutoff=0.75)]
        return Rejection('words', tuple(dict.fromkeys(match[0] for match in words)), tuple(dict.fromkeys(close)))
    if undated:
        return Rejection('date', tuple(date_fields))
    # A record cannot be absent from a table and also satisfy a condition stored in that table.
    for item in conditions:
        table = fields[item.field].get('present_in')
        if item.op == 'is' and item.value is False and table:
            clash = [other.field for other in conditions if other is not item
                     and fields[other.field].get('column', '').split('.')[0] == table]
            if clash:
                return Rejection('combination', (item.field, *clash))
    if limit is not None and not 1 <= limit <= MAX_LIMIT:
        return Rejection('range', (str(limit),))
    return Plan(record_set['id'], 'aggregate' if aggregate else 'filter', conditions=tuple(conditions), order=order,
                limit=limit, aggregate=aggregate)


OPERATORS = {'eq': '=', 'gt': '>', 'ge': '≥', 'lt': '<', 'le': '≤', 'contains': '∋', 'in': '∈'}


def describe(plan, record_set, language='es'):
    """The conditions actually applied, written by the engine and never by a model."""
    english = language == 'en'
    fields = record_set['fields']

    def label(name):
        value = fields[name].get('label', name)
        return value.get('en' if english else 'es', name) if isinstance(value, dict) else value

    parts = []
    for item in plan.conditions:
        if item.op == 'is':
            parts.append(f"{label(item.field)}: {('yes' if english else 'sí') if item.value else 'no'}")
        else:
            value = ', '.join(item.value) if isinstance(item.value, tuple) else item.value
            parts.append(f'{label(item.field)} {OPERATORS[item.op]} {value}')
    text = ('Conditions applied: ' if english else 'Condiciones aplicadas: ') + ('; '.join(parts) or ('none' if english else 'ninguna'))
    if plan.order:
        direction = ('descending' if plan.order[1] == 'desc' else 'ascending') if english else ('descendente' if plan.order[1] == 'desc' else 'ascendente')
        text += f" · {'Order' if english else 'Orden'}: {label(plan.order[0])}, {direction}"
    if plan.limit:
        text += f" · {'Limit' if english else 'Límite'}: {plan.limit}"
    if plan.aggregate:
        if plan.aggregate[0] == 'count':
            text += ' · ' + ('Count' if english else 'Recuento')
        else:
            name, _, unit = plan.aggregate[1].partition(':')
            units = {'year': 'year' if english else 'año', 'month': 'month' if english else 'mes'}
            text += f" · {'Grouped by' if english else 'Agrupado por'}: {label(name)}" + (f' ({units[unit]})' if unit else '')
    return text
