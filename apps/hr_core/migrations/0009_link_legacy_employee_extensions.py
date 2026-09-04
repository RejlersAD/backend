import json

from django.db import migrations
from django.db.models import Q
from django.utils import timezone


LEGACY_TABLE = 'user_profiles'
RETIRED_TABLE = 'user_profiles_retired'


def _unique_code(Employee, desired, field, fallback, max_length=50):
    value = str(desired or fallback).strip()[:max_length]
    if not value:
        value = fallback
    candidate = value
    suffix = 1
    while Employee.objects.filter(**{field: candidate}).exists():
        token = f'-{suffix}'
        candidate = f'{value[:max_length-len(token)]}{token}'
        suffix += 1
    return candidate


def _find(Employee, user_id=None, email=None, codes=None):
    def compatible(candidate):
        if not candidate:
            return None
        # A free-form legacy code must never merge two authenticated users.
        if user_id and candidate.user_id not in (None, user_id):
            return None
        if user_id and candidate.user_id is None:
            candidate.user_id = user_id
            candidate.save(update_fields=['user'])
        return candidate

    if user_id:
        employee = Employee.objects.filter(user_id=user_id).first()
        if employee:
            return employee
    if email:
        employee = compatible(Employee.objects.filter(email__iexact=str(email).strip()).first())
        if employee:
            return employee
    for code in filter(None, [str(v).strip() for v in (codes or []) if v]):
        employee = compatible(Employee.objects.filter(
            Q(employee_number__iexact=code) | Q(employee_code__iexact=code) | Q(emp_code__iexact=code)
        ).first())
        if employee:
            return employee
    return None


def _create_employee(Employee, User, *, user_id=None, email=None, code=None, full_name='', join_date=None, active=True, **extra):
    user = User.objects.filter(pk=user_id).first() if user_id else None
    if user:
        existing = Employee.objects.filter(user_id=user.pk).first()
        if existing:
            return existing
        email = email or user.email
        first_name = user.first_name or ''
        last_name = user.last_name or ''
    else:
        parts = str(full_name or '').strip().split(None, 1)
        first_name = parts[0] if parts else ''
        last_name = parts[1] if len(parts) > 1 else ''
    base = str(code or f'LEGACY-{user_id or Employee.objects.count()+1}').strip()
    employee_number = _unique_code(Employee, base, 'employee_number', 'LEGACY')
    employee_code = _unique_code(Employee, base, 'employee_code', employee_number)
    emp_code = _unique_code(Employee, base, 'emp_code', employee_code, max_length=20)
    safe_extra = {key: value for key, value in extra.items() if value not in (None, '')}
    return Employee.objects.create(
        user=user, email=(str(email).strip().lower() if email else None),
        first_name=first_name, last_name=last_name,
        employee_number=employee_number, employee_code=employee_code, emp_code=emp_code,
        join_date=join_date or (user.date_joined.date() if user else timezone.localdate()),
        employment_status='active' if active else 'exited', **safe_extra,
    )


def _raw_rows(connection, table):
    if table not in connection.introspection.table_names():
        return []
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT * FROM "{table}"')
        columns = [column[0] for column in cursor.description]
        return [dict(zip(columns, row)) for row in cursor.fetchall()]


