"""Source-bound PDF register geometry, refreshed only by explicit extraction work.

The text used by AI checkpoints is never replaced. Read projections consume the
retained cache without opening storage or writing a profile. Analysis/resume
rehashes the original bytes before deciding whether extraction can be reused.
"""
from copy import deepcopy
import hashlib
from io import BytesIO
import logging


logger = logging.getLogger(__name__)
SCHEMA_VERSION = 'pdf-register-geometry/1'
MAX_BYTES = 50 * 1024 * 1024


class RegisterGeometrySourceChanged(ValueError):
    pass


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def build_register_geometry(file_field, extracted_text, *, data=None):
    """Extract from an already opened stream, retaining its immutable identity."""
    from .pdf_register_geometry import GEOMETRY_VERSION, extract_pdf_register_rows

    if data is None:
        file_field.seek(0)
        data = file_field.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError('PDF register geometry exceeds the extraction byte limit.')
    rows = extract_pdf_register_rows(BytesIO(data), extracted_text=extracted_text)
    return {
        'schema_version': SCHEMA_VERSION,
        'extractor_version': GEOMETRY_VERSION,
        'status': 'parsed' if rows else 'not_detected',
        'text_sha256': _digest((extracted_text or '').encode('utf-8')),
        'source_storage_name': getattr(file_field, 'name', None),
        'file_sha256': _digest(data), 'source_size_bytes': len(data),
        'rows': rows,
    }


def cached_register_geometry(file_obj):
    """Return only a cache bound to this saved source; never touch storage."""
    filename = (getattr(file_obj, 'original_filename', None)
                or getattr(getattr(file_obj, 'file', None), 'name', '') or '')
    if not filename.lower().endswith('.pdf'):
        return None
    state = getattr(file_obj, '_state', None)
    if state is not None and state.adding and 'document_profile' not in state.fields_cache:
        return None
    from .pdf_register_geometry import GEOMETRY_VERSION
    profile = getattr(file_obj, 'document_profile', None)
    coverage = getattr(profile, 'extraction_coverage', None) or {}
    result = (coverage.get('structured_evidence') or {}).get('register_geometry')
    if (not isinstance(result, dict) or result.get('schema_version') != SCHEMA_VERSION
            or result.get('extractor_version') != GEOMETRY_VERSION
            or result.get('status') not in {'parsed', 'not_detected'}
            or not isinstance(result.get('rows'), list)
            or not isinstance(result.get('file_sha256'), str) or len(result['file_sha256']) != 64):
        return None
    digest = _digest((file_obj.extracted_text or '').encode('utf-8'))
    if result.get('text_sha256') != digest or coverage.get('text_sha256') != digest:
        return None
    if result.get('source_storage_name') != getattr(file_obj.file, 'name', None):
        return None
    size = getattr(file_obj, 'size_bytes', None)
    if size and size != result.get('source_size_bytes'):
        return None
    return deepcopy(result)


def ensure_register_geometry(file_obj):
    """Refresh a missing/stale cache during an authorized parse/analysis only.

    Missing or unsupported original files retain the existing text fallback.
    Updating a DocumentProfile does not change the source file revision, text,
    past analysis assertions, or resumable AI chunk identity.
    """
    if not (file_obj.original_filename or '').lower().endswith('.pdf'):
        return None
    try:
        with file_obj.file.open('rb') as source:
            data = source.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            return None
        profile = getattr(file_obj, 'document_profile', None)
        recorded = ((getattr(profile, 'extraction_coverage', None) or {}).get('structured_evidence') or {}).get('register_geometry') or {}
        if (recorded.get('source_storage_name') == file_obj.file.name
                and recorded.get('text_sha256') == _digest((file_obj.extracted_text or '').encode('utf-8'))
                and recorded.get('file_sha256') and recorded['file_sha256'] != _digest(data)):
            raise RegisterGeometrySourceChanged(
                'The source PDF changed after text extraction. Reprocess the uploaded document before continuing analysis.'
            )
        cached = cached_register_geometry(file_obj)
        if cached and cached['file_sha256'] == _digest(data):
            return cached
        result = build_register_geometry(file_obj.file, file_obj.extracted_text or '', data=data)
    except RegisterGeometrySourceChanged:
        raise
    except Exception:
        # Source details and extracted text are deliberately absent from logs.
        logger.info('Optional PDF register geometry is unavailable for file %s.', file_obj.pk)
        return None
    from .document_intelligence import profile_document
    from .extraction_coverage import file_coverage

    profile = getattr(file_obj, 'document_profile', None)
    coverage = file_coverage(file_obj, getattr(profile, 'extraction_coverage', None), include_structured=True)
    coverage.setdefault('structured_evidence', {})['register_geometry'] = result
    profile = profile_document(file_obj, extraction_coverage=coverage)
    file_obj.document_profile = profile
    return deepcopy(result)
