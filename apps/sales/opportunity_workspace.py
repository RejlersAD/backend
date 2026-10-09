"""Opportunity-bound storage commands and a SQL-backed provisioning outbox."""
from datetime import timedelta
import re
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from django.db.models import Q
from django.utils import timezone
from rest_framework.exceptions import APIException, PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.data_visibility_mixin import build_visibility_filter

from .models import Client, Deal, OpportunityWorkspace, OpportunityWorkspaceUpload, ProjectHandover
from .attachment_streams import configured_limit, prepare_upload
from .workflow import _audit
from .workspace_graph import (
    WorkspaceError, WorkspaceGraph, file_projection, version_projection, workspace_config,
)


FOLDERS = (
    ('correspondence', 'Correspondence', 'Client emails and clarifications'),
    ('tender', 'Tender', 'RFQs, scope and tender documents'),
    ('proposal', 'Proposal', 'Technical and commercial drafts'),
    ('internal', 'Internal', 'Estimates, reviews and approvals'),
    ('submitted', 'Submitted', 'Issued proposals and submission receipts'),
    ('award', 'Award', 'Award notices and contract documents'),
)
MESSAGES = {
    'not_configured': 'SharePoint workspace setup is not configured.',
    'not_created': 'Create the document workspace for this opportunity.',
    'pending': 'Workspace setup is queued.',
    'creating': 'Creating the opportunity workspace.',
    'ready': 'Workspace ready.',
    'failed': 'Workspace setup failed. Review the connection and retry.',
    'authority_changed': 'The setup requester no longer has access. An authorized user can retry.',
    'configuration_changed': 'The storage destination changed. An administrator must reconcile the existing workspace.',
    'name_conflict': 'A folder or file already uses this name. It was not replaced or linked automatically.',
    'recovery_required': 'A previous storage operation has an uncertain outcome. An administrator must reconcile it before retrying.',
    'remote_access_denied': 'The SharePoint connection does not have access to this location.',
    'remote_scope_changed': 'The linked SharePoint folder moved or changed. An administrator must reconcile it.',
    'remote_missing': 'The linked SharePoint item could not be found.',
    'remote_busy': 'SharePoint is busy. Retry later.',
    'remote_unavailable': 'SharePoint could not complete the request. Retry later.',
    'invalid_remote_response': 'SharePoint returned an unexpected response.',
    'invalid_folder_name': 'The saved VF code is not a valid folder name. Ask an administrator to review this record.',
    'upload_in_progress': 'This upload is still running or awaiting reconciliation. Do not submit another copy.',
    'upload_conflict': 'This upload request was already used for a different file.',
    'not_ready': 'The workspace must be ready before files can be used.',
    'invalid_cursor': 'This file listing expired. Refresh the folder.',
    'workspace_retained': 'This opportunity has a requested or linked document workspace. Preserve it until an administrator reconciles the storage records.',
    'private_storage_unavailable': 'RADAI could not access private attachment storage. Retry the same file or contact your administrator.',
    'private_storage_changed': 'The private storage destination changed. An administrator must restore or reconcile its attachment mappings.',
    'attachment_missing': 'This attachment is unavailable in the selected opportunity folder.',
    'attachment_integrity_failed': 'The stored attachment could not be verified. Contact your administrator.',
    'download_too_large': 'This file exceeds the configured download limit. Open it in SharePoint.',
    'document_changed': 'The document changed while it was being read. Refresh the folder and retry.',
    'version_stale': 'A newer document version exists. Refresh its details before uploading a new version.',
    'cannot_delete_last_version': 'At least one document version must remain. Upload a replacement version first.',
    'delete_not_supported': 'Delete is currently supported for RADAI attachments only.',
    'handover_retained': 'This opportunity has a project handover and cannot be deleted.',
}


class WorkspaceAPIError(APIException):
    status_code = 424

    def __init__(self, code, status_code=424):
        self.status_code = status_code
        super().__init__({'detail': MESSAGES.get(code, MESSAGES['failed']), 'code': code})


