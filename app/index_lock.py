"""A crash-released writer lock shared by CLI and web workers on Windows/Linux."""
from contextlib import contextmanager
import os


class IndexBusyError(RuntimeError):
    pass


@contextmanager
def index_writer(config):
    config.db.mkdir(parents=True, exist_ok=True)
    path = config.db / f'{config.collection}-writer.lock'
    with path.open('a+b') as handle:
        if handle.seek(0, 2) == 0:
            handle.write(b'0');handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError) as error:
            raise IndexBusyError('Otro proceso está escribiendo este índice. Espera a que termine.') from error
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
