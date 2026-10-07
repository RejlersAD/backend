"""Scoped human classification commands and a leased SQL classification outbox."""
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import json
from pathlib import PurePosixPath
import re
import unicodedata
from uuid import UUID, uuid4

from django.db import IntegrityError, transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from .classification_models import (OpportunityDocumentClassification as Classification,
                                    OpportunityDocumentClassificationRun as Run,
                                    OpportunityDocumentClassificationCommand as Command)
from .document_classification_content import (COLORS, LABELS, SOURCE_BYTES, extract_document_text,
                                               intelligence_tags, rule_suggestion, type_catalog)
from .email_ai_provider import analyze_document_sources, email_ai_cache_identity, email_ai_configuration
from .opportunity_workspace import _actor, require_access, workspace_allowed
from .workflow import _audit

LEASE_SECONDS = 180
MAX_ATTEMPTS = 3


class ClassificationConflict(APIException):
    status_code = 409
    default_detail = 'This document or its type changed. Refresh before saving.'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _identity(upload):
    return _digest({key: str(getattr(upload, key)) for key in (
        'pk', 'document_id', 'workspace_id', 'folder_key', 'name', 'size', 'sha256', 'provider',
        'storage_name', 'storage_fingerprint', 'storage_encoding', 'stored_size', 'stored_sha256', 'status')})


def queue_classification(upload, actor):
    """Call within readiness/head/audit transaction; performs no broker/provider I/O."""
    if upload.provider != 'radai' or upload.status != 'ready' or not upload.document_id:
        raise ValueError('Classification requires a ready, canonical private upload.')
    return Run.objects.get_or_create(upload=upload, defaults={
        'requested_by': actor, 'source_sha256': upload.sha256, 'source_identity': _identity(upload),
        'next_attempt_at': timezone.now(),
    })[0]


def classification_projection(upload, actor=None):
    from .document_versions import resolve_document
    document = resolve_document(upload)
    state = Classification.objects.filter(document=document).first() if document else None
    run = Run.objects.filter(upload=upload).first()
    confirmed = state.confirmed_type if state else ''
    kind = confirmed or (run.suggested_type if run else 'unclassified')
    head = document is None or document.head_upload_id == upload.pk
    editable = bool(actor and head and workspace_allowed(actor, 'update'))
    readable = bool(actor and workspace_allowed(actor, 'export'))
    updated_at = max((row.updated_at for row in (state, run) if row), default=None)
    opportunity = upload.workspace.opportunity
    client = opportunity.client
    # Filename/folder signals are available immediately, before the durable
    # worker extracts document content. Worker tags are added when available.
    immediate_tags, _, _, _ = intelligence_tags(upload.folder_key, upload.name, '', kind, [], 'unclassified')
    run_tags = list(dict.fromkeys(immediate_tags + (list(run.tags) if run else [])))
    global_tags = [
        f'opportunity_id:{opportunity.pk}', f'client_name:{client.company_name}', f'country:{client.country or ""}',
        'business_unit:', f'service_line:{",".join(opportunity.service_categories or [])}',
        f'stage:{opportunity.stage}',
    ]
    tags = list(dict.fromkeys(run_tags + global_tags))
    search_keywords = list(dict.fromkeys((list(run.search_keywords) if run else []) + [
        opportunity.deal_code, opportunity.deal_name, client.company_name, client.country,
        *list(opportunity.service_categories or []), opportunity.stage, upload.folder_key,
    ]))
    intelligence = {
        'documentType': LABELS.get(kind, LABELS['unclassified']), 'folder': upload.folder_key,
        'tags': tags, 'confidence': run.confidence if run else 0,
        'recommendedFolder': run.recommended_folder if run else '',
        'reasoning': run.reasoning if run else 'Classification has not been processed.',
        'searchKeywords': [value for value in search_keywords if value],
    }
    return {
        'document_type': kind, 'label': LABELS.get(kind, LABELS['unclassified']),
        'custom_tag': state.custom_tag if state else '',
        'color': COLORS.get(kind, 'slate'),
        'origin': 'confirmed' if confirmed else (run.origin if run else 'unclassified'),
        'status': run.status if run else 'not_requested', 'revision': state.revision if state else 0,
        'suggested_type': run.suggested_type if run else 'unclassified',
        'evidence': run.evidence if run and readable else [],
        'error_code': run.error_code if run else '', 'ai_status': run.ai_status if run else 'not_needed',
        'provider': run.provider if run else '', 'model': run.model if run else '',
        'extraction_code': run.extraction_code if run else '',
        'can_edit': editable, 'can_retry': bool(editable and readable and (not run or run.status not in {'queued', 'running'})),
        'source_upload_id': str(upload.pk), 'source_sha256': upload.sha256,
        'updated_at': updated_at.isoformat() if updated_at else None,
        'confirmed_scope': 'document' if confirmed else None,
        'intelligence': intelligence,
    }


