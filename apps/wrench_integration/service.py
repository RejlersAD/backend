"""
Wrench SmartProject Integration – Service Layer
Implements the real SmartProject API authentication flow:
  1. POST /api/AccessControl/Login  → receive TOKEN
  2. Every subsequent request must include TOKEN in the request body
  3. Every response returns a refreshed TOKEN → must be stored for next call (rolling token)

API Reference: SmartProject API - Rejlers R0.pdf
"""
import logging
import requests
from datetime import timedelta
from django.utils import timezone as dj_timezone

from .models import WrenchConfig, WrenchSyncLog
from .crypto import decrypt_value

logger = logging.getLogger(__name__)

# Timeouts
_TIMEOUT_FAST = 15       # login / health
_TIMEOUT_SEARCH = 90     # document/transmittal search (Wrench returns full dataset)
# Token freshness window – re-login if token older than this
_TOKEN_MAX_AGE_MINUTES = 55


# ─── Internal helpers ─────────────────────────────────────────────────────────

def _get_active_config() -> WrenchConfig:
    cfg = WrenchConfig.objects.filter(is_active=True).first()
    if not cfg:
        raise RuntimeError(
            'No active Wrench configuration. Please configure the integration first.'
        )
    return cfg


def _api_url(cfg: WrenchConfig, path: str) -> str:
    """Build absolute URL: base_url + path (handles trailing slash)."""
    return f"{cfg.base_url.rstrip('/')}/{path.lstrip('/')}"


def _is_token_fresh(cfg: WrenchConfig) -> bool:
    """Return True if we have a valid, recent session token."""
    if not cfg.session_token or not cfg.token_obtained_at:
        return False
    age = dj_timezone.now() - cfg.token_obtained_at
    return age < timedelta(minutes=_TOKEN_MAX_AGE_MINUTES)


def _save_token(cfg: WrenchConfig, token: str) -> None:
    """Persist the rolling session token to the database."""
    cfg.session_token = token
    cfg.token_obtained_at = dj_timezone.now()
    cfg.save(update_fields=['session_token', 'token_obtained_at'])


def _login(cfg: WrenchConfig) -> str:
    """
    Authenticate with Wrench SmartProject.
    POST /api/AccessControl/Login
    Returns the session TOKEN string.
    """
    password = decrypt_value(cfg.encrypted_password)
    payload = {
        'SERVER_ID': cfg.server_id,
        'LOGIN_NAME': cfg.login_name,
        'PASSWORD': password,
        'IS_PASSWORD_ENCRYPTED': cfg.is_password_encrypted,
        'OTP': cfg.otp or '',
    }
    # Add optional session parameters only when configured
    if cfg.language:
        payload['LANGUAGE'] = cfg.language
    if cfg.time_zone_id:
        payload['TIME_ZONE_ID'] = cfg.time_zone_id
    if cfg.workstation_name:
        payload['WORKSTATION_NAME'] = cfg.workstation_name
        payload['WORKSTATION_ID'] = cfg.workstation_name  # API accepts both forms
    url = _api_url(cfg, '/api/AccessControl/Login')
    logger.info('[Wrench] Authenticating: POST %s (user=%s)', url, cfg.login_name)

    resp = requests.post(url, json=payload, timeout=_TIMEOUT_FAST)
    resp.raise_for_status()
    data = resp.json()

    # Extract TOKEN from DataList.LOGIN[0] structure
    login_list = data.get('DataList', {}).get('LOGIN', [[]])[0]
    token = None
    for field in login_list:
        if field.get('FieldName') == 'TOKEN':
            token = field.get('Value')
            break

    # Fallback: some versions return token at top level
    if not token:
        token = data.get('Token') or data.get('token')

    if not token:
        raise RuntimeError('Wrench login succeeded but no TOKEN found in response.')

    _save_token(cfg, token)
    logger.info('[Wrench] Login successful, TOKEN obtained (length=%d)', len(token))
    return token


def _ensure_token(cfg: WrenchConfig) -> str:
    """Return a valid session token, logging in fresh if necessary."""
    if _is_token_fresh(cfg):
        return cfg.session_token
    return _login(cfg)


def _refresh_token_from_response(cfg: WrenchConfig, data: dict) -> None:
    """
    Wrench returns a refreshed token in every response.
    Persist it so the next call uses the latest token.
    """
    new_token = data.get('Token') or data.get('token')
    if new_token and new_token != cfg.session_token:
        _save_token(cfg, new_token)


