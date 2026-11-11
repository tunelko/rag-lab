"""Domain profiles: declarative descriptions of structured record sets.

A profile lives in profiles/<id>/profile.json and is the only place that knows a domain's
fields, words and views. The engine that plans, runs and renders record questions is
generic and reads these declarations. With no active profile the application is a plain
document assistant.

A profile is installed, trusted configuration, not user input. Even so, every view, column
and alias it names is checked against a strict identifier pattern when it is loaded, so
nothing from a manifest can reach SQL except as a known name.
"""
from __future__ import annotations

from functools import lru_cache
import json
import os
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parent / 'profiles'
NAME = re.compile(r'[a-z_][a-z0-9_]{0,63}\Z')
COLUMN = re.compile(r'[a-z_][a-z0-9_]{0,15}\.[a-z_][a-z0-9_]{0,63}\Z')
FIELD_TYPES = {'flag', 'number', 'date', 'identifiers', 'vocabulary', 'enum'}


class ProfileError(ValueError):
    pass


def _name(value, what):
    if not isinstance(value, str) or not NAME.fullmatch(value):
        raise ProfileError(f'Invalid {what}: {value!r}')
    return value


def _column(value, tables, what):
    if not isinstance(value, str) or not COLUMN.fullmatch(value) or value.split('.')[0] not in tables:
        raise ProfileError(f'Invalid {what}: {value!r}')
    return value


def _view(spec, what):
    _name(spec.get('view'), f'{what} view')
    _name(spec.get('column', spec.get('key')), f'{what} column')


def validate(profile):
    """Raise ProfileError unless every SQL name in the profile is a plain identifier of a declared table."""
    _name(profile.get('id'), 'profile id')
    for record_set in profile.get('record_sets', []):
        _name(record_set.get('id'), 'record set id')
        tables = record_set.get('tables', {})
        if not tables:
            raise ProfileError('A record set needs at least one table.')
        for alias, table in tables.items():
            _name(alias, 'table alias')
            _view(table, f'table {alias}')
        re.compile(record_set['identifier']['pattern'])
        visible = record_set.get('visible_when')
        if visible:
            _column(visible['column'], tables, 'visibility column')
        for name, column in record_set.get('columns', {}).items():
            _name(name, 'record column name')
            _column(column, tables, f'record column {name}')
        for name, detail in record_set.get('details', {}).items():
            _name(name, 'detail name')
            _view(detail, f'detail {name}')
            for column in detail.get('columns', []):
                _name(column, f'detail {name} column')
        for name, spec in record_set.get('fields', {}).items():
            _name(name, 'field name')
            if spec.get('type') not in FIELD_TYPES:
                raise ProfileError(f'Field {name} has an unknown type.')
            if 'column' in spec:
                _column(spec['column'], tables, f'field {name} column')
            if 'label_column' in spec:
                _column(spec['label_column'], tables, f'field {name} label column')
            if 'present_in' in spec and spec['present_in'] not in tables:
                raise ProfileError(f'Field {name} refers to an undeclared table.')
            if 'in' in spec:
                _view(spec['in'], f'field {name}')
            if spec['type'] == 'identifiers':
                re.compile(spec['pattern'])
            for value in spec.get('values', {}).values():
                for constraint in value.get('where', []):
                    _column(constraint['column'], tables, f'field {name} constraint')
            if spec.get('parent') and spec['parent'] not in record_set['fields']:
                raise ProfileError(f'Field {name} has an undeclared parent.')
        for rule in record_set.get('recency', []):
            if rule['field'] not in record_set['fields']:
                raise ProfileError('Recency refers to an undeclared field.')
    return profile


@lru_cache(maxsize=16)
def load(profile_id):
    _name(profile_id, 'profile id')
    path = ROOT / profile_id / 'profile.json'
    try:
        profile = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise ProfileError(f'Profile {profile_id} cannot be read.') from error
    if profile.get('id') != profile_id:
        raise ProfileError(f'Profile {profile_id} declares another id.')
    return validate(profile)


def active_ids():
    """Profiles enabled for this installation: RAG_PROFILES, comma separated. Empty means none."""
    return tuple(dict.fromkeys(item.strip().lower() for item in os.getenv('RAG_PROFILES', '').split(',') if item.strip()))


def active():
    return [load(profile_id) for profile_id in active_ids()]


def record_set(topic_id):
    """(profile, record_set) for a reserved topic such as 'records:vulnerabilities', or (None, None).

    'records' alone selects the first record set of the first active profile that has one.
    """
    if not topic_id or not str(topic_id).startswith('records'):
        return None, None
    wanted = str(topic_id).partition(':')[2]
    for profile in active():
        for candidate in profile.get('record_sets', []):
            if not wanted or candidate['id'] == wanted:
                return profile, candidate
    return None, None


def localized(value, language='es'):
    if isinstance(value, dict):
        return value.get('en' if language == 'en' else 'es') or next(iter(value.values()), '')
    return value
