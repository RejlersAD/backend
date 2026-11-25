"""Company evidence enclosed in a Technical Proposal's evidence sections (DEC-TP-001).

Reviewers upload the evidence a section needs (project references, organisation charts, CVs,
subcontractor lists) from the proposal's Evidence review. Files are private RADAI documents in the
opportunity's Internal folder under ``Proposal evidence/<section>/``, so the Proposal folder keeps
only the proposal deliverable, and existing storage, versioning and access rules apply. The next
build encloses them as that section of the PDF.
"""
from __future__ import annotations

import shutil
from pathlib import Path

from rest_framework.exceptions import ValidationError

EVIDENCE_FOLDER = 'internal'
EVIDENCE_PREFIX = 'Proposal evidence/'
EVIDENCE_TYPES = ('.pdf', '.docx', '.doc', '.pptx', '.ppt', '.xlsx', '.xls')
MAX_EVIDENCE_FILES = 20


def accepts_evidence(section):
    """Evidence sections, and authored sections that list documents a reviewer may enclose."""
    return section['kind'] == 'evidence' or (section['kind'] == 'authored' and bool(section.get('evidence')))


def evidence_section_keys(cfg):
    return [section['key'] for section in cfg['sections'] if accepts_evidence(section)]


def _evidence_section(section_key):
    from .proposal_engine_service import config

    section = next((item for item in config()['sections'] if item['key'] == section_key), None)
    if section is None or not accepts_evidence(section):
        raise ValidationError({'section_key': 'Choose a proposal section that takes company evidence.'})
    return section


def _uploads(deal, section_key):
    from .document_versions import current_uploads
    from .models import OpportunityWorkspaceUpload

    return current_uploads(OpportunityWorkspaceUpload.objects.filter(
        workspace__opportunity=deal, provider='radai', folder_key=EVIDENCE_FOLDER, status='ready',
        source_path__startswith=f'{EVIDENCE_PREFIX}{section_key}/',
    )).order_by('created_at', 'pk')


def _item(upload):
    return {'id': f'radai-{upload.pk}', 'name': upload.name, 'size': upload.size,
            'uploaded_at': upload.created_at.isoformat(),
            'uploaded_by': (upload.actor.get_full_name() or upload.actor.username) if upload.actor else None}


def list_evidence(actor, quote_id, section_key):
    from .proposal_workspace_service import _quote

    section = _evidence_section(section_key)
    quote = _quote(actor, quote_id)
    return {'section_key': section_key, 'required': list(section.get('evidence') or []),
            'accepted_types': list(EVIDENCE_TYPES),
            'files': [_item(upload) for upload in _uploads(quote.deal, section_key).select_related('actor')]}


def add_evidence(actor, quote_id, section_key, uploaded, request_id):
    from .models import OpportunityWorkspaceUpload
    from .private_attachments import upload_private_file
    from .proposal_workspace_service import _quote

    _evidence_section(section_key)
    quote = _quote(actor, quote_id, write=True)
    name = str(getattr(uploaded, 'name', '') or '')
    if not name.lower().endswith(EVIDENCE_TYPES):
        raise ValidationError({'file': 'Upload a PDF, Word, PowerPoint or Excel file.'})
    if _uploads(quote.deal, section_key).count() >= MAX_EVIDENCE_FILES:
        raise ValidationError({'file': f'A section can enclose at most {MAX_EVIDENCE_FILES} evidence files.'})
    result, created = upload_private_file(quote.deal, actor, EVIDENCE_FOLDER, uploaded, request_id)
    OpportunityWorkspaceUpload.objects.filter(pk=result['id'][len('radai-'):]).update(
        source_path=f'{EVIDENCE_PREFIX}{section_key}/{name}')
    return list_evidence(actor, quote_id, section_key), created


def remove_evidence(actor, quote_id, section_key, file_id):
    from .private_attachments import delete_private_file
    from .proposal_workspace_service import _quote

    _evidence_section(section_key)
    quote = _quote(actor, quote_id, write=True)
    if not _uploads(quote.deal, section_key).filter(pk=str(file_id).removeprefix('radai-')).exists():
        raise ValidationError({'file': 'This evidence file is not enclosed in the section.'})
    delete_private_file(quote.deal, actor, EVIDENCE_FOLDER, file_id)
    return list_evidence(actor, quote_id, section_key)


def evidence_uploads(deal, section_keys):
    """Current evidence documents per section, for a build."""
    return {key: list(_uploads(deal, key)) for key in section_keys}


def covering_letter(deal):
    """The latest EOI letter in the opportunity's Correspondence folder ({'pdf', 'docx'} uploads).

    EOI letters are filed there as `<deal code>-eoi.pdf/.docx` (see `letter_attachments`); each
    regeneration adds a version, and the current version is the proposal's covering letter.
    """
    from .document_versions import current_uploads
    from .models import OpportunityWorkspaceUpload

    found = {}
    for kind in ('pdf', 'docx'):
        upload = current_uploads(OpportunityWorkspaceUpload.objects.filter(
            workspace__opportunity=deal, provider='radai', folder_key='correspondence', status='ready',
            name__iexact=f'{deal.deal_code}-eoi.{kind}',
        )).order_by('-created_at', '-pk').first()
        if upload:
            found[kind] = upload
    return found


def materialize_letter(letter, directory):
    """Write the covering letter files locally for rendering; {'pdf', 'docx'} paths."""
    return {kind: materialize([upload], Path(directory) / kind)[0] for kind, upload in letter.items()}


def materialize(uploads, directory):
    """Write evidence documents to local files for rendering; returns their paths in order."""
    from .private_attachments import _identity, _read

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for index, upload in enumerate(uploads, 1):
        target = directory / f'{index:02d}-{Path(upload.name).name}'
        with _read(upload, _identity(upload)) as source, open(target, 'wb') as output:
            shutil.copyfileobj(source, output)
        paths.append(target)
    return paths