def get_document_classification(opportunity_id, actor, folder_key, file_id):
    from .models import Deal
    from .private_attachments import _file, _identity as storage_identity
    opportunity = Deal.objects.filter(pk=opportunity_id).first()
    if opportunity is None:
        raise NotFound()
    actor = _actor(getattr(actor, 'pk', None))
    upload = _file(opportunity, actor, folder_key, file_id)
    storage_identity(upload)
    return {'classification': classification_projection(upload, actor), 'type_catalog': type_catalog()}


@contextmanager
def _locked(upload_id):
    from .models import Deal, OpportunityWorkspace, OpportunityWorkspaceUpload, OpportunityDocument
    with transaction.atomic():
        initial = OpportunityWorkspaceUpload.objects.select_related('workspace').filter(pk=upload_id).first()
        if initial is None:
            raise NotFound()
        opportunity = Deal.objects.select_for_update(no_key=True).get(pk=initial.workspace.opportunity_id)
        OpportunityWorkspace.objects.select_for_update().get(pk=initial.workspace_id)
        latest = OpportunityWorkspaceUpload.objects.get(pk=upload_id)
        document = OpportunityDocument.objects.select_for_update().filter(pk=latest.document_id).first()
        upload = OpportunityWorkspaceUpload.objects.select_for_update().get(pk=upload_id)
        yield opportunity, document, upload


def _payload(data, kind):
    required = {'request_id', 'expected_revision'}
    editable = {'document_type', 'custom_tag'} if kind == 'confirm' else set()
    if (not isinstance(data, dict) or not required <= set(data) or set(data) - required - editable - {'reason'}
            or (kind == 'confirm' and not set(data) & editable)):
        raise ValidationError({'detail': 'Send the current revision, request UUID and document type and/or custom tag.'})
    if type(data['expected_revision']) is not int or data['expected_revision'] < 0:
        raise ValidationError({'expected_revision': 'Use the current document type revision.'})
    try:
        request_id = UUID(str(data['request_id']))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError({'request_id': 'Use a request UUID.'}) from None
    if 'document_type' in data and (not isinstance(data['document_type'], str) or data['document_type'] not in LABELS):
        raise ValidationError({'document_type': 'Choose a supported document type.'})
    custom_tag = None
    if 'custom_tag' in data:
        custom_tag = data['custom_tag']
        if (not isinstance(custom_tag, str)
                or any(unicodedata.category(character) in {'Cc', 'Cs', 'Zl', 'Zp'} for character in custom_tag)):
            raise ValidationError({'custom_tag': 'Use a single-line text tag of at most 80 characters, or empty text to clear it.'})
        custom_tag = unicodedata.normalize('NFC', custom_tag).strip()
        if len(custom_tag) > 80:
            raise ValidationError({'custom_tag': 'Use a single-line text tag of at most 80 characters, or empty text to clear it.'})
    reason = data.get('reason', '')
    if not isinstance(reason, str) or len(reason) > 1000 or '\x00' in reason:
        raise ValidationError({'reason': 'Use at most 1000 characters.'})
    return request_id, reason.strip(), custom_tag