def forward(apps, schema_editor):
    Employee = apps.get_model('hr_core', 'EmployeeMaster')
    Archive = apps.get_model('hr_core', 'LegacyEmployeeArchive')
    User = apps.get_model('users', 'User')
    RBACProfile = apps.get_model('rbac', 'UserProfile')
    SalaryInfo = apps.get_model('finance', 'EmployeeSalaryInfo')
    PayrollEmployee = apps.get_model('payroll_engine', 'PayrollEmployee')
    Onboarding = apps.get_model('onboarding', 'OnboardingRecord')
    Offboarding = apps.get_model('onboarding', 'OffboardingRecord')
    Probation = apps.get_model('onboarding', 'ProbationPerformanceReport')
    connection = schema_editor.connection

    legacy_rows = _raw_rows(connection, LEGACY_TABLE)
    for row in legacy_rows:
        user = User.objects.filter(pk=row.get('user_id')).first()
        employee = _find(
            Employee, user_id=row.get('user_id'), email=getattr(user, 'email', None),
            codes=[row.get('employee_number'), row.get('employment_id')],
        )
        if not employee:
            employee = _create_employee(
                Employee, User, user_id=row.get('user_id'), email=getattr(user, 'email', None),
                code=row.get('employee_number') or row.get('employment_id'),
                join_date=user.date_joined.date() if user else None,
                preferred_given_name=row.get('preferred_given_name') or '', initials=row.get('initials') or '',
                date_of_birth=row.get('date_of_birth'), business_unit=row.get('business_unit') or '',
                division=row.get('division') or '', business_area=row.get('business_area') or '',
                office=row.get('office') or '', job_title_uae=row.get('job_title_uae') or '',
                job_title_finland=row.get('job_title_finland') or '', country=row.get('country') or '',
                city=row.get('city') or '', address=row.get('address') or '', postal_code=row.get('postal_code') or '',
                protected_identity=bool(row.get('protected_identity')), is_test_person=bool(row.get('is_test_person')),
                not_signed=bool(row.get('not_signed')),
            )
        payload = json.loads(json.dumps(row, default=str))
        Archive.objects.update_or_create(
            source_table=LEGACY_TABLE, source_pk=str(row.get('id')),
            defaults={'canonical_employee_id': employee.pk, 'payload': payload},
        )

    employees_by_user = {}
    employees_by_email = {}
    employees_by_code = {}
    users_by_id = User.objects.in_bulk()
    pending_employees = []

    def index_employee(employee):
        if employee.user_id:
            employees_by_user[employee.user_id] = employee
        if employee.email:
            employees_by_email[str(employee.email).strip().lower()] = employee
        for value in (employee.employee_number, employee.employee_code, employee.emp_code):
            if value:
                employees_by_code[str(value).strip().lower()] = employee
        return employee

    for canonical in Employee.objects.all().iterator():
        index_employee(canonical)

    def resolve(*, user_id=None, email=None, codes=None):
        employee = employees_by_user.get(user_id) if user_id else None
        if not email and user_id and user_id in users_by_id:
            email = users_by_id[user_id].email
        if not employee and email:
            employee = employees_by_email.get(str(email).strip().lower())
        if not employee:
            for code in filter(None, [str(value).strip().lower() for value in (codes or []) if value]):
                employee = employees_by_code.get(code)
                if employee:
                    break
        if employee and user_id and employee.user_id not in (None, user_id):
            return None
        if employee and user_id and employee.user_id is None:
            employee.user_id = user_id
            employee.save(update_fields=['user'])
            employees_by_user[user_id] = employee
        return employee

    def create_employee(**kwargs):
        user_id = kwargs.pop('user_id', None)
        email = kwargs.pop('email', None)
        code = kwargs.pop('code', None)
        full_name = kwargs.pop('full_name', '')
        join_date = kwargs.pop('join_date', None)
        active = kwargs.pop('active', True)
        user = users_by_id.get(user_id) if user_id else None
        if user:
            email = email or user.email
            first_name, last_name = user.first_name or '', user.last_name or ''
        else:
            parts = str(full_name or '').strip().split(None, 1)
            first_name = parts[0] if parts else ''
            last_name = parts[1] if len(parts) > 1 else ''

        base = str(code or f'LEGACY-{user_id or len(employees_by_code)+1}').strip()

        def local_code(value, field, max_length):
            value = str(value or 'LEGACY').strip()[:max_length] or 'LEGACY'
            candidate, suffix = value, 1
            while candidate.lower() in employees_by_code:
                token = f'-{suffix}'
                candidate = f'{value[:max_length-len(token)]}{token}'
                suffix += 1
            return candidate

        employee_number = local_code(base, 'employee_number', 50)
        employee_code = local_code(base, 'employee_code', 50)
        emp_code = local_code(base, 'emp_code', 20)
        normalized_email = str(email).strip().lower() if email else None
        if normalized_email in employees_by_email:
            normalized_email = None
        safe_extra = {key: value for key, value in kwargs.items() if value not in (None, '')}
        employee = Employee(
            user=user, email=normalized_email, first_name=first_name, last_name=last_name,
            employee_number=employee_number, employee_code=employee_code, emp_code=emp_code,
            join_date=join_date or (user.date_joined.date() if user else timezone.localdate()),
            employment_status='active' if active else 'exited', **safe_extra,
        )
        pending_employees.append(employee)
        return index_employee(employee)

    rbac_updates = []
    for profile in RBACProfile.objects.all().iterator():
        # Access profiles are authoritative for authorization, not employee identity.
        # Link only the same authenticated account; duplicate/free-form employee_id
        # values must never merge two user accounts into one canonical employee.
        employee = resolve(user_id=profile.user_id)
        if employee:
            profile.canonical_employee_id = employee.pk
            rbac_updates.append(profile)

    salary_updates = []
    for salary in SalaryInfo.objects.all().iterator():
        employee = resolve(user_id=salary.user_id, codes=[salary.employee_id])
        if not employee:
            employee = create_employee(
                user_id=salary.user_id, code=salary.employee_id,
                join_date=salary.join_date, active=salary.is_active,
                department=salary.department or '', designation=salary.designation or '',
                bank_name=salary.bank_name or '', bank_account_number=salary.account_number or '',
                iban=salary.iban or '', swift_code=salary.swift_code or '', tax_id=salary.tax_id or '',
                current_base_salary=salary.basic_salary, currency=salary.currency or 'AED',
                exit_date=salary.termination_date,
            )
        salary.canonical_employee_id = employee.pk
        salary_updates.append(salary)

    payroll_updates = []
    for payroll in PayrollEmployee.objects.all().iterator():
        employee = resolve(user_id=payroll.user_id, codes=[payroll.employee_no])
        if not employee:
            employee = create_employee(
                user_id=payroll.user_id, code=payroll.employee_no,
                full_name=payroll.full_name, join_date=payroll.joining_date or payroll.effective_from,
                active=payroll.is_active, department=payroll.department or '',
                designation=payroll.designation or '', iban=payroll.iban or '', bank_name=payroll.bank_name or '',
                current_base_salary=payroll.basic, exit_date=payroll.leaving_date,
            )
        payroll.employee_id = employee.pk
        payroll_updates.append(payroll)

    onboarding_updates = []
    for record in Onboarding.objects.all().iterator():
        employee = resolve(user_id=record.user_id, email=record.employee_email, codes=[record.employee_id])
        if not employee:
            employee = create_employee(
                user_id=record.user_id, email=record.employee_email,
                code=record.employee_id or f'ONB-{record.pk}', full_name=record.employee_name,
                join_date=record.joining_date, department=record.department or '',
                designation=record.position or '', branch=record.branch or '',
            )
        record.canonical_employee_id = employee.pk
        onboarding_updates.append(record)

    offboarding_updates = []
    for record in Offboarding.objects.all().iterator():
        employee = resolve(user_id=record.user_id, email=record.employee_email, codes=[record.employee_id])
        if not employee:
            employee = create_employee(
                user_id=record.user_id, email=record.employee_email,
                code=record.employee_id or f'OFF-{record.pk}', full_name=record.employee_name,
                join_date=record.last_working_day, active=False, exit_date=record.last_working_day,
                department=record.department or '', designation=record.position or '', branch=record.branch or '',
            )
        record.canonical_employee_id = employee.pk
        offboarding_updates.append(record)

    probation_updates = []
    for report in Probation.objects.all().iterator():
        employee = resolve(user_id=report.employee_id)
        if employee:
            report.canonical_employee_id = employee.pk
            probation_updates.append(report)
    Employee.objects.bulk_create(pending_employees)
    RBACProfile.objects.bulk_update(rbac_updates, ['canonical_employee'])
    SalaryInfo.objects.bulk_update(salary_updates, ['canonical_employee'])
    PayrollEmployee.objects.bulk_update(payroll_updates, ['employee'])
    Onboarding.objects.bulk_update(onboarding_updates, ['canonical_employee'])
    Offboarding.objects.bulk_update(offboarding_updates, ['canonical_employee'])
    Probation.objects.bulk_update(probation_updates, ['canonical_employee'])

    # Canonical hierarchy wins; fill only currently empty manager relationships.
    for profile in RBACProfile.objects.exclude(canonical_employee=None).exclude(manager=None).iterator():
        if profile.manager.canonical_employee_id:
            Employee.objects.filter(pk=profile.canonical_employee_id, manager=None).update(manager_id=profile.manager.canonical_employee_id)

    required = {
        'finance.EmployeeSalaryInfo': SalaryInfo.objects.filter(canonical_employee=None).count(),
        'payroll_engine.PayrollEmployee': PayrollEmployee.objects.filter(employee=None).count(),
        'onboarding.OnboardingRecord': Onboarding.objects.filter(canonical_employee=None).count(),
        'onboarding.OffboardingRecord': Offboarding.objects.filter(canonical_employee=None).count(),
        'onboarding.ProbationPerformanceReport': Probation.objects.filter(canonical_employee=None).count(),
    }
    failures = {name: count for name, count in required.items() if count}
    if failures:
        raise RuntimeError(f'Legacy employee retirement blocked by unmapped records: {failures}')
    if len(legacy_rows) != Archive.objects.filter(source_table=LEGACY_TABLE).count():
        raise RuntimeError('Legacy employee retirement blocked: archive count does not match source count.')

    tables = connection.introspection.table_names()
    if LEGACY_TABLE in tables:
        if RETIRED_TABLE in tables:
            raise RuntimeError(f'Cannot retire {LEGACY_TABLE}: {RETIRED_TABLE} already exists.')
        schema_editor.execute(f'ALTER TABLE "{LEGACY_TABLE}" RENAME TO "{RETIRED_TABLE}"')


