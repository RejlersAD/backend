"""Minimal HR Master identities for selecting an imported PO's approver."""

import re

from django.db.models import Q
from django.db.models.functions import Lower
from rest_framework import serializers

from apps.hr_core.models import EmployeeMaster


PLACEHOLDER_POSITION = re.compile(r'designation[\s_-]*\d+', re.IGNORECASE)


def _position(employee):
    for field in ('designation', 'job_title_uae', 'job_title_finland'):
        value = str(employee[field] or '').strip()
        if value and not PLACEHOLDER_POSITION.fullmatch(value):
            return value
    return ''


class ApprovalEmployeeSearch(serializers.Serializer):
    search = serializers.CharField(required=False, allow_blank=True, max_length=200, default='')
    page = serializers.IntegerField(required=False, min_value=1, max_value=10000, default=1)
    page_size = serializers.IntegerField(required=False, min_value=1, max_value=50, default=20)


def approval_employee_directory(params):
    """Return only canonical identity/title fields, including staff without logins."""
    query = ApprovalEmployeeSearch(data=params)
    query.is_valid(raise_exception=True)
    search, page, page_size = (query.validated_data[key] for key in ('search', 'page', 'page_size'))
    employees = EmployeeMaster.objects.filter(
        employment_status__in=('active', 'probation', 'notice_period'),
        protected_identity=False,
        is_test_person=False,
    )
    for term in search.split():
        employees = employees.filter(
            Q(first_name__icontains=term) | Q(last_name__icontains=term)
            | Q(preferred_given_name__icontains=term) | Q(employee_number__icontains=term)
        )
    employees = employees.exclude(first_name='', last_name='').order_by(
        Lower('first_name'), Lower('last_name'), 'employee_number', 'id',
    )
    count = employees.count()
    offset = (page - 1) * page_size
    # Never load broad HR serializers or financial/contact fields for this lookup.
    records = employees.values(
        'id', 'first_name', 'last_name', 'designation', 'job_title_uae',
        'job_title_finland', 'employee_number', 'department', 'user_id',
    )[offset:offset + page_size]
    results = [{
        'id': str(employee['id']),
        'name': ' '.join(part.strip() for part in (employee['first_name'], employee['last_name']) if part.strip()),
        'position': _position(employee),
        'employee_number': employee['employee_number'],
        'department': employee['department'],
        'linked_user_id': str(employee['user_id']) if employee['user_id'] is not None else None,
    } for employee in records]
    return {
        'count': count, 'page': page, 'page_size': page_size,
        'has_more': offset + len(results) < count, 'results': results,
    }
