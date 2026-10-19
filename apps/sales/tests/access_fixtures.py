"""Explicit access policies for sales workflow tests (no mocked authorization)."""
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole
from apps.rbac.module_actions import ensure_module_actions


def grant_sales_actions(user, *codes):
    organization, _ = Organization.objects.get_or_create(code='sales-tests', defaults={'name': 'Sales tests'})
    profile, _ = UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
    if 'sales_opportunities' in codes and not profile.department:
        profile.department = 'Sales'
        profile.save(update_fields=['department'])
    role, _ = Role.objects.get_or_create(code='sales-test-operator', defaults={'name': 'Sales test operator', 'level': 4})
    UserRole.objects.get_or_create(user_profile=profile, role=role)
    for code in codes:
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        ensure_module_actions(Module, Permission, module_ids=[module.pk])
        RoleModule.objects.get_or_create(role=role, module=module)
        for permission in module.permissions.filter(is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)


def complete_proposal_prerequisites(deal, actor):
    """Seed completed gates for isolated tests of later proposal behavior.

    Full lifecycle tests must use the real qualification/read/CEO HTTP actions.
    """
    from apps.notifications.models import Notification
    from apps.sales.models import OpportunityAuditEvent

    Notification.objects.create(
        recipient=actor, title='Synthetic qualification acknowledgement',
        message='Internal review acknowledged.', status='READ', is_read=True,
        metadata={'module': 'sales', 'event_type': 'qualification_submitted',
                  'opportunity_id': str(deal.pk)},
    )
    OpportunityAuditEvent.objects.create(
        opportunity=deal, actor=actor, event_type='ceo_gate_decision',
        from_stage='proposal', to_stage='proposal', data={'decision': 'approved'},
    )


def deny_sales_action(user, module, action):
    """Explicit denial wins over the Sales department's inherited grants."""
    from apps.rbac.models import UserPermissionOverride
    for permission in Permission.objects.filter(module__code=module, action=action, is_active=True):
        UserPermissionOverride.objects.update_or_create(
            user_profile=user.rbac_profile, permission=permission, defaults={'allowed': False},
        )
