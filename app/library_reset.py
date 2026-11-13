"""Empty a library by moving it into a dated backup folder. Nothing is deleted."""
from contextlib import closing
import errno
import os
from pathlib import Path
import shutil
import sqlite3
import time

RESTORE_NOTE = '''RAG Local - copia de la biblioteca vaciada / backup of the emptied library

ES: Para restaurarla, detén el servidor y devuelve cada carpeta a su sitio:
    documents/ -> data/documents/    index/* -> data/chroma/    visual/ -> data/visual/
    (sustituyendo lo que haya). Después arranca el servidor.
EN: To restore it, stop the server and move each folder back:
    documents/ -> data/documents/    index/* -> data/chroma/    visual/ -> data/visual/
    (replacing what is there). Then start the server.
'''


class LibraryBusyError(RuntimeError):
    pass


def _rename(source, target, attempts=6, delay=0.25):
    """Rename with a short wait for readers; a plain rename is all-or-nothing for a folder."""
    for attempt in range(attempts):
        try:
            os.rename(source, target)
            return
        except PermissionError:
            # Windows refuses to rename a file, or a folder holding one, that is open elsewhere.
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
        except OSError as error:
            if error.errno != errno.EXDEV:
                raise
            shutil.move(str(source), str(target))  # another volume: copy, then remove
            return


def index_files(config):
    """Index files of one collection, manifest first: without it nothing is published."""
    prefix = f'{config.collection}-'
    vectors = prefix + 'vectors.sqlite3'
    return [config.db / name for name in (prefix + 'manifest.json', vectors, vectors + '-wal',
                                          vectors + '-shm', prefix + 'topics.sqlite3')]


def empty_library(config, documents, visual_root, backups_root, *, keep_captions=False,
                  attempts=6, delay=0.25):
    """Move documents, index, topics and (optionally) the visual cache to a new backup folder.

    The caller holds the index writer lock and guarantees that no job is running.
    A failure moves everything back and raises LibraryBusyError; the writer lock file stays.
    """
    documents, visual_root = Path(documents), Path(visual_root)
    vectors = config.db / f'{config.collection}-vectors.sqlite3'
    if vectors.exists():
        try:
            # Fold the write-ahead log into the main file so the copy is self-contained.
            with closing(sqlite3.connect(str(vectors), timeout=5)) as db:
                db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        except sqlite3.Error:
            pass  # the log files travel with the database below
    stamp = time.strftime('%Y%m%d-%H%M%S')
    target = Path(backups_root) / stamp
    number = 1
    while target.exists():
        number += 1
        target = Path(backups_root) / f'{stamp}-{number}'
    (target / 'index').mkdir(parents=True)
    plan = [(path, target / 'index' / path.name) for path in index_files(config)]
    if not keep_captions:
        plan.append((visual_root, target / 'visual'))
    plan.append((documents, target / 'documents'))
    moved = []
    try:
        for source, destination in plan:
            if source.exists():
                _rename(source, destination, attempts, delay)
                moved.append((source, destination))
    except OSError as error:
        for source, destination in reversed(moved):
            try:
                os.replace(destination, source)
            except OSError:
                # A reader recreated an empty file meanwhile, or the folder is busy: keep the copy.
                continue
        try:
            (target / 'index').rmdir()
            target.rmdir()
        except OSError:
            pass
        raise LibraryBusyError(f'{Path(error.filename or "").name or type(error).__name__}') from error
    documents.mkdir(parents=True, exist_ok=True)
    keep = target / 'documents' / '.gitkeep'
    if keep.is_file():
        shutil.copyfile(keep, documents / '.gitkeep')
    if not keep_captions:
        visual_root.mkdir(parents=True, exist_ok=True)
    (target / 'LEEME-README.txt').write_text(RESTORE_NOTE, encoding='utf-8')
    return {'path': target, 'moved': [destination.relative_to(target).as_posix() for _, destination in moved]}
