"""Shared identity checks for the employee creation wizard and final submission.

These read-only checks do not reserve an address. The create endpoint must check
again immediately before writing and retain the database uniqueness constraints.
Authorization belongs to the calling view.
"""
import re
import unicodedata

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db.models import Q

from apps.hr_core.models import EmployeeMaster


User = get_user_model()
EMAIL_DOMAIN = 'rejlers.ae'


def _clean(value):
    return value.strip() if isinstance(value, str) else ''


def _name_part(value):
    normalized = unicodedata.normalize('NFKD', _clean(value))
    ascii_name = normalized.encode('ascii', 'ignore').decode('ascii').lower()
    return re.sub(r'[^a-z0-9]', '', ascii_name)


def suggest_employee_email(first_name, surname):
    """Suggest a corporate address; unsupported names require a manual address."""
    first, last = _name_part(first_name), _name_part(surname)
    if not first or not last:
        return ''
    # Keep the local part within 64 characters, including its separating dot.
    return f'{first[:31]}.{last[:32]}@{EMAIL_DOMAIN}'


def _email_error(email):
    if not email:
        return 'Enter a corporate email address or provide names to generate one.'
    if email.count('@') != 1 or email.rsplit('@', 1)[1] != EMAIL_DOMAIN:
        return 'Email address must use the @rejlers.ae domain.'
    if len(email.split('@', 1)[0]) > 64:
        return 'The email account name must not exceed 64 characters.'
    try:
        validate_email(email)
    except ValidationError:
        return 'Enter a valid corporate email address.'
    return None


def _matching_identities(first_name, surname, email):
    query = Q(email__iexact=email) if email else Q(pk__in=[])
    if first_name and surname:
        query |= Q(first_name__iexact=first_name, last_name__iexact=surname)

    def matched_fields(row):
        fields = []
        if email and (row['email'] or '').casefold() == email.casefold():
            fields.append('email')
        if first_name and surname and row['first_name'].casefold() == first_name.casefold() and row['last_name'].casefold() == surname.casefold():
            fields.append('name')
        return fields

    # A canonical employee and its linked login are one identity. Preserve email
    # matches from either source, including historical rows without a login.
    matches = {}
    employees = EmployeeMaster.objects.filter(query).order_by('employee_number').values(
        'pk', 'user_id', 'first_name', 'last_name', 'email', 'employee_number',
    )
    for row in employees:
        key = ('user', row['user_id']) if row['user_id'] is not None else ('employee', row['pk'])
        matches[key] = {
            'employee_name': f"{row['first_name']} {row['last_name']}".strip(),
            'email': row['email'] or '',
            'employee_number': row['employee_number'],
            'match_fields': matched_fields(row),
        }
    users = User.objects.filter(query).order_by('pk').values('pk', 'first_name', 'last_name', 'email')
    for row in users:
        key = ('user', row['pk'])
        fields = matched_fields(row)
        if key in matches:
            matches[key]['match_fields'] = sorted(set(matches[key]['match_fields']) | set(fields))
            if row['email'] and row['email'].casefold() != matches[key]['email'].casefold():
                matches[key]['account_email'] = row['email']
        else:
            matches[key] = {
                'employee_name': f"{row['first_name']} {row['last_name']}".strip(),
                'email': row['email'],
                'employee_number': None,
                'match_fields': fields,
            }
    return list(matches.values())


def _available_suggestion(email):
    local = email.split('@', 1)[0]
    # The prefix remains stable when shortening long local parts for a suffix.
    prefix = local[:48]
    existing = {
        str(value).casefold()
        for model in (User, EmployeeMaster)
        for value in model.objects.filter(email__istartswith=prefix).values_list('email', flat=True)
        if value
    }
    for counter in range(1, 10000):
        suffix = str(counter)
        candidate = f'{local[:64 - len(suffix)]}{suffix}@{EMAIL_DOMAIN}'
        if candidate not in existing:
            return candidate
    return None


def build_employee_identity_preview(first_name, surname, email='', *, include_suggestion=True):
    """Return normalized email, validation errors, and matching saved identities.

    An exact email match blocks creation. A name-only match is a warning because
    distinct employees can have the same name. Missing email generates the same
    deterministic suggestion used by final creation validation.
    """
    first_name, surname = _clean(first_name), _clean(surname)
    supplied_email = _clean(email)
    normalized_email = supplied_email.lower() if supplied_email else suggest_employee_email(first_name, surname)
    errors = {}
    for key, value, label in (('first_name', first_name, 'First name'), ('surname', surname, 'Surname')):
        if not value:
            errors[key] = f'{label} is required.'
        elif len(value) > 100:
            errors[key] = f'{label} must not exceed 100 characters.'
    email_error = _email_error(normalized_email)
    if email_error:
        errors['email'] = email_error

    duplicates = _matching_identities(first_name, surname, normalized_email if not email_error else '')
    email_taken = any('email' in row['match_fields'] for row in duplicates)
    if email_taken:
        errors['email'] = 'An employee or user with this email address already exists.'
    result = {
        'email': normalized_email,
        'available': not errors and not email_taken,
        'duplicate_count': len(duplicates),
        'duplicates': duplicates,
        'errors': errors,
    }
    if include_suggestion and email_taken:
        suggestion = _available_suggestion(normalized_email)
        if suggestion:
            result['suggested_email'] = suggestion
    return result


def validate_employee_creation_identity(first_name, surname, email=''):
    """Apply the preview's normalization and duplicate rules at creation time."""
    return build_employee_identity_preview(first_name, surname, email, include_suggestion=False)