def workspace_allowed(actor, *actions):
    return all(module_action_allowed(actor, 'sales_opportunities', action) for action in ('read', *actions))


def require_access(actor, opportunity, *actions):
    if not workspace_allowed(actor, *actions) or not Deal.objects.filter(
        build_visibility_filter(user=actor, module_code='sales', owner_field='owner'), pk=opportunity.pk,
    ).exists():
        raise PermissionDenied('You do not have access to this opportunity workspace.')


def _actor(pk):
    # Fresh identity prevents a cached RBAC profile surviving revocation in jobs.
    return get_user_model().objects.filter(pk=pk).first() if pk else None


def _registered(workspace):
    return bool(workspace.root_item_id or workspace.folders or workspace.intent)


def register_workspace(opportunity, actor):
    """Called inside the registration transaction; never performs network I/O."""
    config = workspace_config()
    workspace = OpportunityWorkspace.objects.create(
        opportunity=opportunity, requested_by=actor, required_action='create',
        status='pending' if config else 'not_configured',
        config_fingerprint=config.fingerprint if config else '',
    )
    # The caller's atomic opportunity_created audit covers this initial outbox row.
    return workspace


@transaction.atomic
def delete_opportunity_with_workspace_guard(opportunity):
    # Worker checkpoints lock workspace then append a Deal-referencing audit.
    # NO KEY UPDATE allows that FK check to finish while we await the workspace.
    Deal.objects.select_for_update(no_key=True).get(pk=opportunity.pk)
    workspace = OpportunityWorkspace.objects.select_for_update().filter(opportunity=opportunity).first()
    if workspace and (workspace.status != 'not_configured' or _registered(workspace)
                      or workspace.uploads.exists()):
        raise WorkspaceAPIError('workspace_retained', 409)
    if ProjectHandover.objects.filter(opportunity=opportunity).exists():
        raise WorkspaceAPIError('handover_retained', 409)
    # Generated letters belong to the opportunity; remove them and their files.
    for letter in opportunity.letters.all():
        if letter.pdf_file:
            letter.pdf_file.delete(save=False)
        if letter.docx_file:
            letter.docx_file.delete(save=False)
        letter.delete()
    # Source email records are immutable; unlink them from the opportunity.
    opportunity.email_intakes.update(opportunity=None)
    # Activity history only exists for this opportunity.
    opportunity.audit_events.all().delete()
    opportunity.delete()


@transaction.atomic
def delete_client_with_workspace_guard(client):
    # Client -> Deal is a legacy CASCADE. Preserve exactly the same storage
    # evidence boundary as direct opportunity deletion, with a usable 409.
    Client.objects.select_for_update().get(pk=client.pk)
    deals = list(Deal.objects.select_for_update(no_key=True).filter(client=client).order_by('pk').values_list('pk', flat=True))
    for workspace in OpportunityWorkspace.objects.select_for_update().filter(opportunity_id__in=deals):
        if workspace.status != 'not_configured' or _registered(workspace) or workspace.uploads.exists():
            raise WorkspaceAPIError('workspace_retained', 409)
    client.delete()