def _command(opportunity_id, actor, folder_key, file_id, data, kind):
    from .document_versions import materialize_document
    from .models import Deal
    from .private_attachments import _file, _identity as storage_identity
    request_id, reason, custom_tag = _payload(data, kind)
    actor = _actor(getattr(actor, 'pk', None))
    opportunity = Deal.objects.filter(pk=opportunity_id).first()
    if opportunity is None:
        raise NotFound()
    upload = _file(opportunity, actor, folder_key, file_id, 'update', *(['export'] if kind == 'retry' else []))
    with _locked(upload.pk) as (opportunity, document, upload):
        actor = _actor(actor.pk)
        require_access(actor, opportunity, 'update', *(['export'] if kind == 'retry' else []))
        storage_identity(upload)
        if document and document.head_upload_id != upload.pk:
            raise ClassificationConflict()
        if document is None:
            document = materialize_document(upload)
        if document.head_upload_id != upload.pk:
            raise ClassificationConflict()
        state = Classification.objects.select_for_update().filter(document=document).first()
        digest = _digest({'document': str(document.pk), 'upload': str(upload.pk), 'kind': kind, 'data': data})
        previous = Command.objects.filter(actor=actor, request_id=request_id).first()
        if previous:
            if previous.request_hash != digest:
                raise ClassificationConflict()
            return {'classification': classification_projection(upload, actor), 'replayed': True}
        if data['expected_revision'] != (state.revision if state else 0):
            raise ClassificationConflict()
        if kind == 'confirm':
            before = state.confirmed_type if state else ''
            before_custom_tag = state.custom_tag if state else ''
            state = state or Classification(document=document)
            if 'document_type' in data:
                state.confirmed_type = data['document_type']
            if custom_tag is not None:
                state.custom_tag = custom_tag
            state.updated_by = actor
            state.revision += 1
            state.save()
            audit_data = {'before': before, 'document_type': state.confirmed_type,
                          'before_custom_tag': before_custom_tag, 'custom_tag': state.custom_tag,
                          'revision': state.revision}
        else:
            run = Run.objects.select_for_update().filter(upload=upload).first()
            if run and run.status in {'queued', 'running'}:
                raise ClassificationConflict('Classification is already queued or running.')
            if not run:
                run = queue_classification(upload, actor)
            else:
                run.status, run.requested_by, run.attempts = 'queued', actor, 0
                run.source_sha256, run.source_identity = upload.sha256, _identity(upload)
                run.lease_token, run.lease_until = None, None
                run.next_attempt_at, run.error_code = timezone.now(), ''
                run.save()
            audit_data = {'run_id': str(run.pk)}
        Command.objects.create(document=document, upload=upload, actor=actor, request_id=request_id,
                               request_hash=digest, kind=kind)
        event_type = ('document_type_confirmed' if 'document_type' in data else 'document_custom_tag_changed')
        _audit(opportunity, actor, event_type if kind == 'confirm' else 'document_classification_requested',
               reason=reason, data={**audit_data, 'document_id': str(document.pk), 'upload_id': str(upload.pk),
                                   'request_id': str(request_id)})
        return {'classification': classification_projection(upload, actor), 'replayed': False}


def save_document_classification(opportunity_id, actor, folder_key, file_id, data):
    try:
        return _command(opportunity_id, actor, folder_key, file_id, data, 'confirm')
    except IntegrityError:
        raise ClassificationConflict() from None


def retry_document_classification(opportunity_id, actor, folder_key, file_id, data):
    try:
        return _command(opportunity_id, actor, folder_key, file_id, data, 'retry')
    except IntegrityError:
        raise ClassificationConflict() from None


def due_classification_ids(limit=100):
    now = timezone.now()
    return list(Run.objects.filter(
        Q(status='queued', next_attempt_at__lte=now) |
        Q(status='failed', next_attempt_at__lte=now) |
        Q(status='running', lease_until__lte=now),
    ).order_by('next_attempt_at', 'pk').values_list('pk', flat=True)[:limit])


