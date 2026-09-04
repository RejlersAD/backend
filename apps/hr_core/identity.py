"""Canonical employee identity resolution and cross-system consistency checks."""

import uuid

from django.db import transaction
from django.utils import timezone

from .models import EmployeeIdentityAlias, EmployeeMaster


class EmployeeIdentityService:
    """Keep EmployeeMaster as the authority while legacy modules are migrated."""

    @staticmethod
    def _add_alias(employee, source, identifier_type, value, primary=False, metadata=None):
        if value is None or str(value).strip() == '':
            return None
        normalized = EmployeeIdentityAlias.normalize(identifier_type, value)
        cross_source_conflict = EmployeeIdentityAlias.objects.filter(
            identifier_type=identifier_type,
            normalized_value=normalized,
        ).exclude(employee=employee).first()
        if cross_source_conflict:
            return {
                'conflict': True,
                'source': source,
                'identifier_type': identifier_type,
                'value': str(value),
                'employee_id': str(cross_source_conflict.employee_id),
            }
        existing = EmployeeIdentityAlias.objects.filter(
            source=source,
            identifier_type=identifier_type,
            normalized_value=normalized,
        ).first()
        if existing and existing.employee_id != employee.id:
            return {
                'conflict': True,
                'source': source,
                'identifier_type': identifier_type,
                'value': str(value),
                'employee_id': str(existing.employee_id),
            }
        alias, _ = EmployeeIdentityAlias.objects.update_or_create(
            source=source,
            identifier_type=identifier_type,
            normalized_value=normalized,
            defaults={
                'employee': employee,
                'value': str(value).strip(),
                'is_primary': primary,
                'metadata': metadata or {},
                'verified_at': timezone.now(),
            },
        )
        return alias

    @classmethod
    @transaction.atomic
    def register_aliases(cls, employee):
        """Discover identifiers without changing any source-system record."""
        results = []

        def add(*args, **kwargs):
            result = cls._add_alias(employee, *args, **kwargs)
            if result is not None:
                results.append(result)

        add('radai', 'uuid', employee.id, primary=True)
        add('radai', 'user_id', employee.user_id, primary=True)
        add('radai', 'email', employee.email, primary=True)
        add('radai', 'employee_number', employee.employee_number, primary=True)
        add('payroll', 'employee_code', employee.employee_code)
        add('timesheet', 'employee_code', employee.emp_code)
        add('external', 'account_name', employee.account_name)

        try:
            profile = employee.user.rbac_profile
        except Exception:
            profile = None
        if profile:
            add('rbac', 'employee_code', profile.employee_id)
            add('rbac', 'email', employee.user.email)

        try:
            from apps.payroll_engine.models import PayrollEmployee

            payroll_rows = PayrollEmployee.objects.filter(employee=employee)
            if employee.user_id:
                payroll_rows = payroll_rows | PayrollEmployee.objects.filter(user_id=employee.user_id)
            for payroll_employee in payroll_rows.distinct():
                add(
                    'payroll', 'employee_number', payroll_employee.employee_no,
                    metadata={'payroll_employee_id': payroll_employee.pk},
                )
        except Exception:
            pass

        try:
            from apps.onboarding.models import OnboardingRecord

            records = OnboardingRecord.objects.filter(canonical_employee=employee)
            if employee.user_id:
                records = records | OnboardingRecord.objects.filter(user_id=employee.user_id)
            for record in records.distinct().only('id', 'employee_id'):
                add(
                    'onboarding', 'employee_number', record.employee_id,
                    metadata={'onboarding_record_id': record.pk},
                )
        except Exception:
            pass

        return results

    @classmethod
    def resolve(cls, identifier, source=None):
        """Resolve UUID, user ID, email, or any registered legacy code."""
        raw = str(identifier or '').strip()
        if not raw:
            return None

        try:
            canonical_id = uuid.UUID(raw)
        except (TypeError, ValueError, AttributeError):
            canonical_id = None
        employee = EmployeeMaster.objects.filter(id=canonical_id).first() if canonical_id else None
        if employee:
            return employee
        employee = EmployeeMaster.objects.filter(user_id=raw).first() if raw.isdigit() else None
        if employee:
            return employee
        employee = EmployeeMaster.objects.filter(email__iexact=raw).first()
        if employee:
            return employee
        employee = EmployeeMaster.objects.filter(
            employee_number__iexact=raw
        ).first() or EmployeeMaster.objects.filter(employee_code__iexact=raw).first()
        if employee:
            return employee

        aliases = EmployeeIdentityAlias.objects.select_related('employee')
        if source:
            aliases = aliases.filter(source=source)
        normalized_values = {
            EmployeeIdentityAlias.normalize(kind, raw)
            for kind, _label in EmployeeIdentityAlias.TYPE_CHOICES
        }
        employee_ids = list(
            aliases.filter(normalized_value__in=normalized_values)
            .values_list('employee_id', flat=True).distinct()[:2]
        )
        if not employee_ids:
            return None
        if len(employee_ids) > 1:
            return None
        return EmployeeMaster.objects.filter(pk=employee_ids[0]).first()

    @staticmethod
    def consistency_report(employee):
        """Compare identity and organization fields without mutating data."""
        issues = []

        def compare(system, field, canonical, actual):
            left = str(canonical or '').strip()
            right = str(actual or '').strip()
            if left != right:
                issues.append({
                    'system': system,
                    'field': field,
                    'canonical': left,
                    'actual': right,
                })

        user = employee.user
        if user:
            compare('user', 'email', (employee.email or '').casefold(), user.email.casefold())
            compare('user', 'first_name', employee.first_name, user.first_name)
            compare('user', 'last_name', employee.last_name, user.last_name)

        try:
            profile = user.rbac_profile if user else None
        except Exception:
            profile = None
        if user and not profile:
            issues.append({'system': 'rbac', 'field': 'profile', 'canonical': 'present', 'actual': 'missing'})
        elif profile:
            compare('rbac', 'department', employee.department, profile.department)
            compare(
                'rbac', 'job_title',
                employee.designation or employee.job_title_uae or employee.job_title_finland,
                profile.job_title,
            )
            compare('rbac', 'phone', employee.phone_number, profile.phone)
            compare('rbac', 'location', employee.office, profile.location)
            canonical_manager = str(employee.manager.user_id) if employee.manager_id else ''
            profile_manager = str(profile.manager.user_id) if profile.manager_id else ''
            compare('rbac', 'manager_user_id', canonical_manager, profile_manager)

        try:
            from apps.payroll_engine.models import PayrollEmployee

            payroll_rows = PayrollEmployee.objects.filter(employee=employee)
            if user:
                payroll_rows = payroll_rows | PayrollEmployee.objects.filter(user_id=user.id)
            if not payroll_rows.exists():
                issues.append({'system': 'payroll', 'field': 'canonical_link', 'canonical': str(employee.id), 'actual': 'missing'})
            for row in payroll_rows:
                compare('payroll', 'full_name', employee.get_full_name(), row.full_name)
                compare('payroll', 'department', employee.department, row.department)
                compare('payroll', 'designation', employee.designation, row.designation)
        except Exception:
            pass

        return {
            'employee_id': str(employee.id),
            'employee_number': employee.employee_number,
            'display_name': employee.get_display_name(),
            'healthy': not issues,
            'issue_count': len(issues),
            'issues': issues,
        }

    @classmethod
    @transaction.atomic
    def repair_shared_fields(cls, employee):
        """Push canonical non-financial fields to active compatibility records."""
        from .services import EmployeeService

        EmployeeService.sync_to_rbac_profile(employee)
        try:
            from apps.payroll_engine.models import PayrollEmployee

            PayrollEmployee.objects.filter(employee=employee).update(
                full_name=employee.get_full_name(),
                department=employee.department,
                designation=employee.designation,
                joining_date=employee.join_date,
            )
        except Exception:
            pass
        cls.register_aliases(employee)
        return cls.consistency_report(employee)