def workspace_projection(opportunity, actor):
    from .private_attachments import private_storage_projection
    from .folder_tags import folder_tag_rows
    from .document_classification import type_catalog
    require_access(actor, opportunity)
    tags = folder_tag_rows(opportunity, actor)
    workspace = OpportunityWorkspace.objects.filter(opportunity=opportunity).first()
    config = workspace_config()
    state = workspace.status if workspace else 'not_created'
    code = workspace.error_code if workspace else ''
    linked = bool(config and workspace and workspace.config_fingerprint == config.fingerprint)
    if not config:
        state, code = 'not_configured', ''
    elif workspace and not linked and _registered(workspace):
        state, code = 'failed', 'configuration_changed'
    elif workspace and workspace.status == 'not_configured':
        state, code = 'not_created', ''
    live_folders = {}
    root_url = workspace.web_url if linked and workspace.web_url else None
    if linked and state == 'ready':
        try:
            graph = WorkspaceGraph(config)
            graph.verify_root()
            root = graph.validate_item(graph.item(workspace.root_item_id), name=opportunity.deal_code,
                                       parent_id=config.root_item_id)
            root_url = root['webUrl']
            children, cursor = [], None
            for _ in range(10):
                page = graph.children(workspace.root_item_id, cursor)
                children.extend(page['value'])
                cursor = page.get('@odata.nextLink')
                if not cursor:
                    break
            if cursor:
                raise WorkspaceError('invalid_remote_response')
            if any(not isinstance(item, dict) or not isinstance(item.get('id'), str) for item in children):
                raise WorkspaceError('invalid_remote_response')
            by_id = {item['id']: item for item in children}
            for key, name, _ in FOLDERS:
                item = by_id.get(workspace.folders.get(key, {}).get('id'))
                if item is None:
                    raise WorkspaceError('remote_missing')
                graph.validate_item(item, name=name, parent_id=workspace.root_item_id)
                count = item.get('folder', {}).get('childCount')
                live_folders[key] = {'web_url': item['webUrl'],
                                     'item_count': count if isinstance(count, int) and not isinstance(count, bool) and count >= 0 else None}
        except WorkspaceError as exc:
            state, code, linked, root_url = 'failed', exc.code, False, None
            live_folders = {}
    manage = workspace_allowed(actor, 'update')
    can_upload = linked and state == 'ready' and workspace_allowed(actor, 'update', 'create')
    return {
        'opportunity_id': str(opportunity.pk), 'deal_code': opportunity.deal_code,
        'status': state, 'message': MESSAGES.get(code, MESSAGES.get(state, MESSAGES['failed'])),
        'error_code': code or None,
        'web_url': root_url,
        'can_manage': bool(manage and config), 'can_upload': bool(can_upload),
        'can_edit_tags': bool(manage),
        'type_catalog': type_catalog(),
        'folders': [{
            'key': key, 'name': name, 'purpose': purpose,
            'tag': tags[key]['tag'], 'tag_token': tags[key]['expected_token'],
            'item_count': live_folders.get(key, {}).get('item_count'),
            'web_url': live_folders.get(key, {}).get('web_url') if linked else None,
        } for key, name, purpose in FOLDERS],
        'updated_at': workspace.updated_at.isoformat() if workspace else None,
        'max_upload_bytes': upload_limit(),
        'radai_storage': private_storage_projection(opportunity, actor),
    }


@transaction.atomic
def setup_workspace(opportunity, actor):
    require_access(actor, opportunity, 'update')
    config = workspace_config()
    if not config:
        raise WorkspaceAPIError('not_configured')
    Deal.objects.select_for_update(no_key=True).get(pk=opportunity.pk)
    workspace, _ = OpportunityWorkspace.objects.get_or_create(opportunity=opportunity)
    if workspace.config_fingerprint and workspace.config_fingerprint != config.fingerprint and _registered(workspace):
        raise WorkspaceAPIError('configuration_changed', 409)
    if workspace.status == 'creating' and workspace.lease_until and workspace.lease_until > timezone.now():
        return workspace
    if workspace.intent:
        raise WorkspaceAPIError('recovery_required', 409)
    if workspace.status == 'ready' and workspace.config_fingerprint == config.fingerprint:
        return workspace
    workspace.requested_by = actor
    workspace.required_action = 'update'
    workspace.config_fingerprint = config.fingerprint
    workspace.status, workspace.error_code = 'pending', ''
    workspace.lease_token, workspace.lease_until = None, None
    workspace.save()
    _audit(opportunity, actor, 'workspace_requested', data={'workspace_id': str(workspace.pk)})
    return workspace


