from django.db import migrations
from django.db.models import Q


def forward(apps, schema_editor):
    Employee = apps.get_model('hr_core', 'EmployeeMaster')
    Onboarding = apps.get_model('onboarding', 'OnboardingRecord')

    updates = []
    unresolved = []
    for record in Onboarding.objects.filter(canonical_employee=None).iterator():
        employee = None
        if record.user_id:
            employee = Employee.objects.filter(user_id=record.user_id).first()
        if not employee and record.employee_email:
            employee = Employee.objects.filter(email__iexact=record.employee_email.strip()).first()
        if not employee and record.employee_id:
            code = record.employee_id.strip()
            employee = Employee.objects.filter(
                Q(employee_number__iexact=code)
                | Q(employee_code__iexact=code)
                | Q(emp_code__iexact=code)
            ).first()
        if not employee:
            unresolved.append(str(record.pk))
            continue
        record.canonical_employee_id = employee.pk
        updates.append(record)

    if updates:
        Onboarding.objects.bulk_update(updates, ['canonical_employee'])
    if unresolved:
        raise RuntimeError(
            'Canonical onboarding history backfill failed for records: '
            + ', '.join(unresolved[:20])
        )


def backward(apps, schema_editor):
    # Retain the oldest canonical link that fits the restored one-to-one state.
    Onboarding = apps.get_model('onboarding', 'OnboardingRecord')
    seen = set()
    clear_ids = []
    for record in Onboarding.objects.exclude(canonical_employee=None).order_by('created_at', 'pk').iterator():
        if record.canonical_employee_id in seen:
            clear_ids.append(record.pk)
        else:
            seen.add(record.canonical_employee_id)
    if clear_ids:
        Onboarding.objects.filter(pk__in=clear_ids).update(canonical_employee=None)


class Migration(migrations.Migration):

    dependencies = [
        ('hr_core', '0009_link_legacy_employee_extensions'),
        ('onboarding', '0014_alter_onboardingrecord_canonical_employee'),
    ]

    operations = [
        migrations.RunPython(forward, backward),
    ]
