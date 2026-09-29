"""Request-local, authorized exact-name proposals; never link or create clients."""

import re
import unicodedata
from itertools import islice

from django.db import DatabaseError

from apps.rbac.action_policy import module_action_allowed

from .email_permissions import visible_email_clients


NAME_FIELDS = ('company_name', 'legal_name', 'trading_name')
PROJECTION = ('id', 'client_code', *NAME_FIELDS, 'status', 'verification_status', 'new_proposals_permitted')
MAX_CANDIDATES = 20
MAX_SOURCE_IDS = 300
MAX_EVIDENCE_LENGTH = 900
CONFLICT = re.compile(
    r'\bconflict\w*\b.*\b(?:customer|company|organization)[ _]name\b|'
    r'\b(?:customer|company|organization)[ _]name\b.*\bconflict\w*\b', re.I,
)


def normalize_customer_name(value):
    """Preserve legal suffixes/punctuation; no fuzzy, acronym or domain matches."""
    if not isinstance(value, str):
        return ''
    return ' '.join(unicodedata.normalize('NFC', value).casefold().split())


def _result(status='unavailable', *, name='', excerpt='', source_ids=()):
    return {
        'version': 1, 'status': status, 'method': 'exact_name_v1',
        'detected_name': name, 'needs_review': True,
        'evidence': {'excerpt': excerpt, 'source_ids': list(source_ids)},
        'candidates': [], 'has_more': False,
    }


def _source(information):
    """Use only the resolved customer claim and its existing source references."""
    if not isinstance(information, dict):
        return _result()
    name_key = 'organization_name' if information.get('detection_version') == 2 else 'customer_name'
    if name_key not in information:
        return _result()
    name = information[name_key]
    evidence = information.get('evidence')
    field_sources = information.get('field_sources')
    analysis = information.get('analysis')
    if (not isinstance(name, str) or len(name) > 300 or not isinstance(evidence, dict)
            or not isinstance(field_sources, dict) or not isinstance(analysis, dict)):
        return _result()
    excerpt = evidence.get(name_key, '')
    references = field_sources.get(name_key, [])
    if not isinstance(excerpt, str) or len(excerpt) > MAX_EVIDENCE_LENGTH:
        return _result()
    if not name.strip() and not excerpt.strip() and not references:
        return _result('not_detected')
    sources = analysis.get('sources')
    if (not isinstance(sources, list) or not 1 <= len(sources) <= MAX_SOURCE_IDS
            or not isinstance(references, list) or not 1 <= len(references) <= MAX_SOURCE_IDS
            or not excerpt.strip()):
        return _result()
    valid_ids = set()
    for source in sources:
        source_id = source.get('id') if isinstance(source, dict) else None
        if not isinstance(source_id, str) or not source_id or source_id in valid_ids:
            return _result()
        valid_ids.add(source_id)
    if any(not isinstance(value, str) or value not in valid_ids for value in references):
        return _result()
    source_ids = list(dict.fromkeys(references))
    warnings = information.get('warnings', [])
    conflicting = isinstance(warnings, list) and any(isinstance(warning, str) and CONFLICT.search(warning) for warning in warnings)
    if not name.strip() or conflicting:
        return _result('conflicting', name=name, excerpt=excerpt, source_ids=source_ids)
    if normalize_customer_name(name) not in normalize_customer_name(excerpt):
        return _result()
    return _result('no_match', name=name, excerpt=excerpt, source_ids=source_ids)


class EmailCustomerMatcher:
    """Scan authorized minimal client fields once per request, on first valid claim.

    The complete normalized index is O(N) memory in authorized client names.
    iterator() controls database fetch batches, not the retained index size.
    This object must never be stored on a user, process or cross-request cache.
    """

    def __init__(self, user):
        self.user = user
        self._index = None
        self._failed = False

    def _load_index(self):
        index = {}
        try:
            rows = visible_email_clients(self.user).order_by('company_name', 'client_code', 'pk').values(*PROJECTION)
            for row in rows.iterator(chunk_size=500):
                grouped = {}
                for field in NAME_FIELDS:
                    normalized = normalize_customer_name(row[field])
                    if normalized:
                        grouped.setdefault(normalized, []).append(field)
                candidate = {
                    'id': str(row['id']), 'client_code': row['client_code'], 'company_name': row['company_name'],
                    'status': row['status'], 'verification_status': row['verification_status'],
                    'new_proposals_permitted': row['new_proposals_permitted'],
                }
                for normalized, matched_fields in grouped.items():
                    # One canonical row may share a company/legal/trading name.
                    index.setdefault(normalized, {})[candidate['id']] = (candidate, matched_fields)
        except DatabaseError:
            # Never turn an interrupted/partial scan into a unique or absent match.
            self._failed = True
            self._index = None
            return
        self._index = index

    def match(self, information):
        user = self.user
        if user is None:
            return _result()
        if (not user.is_authenticated or not user.is_active
                or not module_action_allowed(user, 'sales_clients', 'read')):
            return _result('denied')
        result = _source(information)
        if result['status'] != 'no_match':
            return result
        if self._failed:
            return _result()
        if self._index is None:
            self._load_index()
        if self._failed:
            return _result()
        candidates = self._index.get(normalize_customer_name(result['detected_name']), {})
        count = len(candidates)
        result['status'] = 'matched' if count == 1 else 'ambiguous' if count > 1 else 'no_match'
        result['has_more'] = count > MAX_CANDIDATES
        result['candidates'] = [
            {**candidate, 'matched_fields': list(fields)}
            for candidate, fields in islice(candidates.values(), MAX_CANDIDATES)
        ]
        return result


def enrich_customer_match(information, *, request=None):
    """Add a user-specific suggestion after the source's existing access guard."""
    if not isinstance(information, dict):
        return {'customer_match': _result()}
    user = getattr(request, 'user', None) if request is not None else None
    matcher = getattr(request, '_sales_email_customer_matcher', None) if request is not None else None
    if not isinstance(matcher, EmailCustomerMatcher) or matcher.user is not user:
        matcher = EmailCustomerMatcher(user)
        if request is not None:
            request._sales_email_customer_matcher = matcher
    return {**information, 'customer_match': matcher.match(information)}