def due_workspace_ids():
    if workspace_config() is None:
        return []
    return list(OpportunityWorkspace.objects.filter(
        Q(status='pending') | Q(status='creating', lease_until__lt=timezone.now()),
    ).order_by('updated_at').values_list('pk', flat=True)[:50])


@transaction.atomic
def _claim(workspace_id):
    workspace = OpportunityWorkspace.objects.select_for_update().filter(pk=workspace_id).first()
    config = workspace_config()
    if not config or not workspace or workspace.status not in ('pending', 'creating'):
        return None
    if workspace.lease_until and workspace.lease_until > timezone.now():
        return None
    token = uuid4()
    workspace.lease_token, workspace.lease_until = token, timezone.now() + timedelta(seconds=120)
    workspace.status = 'creating'
    workspace.save()
    return workspace, config, token


def _check_execution(workspace, config, token):
    current = OpportunityWorkspace.objects.get(pk=workspace.pk)
    if current.lease_token != token or not current.lease_until or current.lease_until <= timezone.now():
        raise WorkspaceError('lease_lost')
    active = workspace_config()
    if not active or active.fingerprint != config.fingerprint or current.config_fingerprint != config.fingerprint:
        raise WorkspaceError('configuration_changed')
    try:
        require_access(_actor(current.requested_by_id), current.opportunity, current.required_action)
    except PermissionDenied:
        raise WorkspaceError('authority_changed') from None
    OpportunityWorkspace.objects.filter(pk=current.pk, lease_token=token).update(
        lease_until=timezone.now() + timedelta(seconds=120), updated_at=timezone.now())


@transaction.atomic
def _checkpoint(workspace, token, **fields):
    current = OpportunityWorkspace.objects.select_for_update().get(pk=workspace.pk)
    if current.lease_token != token or current.lease_until <= timezone.now():
        raise WorkspaceError('lease_lost')
    for key, value in fields.items():
        setattr(current, key, value)
        setattr(workspace, key, value)
    current.save()


def _folder_mapping(item):
    return {'id': item['id'], 'web_url': item['webUrl']}


def run_workspace_setup(workspace_id):
    claimed = _claim(workspace_id)
    if not claimed:
        return {'status': 'skipped'}
    workspace, config, token = claimed
    try:
        _check_execution(workspace, config, token)
        if workspace.intent:
            raise WorkspaceError('recovery_required')
        graph = WorkspaceGraph(config)
        graph.verify_root()
        if workspace.root_item_id:
            graph.validate_item(graph.item(workspace.root_item_id), name=workspace.opportunity.deal_code,
                                parent_id=config.root_item_id)
        else:
            _check_execution(workspace, config, token)
            _checkpoint(workspace, token, intent={'key': 'root', 'name': workspace.opportunity.deal_code,
                                                  'parent_id': config.root_item_id})
            item = graph.create_folder(config.root_item_id, workspace.opportunity.deal_code)
            _checkpoint(workspace, token, root_item_id=item['id'], web_url=item['webUrl'], intent={})
        for key, name, _ in FOLDERS:
            _check_execution(workspace, config, token)
            existing = workspace.folders.get(key)
            if existing:
                graph.validate_item(graph.item(existing['id']), name=name, parent_id=workspace.root_item_id)
                continue
            _checkpoint(workspace, token, intent={'key': key, 'name': name, 'parent_id': workspace.root_item_id})
            item = graph.create_folder(workspace.root_item_id, name)
            _checkpoint(workspace, token, folders={**workspace.folders, key: _folder_mapping(item)}, intent={})
        _check_execution(workspace, config, token)
        with transaction.atomic():
            _checkpoint(workspace, token, status='ready', error_code='', lease_until=None, lease_token=None)
            _audit(workspace.opportunity, _actor(workspace.requested_by_id), 'workspace_ready',
                   data={'workspace_id': str(workspace.pk)})
        return {'status': 'ready'}
    except Exception as exc:
        code = exc.code if isinstance(exc, WorkspaceError) else 'recovery_required'
        if code == 'lease_lost':
            return {'status': 'superseded'}
        with transaction.atomic():
            current = OpportunityWorkspace.objects.select_for_update().get(pk=workspace.pk)
            if current.lease_token != token:
                return {'status': 'superseded'}
            # Explicit provider rejections prove that this operation did not create an item.
            if code in {'name_conflict', 'remote_access_denied', 'remote_busy', 'invalid_folder_name'}:
                current.intent = {}
            elif current.intent:
                code = 'recovery_required'
            current.status, current.error_code = 'failed', code
            current.lease_token, current.lease_until = None, None
            current.save()
            _audit(current.opportunity, _actor(current.requested_by_id), 'workspace_failed',
                   data={'workspace_id': str(current.pk), 'code': code})
        return {'status': 'failed', 'code': code}


