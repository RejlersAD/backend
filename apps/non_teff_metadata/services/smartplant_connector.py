"""
SmartPlant Foundation (SPF) connector
-------------------------------------

Pluggable, fully soft-coded bridge that pushes a finished Master Index
batch directly into SmartPlant Foundation (or any compatible document-
control system) without leaving the RAD AI UI.

Design goals
~~~~~~~~~~~~
* **Additive** — touches no existing core extraction or export logic.
* **Soft-coded** — every endpoint, header, mapping, retry, timeout,
  workflow name and field name lives in
  ``config/smartplant_config.json``.  Edit JSON to retune behaviour.
* **Multi-mode** — one of the following transports (set by
  ``default_mode`` in JSON or ``mode=…`` in the API call):

  ============= ========================================================
  Mode          Behaviour
  ============= ========================================================
  ``disabled``   Feature off — connector returns an explicit error.
  ``dry_run``    Build the SPF JSON payload + Excel workbook and return
                 them to the caller WITHOUT calling SPF.  Default — lets
                 the UI work end-to-end before SPF credentials exist.
  ``rest_api``   POST a JSON envelope (with base64 workbook) to a
                 SPF-compatible REST endpoint.
  ``webhook``    POST the same envelope to a generic webhook
                 (e.g. Power Automate / n8n).
  ``s3_dropzone``Upload .xlsx + manifest.json to an S3 prefix that an
                 SPF Adapter is watching.
  ``local_dropzone`` Copy .xlsx + manifest.json to a server-mounted
                 folder watched by the on-prem SPF Adapter.
  ============= ========================================================

* **Secret-safe** — all credentials referenced by ``${ENV_VAR}`` strings
  in the JSON are resolved from process env at runtime.  No secret ever
  lives on disk.

Public API
~~~~~~~~~~
``push_batch(batch, items, workbook_bytes, *, mode=None, user=None)``
    Top-level entry called by ``batch_views.smartplant_push``.  Returns
    a structured result dict that is forwarded verbatim to the frontend.

``get_status()``
    Read-only snapshot used by the frontend to draw the SmartPlant
    button (label, mode, enabled flag, env-var readiness).
"""

from __future__ import annotations