def _authorize(opportunity, document, upload, run):
    from .private_attachments import _identity as storage_identity
    from .opportunity_workspace import WorkspaceAPIError
    actor = _actor(run.requested_by_id)
    require_access(actor, opportunity, 'update', 'export')
    if (not document or document.head_upload_id != upload.pk or upload.status != 'ready'
            or upload.provider != 'radai' or upload.sha256 != run.source_sha256
            or _identity(upload) != run.source_identity):
        raise ClassificationConflict()
    try:
        storage_identity(upload)
    except WorkspaceAPIError:
        raise ClassificationConflict() from None
    return actor


def _claim(run_id):
    initial = Run.objects.filter(pk=run_id).first()
    if initial is None:
        return None
    with _locked(initial.upload_id) as (opportunity, document, upload):
        run = Run.objects.select_for_update().get(pk=run_id)
        now = timezone.now()
        if (run.status == 'running' and run.lease_until and run.lease_until > now) or run.status in {'completed', 'blocked'}:
            return None
        if run.next_attempt_at and run.next_attempt_at > now:
            return None
        try:
            actor = _authorize(opportunity, document, upload, run)
        except (PermissionDenied, ClassificationConflict):
            run.status, run.error_code = 'blocked', 'source_or_access_changed'
            run.next_attempt_at, run.lease_token, run.lease_until = None, None, None
            run.save()
            _audit(opportunity, _actor(run.requested_by_id), 'document_classification_blocked',
                   data={'run_id': str(run.pk), 'upload_id': str(upload.pk), 'error_code': run.error_code})
            return None
        if run.attempts >= MAX_ATTEMPTS:
            run.status, run.error_code, run.next_attempt_at = 'failed', 'attempts_exhausted', None
            run.lease_token, run.lease_until = None, None
            run.save()
            return None
        run.status, run.lease_token = 'running', uuid4()
        run.lease_until = now + timedelta(seconds=LEASE_SECONDS)
        run.attempts += 1
        run.save()
        return run, upload, actor, opportunity


def _ai_proposal(name, text, folder_key):
    schema = {'type': 'object', 'additionalProperties': False, 'required': ['document_type', 'source', 'excerpt'],
              'properties': {'document_type': {'type': 'string', 'enum': list(LABELS)},
                             'source': {'type': 'string', 'enum': ['filename', 'content']},
                             'excerpt': {'type': 'string'}}}
    result = analyze_document_sources({'filename': name, 'content': text, 'category_context': folder_key}, schema,
        instructions='Use exactly one supported business type. Category is context, never sole evidence. '
        'Incoming/outgoing mail requires explicit direction in the source; sender domains do not establish it. '
        'Cite at most 160 exact characters from filename or content. Return unclassified and an empty excerpt '
        'if unsupported or ambiguous. These are document purpose labels, never workflow approvals. Types: ' +
        json.dumps(LABELS))
    if result['status'] != 'completed':
        return result, None
    proposal = result.get('proposal')
    valid = (isinstance(proposal, dict) and set(proposal) == {'document_type', 'source', 'excerpt'}
             and isinstance(proposal.get('document_type'), str) and proposal['document_type'] in LABELS
             and proposal.get('source') in {'filename', 'content'} and isinstance(proposal.get('excerpt'), str)
             and len(proposal['excerpt']) <= 160)
    if valid and proposal['document_type'] != 'unclassified':
        source = name if proposal['source'] == 'filename' else text
        valid = bool(proposal['excerpt'].strip() and proposal['excerpt'] in source)
        if proposal['document_type'] in {'incoming_mail', 'outgoing_mail'}:
            direction = 'incoming' if proposal['document_type'] == 'incoming_mail' else 'outgoing'
            valid = valid and direction in proposal['excerpt'].lower()
    if not valid:
        return {**result, 'status': 'failed', 'error_code': 'invalid_evidence'}, None
    evidence = [] if proposal['document_type'] == 'unclassified' else [{
        'source': proposal['source'], 'rule': 'ai_source_excerpt', 'matched_text': proposal['excerpt']}]
    return result, (proposal['document_type'], evidence)


