"""
Valve MTO — Async Job Runner
============================

Tiny job framework on top of Django's cache (Redis in prod, locmem in dev).
Avoids the need for a Celery task definition just for one extraction job.

Lifecycle
---------
    * `start_job(pdf_path, file_name)` → returns job_id, spawns a daemon thread
      that runs `extract_valve_mto_streaming(...)`.
    * The extractor calls back into `JobStore.update(job_id, ...)` after each
      batch so the frontend can poll incremental progress.
    * `JobStore.get(job_id)` returns the current snapshot.

State shape (as stored in cache):
    {
      "status":     "queued" | "running" | "done" | "error",
      "progress":   { "current": int, "total": int, "rows": int },
      "engine":     "vision",
      "page_count": int,
      "rows":       [ ... ],
      "project_meta": { ... },
      "warnings":   [ ... ],
      "error":      "<message if status==error>",
      "started_at": ISO-8601,
      "updated_at": ISO-8601,
      "filename":   "<original upload name>"
    }

Soft-coded:
    * Cache prefix and TTL.
    * Maximum age before a stale job is treated as gone.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

# ─── Soft-coded constants ────────────────────────────────────────────────
CACHE_PREFIX = 'valve_mto:job:'
CACHE_TTL    = 60 * 60 * 6                # 6 hours — plenty for any user flow
DEFAULT_KIND = 'valve_mto'


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ─── Cache-backed job store ──────────────────────────────────────────────
class JobStore:
    @staticmethod
    def _key(job_id: str) -> str:
        return f'{CACHE_PREFIX}{job_id}'

    @staticmethod
    def create(initial: Dict[str, Any]) -> str:
        job_id = uuid.uuid4().hex
        snapshot = {
            'status':     'queued',
            'progress':   {'current': 0, 'total': 0, 'rows': 0},
            'rows':       [],
            'project_meta': {},
            'warnings':   [],
            'error':      None,
            'engine':     'vision',
            'page_count': 0,
            'started_at': _now_iso(),
            'updated_at': _now_iso(),
            **initial,
        }
        cache.set(JobStore._key(job_id), snapshot, timeout=CACHE_TTL)
        return job_id

    @staticmethod
    def get(job_id: str) -> Optional[Dict[str, Any]]:
        return cache.get(JobStore._key(job_id))

    @staticmethod
    def update(job_id: str, **patch) -> None:
        snap = cache.get(JobStore._key(job_id))
        if not snap:
            logger.warning('[ValveMTO] update on missing job %s', job_id)
            return
        snap.update(patch)
        snap['updated_at'] = _now_iso()
        cache.set(JobStore._key(job_id), snap, timeout=CACHE_TTL)

    @staticmethod
    def merge_progress(job_id: str, *, current: int, total: int, rows_so_far: int) -> None:
        snap = cache.get(JobStore._key(job_id))
        if not snap:
            return
        snap['progress'] = {'current': current, 'total': total, 'rows': rows_so_far}
        snap['updated_at'] = _now_iso()
        cache.set(JobStore._key(job_id), snap, timeout=CACHE_TTL)


# ─── Background runner ──────────────────────────────────────────────────
def _run_in_thread(job_id: str, pdf_path: str, filename: str) -> None:
    """Execute extraction; clean up the temp file when done."""
    # Late import — keeps this module importable without the heavy deps.
    from .piping_valve_mto_extractor import extract_valve_mto_streaming

    try:
        JobStore.update(job_id, status='running')
        result = extract_valve_mto_streaming(
            pdf_path=pdf_path,
            on_progress=lambda current, total, rows_so_far: JobStore.merge_progress(
                job_id, current=current, total=total, rows_so_far=rows_so_far,
            ),
            on_partial=lambda rows, meta: JobStore.update(
                job_id, rows=rows, project_meta=meta,
            ),
        )
        JobStore.update(
            job_id,
            status='done',
            engine=result.get('engine', 'vision'),
            page_count=result.get('page_count', 0),
            rows=result.get('rows', []),
            project_meta=result.get('project_meta', {}),
            warnings=result.get('warnings', []),
        )
    except Exception as exc:                                      # pragma: no cover
        logger.exception('[ValveMTO] job %s crashed', job_id)
        JobStore.update(job_id, status='error', error=str(exc))
    finally:
        try:
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
        except OSError:
            pass


def start_job(pdf_path: str, filename: str) -> str:
    """Create a job snapshot in cache and start a daemon thread."""
    job_id = JobStore.create({'filename': filename})
    th = threading.Thread(
        target=_run_in_thread,
        args=(job_id, pdf_path, filename),
        name=f'valve-mto-{job_id[:8]}',
        daemon=True,
    )
    th.start()
    logger.info('[ValveMTO] job %s started for %s', job_id, filename)
    return job_id