def _ready_folder(opportunity, actor, key, *actions):
    require_access(actor, opportunity, *actions)
    names = {folder_key: name for folder_key, name, _ in FOLDERS}
    if key not in names:
        raise ValidationError({'folder_key': 'Choose one of the six opportunity folders.'})
    workspace = OpportunityWorkspace.objects.filter(opportunity=opportunity).first()
    config = workspace_config()
    if not config:
        raise WorkspaceAPIError('not_configured')
    if not workspace or workspace.status != 'ready':
        raise WorkspaceAPIError('not_ready', 409)
    if workspace.config_fingerprint != config.fingerprint:
        raise WorkspaceAPIError('configuration_changed', 409)
    mapping = workspace.folders.get(key)
    if not mapping:
        raise WorkspaceAPIError('not_ready', 409)
    graph = WorkspaceGraph(config)
    try:
        graph.verify_root()
        graph.validate_item(graph.item(workspace.root_item_id), name=opportunity.deal_code, parent_id=config.root_item_id)
        folder = graph.validate_item(graph.item(mapping['id']), name=names[key], parent_id=workspace.root_item_id)
    except WorkspaceError as exc:
        raise WorkspaceAPIError(exc.code) from None
    return workspace, graph, folder


def list_workspace_files(opportunity, actor, key, cursor=None):
    workspace, graph, folder = _ready_folder(opportunity, actor, key)
    next_link = None
    if cursor:
        try:
            payload = signing.loads(cursor, salt='sales-workspace-files', max_age=900)
            if payload['workspace'] != str(workspace.pk) or payload['folder'] != folder['id']:
                raise ValueError()
            next_link = payload['link']
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise WorkspaceAPIError('invalid_cursor', 400) from None
    try:
        page = graph.children(folder['id'], next_link)
        files = []
        for item in page['value']:
            # A nested folder is displayed as a SharePoint link; recursion stays there.
            graph.validate_item(item, parent_id=folder['id'], folder='folder' in item)
            files.append({**file_projection(graph, item), 'is_folder': 'folder' in item})
        next_cursor = signing.dumps({'workspace': str(workspace.pk), 'folder': folder['id'],
                                     'link': page['@odata.nextLink']}, salt='sales-workspace-files') if page.get('@odata.nextLink') else None
    except WorkspaceError as exc:
        raise WorkspaceAPIError(exc.code) from None
    return {'folder_key': key, 'files': files, 'item_count': folder.get('folder', {}).get('childCount'),
            'next_cursor': next_cursor}


def download_limit():
    return configured_limit('SALES_WORKSPACE_MAX_DOWNLOAD_BYTES')


