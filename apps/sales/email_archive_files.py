"""Opt-in local-test handoff, bound to an immutable source and reviewed target."""
from email import policy
from email.parser import BytesParser
from io import BytesIO
import re
import secrets
from uuid import NAMESPACE_URL, uuid5

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils.dateparse import parse_datetime
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from .email_archive_source import ArchiveError, MAX_FILE_BYTES, payload_from_eml
from .document_classification_content import TYPE_FOLDERS, extract_document_text, rule_suggestion
from .email_permissions import require_email_capture_access, visible_email_clients, visible_email_intakes
from .intake_views import SalesEmailIntakeThrottle
from .models import OpportunityAuditEvent, OpportunityWorkspaceUpload, SalesEmailIntake
from .opportunity_workspace import _actor, require_access
from .private_attachments import upload_private_file


def authorized_source(source_id):
    intake = SalesEmailIntake.objects.select_related('opportunity').filter(
        source_message_id=source_id, mailbox_connection__isnull=True).first()
    if not intake or 'RADAI-TEST' not in intake.subject.upper():
        raise NotFound('Test archive source not found.')
    if not intake.opportunity_id or intake.status != 'converted':
        return intake, None
    actor = _actor(intake.reviewed_by_id)
    if not actor or not actor.is_active:
        raise PermissionDenied('The archive reviewer is unavailable.')
    require_email_capture_access(actor)
    if not visible_email_intakes(actor).filter(pk=intake.pk).exists():
        raise PermissionDenied('The source is no longer available to its reviewer.')
    require_access(actor, intake.opportunity, 'create', 'update')
    if not visible_email_clients(actor).filter(pk=intake.opportunity.client_id).exists():
        raise PermissionDenied('The linked client is unavailable to its reviewer.')
    if not OpportunityAuditEvent.objects.filter(
        opportunity_id=intake.opportunity_id, actor=actor,
        event_type__in=('opportunity_created_from_email', 'opportunity_linked_from_email'),
        data__source_email_intake_id=str(intake.pk),
    ).exists():
        raise PermissionDenied('A reviewed email-to-opportunity link is required.')
    return intake, actor


def _safe_name(name, index, source_id):
    basename = str(name or f'Attachment_{index}.bin').replace('\\', '/').rsplit('/', 1)[-1]
    basename = re.sub(r'[\x00-\x1f\x7f"*:<>?/\\|]', '_', basename).strip(' .')
    if not basename or len(basename) > 160:
        raise ValidationError({'file': 'An attachment filename needs review.'})
    # Different incoming emails or same-name parts must not silently become
    # revisions of one another. Retain the original base name with a source prefix.
    return f'{source_id[-64:-52]}_{index:02d}_{basename}'