# ─── Public API ───────────────────────────────────────────────────────────────

def verify_connection(cfg: WrenchConfig) -> dict:
    """
    Test connection by performing a real login.
    Returns {'success': bool, 'message': str}.
    """
    try:
        _login(cfg)
        cfg.connection_verified = True
        cfg.last_verified_at = dj_timezone.now()
        cfg.save(update_fields=['connection_verified', 'last_verified_at'])
        return {
            'success': True,
            'message': 'Login successful. Wrench connection verified.',
        }
    except requests.exceptions.SSLError as exc:
        return {'success': False, 'message': f'SSL error: {exc}'}
    except requests.exceptions.ConnectionError:
        return {'success': False, 'message': 'Unable to reach the Wrench server. Check the base URL.'}
    except requests.exceptions.Timeout:
        return {'success': False, 'message': f'Login timed out after {_TIMEOUT_FAST}s.'}
    except requests.exceptions.HTTPError as exc:
        return {'success': False, 'message': f'Wrench returned HTTP {exc.response.status_code}.'}
    except RuntimeError as exc:
        cfg.connection_verified = False
        cfg.save(update_fields=['connection_verified'])
        return {'success': False, 'message': str(exc)}
    except Exception as exc:
        logger.error('[Wrench] verify_connection error: %s', exc, exc_info=True)
        cfg.connection_verified = False
        cfg.save(update_fields=['connection_verified'])
        return {'success': False, 'message': 'Unexpected error during connection test.'}


def search_documents(
    cfg: WrenchConfig,
    *,
    page: int = 1,
    page_size: int = 50,
    discipline: str = None,
    doc_type: str = None,
    date_from: str = None,   # format: 'YYYY/MM/DD HH:MM'
    date_to: str = None,
    doc_no: str = None,
    order_no: str = None,    # filter by Transmittal ORDER_NO (used as fallback by get_transmittal_documents)
) -> dict:
    """
    Search Wrench documents using the SearchObject API.
    POST <<SVC URL>>/DocumentSearch/SearchObject

    Returns the parsed response dict with:
      - 'total': int
      - 'documents': list of flat dicts (DOC_NO, DOC_DESCRIPTION, etc.)
      - 'token': refreshed token
    """
    token = _ensure_token(cfg)

    search_criteria = []
    criterion_id = 1

    # Date range filter (APPROVED_ON  Operator 4=GT, 5=LT)
    if date_from:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'APPROVED_ON',
            'FieldValue': date_from,
            'Operator': 4,
            'RangeId': 0,
        })
        criterion_id += 1
    if date_to:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'APPROVED_ON',
            'FieldValue': date_to,
            'Operator': 5,
            'RangeId': 0,
        })
        criterion_id += 1
    if doc_no:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'DOC_NO',
            'FieldValue': doc_no,
            'Operator': 0,
            'RangeId': 0,
        })
        criterion_id += 1
    if discipline:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'DISCIPLINE',
            'FieldValue': discipline,
            'Operator': 0,
            'RangeId': 0,
        })
        criterion_id += 1
    if order_no:
        search_criteria.append({
            'ProcessID': criterion_id,
            'FieldName': 'ORDER_NO',
            'FieldValue': order_no,
            'Operator': 0,
            'RangeId': 0,
        })
        criterion_id += 1

    # Fields we want returned
    RETURN_FIELDS = [
        'DOC_NO', 'DOC_DESCRIPTION', 'ORDER_NO', 'ORDER_DESCRIPTION',
        'GENEALOGY_STRING', 'CREATED_BY_USER', 'WF_TEAM_NAME', 'IDOC_ID',
        'DOC_TYPE', 'IS_DEPENDENT', 'APPROVED_ON',
    ]
    filter_fields = [
        {'ProcessID': i + 1, 'FieldName': f}
        for i, f in enumerate(RETURN_FIELDS)
    ]

    payload = {
        'SearchObjectType': 0,
        'SearchType': 0,
        'SearchResultMode': 0,
        'ObjectSearchDetails': [{
            'ProcessID': 1,
            'RowCount': page_size,
            'PageNumber': page,
            'SearchType': 0,
            'SearchPurpose': 0,
            'SchemaOnly': 0,
        }],
        'ObjectSearchCriteriaDetails': search_criteria,
        'ObjectSearchFilterDetails': filter_fields,
        'Token': token,
        'LoginName': cfg.login_name,
        'LoggedinUserId': 0,
        'ServerId': cfg.server_id,
    }

    # Use dedicated SVC URL if configured, else fall back to the main base URL.
    # Many Wrench installations expose DocumentSearch on the same host as the WebAPI.
    using_fallback = not cfg.svc_url
    search_base = (cfg.svc_url or cfg.base_url).rstrip('/')
    url = f"{search_base}/DocumentSearch/SearchObject"
    logger.info('[Wrench] Searching documents: POST %s (page=%d, size=%d, fallback=%s)',
                url, page, page_size, using_fallback)
    resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEARCH)
    if resp.status_code == 404:
        if using_fallback:
            raise RuntimeError(
                f'DocumentSearch endpoint not found at the main WebAPI host ({url}). '
                'Your Wrench admin may have the DocumentSearch service on a separate host. '
                'Add its URL in Configuration → "Document Search Service URL".'
            )
        raise RuntimeError(
            f'DocumentSearch endpoint not found at the configured SVC URL ({url}). '
            'Verify the URL in Configuration → "Document Search Service URL" is correct.'
        )
    resp.raise_for_status()
    data = resp.json()

    # Refresh rolling token
    _refresh_token_from_response(cfg, data)

    # Flatten ObjectSearchResults (list of lists of {PropertyName, PropertyValue})
    raw_results = data.get('ObjectSearchResults', [])
    documents = []
    for row in raw_results:
        doc = {}
        for prop in row:
            name = prop.get('PropertyName') or prop.get('FieldName', '')
            value = prop.get('PropertyValue') or prop.get('Value', '')
            doc[name] = value
        if doc:
            documents.append(doc)

    return {
        'total': data.get('TotalSearchResultCount', len(documents)),
        'documents': documents,
        'operation_status': data.get('OperationStatus', -1),
        'error_msg': data.get('ErrorMsg'),
    }