def backward(apps, schema_editor):
    connection = schema_editor.connection
    tables = connection.introspection.table_names()
    if RETIRED_TABLE in tables and LEGACY_TABLE not in tables:
        schema_editor.execute(f'ALTER TABLE "{RETIRED_TABLE}" RENAME TO "{LEGACY_TABLE}"')
    apps.get_model('rbac', 'UserProfile').objects.update(canonical_employee=None)
    apps.get_model('finance', 'EmployeeSalaryInfo').objects.update(canonical_employee=None)
    apps.get_model('payroll_engine', 'PayrollEmployee').objects.update(employee=None)
    apps.get_model('onboarding', 'OnboardingRecord').objects.update(canonical_employee=None)
    apps.get_model('onboarding', 'OffboardingRecord').objects.update(canonical_employee=None)
    apps.get_model('onboarding', 'ProbationPerformanceReport').objects.update(canonical_employee=None)


class Migration(migrations.Migration):
    atomic = True

    dependencies = [
        ('hr_core', '0008_legacyemployeearchive_and_more'),
        ('users', '0006_delete_userprofile'),
        ('rbac', '0049_userprofile_employee'),
        ('finance', '0010_remove_workflownotificationlog_workflow_and_more'),
        ('payroll_engine', '0018_alter_payrollemployee_employee'),
        ('onboarding', '0013_offboardingrecord_employee_onboardingrecord_employee_and_more'),
    ]

    operations = [migrations.RunPython(forward, backward)]
