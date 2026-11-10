"""Shared local answer strings; browser preferences remain per client."""

from functools import lru_cache
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=2)
def catalog(language):
    code = language if language in {'es', 'en'} else 'es'
    return json.loads((ROOT / 'dist' / 'locales' / f'{code}.json').read_text(encoding='utf-8'))


def text(key, language='es', **values):
    return catalog(language).get(key, catalog('es')[key]).format_map(values)