# ─── Soft-coded constants for the REST document-list endpoint ─────────────────
# Wrench SmartProject REST document fields returned by GetDocumentList.
# Mirrors the TRANSMITTAL_LIST pattern — adjust if your Wrench version uses different keys.
_DOC_LIST_DATA_KEY   = 'DOCUMENT_LIST'    # DataList key containing the document rows
_DOC_LIST_URL_PATH   = '/api/Document/GetDocumentList'   # REST path on main WebAPI host
_DOC_LIST_ALT_PATHS  = [                  # fallback paths tried in order if primary 404s
    '/api/Documents/GetDocumentList',
    '/api/Document/GetDocList',
    '/api/Docs/GetDocumentList',
]

# ─── Soft-coded constants for per-transmittal document fetch ──────────────────
# Transmittal-specific REST endpoints tried before falling back to DocumentSearch.
# These share the same WebAPI host as GetTransmittalList (no separate SVC URL needed).
_TRANS_DOC_REST_PATHS = [
    '/api/Transmittal/GetTransmittalDocumentList',
    '/api/Transmittal/GetTransmittalDocuments',
    '/api/Transmittal/GetDocumentListByTransmittal',
    '/api/Transmittal/GetTransmittalDetail',
]
# DataList key names tried (in order) when parsing the transmittal-doc response
_TRANS_DOC_DATA_KEYS = [
    'TRANSMITTAL_DOCUMENT_LIST',
    'DOCUMENT_LIST',
    'TRANS_DOCUMENT_LIST',
    'DOCUMENT',
]


def _flatten_doc_rows(raw_list: list) -> list:
    """
    Normalise a raw list of document rows from any Wrench REST response into
    a flat list of dicts — handles both list-of-{FieldName,Value} and flat-dict formats.
    """
    documents = []
    for row in raw_list:
        item = {}
        if isinstance(row, list):
            for field in row:
                name = field.get('FieldName', '')
                value = field.get('Value')
                if name:
                    item[name] = value
        elif isinstance(row, dict):
            item = row
        if item:
            documents.append(item)
    return documents


