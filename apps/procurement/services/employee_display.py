"""Name-only presentation helpers for procurement approvers."""

import re


GENERIC_USERNAMES = {'admin', 'administrator', 'user'}


def _clean(value):
    return str(value or '').strip()


def _humanize_identifier(value):
    local_part = _clean(value).split('@', 1)[0]
    words = re.sub(r'[._-]+', ' ', local_part).strip()
    return words.title()


def employee_display_names(users):
    """Return employee display names keyed by user ID, without exposing email."""
    users = list(users)
    user_ids = [user.pk for user in users if getattr(user, 'pk', None) is not None]
    names = {}

    if user_ids:
        # EmployeeMaster is the canonical source. Some older employee rows were
        # imported without names, so completed onboarding records are the safe
        # compatibility source until those master rows are repaired.
        try:
            from apps.hr_core.models import EmployeeMaster

            for employee in EmployeeMaster.objects.filter(user_id__in=user_ids):
                name = _clean(employee.get_display_name())
                if name:
                    names[str(employee.user_id)] = name
        except Exception:
            pass

        missing_ids = [user_id for user_id in user_ids if str(user_id) not in names]
        if missing_ids:
            try:
                from apps.onboarding.models import OnboardingRecord

                records = OnboardingRecord.objects.filter(
                    user_id__in=missing_ids,
                ).exclude(employee_name='').order_by('user_id', '-updated_at')
                for record in records:
                    names.setdefault(str(record.user_id), _clean(record.employee_name))
            except Exception:
                pass

    for user in users:
        key = str(getattr(user, 'pk', ''))
        if names.get(key):
            continue
        full_name = _clean(user.get_full_name())
        if full_name:
            names[key] = full_name
            continue
        username = _clean(user.get_username())
        if username and '@' not in username and username.casefold() not in GENERIC_USERNAMES:
            names[key] = username
            continue
        names[key] = _humanize_identifier(getattr(user, 'email', '') or username) or 'Assigned Employee'

    return names


def employee_display_name(user):
    if user is None:
        return 'Assigned Employee'
    return employee_display_names([user]).get(str(getattr(user, 'pk', '')), 'Assigned Employee')


def name_only(value):
    """Make legacy email-valued name fields suitable for display."""
    cleaned = _clean(value)
    return _humanize_identifier(cleaned) if '@' in cleaned else cleaned
