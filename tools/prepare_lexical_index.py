"""Experimentally add/remove optional FTS5 with an explicit verified backup."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from index_lock import index_writer
import rag


def backup_database(config, source):
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    directory = config.db.parent / 'vector-backups' / f'{stamp}-{config.collection}-lexical'
    directory.mkdir(parents=True, exist_ok=False)
    target = directory / source.name
    source_db = sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)
    target_db = sqlite3.connect(target)
    try:
        source_db.backup(target_db)
        if target_db.execute('PRAGMA integrity_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite backup integrity check failed.')
    finally:
        target_db.close()
        source_db.close()
    manifest = config.db / f'{config.collection}-manifest.json'
    if manifest.exists():
        shutil.copy2(manifest, directory / manifest.name)
    return directory


def status(store):
    with store.connect() as db:
        version = db.execute('PRAGMA user_version').fetchone()[0]
        vectors = db.execute('SELECT COUNT(*) FROM vectors').fetchone()[0]
    return {'collection': store.path.stem.removesuffix('-vectors'),
            'user_version': version, 'vectors': vectors,
            'lexical_index': store.lexical_index_available()}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--remove', action='store_true', help='Remove only the derived FTS index.')
    action.add_argument('--status', action='store_true', help='Read status without changing files.')
    parser.add_argument('--db', type=Path, help='Override the configured vector directory.')
    parser.add_argument('--collection', help='Override RAG_COLLECTION.')
    args = parser.parse_args()
    config = rag.Config()
    if args.db:
        config = replace(config, db=args.db.resolve())
    if args.collection:
        config = replace(config, collection=args.collection)
    path = config.db / f'{config.collection}-vectors.sqlite3'
    if not path.is_file():
        raise SystemExit(f'Vector database not found: {path}')
    store = rag.open_store(config, None)
    if args.status:
        print(json.dumps(status(store), ensure_ascii=False, indent=2))
        return
    with index_writer(config):
        available = store.lexical_index_available()
        if (args.remove and not available) or (not args.remove and available):
            report = status(store)
            report.update({'action': 'unchanged', 'backup': None,
                           'models_loaded': 0, 'network_calls': 0})
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return
        backup = backup_database(config, path)
        print(f'Backup: {backup}', flush=True)
        if args.remove:
            store.remove_lexical_index()
            action_name = 'removed'
        else:
            store.ensure_lexical_index()
            action_name = 'prepared'
        report = status(store)
        if not args.remove:
            with store.connect() as db:
                fts_rows = db.execute('SELECT COUNT(*) FROM vectors_fts').fetchone()[0]
                mapping_rows = db.execute('SELECT COUNT(*) FROM fts_rows').fetchone()[0]
                missing = db.execute('''SELECT COUNT(*) FROM vectors v
                    LEFT JOIN fts_rows f ON f.id=v.id WHERE f.id IS NULL''').fetchone()[0]
                missing_fts = db.execute('''SELECT COUNT(*) FROM fts_rows f
                    LEFT JOIN vectors_fts v ON v.rowid=f.docid WHERE v.rowid IS NULL''').fetchone()[0]
            if fts_rows != report['vectors'] or mapping_rows != report['vectors'] or missing or missing_fts:
                raise RuntimeError(f'FTS verification failed; restore backup: {backup}')
            report.update({'fts_rows': fts_rows, 'mapping_rows': mapping_rows,
                           'missing_vectors': missing, 'missing_fts_rows': missing_fts})
        report.update({'action': action_name, 'backup': str(backup),
                       'models_loaded': 0, 'network_calls': 0})
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