def get_transmittal_documents(
    cfg: WrenchConfig,
    *,
    order_no: str,
    trans_id: str = None,
    page: int = 1,
    page_size: int = 200,
) -> dict:
    """
    Fetch documents linked to a specific transmittal.

    Strategy (tried in order, first success wins):
      1. Transmittal-specific REST endpoints on the same host as GetTransmittalList
         (_TRANS_DOC_REST_PATHS) — no SVC URL needed.
      2. Generic Document REST endpoint (GetDocumentList + _DOC_LIST_ALT_PATHS).
      3. DocumentSearch/SearchObject fallback with ORDER_NO criterion (uses SVC URL if set,
         else the main host — same behaviour as search_documents()).

    Returns { total, documents, source }.
    """
    token = _ensure_token(cfg)

    # Payload common to all REST attempts
    base_payload = {
        'TOKEN':       token,
        'SERVER_ID':   cfg.server_id,
        'LOGIN_NAME':  cfg.login_name,
        'ROW_COUNT':   page_size,
        'PAGE_NUMBER': page,
        'ORDER_NO':    order_no,
    }
    if trans_id:
        base_payload['TRANS_ID'] = trans_id

    # ── Strategy 1: Transmittal-specific REST paths ───────────────────────────
    for path in _TRANS_DOC_REST_PATHS:
        url = _api_url(cfg, path)
        logger.info('[Wrench] get_transmittal_documents: trying %s (order_no=%s)', url, order_no)
        try:
            resp = requests.post(url, json=base_payload, timeout=_TIMEOUT_SEARCH)
            if resp.status_code == 404:
                logger.debug('[Wrench] %s → 404, trying next', url)
                continue
            resp.raise_for_status()
            data = resp.json()
            _refresh_token_from_response(cfg, data)

            # Try each candidate DataList key
            raw_list = []
            for key in _TRANS_DOC_DATA_KEYS:
                raw_list = data.get('DataList', {}).get(key, [])
                if raw_list:
                    logger.info('[Wrench] found %d doc rows under DataList.%s', len(raw_list), key)
                    break

            # Also check top-level lists in case the response is unwrapped
            if not raw_list:
                raw_list = data.get('DocumentList', [])
            if not raw_list:
                raw_list = data.get('ObjectSearchResults', [])

            documents = _flatten_doc_rows(raw_list)

            # Accept empty list as valid success — the transmittal may genuinely have no docs
            total = len(documents)
            start = (page - 1) * page_size
            return {
                'total':     total,
                'documents': documents[start: start + page_size],
                'source':    f'rest:{path}',
            }

        except requests.exceptions.HTTPError as exc:
            logger.debug('[Wrench] HTTP error on %s: %s', url, exc)
            continue
        except Exception as exc:
            logger.debug('[Wrench] Unexpected error on %s: %s', url, exc)
            continue

    # ── Strategy 2: Generic Document list endpoint (GetDocumentList + alts) ──
    doc_list_paths = [_DOC_LIST_URL_PATH] + _DOC_LIST_ALT_PATHS
    for path in doc_list_paths:
        url = _api_url(cfg, path)
        logger.info('[Wrench] get_transmittal_documents: fallback to %s', url)
        try:
            resp = requests.post(url, json=base_payload, timeout=_TIMEOUT_SEARCH)
            if resp.status_code == 404:
                logger.debug('[Wrench] %s → 404', url)
                continue
            resp.raise_for_status()
            data = resp.json()
            _refresh_token_from_response(cfg, data)

            raw_list = (
                data.get('DataList', {}).get(_DOC_LIST_DATA_KEY, [])
                or data.get('DataList', {}).get('DOCUMENT', [])
                or data.get('DocumentList', [])
            )
            documents = _flatten_doc_rows(raw_list)
            total = len(documents)
            start = (page - 1) * page_size
            return {
                'total':     total,
                'documents': documents[start: start + page_size],
                'source':    f'rest:{path}',
            }
        except Exception as exc:
            logger.debug('[Wrench] Doc-list fallback error on %s: %s', url, exc)
            continue

    # ── Strategy 3: DocumentSearch/SearchObject with ORDER_NO criterion ───────
    logger.info('[Wrench] get_transmittal_documents: all REST paths failed, trying DocumentSearch (order_no=%s)', order_no)
    try:
        result = search_documents(cfg, page=page, page_size=page_size, order_no=order_no)
        result['source'] = 'document_search'
        return result
    except Exception as svc_exc:
        logger.warning('[Wrench] DocumentSearch fallback also failed: %s', svc_exc)

    raise RuntimeError(
        f'No Wrench endpoint returned document data for transmittal ORDER_NO={order_no}. '
        f'Tried transmittal-specific paths ({_TRANS_DOC_REST_PATHS}), '
        f'generic document paths ({doc_list_paths}), and DocumentSearch. '
        'Check that the Wrench WebAPI exposes document listing, or configure a DocumentSearch SVC URL.'
    )


