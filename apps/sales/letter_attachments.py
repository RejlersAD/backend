"""Attach generated letter files to the opportunity Correspondence folder.

Uses the exact same upload services as the manual workspace-upload endpoint
(`DealViewSet.workspace_upload`): one call per file to `upload_workspace_file`
(SharePoint) or `upload_private_file` (RADAI), with the provider chosen the
same way the workspace UI chooses its storage — SharePoint when a workspace is
configured for the deployment, RADAI otherwise. Document records, metadata,
permissions, classification runs and the workspace upload audit entries are
all created by those services, identical to a manually uploaded file.
"""

import logging
from uuid import uuid4

from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from .letter_docx import DOCX_CONTENT_TYPE, get_letter_docx_bytes
from .letter_pdf import get_letter_pdf_bytes
from .opportunity_workspace import upload_workspace_file
from .private_attachments import upload_private_file
from .workspace_graph import workspace_config

logger = logging.getLogger(__name__)

CORRESPONDENCE_FOLDER = 'correspondence'
PDF_CONTENT_TYPE = 'application/pdf'


def _read_field_file(field_file):
    field_file.open('rb')
    content = field_file.read()
    field_file.close()
    return content


def _letter_file_payloads(letter):
    """Return [(kind, filename, content, content_type)] for the letter outputs.

    Filenames match the download endpoints; re-attaching after a regeneration
    uploads a new version of the same Correspondence document.
    """
    base = f"{letter.opportunity.deal_code}-{letter.letter_type}"
    payloads = []
    if letter.pdf_file:
        try:
            payloads.append(('pdf', f"{base}.pdf", _read_field_file(letter.pdf_file), PDF_CONTENT_TYPE))
        except Exception as exc:
            logger.error(f"Letter {letter.id}: unable to read PDF for attach: {exc}")
    if letter.docx_file:
        try:
            payloads.append(('docx', f"{base}.docx", _read_field_file(letter.docx_file), DOCX_CONTENT_TYPE))
        except Exception as exc:
            logger.error(f"Letter {letter.id}: unable to read DOCX for attach: {exc}")
    # Ensure bytes exist even for letters generated before file fields were populated.
    if not any(kind == 'pdf' for kind, *_ in payloads):
        try:
            payloads.append(('pdf', f"{base}.pdf", get_letter_pdf_bytes(letter), PDF_CONTENT_TYPE))
        except Exception as exc:
            logger.error(f"Letter {letter.id}: on-demand PDF generation for attach failed: {exc}")
    if not any(kind == 'docx' for kind, *_ in payloads):
        try:
            payloads.append(('docx', f"{base}.docx", get_letter_docx_bytes(letter), DOCX_CONTENT_TYPE))
        except Exception as exc:
            logger.error(f"Letter {letter.id}: on-demand DOCX generation for attach failed: {exc}")
    return payloads


def attach_letter_files(letter, actor):
    """Upload the letter PDF+DOCX to the Correspondence folder via the standard
    workspace upload service and record the outcome. Never raises.

    One service call per file — the same call the manual workspace upload
    endpoint makes for the deployment's storage (no fallback chain).
    """
    from .workflow import _audit

    existing = (letter.custom_data or {}).get('attachments') or {}
    version = int(existing.get('version') or 0) + 1
    provider = 'sharepoint' if workspace_config() is not None else 'radai'
    upload = upload_workspace_file if provider == 'sharepoint' else upload_private_file

    files = []
    for kind, filename, content, content_type in _letter_file_payloads(letter):
        # The upload ledger keys attempts by a UUID request id — the manual
        # upload endpoint validates one per upload, so mirror that exactly.
        request_id = uuid4()
        uploaded = SimpleUploadedFile(filename, content, content_type=content_type)
        try:
            result, _created = upload(
                letter.opportunity, actor, CORRESPONDENCE_FOLDER, uploaded, request_id,
            )
            files.append({'kind': kind, 'name': filename, 'provider': provider, 'status': 'ready', 'file': result})
        except Exception as exc:
            logger.error(f"Letter {letter.id}: attach of {filename} via {provider} failed: {exc}")
            files.append({'kind': kind, 'name': filename, 'provider': provider, 'status': 'failed', 'error': str(exc)})

    custom_data = dict(letter.custom_data or {})
    custom_data['attachments'] = {
        'version': version,
        'folder': CORRESPONDENCE_FOLDER,
        'provider': provider,
        'attached_at': timezone.now().isoformat(),
        'files': files,
    }
    letter.custom_data = custom_data
    letter.save(update_fields=['custom_data', 'updated_at'])

    ready = [f['name'] for f in files if f.get('status') == 'ready']
    failed = [f for f in files if f.get('status') != 'ready']
    if ready:
        reason = f"Attached {', '.join(ready)} to the Correspondence folder ({provider} storage)"
    else:
        details = '; '.join(f"{f['name']}: {f.get('error') or 'upload failed'}" for f in failed)
        reason = f"Letter attach to the Correspondence folder failed ({details})"
    _audit(
        letter.opportunity,
        actor,
        'letter_attached',
        reason=reason,
        data={
            'letter_id': str(letter.id),
            'folder': CORRESPONDENCE_FOLDER,
            'storage_provider': provider,
            'files': ready,
            'failed_files': [f['name'] for f in failed],
            'errors': {f['name']: f.get('error') for f in failed},
        },
    )
    return custom_data['attachments']
