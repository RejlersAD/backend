"""Deterministic, project-scoped excerpts from existing controlled file stores."""
import csv
import hashlib
import io
import re
from pathlib import Path

from rest_framework.exceptions import ValidationError


MAX_BYTES = 12 * 1024 * 1024
MAX_PAGES = 100
MAX_PAGE_CHARS = 4000
ALLOWED = {'.pdf', '.docx', '.xlsx', '.csv', '.txt'}


def extract_pages(file_field, filename):
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED:
        raise ValidationError({'file': 'Supported knowledge formats: PDF, DOCX, XLSX, CSV, TXT.'})
    with file_field.open('rb') as stream:
        raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValidationError({'file': 'Knowledge file exceeds the 12 MB processing limit.'})
    digest = hashlib.sha256(raw).hexdigest()
    pages = []
    truncated = False
    if suffix == '.pdf':
        import pymupdf
        with pymupdf.open(stream=raw, filetype='pdf') as document:
            truncated = len(document) > MAX_PAGES
            for index in range(min(len(document), MAX_PAGES)):
                text = document[index].get_text()
                truncated |= len(text) > MAX_PAGE_CHARS
                pages.append({'location': f'page {index + 1}', 'text': text[:MAX_PAGE_CHARS]})
    elif suffix == '.docx':
        from docx import Document
        document = Document(io.BytesIO(raw))
        text = '\n'.join(paragraph.text for paragraph in document.paragraphs)
        truncated = len(text) > MAX_PAGE_CHARS
        pages = [{'location': 'document', 'text': text[:MAX_PAGE_CHARS]}]
    elif suffix == '.xlsx':
        from openpyxl import load_workbook
        workbook = load_workbook(io.BytesIO(raw), read_only=True, data_only=True)
        try:
            truncated = len(workbook.worksheets) > MAX_PAGES
            for sheet in workbook.worksheets[:MAX_PAGES]:
                rows = (' | '.join(str(cell) for cell in row if cell is not None)
                        for row in sheet.iter_rows(max_row=500, values_only=True))
                text = '\n'.join(rows)
                truncated |= len(text) > MAX_PAGE_CHARS or bool(sheet.max_row and sheet.max_row > 500)
                pages.append({'location': f'sheet {sheet.title}', 'text': text[:MAX_PAGE_CHARS]})
        finally:
            workbook.close()
    else:
        text = raw.decode('utf-8-sig', errors='replace')
        if suffix == '.csv':
            text = '\n'.join(' | '.join(row) for row in csv.reader(io.StringIO(text)))
        truncated = len(text) > MAX_PAGE_CHARS
        pages = [{'location': 'document', 'text': text[:MAX_PAGE_CHARS]}]
    return digest, [page for page in pages if page['text'].strip()], truncated


def process_source(source):
    """Persist parse state; no AI call and no approval inference."""
    source.status = 'processing'
    source.save(update_fields=['status', 'updated_at'])
    document = source.project_document or source.crs_revision.document
    file_field = document.file if source.project_document else document.pdf_file
    filename = (document.original_filename or file_field.name) if source.project_document else file_field.name
    try:
        if not file_field:
            raise ValueError('Source has no stored file.')
        digest, pages, truncated = extract_pages(file_field, filename)
        source.content_sha256 = digest
        source.extracted_pages = pages
        source.status = 'ready' if pages and not truncated else 'needs_review'
        source.error = ('Extracted text exceeded processing limits; review the source.' if truncated else
                        '' if pages else 'No embedded text found; scanned documents need review.')
    except ValidationError as exc:
        source.status = 'failed'
        source.error = str(exc)[:300]
        source.extracted_pages = []
    except Exception:  # storage/parser internals must not be returned to clients
        source.status = 'failed'
        source.error = 'Source could not be processed. Check the file and retry.'
        source.extracted_pages = []
    source.save(update_fields=['status', 'error', 'content_sha256', 'extracted_pages', 'updated_at'])
    return source


def source_available(source, user):
    from apps.rbac.action_policy import module_action_allowed
    if source.project_document_id:
        return not source.project_document.is_deleted
    from apps.pid_checker_v2.models import PidEngineeringEvidenceLink
    return (module_action_allowed(user, 'crs_documents', 'read')
            and module_action_allowed(user, 'pid_analysis', 'read')
            and PidEngineeringEvidenceLink.objects.filter(
                project_id=source.project_id, revision_id=source.crs_revision_id).exists())


def source_hash_matches(source):
    if not source.content_sha256:
        return False
    file_field = source.project_document.file if source.project_document_id else source.crs_revision.document.pdf_file
    if not file_field:
        return False
    digest = hashlib.sha256()
    count = 0
    try:
        with file_field.open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                count += len(chunk)
                if count > MAX_BYTES:
                    return False
                digest.update(chunk)
    except (FileNotFoundError, OSError):
        return False
    return digest.hexdigest() == source.content_sha256


def retrieve(project, question, user, *, limit=5):
    from .project_models import ProjectKnowledgeSource
    terms = set(re.findall(r'[a-z0-9]{3,}', question.casefold()))
    matches = []
    sources = ProjectKnowledgeSource.objects.filter(project=project, status='ready').select_related(
        'project_document', 'crs_revision__chain', 'crs_revision__document',
    ).order_by('-created_at')[:200]
    for source in sources:
        if not source_available(source, user):
            continue
        for page in source.extracted_pages:
            excerpt = str(page.get('text', ''))
            score = sum(1 for term in terms if term in excerpt.casefold())
            if score:
                matches.append((score, source, page))
    matches.sort(key=lambda item: (-item[0], item[1].pk, item[2].get('location', '')))
    citations = []
    integrity = {}
    for _, source, page in matches:
        if source.pk not in integrity:
            integrity[source.pk] = source_hash_matches(source)
        if integrity[source.pk]:
            citations.append(citation(source, page))
        if len(citations) >= limit:
            break
    return citations


def citation(source, page):
    if source.crs_revision_id:
        revision = source.crs_revision
        title = revision.document.document_name
        revision_label = revision.revision_label
        approval = 'approved' if (revision.chain.status == 'approved' and revision.status == 'completed'
                                  and revision.document.status == 'completed') else 'unverified'
    else:
        title = source.project_document.title or source.project_document.original_filename
        revision_label = None
        approval = 'unverified'
    return {'source_id': source.pk, 'document': title, 'revision': revision_label,
            'location': page.get('location', 'document'), 'excerpt': str(page.get('text', ''))[:1200],
            'approval': approval, 'sha256': source.content_sha256}