def get_document_list(
    cfg: WrenchConfig,
    *,
    page: int = 1,
    page_size: int = 50,
    discipline: str = None,
    doc_no: str = None,
    order_no: str = None,   # filter by transmittal ORDER_NO to list linked documents
) -> dict:
    """
    Fetch documents via the Wrench SmartProject REST WebAPI — same host as transmittals.
    POST <<base_url>>/api/Document/GetDocumentList

    This does NOT require the separate DocumentSearch SVC URL.
    Falls back through _DOC_LIST_ALT_PATHS if the primary path returns 404.

    Returns:
      - 'total': int
      - 'documents': list[dict]  (DOC_NO, DOC_DESCRIPTION, DISCIPLINE, etc.)
      - 'source': 'rest'
    """
    token = _ensure_token(cfg)

    # Build payload — same ALL_CAPS flat format as GetTransmittalList
    payload = {
        'TOKEN':       token,
        'SERVER_ID':   cfg.server_id,
        'LOGIN_NAME':  cfg.login_name,
        'ROW_COUNT':   page_size,
        'PAGE_NUMBER': page,
    }
    if discipline:
        payload['DISCIPLINE'] = discipline
    if doc_no:
        payload['DOC_NO'] = doc_no
    if order_no:
        payload['ORDER_NO'] = order_no

    # Try primary path, then fallbacks
    paths_to_try = [_DOC_LIST_URL_PATH] + _DOC_LIST_ALT_PATHS
    last_exc = None

    for path in paths_to_try:
        url = _api_url(cfg, path)
        logger.info('[Wrench] Fetching document list: POST %s (page=%d, size=%d)', url, page, page_size)
        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEARCH)
            if resp.status_code == 404:
                logger.debug('[Wrench] %s → 404, trying next path', url)
                last_exc = RuntimeError(f'404 at {url}')
                continue
            resp.raise_for_status()
            data = resp.json()
            _refresh_token_from_response(cfg, data)

            # Flatten DataList.<_DOC_LIST_DATA_KEY> — same pattern as transmittals
            raw_list = data.get('DataList', {}).get(_DOC_LIST_DATA_KEY, [])

            # Some Wrench versions return a flat list of property dicts at top level
            if not raw_list:
                raw_list = data.get('DataList', {}).get('DOCUMENT', [])
            if not raw_list:
                raw_list = data.get('DocumentList', [])

            documents = []
            for row in raw_list:
                item = {}
                if isinstance(row, list):
                    # List-of-{FieldName, Value} pairs (TRANSMITTAL_LIST style)
                    for field in row:
                        name = field.get('FieldName', '')
                        value = field.get('Value')
                        if name:
                            item[name] = value
                elif isinstance(row, dict):
                    # Flat dict (some REST APIs return this directly)
                    item = row
                if item:
                    documents.append(item)

            total_available = len(documents)

            # In-service pagination (consistent with transmittal pattern)
            start = (page - 1) * page_size
            end   = start + page_size
            page_slice = documents[start:end]

            return {
                'total':    total_available,
                'documents': page_slice,
                'source':   'rest',
                'operation_status': data.get('OperationStatus', -1),
            }

        except RuntimeError:
            raise
        except requests.exceptions.HTTPError as exc:
            last_exc = exc
            logger.debug('[Wrench] HTTP error on %s: %s', url, exc)
            continue
        except Exception as exc:
            last_exc = exc
            break

    raise RuntimeError(
        f'Could not reach the Wrench document list endpoint. Tried: {paths_to_try}. '
        f'Last error: {last_exc}'
    )