def run_document_classification(run_id):
    claimed = _claim(run_id)
    if not claimed:
        return {'processed': False}
    run, upload, actor, opportunity = claimed
    result = {'suggested_type': 'unclassified', 'origin': 'unclassified', 'evidence': [], 'error_code': '',
              'ai_status': 'not_needed', 'provider': '', 'model': '', 'extraction_code': '', 'tags': [],
              'confidence': 0, 'recommended_folder': '', 'reasoning': '', 'search_keywords': []}
    provider_identity = None
    try:
        from .private_attachments import download_private_file
        text = ''
        if upload.size <= SOURCE_BYTES:
            stream, _ = download_private_file(opportunity, actor, upload.folder_key, 'radai-' + str(upload.pk))
            try:
                text, result['extraction_code'] = extract_document_text(stream, upload.name, upload.size)
            finally:
                stream.close()
        else:
            result['extraction_code'] = 'source_too_large'
        kind, evidence = rule_suggestion(upload.name, text)
        result.update(suggested_type=kind, origin='rule' if kind != 'unclassified' else 'unclassified', evidence=evidence)
        # Automatic document tagging is deliberately deterministic. Folder,
        # filename and extracted content are sufficient for the supported tag
        # vocabulary; ambiguous files remain reviewable instead of waiting for
        # an external AI provider or sending document content off-box.
        if kind == 'unclassified':
            result.update(ai_status='disabled', error_code='ai_not_used')
        tags, confidence, recommended, reasoning = intelligence_tags(
            upload.folder_key, upload.name, text, result['suggested_type'], result['evidence'], result['origin'])
        result.update(tags=tags, confidence=confidence, recommended_folder=recommended, reasoning=reasoning,
                      search_keywords=list(dict.fromkeys([*tags, *re.findall(r'[A-Za-z0-9][A-Za-z0-9_-]{2,}',
                                                                           PurePosixPath(upload.name).stem)[:20]])))
    except PermissionDenied:
        result['error_code'] = 'source_or_access_changed'
    except Exception:
        # Never include file text, provider payloads or storage paths in diagnostics.
        result['error_code'] = 'processing_unavailable'
    with _locked(upload.pk) as (opportunity, document, current_upload):
        current = Run.objects.select_for_update().get(pk=run.pk)
        if current.lease_token != run.lease_token or not current.lease_until or current.lease_until <= timezone.now():
            return {'processed': False}
        try:
            actor = _authorize(opportunity, document, current_upload, current)
            if provider_identity and provider_identity != email_ai_cache_identity():
                raise ClassificationConflict()
        except (PermissionDenied, ClassificationConflict):
            result.update(suggested_type='unclassified', origin='unclassified', evidence=[], tags=[], confidence=0,
                          recommended_folder='', reasoning='Source or access changed; manual review is required.',
                          search_keywords=[], error_code='source_or_access_changed')
        code = result['error_code']
        current.status = ('blocked' if code == 'source_or_access_changed' else
                          'failed' if result['ai_status'] == 'failed' or code == 'processing_unavailable' else 'completed')
        for key, value in result.items():
            setattr(current, key, value)
        current.lease_token, current.lease_until = None, None
        current.next_attempt_at = (timezone.now() + timedelta(seconds=60 * current.attempts)
                                   if current.status == 'failed' and current.attempts < MAX_ATTEMPTS else None)
        current.save()
        _audit(opportunity, actor, 'document_classification_finished', data={
            'upload_id': str(upload.pk), 'run_id': str(current.pk), 'status': current.status,
            'suggested_type': current.suggested_type, 'origin': current.origin,
            'error_code': code, 'source_sha256': current.source_sha256,
        })
    return {'processed': True, 'status': current.status}
