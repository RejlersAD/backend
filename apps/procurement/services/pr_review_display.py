"""Canonical display references; never route assignments or approval evidence."""

import re

from django.db.models import Q

from .employee_display import employee_display_names


def unknown_approver_name(value):
    name = ' '.join(str(value or '').split())
    return not name or bool(re.fullmatch(
        r'unknown(?:\s+.*)?|not\s+(?:detected|recorded|known|available)|unidentified|unreadable|n/?a|none|null|[-?\d\s]+',
        name, re.IGNORECASE,
    ))


def is_richa_name(value):
    return ' '.join(str(value or '').casefold().split()) in {'richa', 'richa hannah thomas', 'richa thomas'}


def default_level_zero_approver():
    from apps.rbac.models import UserProfile
    from apps.hr_core.models import EmployeeMaster

    # The historical finance route identifies richa@rejlers.ae. Always resolve
    # an active directory record and its current canonical name; no synthetic ID.
    profiles = list(UserProfile.objects.filter(
        Q(user__email__iexact='richa@rejlers.ae')
        | Q(user__first_name__istartswith='Richa')
        | Q(user__employee_master__first_name__istartswith='Richa')
        | Q(user__employee_master__preferred_given_name__istartswith='Richa'),
        status='active', is_deleted=False, user__is_active=True,
    ).select_related('user').distinct())
    names = employee_display_names(profile.user for profile in profiles)
    employees = {employee.user_id: employee for employee in EmployeeMaster.objects.filter(
        user_id__in=[profile.user_id for profile in profiles],
    )}
    candidates = []
    for profile in profiles:
        employee = employees.get(profile.user_id)
        if employee and employee.employment_status not in {'active', 'probation', 'notice_period'}:
            continue
        name = names.get(str(profile.user_id), '')
        if ' '.join(name.casefold().split()) not in {'richa hannah thomas', 'richa thomas'}:
            continue
        candidates.append((profile, employee, name))
    preferred = [entry for entry in candidates if entry[0].user.email.casefold() == 'richa@rejlers.ae']
    selected = preferred if preferred else candidates
    if len(selected) != 1:
        return None
    profile, employee, name = selected[0]
    return {
        'id': str(profile.user_id), 'full_name': name, 'email': profile.user.email,
        'job_title': (employee.designation or employee.job_title_uae or employee.job_title_finland or profile.job_title)
        if employee else profile.job_title,
        'level': 0, 'status': 'not_recorded', 'source': 'canonical_employee_reference',
    }
