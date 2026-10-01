"""Scoped custom tags for logical opportunity folders, without storage I/O."""
import hashlib
import json
import unicodedata

from django.contrib.auth import get_user_model
from django.core import signing
from django.db import transaction
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError

from apps.rbac.data_visibility_mixin import build_visibility_filter
from .models import Deal, OpportunityFolderTag
from .opportunity_workspace import FOLDERS, require_access
from .workflow import _audit


class FolderTagConflict(APIException):
    status_code = 409
    default_detail = 'This folder tag changed. Refresh the folder overview before saving.'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def _state(opportunity, folder_key, actor, row):
    return {'opportunity_id': str(opportunity.pk), 'folder_key': folder_key,
            'actor_id': str(actor.pk), 'revision': row.revision if row else 0,
            'tag_hash': _digest(row.tag if row else '')}


def folder_tag_projection(opportunity, folder_key, actor, row=None):
    return {'folder_key': folder_key, 'tag': row.tag if row else '',
            'expected_token': signing.dumps(_state(opportunity, folder_key, actor, row),
                                           salt='sales-opportunity-folder-tag', compress=True)}


def folder_tag_rows(opportunity, actor):
    rows = {row.folder_key: row for row in OpportunityFolderTag.objects.filter(opportunity=opportunity)}
    return {key: folder_tag_projection(opportunity, key, actor, rows.get(key)) for key, _, _ in FOLDERS}


def _payload(data):
    if not isinstance(data, dict) or set(data) != {'tag', 'expected_token'}:
        raise ValidationError({'detail': 'Send the tag text and its expected_token.'})
    tag = data.get('tag')
    if (not isinstance(tag, str) or len(tag) > 64
            or any(unicodedata.category(character) in {'Cc', 'Cs'} for character in tag)):
        raise ValidationError({'tag': 'Use a single-line text tag of at most 64 characters, or empty text to clear it.'})
    token = data.get('expected_token')
    if not isinstance(token, str) or not token or len(token) > 2048:
        raise ValidationError({'expected_token': 'Refresh the folder overview before editing this tag.'})
    return tag.strip(), token


@transaction.atomic
def save_folder_tag(opportunity_id, actor, folder_key, data):
    if folder_key not in {key for key, _, _ in FOLDERS}:
        raise ValidationError({'folder_key': 'Choose one of the six opportunity folders.'})
    tag, token = _payload(data)
    actor = get_user_model().objects.select_for_update(no_key=True).filter(
        pk=getattr(actor, 'pk', None), is_active=True).first()
    if actor is None:
        raise PermissionDenied('Your current account cannot edit folder tags.')
    visible = Deal.objects.filter(build_visibility_filter(user=actor, module_code='sales', owner_field='owner'))
    opportunity = Deal.objects.select_for_update(no_key=True).filter(
        pk=opportunity_id, pk__in=visible.values('pk')).first()
    if opportunity is None:
        raise NotFound('This opportunity is unavailable.')
    require_access(actor, opportunity, 'update')
    row = OpportunityFolderTag.objects.select_for_update().filter(opportunity=opportunity, folder_key=folder_key).first()
    try:
        expected = signing.loads(token, salt='sales-opportunity-folder-tag', max_age=3600)
        if (not isinstance(expected, dict) or expected.get('actor_id') != str(actor.pk)
                or expected.get('opportunity_id') != str(opportunity.pk) or expected.get('folder_key') != folder_key):
            raise ValueError()
    except (signing.BadSignature, ValueError, TypeError):
        raise FolderTagConflict() from None
    request_hash = _digest({'actor_id': str(actor.pk), 'token': token, 'tag': tag})
    if row and row.last_request_hash == request_hash and row.tag == tag:
        return {**folder_tag_projection(opportunity, folder_key, actor, row), 'replayed': True}
    if expected != _state(opportunity, folder_key, actor, row):
        raise FolderTagConflict()
    before = row.tag if row else ''
    if before == tag:
        return {**folder_tag_projection(opportunity, folder_key, actor, row), 'replayed': False}
    if row is None:
        row = OpportunityFolderTag(opportunity=opportunity, folder_key=folder_key)
    else:
        row.revision += 1
    row.tag, row.last_request_hash, row.updated_by = tag, request_hash, actor
    row.save()
    _audit(opportunity, actor, 'workspace_folder_tag_changed', data={
        'folder_key': folder_key, 'before': before, 'tag': tag, 'revision': row.revision})
    return {**folder_tag_projection(opportunity, folder_key, actor, row), 'replayed': False}
