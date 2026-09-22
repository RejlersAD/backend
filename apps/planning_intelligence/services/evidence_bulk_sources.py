"""Pure citation repair for a supported row in unchanged original PDF bytes.

The geometry adapter's pipe-separated row is a presentation, not a verbatim
saved-text quote. Re-read the PDF, match its precise physical row and field,
then locate the corresponding unchanged text row. The caller must append a new
fact and retain the original assertion; this module never writes or accepts it.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
from io import BytesIO
from math import isfinite

from .evidence_schema import validate_value
from .reference_schedule_geometry import ADAPTER, MAX_BYTES, parse_reference_schedule_pdf
from .reference_schedule_text import parse_reference_schedule_text


SCHEMA = 'printed-schedule-citation-repair-1'
_FIELDS = {'identity': 'title', 'start_date': 'planned_start_date', 'finish_date': 'planned_finish_date'}


def _box(value):
    if (not isinstance(value, (list, tuple)) or len(value) != 4
            or any(type(item) not in (int, float) or not isfinite(item) for item in value)):
        return None
    return tuple(value)


def _space(value):
    return ' '.join(value.split()) if isinstance(value, str) else None


class GeometryCitationVerifier:
    """Cache immutable byte/text objects and both parsers once per document.

    Each call still checks document/source identities and exact field values.
    No title matching across rows, calendar defaults, type inference or native
    duration-unit assumptions are supported.
    """
    def __init__(self):
        self._documents = {}

    def _load(self, document, raw_bytes):
        text = getattr(document, 'extracted_text', None)
        if (not isinstance(raw_bytes, bytes) or not raw_bytes.startswith(b'%PDF-')
                or len(raw_bytes) > MAX_BYTES or not isinstance(text, str) or len(text) > 2_000_000):
            return None
        key = (str(document.pk), document.file_sha256, document.text_sha256, id(raw_bytes), id(text))
        if key in self._documents:
            return self._documents[key]['index']
        # Keep strong references: object IDs cannot be reused for different
        # content while this verifier is alive. bytes and str are immutable.
        entry = {'raw_bytes': raw_bytes, 'text': text, 'index': None}
        self._documents[key] = entry
        if (hashlib.sha256(raw_bytes).hexdigest() != document.file_sha256
                or hashlib.sha256(text.encode('utf-8')).hexdigest() != document.text_sha256):
            return None
        try:
            geometry = parse_reference_schedule_pdf(BytesIO(raw_bytes))
            saved = parse_reference_schedule_text(text)
        except Exception:
            # A damaged or unsupported document leaves the assertion unresolved.
            return None
        if (geometry.get('adapter') != ADAPTER or geometry.get('status') not in {'parsed', 'partial'}
                or geometry.get('checksum_sha256') != document.file_sha256
                or not geometry.get('rows') or not saved.get('rows')):
            return None
        geometry_rows, saved_rows, quotes = defaultdict(list), defaultdict(list), defaultdict(list)
        for row in geometry['rows']:
            geometry_rows[row.get('row_number')].append(row)
        for row in saved['rows']:
            saved_rows[row.get('row_number')].append(row)
        offset = 0
        for line in text.splitlines(keepends=True):
            stripped = line.strip()
            if stripped:
                start = offset + len(line) - len(line.lstrip())
                quotes[stripped].append((start, start + len(stripped)))
            offset += len(line)
        entry['index'] = {'geometry': geometry_rows, 'saved': saved_rows, 'quotes': quotes}
        return entry['index']

    def repair(self, node, source, document, raw_bytes):
        """Return unchanged value plus independently grounded replacement source.

        ``None`` means no safe repair. ``proof`` binds the new citation to the
        original fact, original bytes and the PDF field, including the column
        of a single-date milestone. Unknown units and nonprinted activity types
        remain unresolved even when their neighbouring cells are readable.
        """
        prop, value = getattr(node, 'property', None), getattr(node, 'value', None)
        fact_id = getattr(node, 'pk', None)
        if prop not in _FIELDS or fact_id is None or validate_value(prop, value):
            return None
        if not isinstance(source, dict) or getattr(document, 'integrity_status', None) != 'verified':
            return None
        if (source.get('document_version') != str(document.pk)
                or source.get('file_id') != document.source_file_id
                or source.get('sha256') != document.file_sha256
                or source.get('text_sha256') != document.text_sha256):
            return None
        locator = source.get('locator')
        if (not isinstance(locator, dict) or type(locator.get('page')) is not int or locator['page'] < 1
                or type(locator.get('row')) is not int or locator['row'] < 1 or _box(locator.get('bbox')) is None):
            return None
        indexed = self._load(document, raw_bytes)
        if not indexed:
            return None
        geometry_rows, saved_rows = indexed['geometry'][locator['row']], indexed['saved'][locator['row']]
        if len(geometry_rows) != 1 or len(saved_rows) != 1:
            return None
        row, saved = geometry_rows[0], saved_rows[0]
        row_locator, saved_locator = row.get('source_locator') or {}, saved.get('source_locator') or {}
        if row_locator.get('page') != locator['page'] or saved_locator.get('page') not in (None, locator['page']):
            return None
        field = _FIELDS[prop]
        field_evidence = (row.get('field_evidence') or {}).get(field) or {}
        allowed_boxes = {_box(row_locator.get('bbox'))}
        if field_evidence:
            allowed_boxes.add(_box((field_evidence.get('source_locator') or {}).get('bbox')))
        if _box(locator['bbox']) not in allowed_boxes:
            return None
        if (row.get('kind') != saved.get('kind') or row.get('activity_id') != saved.get('activity_id')
                or _space(row.get('title')) != _space(saved.get('title'))
                or getattr(node, 'entity_name', None) != (row.get('title') or '')[:500]
                or row.get(field) != value):
            return None
        if prop == 'identity':
            if _space(saved.get('title')) != _space(value):
                return None
        else:
            if field_evidence.get('status') != 'extracted' or not field_evidence.get('raw_text'):
                return None
            # Collapsed PDF text loses a blank endpoint's column. The original
            # PDF geometry must identify it, while the saved row must contain
            # the same sole printed date. Never fill the opposite blank cell.
            saved_value = saved.get(field)
            if saved_value != value:
                other = 'planned_finish_date' if field == 'planned_start_date' else 'planned_start_date'
                if (saved.get('date_columns_status') != 'ambiguous'
                        or saved.get('printed_single_date') != value or row.get(other) is not None):
                    return None
        quote = saved.get('raw_text')
        positions = indexed['quotes'].get(quote, [])
        if not isinstance(quote, str) or not quote or len(positions) != 1:
            return None
        start, end = positions[0]
        if document.extracted_text[start:end] != quote:
            return None
        replacement = deepcopy(source)
        replacement.pop('context_excerpt', None)
        replacement.update(
            locator={'page': locator['page'], 'row': locator['row'], 'bbox': deepcopy(row_locator['bbox']),
                     'character_start': start, 'character_end': end},
            excerpt=quote, verbatim=quote, quote_verified=True,
            verification_method=SCHEMA,
        )
        proof = {
            'schema': SCHEMA, 'original_fact_id': str(fact_id), 'property': prop, 'value': deepcopy(value),
            'document_version': str(document.pk), 'file_sha256': document.file_sha256,
            'text_sha256': document.text_sha256, 'adapter': ADAPTER,
            'original_locator': deepcopy(locator), 'source_locator': deepcopy(replacement['locator']),
            'row': {'activity_id': row.get('activity_id'), 'title': row['title'], 'kind': row['kind']},
            'field': field, 'field_evidence': deepcopy(field_evidence) if field_evidence else {
                'raw_text': row['title'], 'source_locator': deepcopy(row_locator), 'status': 'extracted',
            },
        }
        return {'source': replacement, 'value': deepcopy(value), 'proof': proof}
