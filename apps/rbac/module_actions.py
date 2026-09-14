"""The six editable actions available on every active module."""
from uuid import NAMESPACE_URL, uuid5

MODULE_ACTIONS = (
    ('read', 'View'), ('create', 'Create'), ('update', 'Edit'),
    ('approve', 'Approve'), ('delete', 'Delete'), ('export', 'Export'),
)


def ensure_module_actions(Module, Permission, *, using='default', module_ids=None):
    """Add missing definitions only; never assign roles or grant user access."""
    modules = Module.objects.using(using).filter(is_active=True)
    if module_ids is not None:
        modules = modules.filter(pk__in=module_ids)
    modules = list(modules)
    existing = set(Permission.objects.using(using).filter(
        module_id__in=[module.pk for module in modules], is_active=True,
    ).values_list('module_id', 'action'))
    used_codes = set(Permission.objects.using(using).values_list('code', flat=True))
    existing_ids = set(Permission.objects.using(using).values_list('pk', flat=True))
    missing = []
    for module in modules:
        for action, label in MODULE_ACTIONS:
            if (module.pk, action) in existing:
                continue
            identifier = uuid5(NAMESPACE_URL, f'radai/module/{module.pk}/action/{action}')
            while identifier in existing_ids:
                identifier = uuid5(NAMESPACE_URL, f'{identifier}/replacement')
            code = f'{module.code}.{action}'
            if len(code) > 100 or code in used_codes:
                # Do not reactivate a deliberately disabled historical permission.
                code = f'module_action.{identifier.hex}.{action}'
            missing.append(Permission(
                id=identifier, module_id=module.pk, code=code,
                name=f'{module.name}: {label}'[:100], action=action,
                description='Standard module action permission.', is_active=True,
            ))
            used_codes.add(code)
    Permission.objects.using(using).bulk_create(missing, ignore_conflicts=True)
    return len(missing)