import base64
import datetime as dt
import io
import json
import logging
import os
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Soft-coded constants (only the few that aren't worth living in JSON)
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'smartplant_config.json'

# Cache the resolved config for the lifetime of the worker.  Re-load on
# SIGHUP via ``reload_config()``.
_CACHE: Dict[str, Any] = {'config': None}

# Modes the connector accepts.  Centralised so the view layer can validate.
SUPPORTED_MODES = ('disabled', 'dry_run', 'rest_api', 'webhook',
                   's3_dropzone', 'local_dropzone')

# Reserved key used to stash audit history on ``NonTeffBatch.batch_defaults``
# so we don't need a schema migration.  Double underscores keep it out of
# the visible column space.
AUDIT_BATCH_DEFAULTS_KEY = '__smartplant_history__'


# ---------------------------------------------------------------------------
# Config loader
# ---------------------------------------------------------------------------

_ENV_VAR_RX = re.compile(r'\$\{([A-Z0-9_]+)\}')


def _expand_env(value: Any) -> Any:
    """Recursively resolve ``${ENV_VAR}`` placeholders inside any structure."""
    if isinstance(value, str):
        return _ENV_VAR_RX.sub(lambda m: os.getenv(m.group(1), ''), value)
    if isinstance(value, list):
        return [_expand_env(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand_env(v) for k, v in value.items()}
    return value


def _load_raw() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        logger.warning('SmartPlant config not found at %s — feature disabled', CONFIG_PATH)
        return {'enabled': False, 'default_mode': 'disabled'}
    try:
        with open(CONFIG_PATH, 'r', encoding='utf-8') as fh:
            return json.load(fh)
    except Exception:
        logger.exception('SmartPlant config %s could not be parsed', CONFIG_PATH)
        return {'enabled': False, 'default_mode': 'disabled'}


def get_config(*, fresh: bool = False) -> Dict[str, Any]:
    """Return the resolved (env-expanded) connector config."""
    if fresh or _CACHE.get('config') is None:
        raw = _load_raw()
        _CACHE['config'] = _expand_env(raw)
    return _CACHE['config']


def reload_config() -> Dict[str, Any]:
    """Force re-read from disk.  Exposed for management commands."""
    return get_config(fresh=True)


# ---------------------------------------------------------------------------
# Status (UI introspection)
# ---------------------------------------------------------------------------

def _missing_env_for_mode(cfg: Dict[str, Any], mode: str) -> List[str]:
    """List env-var names that are referenced but currently empty."""
    raw = _load_raw()
    raw_modes = (raw.get('modes') or {}).get(mode) or {}
    missing: List[str] = []
    for v in raw_modes.values():
        if isinstance(v, str):
            for env_name in _ENV_VAR_RX.findall(v):
                if not os.getenv(env_name):
                    missing.append(env_name)
    return sorted(set(missing))


def get_status() -> Dict[str, Any]:
    """Snapshot used by the frontend to draw the SmartPlant button."""
    cfg = get_config()
    default_mode = (cfg.get('default_mode') or 'disabled').strip().lower()
    if default_mode not in SUPPORTED_MODES:
        default_mode = 'disabled'
    enabled = bool(cfg.get('enabled')) and default_mode != 'disabled'
    return {
        'enabled':             enabled,
        'mode':                default_mode,
        'supported_modes':     list(SUPPORTED_MODES),
        'missing_env_vars':    _missing_env_for_mode(cfg, default_mode) if default_mode not in ('disabled', 'dry_run') else [],
        'transmittal_subject': (cfg.get('transmittal') or {}).get('subject_template', ''),
    }


# ---------------------------------------------------------------------------
# Payload builder
# ---------------------------------------------------------------------------

def _normalise_value(v: Any) -> Any:
    """Stringify cells; keep None / numbers as-is."""
    if v is None:
        return ''
    if isinstance(v, (int, float, bool)):
        return v
    return str(v)


def _map_row(row: Dict[str, Any], mapping: Dict[str, str], skip: Iterable[str]) -> Dict[str, Any]:
    """
    Translate a master-index row to SPF attribute names.

    * Keys in ``skip`` are dropped.
    * Keys present in ``mapping`` are renamed.
    * Unknown keys are passed through verbatim — keeps custom columns alive.
    """
    out: Dict[str, Any] = {}
    skip_set = {s.lower() for s in (skip or [])}
    for k, v in (row or {}).items():
        if not k or k.lower() in skip_set:
            continue
        target = mapping.get(k, k)
        out[target] = _normalise_value(v)
    return out


def build_envelope(*, batch, items: List[Dict[str, Any]],
                   workbook_bytes: bytes, user=None,
                   cfg: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """
    Build the SPF JSON envelope.  Pure function — no side effects.

    The envelope is consumed verbatim by all transports; the workbook is
    optionally embedded as base64 so a single REST call carries everything.
    """
    cfg = cfg or get_config()
    mapping = cfg.get('field_mapping') or {}
    skip = cfg.get('skip_keys') or []
    transmittal_cfg = cfg.get('transmittal') or {}

    subject_template = transmittal_cfg.get('subject_template', 'Master Index — {plant} — {batch_name}')
    subject = subject_template.format(
        plant=batch.plant or 'BATCH',
        batch_name=batch.name or str(batch.batch_id),
    )

    documents = [_map_row(row, mapping, skip) for row in items]

    envelope: Dict[str, Any] = {
        'transmittal_id':   uuid.uuid4().hex,
        'transmittal_type': transmittal_cfg.get('type', 'Document Transmittal'),
        'workflow':         transmittal_cfg.get('default_workflow', 'InboundDocumentLoad'),
        'subject':          subject,
        'plant':            batch.plant or '',
        'project':          (batch.batch_defaults or {}).get('project_title', ''),
        'batch_id':         str(batch.batch_id),
        'batch_name':       batch.name or '',
        'document_count':   len(documents),
        'documents':        documents,
        'sent_at_utc':      dt.datetime.utcnow().isoformat() + 'Z',
        'sent_by':          getattr(user, 'username', '') or getattr(user, 'email', '') or 'system',
        'source_system':    'RAD AI',
    }

    if transmittal_cfg.get('include_workbook') and workbook_bytes:
        envelope[transmittal_cfg.get('workbook_field', 'AttachedWorkbookBase64')] = base64.b64encode(workbook_bytes).decode('ascii')

    return envelope


# ---------------------------------------------------------------------------
# Transport implementations — each returns a dict suitable for the API
# response, with at minimum ``status``, ``mode``, ``message``.
# ---------------------------------------------------------------------------

def _slug_filename(plant: str, batch_id: str) -> str:
    safe = re.sub(r'[^A-Za-z0-9._-]+', '_', (plant or 'BATCH').strip())[:32] or 'BATCH'
    return f'Master_Index_{safe}_{batch_id[:8]}.xlsx'


def _do_dry_run(*, envelope, workbook_bytes, mode_cfg, batch) -> Dict[str, Any]:
    sample = (envelope.get('documents') or [])[:1]
    return {
        'status':          'ok',
        'mode':            'dry_run',
        'message':         'Dry-run completed — payload built but not transmitted.',
        'document_count':  envelope.get('document_count'),
        'workbook_size':   len(workbook_bytes or b''),
        'sample_document': sample[0] if sample else {},
        'transmittal_id':  envelope.get('transmittal_id'),
    }


def _do_rest_api(*, envelope, workbook_bytes, mode_cfg, batch) -> Dict[str, Any]:
    try:
        import requests  # noqa: WPS433 — local import, optional dep
    except Exception:
        return _err('rest_api', 'python "requests" not installed inside container')

    endpoint = (mode_cfg.get('endpoint') or '').strip()
    if not endpoint:
        return _err('rest_api', 'rest_api.endpoint is empty (set SPF_REST_ENDPOINT)')

    headers = {'Content-Type': 'application/json'}
    auth_header = mode_cfg.get('auth_header') or 'Authorization'
    auth_template = (mode_cfg.get('auth_template') or '').strip()
    if auth_template:
        headers[auth_header] = auth_template

    timeout_s = float(mode_cfg.get('timeout_s', 90))
    retries = int(mode_cfg.get('retries', 0))
    backoff = float(mode_cfg.get('retry_backoff_s', 5))
    verify = bool(mode_cfg.get('verify_tls', True))

    last_err = ''
    for attempt in range(retries + 1):
        try:
            resp = requests.post(endpoint, json=envelope, headers=headers,
                                 timeout=timeout_s, verify=verify)
            ok = 200 <= resp.status_code < 300
            return {
                'status':           'ok' if ok else 'error',
                'mode':             'rest_api',
                'http_status':      resp.status_code,
                'message':          'SmartPlant accepted the payload' if ok else f'SPF rejected: {resp.text[:300]}',
                'document_count':   envelope.get('document_count'),
                'transmittal_id':   envelope.get('transmittal_id'),
                'response_excerpt': resp.text[:500],
            }
        except Exception as exc:  # network / timeout / DNS
            last_err = str(exc)
            if attempt < retries:
                time.sleep(backoff * (attempt + 1))
                continue
            return _err('rest_api', f'POST failed after {retries + 1} attempt(s): {last_err}')


def _do_webhook(*, envelope, workbook_bytes, mode_cfg, batch) -> Dict[str, Any]:
    try:
        import requests  # noqa: WPS433
    except Exception:
        return _err('webhook', 'python "requests" not installed inside container')

    endpoint = (mode_cfg.get('endpoint') or '').strip()
    if not endpoint:
        return _err('webhook', 'webhook.endpoint is empty (set SPF_WEBHOOK_URL)')

    headers = {'Content-Type': 'application/json'}
    secret_header = mode_cfg.get('secret_header')
    secret_value = mode_cfg.get('secret_value')
    if secret_header and secret_value:
        headers[secret_header] = secret_value

    try:
        resp = requests.post(endpoint, json=envelope, headers=headers,
                             timeout=float(mode_cfg.get('timeout_s', 60)),
                             verify=bool(mode_cfg.get('verify_tls', True)))
        ok = 200 <= resp.status_code < 300
        return {
            'status':         'ok' if ok else 'error',
            'mode':           'webhook',
            'http_status':    resp.status_code,
            'message':        'Webhook accepted the payload' if ok else f'Webhook rejected: {resp.text[:300]}',
            'document_count': envelope.get('document_count'),
            'transmittal_id': envelope.get('transmittal_id'),
        }
    except Exception as exc:
        return _err('webhook', f'POST failed: {exc}')


def _do_s3_dropzone(*, envelope, workbook_bytes, mode_cfg, batch) -> Dict[str, Any]:
    bucket = (mode_cfg.get('bucket') or '').strip()
    if not bucket:
        return _err('s3_dropzone', 's3_dropzone.bucket is empty (set SPF_S3_BUCKET)')
    try:
        import boto3  # noqa: WPS433
    except Exception:
        return _err('s3_dropzone', 'boto3 not installed inside container')

    prefix = (mode_cfg.get('prefix') or '').lstrip('/')
    region = mode_cfg.get('region') or os.getenv('AWS_REGION') or 'us-east-1'
    kms_key_id = (mode_cfg.get('kms_key_id') or '').strip()

    workbook_key = f'{prefix}{envelope["transmittal_id"]}/{_slug_filename(batch.plant or "", str(batch.batch_id))}'
    manifest_key = f'{prefix}{envelope["transmittal_id"]}/manifest.json'

    extra_args: Dict[str, Any] = {'ContentType': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'}
    if kms_key_id:
        extra_args.update({'ServerSideEncryption': 'aws:kms', 'SSEKMSKeyId': kms_key_id})

    try:
        s3 = boto3.client('s3', region_name=region)
        s3.upload_fileobj(io.BytesIO(workbook_bytes), bucket, workbook_key, ExtraArgs=extra_args)
        # The manifest excludes the heavy base64 workbook field — SPF reads
        # the .xlsx separately from S3.
        manifest = {k: v for k, v in envelope.items() if k != 'AttachedWorkbookBase64'}
        manifest['workbook_s3_key'] = workbook_key
        s3.put_object(
            Bucket=bucket, Key=manifest_key,
            Body=json.dumps(manifest, indent=2).encode('utf-8'),
            ContentType='application/json',
            **({'ServerSideEncryption': 'aws:kms', 'SSEKMSKeyId': kms_key_id} if kms_key_id else {}),
        )
        return {
            'status':         'ok',
            'mode':           's3_dropzone',
            'message':        f'Uploaded to s3://{bucket}/{prefix}',
            'document_count': envelope.get('document_count'),
            'transmittal_id': envelope.get('transmittal_id'),
            'workbook_key':   workbook_key,
            'manifest_key':   manifest_key,
        }
    except Exception as exc:
        return _err('s3_dropzone', f'S3 upload failed: {exc}')


def _do_local_dropzone(*, envelope, workbook_bytes, mode_cfg, batch) -> Dict[str, Any]:
    directory = (mode_cfg.get('directory') or '').strip()
    if not directory:
        return _err('local_dropzone', 'local_dropzone.directory is empty (set SPF_DROPZONE_DIR)')
    try:
        target = Path(directory) / envelope['transmittal_id']
        target.mkdir(parents=True, exist_ok=True)
        wb_path = target / _slug_filename(batch.plant or '', str(batch.batch_id))
        with open(wb_path, 'wb') as fh:
            fh.write(workbook_bytes or b'')
        manifest = {k: v for k, v in envelope.items() if k != 'AttachedWorkbookBase64'}
        manifest['workbook_path'] = str(wb_path)
        with open(target / 'manifest.json', 'w', encoding='utf-8') as fh:
            json.dump(manifest, fh, indent=2)
        return {
            'status':         'ok',
            'mode':           'local_dropzone',
            'message':        f'Wrote payload to {target}',
            'document_count': envelope.get('document_count'),
            'transmittal_id': envelope.get('transmittal_id'),
            'workbook_path':  str(wb_path),
        }
    except Exception as exc:
        return _err('local_dropzone', f'Local dropzone write failed: {exc}')


def _err(mode: str, message: str) -> Dict[str, Any]:
    return {'status': 'error', 'mode': mode, 'message': message}


_DISPATCH = {
    'dry_run':        _do_dry_run,
    'rest_api':       _do_rest_api,
    'webhook':        _do_webhook,
    's3_dropzone':    _do_s3_dropzone,
    'local_dropzone': _do_local_dropzone,
}


# ---------------------------------------------------------------------------
# Audit
# ---------------------------------------------------------------------------

def _record_audit(batch, result: Dict[str, Any], user=None) -> None:
    """Append a tiny audit entry to ``batch.batch_defaults`` (no migration)."""
    cfg = get_config()
    audit_cfg = cfg.get('audit') or {}
    if not audit_cfg.get('enabled'):
        return
    try:
        defaults = dict(batch.batch_defaults or {})
        history: List[Dict[str, Any]] = list(defaults.get(AUDIT_BATCH_DEFAULTS_KEY) or [])
        history.append({
            'at':              dt.datetime.utcnow().isoformat() + 'Z',
            'by':              getattr(user, 'username', '') or getattr(user, 'email', '') or 'system',
            'mode':            result.get('mode'),
            'status':          result.get('status'),
            'message':         result.get('message', '')[:280],
            'document_count':  result.get('document_count'),
            'transmittal_id':  result.get('transmittal_id'),
        })
        max_entries = int(audit_cfg.get('max_entries', 10))
        if max_entries > 0:
            history = history[-max_entries:]
        defaults[AUDIT_BATCH_DEFAULTS_KEY] = history
        batch.batch_defaults = defaults
        batch.save(update_fields=['batch_defaults', 'updated_at'])
    except Exception:
        logger.exception('SmartPlant audit write failed for batch %s', getattr(batch, 'batch_id', '?'))


def get_audit_history(batch) -> List[Dict[str, Any]]:
    """Return the most-recent SmartPlant push attempts for this batch."""
    return list((batch.batch_defaults or {}).get(AUDIT_BATCH_DEFAULTS_KEY) or [])


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def push_batch(*, batch, items: List[Dict[str, Any]],
               workbook_bytes: bytes, mode: Optional[str] = None,
               user=None) -> Dict[str, Any]:
    """
    Top-level entry: build envelope and dispatch to the configured mode.

    Returns a result dict suitable for `JsonResponse(payload)`.
    """
    cfg = get_config()
    if not cfg.get('enabled'):
        return _err('disabled', 'SmartPlant connector is disabled in config.')

    requested = (mode or cfg.get('default_mode') or 'dry_run').strip().lower()
    if requested not in _DISPATCH and requested != 'disabled':
        return _err(requested, f'Unsupported mode "{requested}". '
                               f'Supported: {", ".join(SUPPORTED_MODES)}')
    if requested == 'disabled':
        return _err('disabled', 'SmartPlant connector is disabled.')

    envelope = build_envelope(
        batch=batch, items=items, workbook_bytes=workbook_bytes,
        user=user, cfg=cfg,
    )
    mode_cfg = (cfg.get('modes') or {}).get(requested) or {}

    try:
        handler = _DISPATCH[requested]
        result = handler(
            envelope=envelope,
            workbook_bytes=workbook_bytes,
            mode_cfg=mode_cfg,
            batch=batch,
        )
    except Exception as exc:
        logger.exception('SmartPlant push failed for batch %s (mode=%s)',
                         batch.batch_id, requested)
        result = _err(requested, f'Unhandled exception: {exc}')

    _record_audit(batch, result, user=user)
    return result