def get_transmittals(
    cfg: WrenchConfig,
    *,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    """
    Fetch transmittals via the Wrench SmartProject REST WebAPI.
    POST <<base_url>>/api/Transmittal/GetTransmittalList

    Note: this Wrench instance returns all records regardless of ROW_COUNT/PAGE_NUMBER,
    so pagination is applied in-service after receiving the full result set.

    Returns:
      - 'total': int          – total available records from Wrench
      - 'transmittals': list  – the requested page slice
      - 'page': int, 'page_size': int
      - 'operation_status': int
    """
    token = _ensure_token(cfg)
    url = _api_url(cfg, '/api/Transmittal/GetTransmittalList')
    # Wrench REST API: flat ALL_CAPS fields, no wrapper object
    payload = {
        'TOKEN': token,
        'SERVER_ID': cfg.server_id,
        'LOGIN_NAME': cfg.login_name,
        'ROW_COUNT': page_size,
        'PAGE_NUMBER': page,
    }
    logger.info('[Wrench] Fetching transmittals: POST %s (page=%d, size=%d)', url, page, page_size)
    resp = requests.post(url, json=payload, timeout=_TIMEOUT_SEARCH)
    resp.raise_for_status()
    data = resp.json()

    _refresh_token_from_response(cfg, data)

    # Flatten DataList.TRANSMITTAL_LIST — list-of-lists, each inner list is FieldName/Value pairs
    raw_list = data.get('DataList', {}).get('TRANSMITTAL_LIST', [])
    transmittals = []
    for row in raw_list:
        item = {}
        for field in row:
            name = field.get('FieldName', '')
            value = field.get('Value')
            if name:
                item[name] = value
        if item:
            transmittals.append(item)

    total_available = len(transmittals)

    # Apply in-service pagination (API ignores ROW_COUNT on this instance)
    start = (page - 1) * page_size
    end = start + page_size
    page_slice = transmittals[start:end]

    op_status = -1
    process_details = data.get('ProcessDetails', [{}])
    if process_details:
        op_status = process_details[0].get('ProcessStatus', -1)

    return {
        'total': total_available,
        'transmittals': page_slice,
        'page': page,
        'page_size': page_size,
        'operation_status': op_status,
        'error_msg': data.get('ErrorMsg'),
    }


def run_sync(direction: str, entity_type: str, triggered_by, filters: dict = None) -> WrenchSyncLog:
    """
    Perform a data sync between RADAI and Wrench.
    For wrench_to_radai + document: calls SearchObject.
    Creates + updates a WrenchSyncLog record.
    """
    cfg = _get_active_config()
    log = WrenchSyncLog.objects.create(
        config=cfg,
        triggered_by=triggered_by,
        direction=direction,
        entity_type=entity_type,
        status='in_progress',
    )

    try:
        if direction == 'wrench_to_radai':
            if entity_type in ('document', 'doc_search'):
                result = search_documents(cfg, **(filters or {}))
                log.records_requested = result['total']
                log.records_synced = len(result['documents'])
                log.records_failed = 0
                log.sync_details = {
                    'total_in_wrench': result['total'],
                    'fetched': len(result['documents']),
                    'sample_doc_nos': [d.get('DOC_NO', '') for d in result['documents'][:5]],
                    'operation_status': result.get('operation_status'),
                }
            elif entity_type == 'transmittal':
                result = get_transmittals(cfg, **(filters or {}))
                log.records_requested = result['total']
                log.records_synced = len(result['transmittals'])
                log.records_failed = 0
                log.sync_details = {
                    'total_fetched': result['total'],
                    'sample_transmittals': result['transmittals'][:3],
                    'operation_status': result.get('operation_status'),
                }
            elif entity_type == 'all':
                # Try transmittals (REST endpoint); documents require SVC URL configuration
                result = get_transmittals(cfg)
                log.records_requested = result['total']
                log.records_synced = len(result['transmittals'])
                log.records_failed = 0
                log.sync_details = {
                    'entity_types_attempted': ['transmittal'],
                    'transmittals_fetched': result['total'],
                    'operation_status': result.get('operation_status'),
                }
            else:
                # project / user – placeholder
                _ensure_token(cfg)  # validate connection is alive
                log.records_requested = 0
                log.records_synced = 0
                log.sync_details = {'note': f'Sync for entity_type={entity_type} – implement specific endpoint.'}
        else:
            # RADAI → Wrench: future implementation
            log.records_requested = 0
            log.records_synced = 0
            log.sync_details = {'note': 'Push direction reserved for future implementation.'}

        log.status = 'success'

    except Exception as exc:
        log.status = 'failed'
        log.error_message = str(exc)
        logger.error('[Wrench] sync failed: %s', exc, exc_info=True)
    finally:
        log.completed_at = dj_timezone.now()
        log.save()

    return log

