"""Roll back source files created by a multi-document database transaction."""

from contextlib import contextmanager
from contextvars import ContextVar
import logging
from pathlib import PurePosixPath
from uuid import uuid4

from django.db import transaction


_sources = ContextVar('procurement_import_sources', default=None)
logger = logging.getLogger(__name__)


def save_import_source(storage, key, content):
    sources = _sources.get()
    if sources is not None:
        # A paired transaction must never overwrite an already retained source.
        path = PurePosixPath(key)
        key = str(path.with_name(f'{uuid4().hex}_{path.name}'))
    saved = storage.save(key, content)
    if sources is not None:
        sources.append((storage, saved))
    return saved


@contextmanager
def atomic_source_import():
    sources = []
    token = _sources.set(sources)
    try:
        with transaction.atomic():
            yield
    except Exception:
        for storage, key in reversed(sources):
            try:
                storage.delete(key)
            except Exception:
                logger.exception('Could not clean up a rolled-back procurement source.')
        raise
    finally:
        _sources.reset(token)
