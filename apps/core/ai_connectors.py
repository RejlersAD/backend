"""RADAI Intelligence connectors: bring records from other RADAI modules into a chat.

A connector finds records the user is already allowed to read and turns the chosen one into a
file. The chat then treats it exactly like an uploaded attachment (same validation, extraction,
retrieval and audit), so no record is ever shown to someone who couldn't open it in its module.
"""
import csv
import io
from pathlib import Path

from django.db.models import Q
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from .intelligence_knowledge import ALLOWED, MAX_BYTES

SEARCH_LIMIT = 25
LINE_LIST_MAX_ROWS = 5000
LINE_LIST_COLUMNS = [
    ('tag', 'Line tag'), ('size', 'Size'), ('service_code', 'Service'), ('serial', 'Serial'), ('spec', 'Spec'),
    ('fluid_service', 'Fluid service'), ('from_ref', 'From'), ('to_ref', 'To'), ('pid_no', 'P&ID'),
]


def _allowed(user, module):
    from apps.rbac.action_policy import module_action_allowed
    return module_action_allowed(user, module, 'read')


def _project_documents(user, q):
    from apps.planning_intelligence.access import can_access_enterprise_project
    from apps.project_control.models import ProjectDocument
    rows = (ProjectDocument.objects.filter(is_deleted=False).select_related('project')
            .exclude(file='').order_by('-created_at'))
    if q:
        rows = rows.filter(Q(title__icontains=q) | Q(original_filename__icontains=q) | Q(project__name__icontains=q))
    items = []
    for doc in rows[:SEARCH_LIMIT * 4]:  # over-fetch, then drop projects the user can't open
        name = doc.original_filename or Path(doc.file.name).name
        if Path(name).suffix.lower() not in ALLOWED or not can_access_enterprise_project(user, doc.project):
            continue
        items.append({'id': str(doc.pk), 'title': doc.title or name, 'subtitle': f'{doc.project.name} · {name}',
                      'meta': doc.get_kind_display() if hasattr(doc, 'get_kind_display') else doc.kind,
                      'size': doc.size_bytes, 'created_at': doc.created_at.isoformat()})
        if len(items) >= SEARCH_LIMIT:
            break
    return items


def _project_document_file(user, item_id):
    from apps.planning_intelligence.access import can_access_enterprise_project
    from apps.project_control.models import ProjectDocument
    doc = ProjectDocument.objects.filter(pk=item_id, is_deleted=False).select_related('project').first()
    if not doc or not doc.file or not can_access_enterprise_project(user, doc.project):
        raise NotFound('Document not found.')
    name = doc.original_filename or Path(doc.file.name).name
    if Path(name).suffix.lower() not in ALLOWED:
        raise ValidationError({'item_id': 'RADAI can read PDF, Word, Excel, CSV and text documents only.'})
    if (doc.size_bytes or 0) > MAX_BYTES:
        raise ValidationError({'item_id': 'This document is larger than 12 MB.'})
    with doc.file.open('rb') as handle:
        content = handle.read(MAX_BYTES + 1)
    return name, content, doc.content_type or '', {'project_document_id': str(doc.pk), 'project_id': str(doc.project_id)}


def _line_lists(user, q):
    from apps.pid_checker_v2.models import PidCheckerV2LineListUpload
    rows = PidCheckerV2LineListUpload.objects.filter(created_by=user).order_by('-is_active', '-created_at')
    if q:
        rows = rows.filter(Q(title__icontains=q) | Q(filename__icontains=q) | Q(doc_no__icontains=q))
    return [{'id': str(r.line_list_id), 'title': r.title or r.filename,
             'subtitle': ' · '.join(filter(None, [r.doc_no, r.filename, r.sheet_name])),
             'meta': f"{r.total_rows} lines{' · active' if r.is_active else ''}",
             'created_at': r.created_at.isoformat()} for r in rows[:SEARCH_LIMIT]]


def _line_list_file(user, item_id):
    from apps.pid_checker_v2.models import PidCheckerV2LineListUpload
    upload = PidCheckerV2LineListUpload.objects.filter(line_list_id=item_id, created_by=user).first()
    if not upload:
        raise NotFound('Line list not found.')
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in LINE_LIST_COLUMNS])
    rows = upload.rows.order_by('excel_row').values_list(*[field for field, _ in LINE_LIST_COLUMNS])
    for row in rows[:LINE_LIST_MAX_ROWS]:
        writer.writerow(['' if value is None else value for value in row])
    title = (upload.title or Path(upload.filename).stem or 'Line list').replace('/', '-')[:120]
    return f'Line list - {title}.csv', buffer.getvalue().encode('utf-8'), 'text/csv', {'line_list_id': str(upload.line_list_id)}


CONNECTORS = {
    'project_documents': {
        'label': 'Project documents', 'module': 'project_control',
        'description': 'Documents uploaded to Project Control for projects you can access.',
        'search': _project_documents, 'file': _project_document_file,
    },
    'line_lists': {
        'label': 'Line lists', 'module': 'pid_analysis',
        'description': 'Line lists you uploaded to the P&ID checker, as a table.',
        'search': _line_lists, 'file': _line_list_file,
    },
}


def available(user):
    return [{'id': key, 'label': c['label'], 'description': c['description'], 'available': _allowed(user, c['module']),
             'reason': '' if _allowed(user, c['module']) else f"You don't have read access to {c['label'].lower()}."}
            for key, c in CONNECTORS.items()]


def _connector(user, key):
    connector = CONNECTORS.get(key)
    if not connector:
        raise NotFound('Unknown connector.')
    if not _allowed(user, connector['module']):
        raise PermissionDenied(f"You don't have read access to {connector['label'].lower()}.")
    return connector


def search(user, key, q=''):
    return _connector(user, key)['search'](user, (q or '').strip()[:120])


def fetch(user, key, item_id):
    """Return (filename, bytes, content_type, provenance) for one record the user may read."""
    return _connector(user, key)['file'](user, str(item_id)[:64])
