"""Lossless, versioned identity boundaries for document-backed planning.

Identifiers are opaque strings: punctuation, case, spacing and leading zeros
are material. A matching identifier is only a candidate association within its
source scope. It never authorizes merging source records or crossing revisions.
"""
from __future__ import annotations

import hashlib
import json


IDENTITY_POLICY_VERSION = 'exact-source-identity-v1'


def stable_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':'), default=str).encode('utf-8')).hexdigest()


def exact_identifier(value):
    """Return the original identifier, never a lossy lookup normalization."""
    return value if isinstance(value, str) and value and not value.isspace() else None


def source_scope(reference):
    """A scope cannot silently cross project, document, revision or namespace.

    Missing version metadata remains missing, rather than being assigned the
    current version. Callers can therefore distinguish a legacy reference from
    a reference captured against a versioned source.
    """
    reference = reference or {}
    locator = reference.get('locator') or {}
    file_id = reference.get('file_id')
    if file_id is None:
        return None
    return (
        str(reference.get('project_id')) if reference.get('project_id') is not None else None,
        str(file_id),
        reference.get('document_version'),
        reference.get('checksum_sha256'),
        reference.get('extracted_text_sha256'),
        reference.get('namespace') or locator.get('sheet') or '',
        reference.get('document_revision') or '',
    )


def identifier_key(identifier, reference):
    identifier = exact_identifier(identifier)
    scope = source_scope(reference)
    return (scope, identifier) if identifier is not None and scope is not None else None


def occurrence_key(reference, *, identifier=None, fact_id=None):
    """Stable source-occurrence identity; duplicate names never collapse rows."""
    reference = reference or {}
    return stable_digest({
        'policy': IDENTITY_POLICY_VERSION, 'scope': source_scope(reference),
        'locator': reference.get('locator') or {}, 'identifier': identifier,
        'fact_id': fact_id,
    })


def same_source_location(left, right):
    """Two facts may describe the same physical fragment, not merely a title."""
    left, right = left or {}, right or {}
    if source_scope(left) is None or source_scope(left) != source_scope(right):
        return False
    first, second = left.get('locator') or {}, right.get('locator') or {}
    # Namespace-only locators do not locate a row or a passage.
    if first.get('line') is not None and second.get('line') is not None:
        return (first['line'] == second['line']
                and first.get('line_end', first['line']) == second.get('line_end', second['line']))
    if all(first.get(key) is not None and second.get(key) is not None
           for key in ('character_start', 'character_end')):
        return all(first[key] == second[key] for key in ('character_start', 'character_end'))
    coordinates = {'row', 'cell', 'cell_range', 'paragraph', 'bbox', 'bounding_box'}
    return bool(coordinates.intersection(first)) and first == second


def identity_candidates(records):
    """Propose review groups for exact scoped IDs; never return merged facts."""
    groups = {}
    for index, record in enumerate(records):
        reference = (record.get('source_references') or [{}])[0]
        key = identifier_key(record.get('activity_id') or record.get('document_number'), reference)
        if key is not None:
            groups.setdefault(key, []).append(index)
    return [{'record_indexes': indexes, 'identifier': key[1], 'status': 'requires_review',
             'policy': IDENTITY_POLICY_VERSION, 'automatic_merge': False}
            for key, indexes in groups.items() if len(indexes) > 1]