def _ready_file(opportunity, actor, key, file_id, *actions):
    if (not isinstance(file_id, str) or not re.fullmatch(r'[A-Za-z0-9!_.-]{1,255}', file_id)
            or file_id in ('.', '..')):
        raise ValidationError({'file_id': 'Choose a file in this opportunity folder.'})
    workspace, graph, folder = _ready_folder(opportunity, actor, key, *actions)
    try:
        item = graph.validate_item(graph.item(file_id), parent_id=folder['id'], folder=False)
        if item['id'] != file_id or item.get('folder') is not None or item.get('remoteItem') is not None:
            raise WorkspaceError('remote_scope_changed')
    except WorkspaceError as exc:
        raise WorkspaceAPIError(exc.code) from None
    return workspace, graph, folder, item


def workspace_file_details(opportunity, actor, key, file_id):
    if str(file_id).startswith('radai-'):
        from .private_attachments import private_file_details
        return private_file_details(opportunity, actor, key, file_id)
    _, graph, _, item = _ready_file(opportunity, actor, key, file_id)
    result = file_projection(graph, item)
    size = result['size']
    limit = download_limit()
    return {**result, 'is_folder': False, 'folder_key': key,
            'can_download': workspace_allowed(actor, 'export') and isinstance(size, int)
                            and not isinstance(size, bool) and size >= 0 and (limit is None or size <= limit),
            'max_download_bytes': limit}


def list_workspace_file_versions(opportunity, actor, key, file_id, cursor=None):
    if str(file_id).startswith('radai-'):
        from .private_attachments import private_file_versions
        return private_file_versions(opportunity, actor, key, file_id, cursor)
    workspace, graph, folder, item = _ready_file(opportunity, actor, key, file_id)
    next_link = None
    if cursor:
        try:
            payload = signing.loads(cursor, salt='sales-workspace-versions', max_age=900)
            if (payload['workspace'], payload['folder'], payload['file']) != (str(workspace.pk), folder['id'], file_id):
                raise ValueError()
            next_link = payload['link']
        except (signing.BadSignature, ValueError, KeyError, TypeError):
            raise WorkspaceAPIError('invalid_cursor', 400) from None
    try:
        page = graph.versions(file_id, next_link)
        current_version = file_projection(graph, item)['version']
        versions = [version_projection(version, current_version) for version in page['value']]
        next_cursor = signing.dumps({'workspace': str(workspace.pk), 'folder': folder['id'], 'file': file_id,
                                     'link': page['@odata.nextLink']}, salt='sales-workspace-versions') if page.get('@odata.nextLink') else None
    except WorkspaceError as exc:
        raise WorkspaceAPIError(exc.code) from None
    return {'file_id': file_id, 'versions': versions, 'next_cursor': next_cursor}


def download_workspace_file(opportunity, actor, key, file_id):
    if str(file_id).startswith('radai-'):
        from .private_attachments import download_private_file
        return download_private_file(opportunity, actor, key, file_id)
    _, graph, _, item = _ready_file(opportunity, actor, key, file_id, 'export')
    output = None
    try:
        output = graph.download(file_id, item.get('size'), download_limit())
        # A completed buffer is still private. Recheck scope/authority and current
        # file identity before returning it, including deletion/move during I/O.
        _, current_graph, _, current = _ready_file(opportunity, _actor(actor.pk), key, file_id, 'export')
        if (current_graph.config.fingerprint != graph.config.fingerprint
                or any(current.get(field) != item.get(field) for field in ('id', 'name', 'size', 'eTag', 'lastModifiedDateTime'))):
            raise WorkspaceError('document_changed')
        return output, item['name']
    except Exception as exc:
        if output is not None:
            output.close()
        if isinstance(exc, WorkspaceError):
            raise WorkspaceAPIError(exc.code) from None
        raise


def delete_workspace_file(opportunity, actor, key, file_id):
    if str(file_id).startswith('radai-'):
        from .private_attachments import delete_private_file
        return delete_private_file(opportunity, actor, key, file_id)
    raise WorkspaceAPIError('delete_not_supported', 409)


def upload_limit():
    return configured_limit('SALES_WORKSPACE_MAX_UPLOAD_BYTES')


