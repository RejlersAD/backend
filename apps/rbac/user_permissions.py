"""Reviewed individual permission changes, separate from role membership."""
import hashlib
import json

from django.db import transaction
from rest_framework import serializers
from rest_framework.response import Response

from .models import Permission, UserProfile, UserPermissionOverride
from .utils import create_audit_log


class ChangeSerializer(serializers.Serializer):
    permission_id = serializers.UUIDField()
    effect = serializers.ChoiceField(choices=['allow', 'deny', 'inherit'])


class ReviewSerializer(serializers.Serializer):
    snapshot = serializers.CharField(max_length=64)
    reason = serializers.CharField(max_length=1000, allow_blank=False)
    changes = ChangeSerializer(many=True, allow_empty=False)


def permission_state(profile, actor):
    catalogue = list(Permission.objects.filter(is_active=True).select_related('module').order_by('module__name', 'action', 'code'))
    inherited = set(p.id for p in catalogue) if profile.is_super_admin() else set(
        Permission.objects.filter(roles__in=profile.roles.filter(is_active=True), is_active=True).values_list('id', flat=True)
    )
    overrides = dict(profile.permission_overrides.values_list('permission_id', 'allowed'))
    rows = [{
        'id': str(p.id), 'code': p.code, 'name': p.name, 'action': p.action,
        'module': str(p.module_id), 'module_name': p.module.name,
        'inherited': p.id in inherited,
        'effect': ('allow' if overrides[p.id] else 'deny') if p.id in overrides else 'inherit',
        'effective': overrides.get(p.id, p.id in inherited),
    } for p in catalogue]
    snapshot = hashlib.sha256(json.dumps({
        'rows': rows, 'roles': sorted(str(pk) for pk in profile.roles.values_list('pk', flat=True)),
        'overrides': sorted((str(pk), value) for pk, value in overrides.items()),
    }, sort_keys=True).encode()).hexdigest()
    return {'user_id': str(profile.id), 'permissions': rows, 'snapshot': snapshot,
            'locked': profile.user_id == actor.pk,
            'lock_reason': 'Another Super Administrator must edit your permissions.' if profile.user_id == actor.pk else ''}


def user_permission_response(view, request):
    target = view.get_object()
    if request.method == 'GET':
        return Response(permission_state(target, request.user))
    serializer = ReviewSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)
    data = serializer.validated_data
    with transaction.atomic():
        profile = UserProfile.objects.select_for_update().select_related('user').get(pk=target.pk)
        before = permission_state(profile, request.user)
        if before['locked']:
            return Response({'detail': before['lock_reason']}, status=403)
        if data['snapshot'] != before['snapshot']:
            return Response({'detail': 'This user\'s permissions changed. Reload and review again.'}, status=409)
        changes = data['changes']
        ids = [str(change['permission_id']) for change in changes]
        if len(ids) != len(set(ids)) or not set(ids).issubset({p['id'] for p in before['permissions']}):
            return Response({'detail': 'Select unique, active permissions.'}, status=400)
        # A category edit can change hundreds of cells; persist it in bulk.
        inherited_ids = [change['permission_id'] for change in changes if change['effect'] == 'inherit']
        if inherited_ids:
            UserPermissionOverride.objects.filter(user_profile=profile, permission_id__in=inherited_ids).delete()
        overrides = [UserPermissionOverride(user_profile=profile, permission_id=change['permission_id'],
                                            allowed=change['effect'] == 'allow')
                     for change in changes if change['effect'] != 'inherit']
        if overrides:
            UserPermissionOverride.objects.bulk_create(
                overrides, update_conflicts=True, unique_fields=['user_profile', 'permission'],
                update_fields=['allowed', 'updated_at'],
            )
        after = permission_state(profile, request.user)
        if after['snapshot'] != before['snapshot']:
            create_audit_log(
                user=request.user, action='update', resource_type='UserProfile', resource_id=profile.pk,
                resource_repr=profile.user.get_full_name() or profile.user.email,
                changes={'permissions': {'before': before['permissions'], 'after': after['permissions']}},
                metadata={'reason': data['reason'], 'audit_source': 'user_permission_review',
                          'action_label': 'Update individual permissions', 'target_email': profile.user.email},
            )
    return Response(after)
