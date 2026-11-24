"""Consolidate RADAI to its two organizations: Rejlers Abu Dhabi (RAD) and Rejlers India (RIN).

Data-only and safe to run again. Workflow logic and stored codes are unchanged.

1. Ensure both organizations exist, are active and carry the agreed names.
2. Move every record linked to any other organization ('Default Organization', ...)
   to RAD, or to RIN when that organization's name/code refers to India.
   A row that cannot move (a one-per-organization record the target already has)
   stays where it is.
3. Delete each other organization once nothing references it; otherwise mark it
   inactive so it disappears from pickers.
4. Normalize stored branch values to the RAD / RIN codes, and organization-like
   business-unit text to the two names.

Reverse is a no-op: merged records cannot be split back automatically.
"""
import re

from django.db import IntegrityError, migrations, transaction

RAD, RIN = 'RAD', 'RIN'
NAMES = {RAD: 'Rejlers Abu Dhabi', RIN: 'Rejlers India (RIN)'}
ORG_CODES = {RAD: 'REJ_UAE', RIN: 'REJ_RIN'}
COUNTRIES = {RAD: 'United Arab Emirates', RIN: 'India'}
_INDIA = re.compile(r'\b(india|rin)\b|\bin\s*$', re.IGNORECASE)

# Every foreign key to rbac.Organization (app, model, field). Keep in step with the models.
ORGANIZATION_LINKS = [
    ('rbac', 'UserProfile', 'organization'),
    ('rbac', 'AIOutcomeEvidence', 'organization'),
    ('rbac', 'AIWorkflowRun', 'organization'),
    ('rbac', 'AIWorkforceSnapshot', 'organization'),
    ('core', 'AICustomSkill', 'organization'),
    ('enterprise', 'OrganizationAdmin', 'organization'),
    ('enterprise', 'Team', 'organization'),
    ('enterprise', 'OrganizationEdition', 'organization'),
    ('enterprise', 'OrganizationPolicy', 'organization'),
]
BRANCH_FIELDS = [
    ('hr_core', 'EmployeeMaster'),
    ('payroll', 'EmployeeLeaveRecord'),
    ('onboarding', 'OnboardingRecord'),
    ('onboarding', 'OffboardingRecord'),
]


def entity_for(value):
    text = str(value or '').strip()
    if not text:
        return None
    if text.upper() in NAMES:
        return text.upper()
    return RIN if _INDIA.search(text.replace('_', ' ')) else RAD


def _model(apps, app_label, name):
    try:
        return apps.get_model(app_label, name)
    except LookupError:  # app not installed in this deployment
        return None


def _ensure(Organization, entity):
    org = (Organization.objects.filter(code=ORG_CODES[entity]).first()
           or Organization.objects.filter(name__iexact=NAMES[entity]).first())
    if org is None:
        return Organization.objects.create(code=ORG_CODES[entity], name=NAMES[entity], is_active=True,
                                           country=COUNTRIES[entity])
    changed = []
    if org.name != NAMES[entity] and not Organization.objects.filter(name=NAMES[entity]).exclude(pk=org.pk).exists():
        org.name = NAMES[entity]
        changed.append('name')
    if not org.is_active:
        org.is_active = True
        changed.append('is_active')
    if not org.country:
        org.country = COUNTRIES[entity]
        changed.append('country')
    if changed:
        org.save(update_fields=changed)
    return org


def _still_referenced(org):
    """True if anything at all still points at ``org`` (any relation, listed or not).

    Deleting would cascade, so an organization is only removed when every
    reverse relation Django knows about is empty.
    """
    for rel in org._meta.related_objects:
        accessor = rel.get_accessor_name()
        if rel.one_to_one:
            if rel.related_model._default_manager.filter(**{rel.field.name: org}).exists():
                return True
        elif getattr(org, accessor).exists():
            return True
    return False


def consolidate(apps, schema_editor):
    Organization = apps.get_model('rbac', 'Organization')
    targets = {RAD: _ensure(Organization, RAD), RIN: _ensure(Organization, RIN)}
    keep = {org.pk for org in targets.values()}
    links = [(M, field) for M, field in ((_model(apps, a, m), f) for a, m, f in ORGANIZATION_LINKS) if M is not None]

    for org in Organization.objects.exclude(pk__in=keep):
        target = targets[RIN if RIN in {entity_for(org.name), entity_for(org.code)} else RAD]
        stuck = 0
        for M, field in links:
            for row in M.objects.filter(**{field: org}):
                try:
                    with transaction.atomic():
                        setattr(row, f'{field}_id', target.pk)
                        row.save(update_fields=[field])
                except IntegrityError:
                    stuck += 1  # the target already holds its one-per-organization record
        if stuck == 0 and not _still_referenced(org):
            org.delete()
        else:
            org.is_active = False
            org.save(update_fields=['is_active'])

    for app_label, name in BRANCH_FIELDS:
        M = _model(apps, app_label, name)
        if M is None:
            continue
        values = M.objects.exclude(branch__in=['', RAD, RIN]).exclude(branch__isnull=True).values_list('branch', flat=True).distinct()
        for value in list(values):
            M.objects.filter(branch=value).update(branch=entity_for(value))

    Employee = _model(apps, 'hr_core', 'EmployeeMaster')
    if Employee is not None:
        for value in list(Employee.objects.exclude(business_unit='').values_list('business_unit', flat=True).distinct()):
            text = str(value or '').casefold()
            if 'rejlers' in text or 'abu dhabi' in text or 'india' in text:
                Employee.objects.filter(business_unit=value).update(business_unit=NAMES[entity_for(value)])


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0070_executive_all_tab_permissions'),
        ('core', '0024_ai_custom_skills'),
        ('enterprise', '0002_organizationpolicy_allow_web_search'),
        ('hr_core', '0023_alter_employeemaster_branch'),
        ('payroll', '0026_alter_employeeleaverecord_branch'),
        ('onboarding', '0016_alter_offboardingrecord_branch_and_more'),
    ]

    operations = [migrations.RunPython(consolidate, migrations.RunPython.noop)]