def upload_workspace_file(opportunity, actor, key, uploaded, request_id):
    # Validate before reading the payload or making any remote request.
    require_access(actor, opportunity, 'create', 'update')
    try:
        with prepare_upload(uploaded) as prepared:
            return _upload_workspace_prepared(opportunity, actor, key, uploaded, request_id, prepared)
    except OSError:
        raise WorkspaceAPIError('private_storage_unavailable') from None


def _upload_workspace_prepared(opportunity, actor, key, uploaded, request_id, prepared):
    digest = prepared['sha256']
    workspace, graph, folder = _ready_folder(opportunity, actor, key, 'create', 'update')
    with transaction.atomic():
        OpportunityWorkspace.objects.select_for_update().get(pk=workspace.pk)
        attempt = OpportunityWorkspaceUpload.objects.filter(workspace=workspace, request_id=request_id).first()
        if attempt:
            if (attempt.provider, attempt.actor_id, attempt.folder_key, attempt.name, attempt.size, attempt.sha256) != (
                    'sharepoint', actor.pk, key, uploaded.name, prepared['size'], digest):
                raise WorkspaceAPIError('upload_conflict', 409)
            if attempt.status == 'ready':
                return attempt.result, False
            if attempt.status != 'failed' or attempt.error_code not in {'remote_access_denied', 'remote_busy', 'configuration_changed', 'authority_changed'}:
                raise WorkspaceAPIError('upload_in_progress', 409)
            attempt.status, attempt.error_code = 'uploading', ''
            attempt.save(update_fields=['status', 'error_code', 'updated_at'])
        else:
            attempt = OpportunityWorkspaceUpload.objects.create(
                workspace=workspace, request_id=request_id, actor=actor, folder_key=key,
                name=uploaded.name, size=prepared['size'], sha256=digest,
            )
        _audit(opportunity, actor, 'workspace_upload_started', data={'upload_id': str(attempt.pk), 'folder_key': key})
    # This ledger must have committed before I/O (route guard exempts only this action).
    transfer_finished = False
    try:
        def current_scope():
            current_actor = _actor(actor.pk)
            require_access(current_actor, opportunity, 'create', 'update')
            active = workspace_config()
            if not active or active.fingerprint != graph.config.fingerprint:
                raise WorkspaceError('configuration_changed')
            current_workspace, current_graph, current_folder = _ready_folder(opportunity, current_actor, key, 'create', 'update')
            if (current_workspace.pk != workspace.pk or current_graph.config.fingerprint != graph.config.fingerprint
                    or current_folder['id'] != folder['id']):
                raise WorkspaceError('remote_scope_changed')
        item = graph.upload(folder['id'], uploaded.name, prepared['original'],
                            size=prepared['size'], before_chunk=current_scope)
        transfer_finished = True
        current_scope()
        result = file_projection(graph, item)
        with transaction.atomic():
            attempt.status, attempt.result = 'ready', result
            attempt.save(update_fields=['status', 'result', 'updated_at'])
            _audit(opportunity, actor, 'workspace_file_uploaded',
                   data={'upload_id': str(attempt.pk), 'folder_key': key, 'item_id': item['id']})
        return result, True
    except Exception as exc:
        code = (exc.code if isinstance(exc, WorkspaceError)
                else 'authority_changed' if isinstance(exc, PermissionDenied) else 'recovery_required')
        # Even a timeout can mean Graph committed: retain identity and never overwrite/retry blindly.
        OpportunityWorkspaceUpload.objects.filter(pk=attempt.pk).update(
            status='failed' if not transfer_finished and not getattr(exc, 'upload_started', False) and code in {
                'name_conflict', 'remote_access_denied', 'remote_busy', 'configuration_changed', 'authority_changed'} else 'uncertain',
            error_code=code, updated_at=timezone.now())
        raise WorkspaceAPIError(code, 409 if code == 'name_conflict' else 424) from None