def archive_reviewed_files(source_id, mailbox, raw):
    intake, actor = authorized_source(source_id)
    if actor is None:
        return {'state': 'awaiting_review', 'file_count': 0}
    try:
        payload = payload_from_eml(raw, mailbox)
    except (ArchiveError, ValueError, TypeError, LookupError):
        raise ValidationError({'file': 'A valid original test email is required.'}) from None
    if (not payload or payload['source_message_id'] != intake.source_message_id
            or any(str(payload[key]).strip() != str(getattr(intake, key)).strip()
                   for key in ('subject', 'sender_name', 'sender_email', 'internet_message_id', 'body_preview'))
            or parse_datetime(payload['received_at']) != intake.received_at
            or payload['has_attachments'] != intake.has_attachments):
        raise ValidationError({'file': 'The original email does not match the reviewed source.'})
    message = BytesParser(policy=policy.default).parsebytes(raw)
    files = [('correspondence', f'Incoming_{intake.pk}.eml', raw, 'message/rfc822')]
    warnings = []
    # Direct attachments only; no execution, links, archives or nested mail expansion.
    for index, part in enumerate(message.iter_attachments(), 1):
        if part.get_content_disposition() != 'attachment':
            continue
        if part.is_multipart():
            warnings.append('nested_attachment_retained_in_original_email')
            continue
        content = part.get_payload(decode=True)
        if not content:
            raise ValidationError({'file': 'An empty or unsupported attachment needs review.'})
        name = _safe_name(part.get_filename(), index, source_id)
        text, diagnostic = extract_document_text(BytesIO(content), name, len(content))
        kind, evidence = rule_suggestion(name, text)
        # A filename alone cannot route a document into Award or Submitted.
        content_backed = not diagnostic and any(item['source'] == 'content' for item in evidence)
        folder = TYPE_FOLDERS.get(kind, 'tender') if content_backed else 'tender'
        if not content_backed or kind == 'unclassified':
            warnings.append('attachment_routing_requires_review')
        files.append((folder, name, content, part.get_content_type()))
        if len(files) > 21:
            raise ValidationError({'file': 'The local pilot supports at most 20 direct attachments per email.'})
    for index, (folder, name, content, content_type) in enumerate(files):
        current, current_actor = authorized_source(source_id)
        if (not current_actor or current_actor.pk != actor.pk
                or current.opportunity_id != intake.opportunity_id):
            raise PermissionDenied('The reviewed archive target changed.')
        request_id = uuid5(NAMESPACE_URL, f'radai-test-archive:{intake.pk}:{index}')
        previous = OpportunityWorkspaceUpload.objects.filter(
            workspace__opportunity=intake.opportunity, request_id=request_id,
        ).first()
        if previous:
            # A retry must retain its originally recorded destination, including
            # uploads made by the earlier Tender-only pilot.
            folder = previous.folder_key
        upload_private_file(intake.opportunity, current_actor, folder,
                            SimpleUploadedFile(name, content, content_type=content_type), request_id)
    return {'state': 'complete', 'file_count': len(files), 'warnings': sorted(set(warnings))}


@api_view(['POST'])
@authentication_classes([])
@permission_classes([AllowAny])
@throttle_classes([SalesEmailIntakeThrottle])
def sales_test_archive_files(request):
    # The existing intake credential identifies the local archive agent. It may
    # only act for a converted test source and its currently authorized reviewer;
    # caller-supplied actors, opportunities, folders and filenames are rejected.
    if (not getattr(settings, 'SALES_TEST_ARCHIVE_FILES_ENABLED', False)
            or settings.ENVIRONMENT not in {'development', 'local', 'testing', 'test'}):
        error = APIException('Local test archive file handoff is not enabled.')
        error.status_code = 503
        raise error
    expected = str(getattr(settings, 'SALES_EMAIL_INTAKE_WEBHOOK_KEY', '') or '')
    if not expected or not secrets.compare_digest(expected, request.headers.get('X-RADAI-Webhook-Key', '')):
        raise PermissionDenied('Invalid webhook credential.')
    allowed = {'source_message_id', 'mailbox', 'file'}
    if set(request.data) - allowed:
        raise ValidationError({'detail': 'Only a test source, mailbox and original email may be supplied.'})
    source_id = request.data.get('source_message_id', '')
    if not isinstance(source_id, str) or not re.fullmatch(r'onedrive-test:[a-f0-9]{64}', source_id):
        raise ValidationError({'source_message_id': 'A local test archive source is required.'})
    intake, actor = authorized_source(source_id)
    if actor is None:
        return Response({'state': 'awaiting_review', 'file_count': 0})
    uploaded = request.FILES.get('file')
    if uploaded is None:
        return Response({'state': 'ready', 'file_count': 0})
    mailbox = request.data.get('mailbox', '')
    if not isinstance(mailbox, str) or not re.fullmatch(r'[^\s<>@]+@[^\s<>@]+\.[^\s<>@]+', mailbox):
        raise ValidationError({'mailbox': 'The receiving test mailbox is required.'})
    if uploaded.size > MAX_FILE_BYTES:
        raise ValidationError({'file': 'The original email exceeds the local pilot size limit.'})
    return Response(archive_reviewed_files(source_id, mailbox, uploaded.read(MAX_FILE_BYTES + 1)))
