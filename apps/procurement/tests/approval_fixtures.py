"""Explicit business positions and effective grants for approval regression fixtures."""

from datetime import date

from apps.hr_core.models import EmployeeMaster
from apps.rbac.models import Module, Organization, Permission, Role, RoleModule, RolePermission, UserProfile, UserRole


def grant_approval(user, *modules):
    organization, _ = Organization.objects.get_or_create(code='approval-fixtures', defaults={'name': 'Approval fixtures'})
    UserProfile.objects.get_or_create(user=user, defaults={'organization': organization})
    for code in modules or ('procurement_requisitions', 'procurement_orders'):
        module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
        Permission.objects.get_or_create(code=f'{code}.approve', defaults={
            'module': module, 'name': f'Approve {code}', 'action': 'approve',
        })
        role, _ = Role.objects.get_or_create(code=f'test_approve_{code}', defaults={'name': code, 'level': 3})
        RoleModule.objects.get_or_create(role=role, module=module)
        for permission in module.permissions.filter(action='approve', is_active=True):
            RolePermission.objects.get_or_create(role=role, permission=permission)
        UserRole.objects.get_or_create(user_profile=user.rbac_profile, role=role)


def set_position(user, title='Engineer'):
    key = f'APPROVAL-{user.pk}'
    employee, _ = EmployeeMaster.objects.get_or_create(user=user, defaults={
        'employee_number': key, 'employee_code': key, 'emp_code': key[:20],
        'first_name': user.first_name or user.username, 'last_name': user.last_name,
        'email': user.email, 'designation': title, 'join_date': date(2020, 1, 1),
    })
    EmployeeMaster.objects.filter(pk=employee.pk).update(
        designation=title, job_title_uae=title, employment_status='active',
    )
    return employee
