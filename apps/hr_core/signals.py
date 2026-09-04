"""Automated compatibility sync around the canonical EmployeeMaster record."""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.db import transaction

from apps.hr_core.models import EmployeeMaster

User = get_user_model()


@receiver(post_save, sender=EmployeeMaster, dispatch_uid='hr_master_sync_consumers')
def sync_employee_master_consumers(sender, instance, raw=False, **kwargs):
    if raw:
        return
    from apps.hr_core.identity import EmployeeIdentityService
    from apps.hr_core.services import EmployeeService

    EmployeeService.sync_to_rbac_profile(instance)
    _link_domain_extensions(instance)
    transaction.on_commit(lambda: EmployeeIdentityService.register_aliases(instance))


@receiver(post_save, sender=User, dispatch_uid='hr_user_sync_master_identity')
def sync_user_identity_to_employee_master(sender, instance, raw=False, **kwargs):
    if raw:
        return
    updated = EmployeeMaster.objects.filter(user=instance).update(
        email=instance.email,
        first_name=instance.first_name or '',
        last_name=instance.last_name or '',
    )
    if updated:
        from apps.hr_core.identity import EmployeeIdentityService

        def refresh_aliases():
            employee = EmployeeMaster.objects.filter(user_id=instance.pk).first()
            if employee:
                EmployeeIdentityService.register_aliases(employee)

        transaction.on_commit(refresh_aliases)


def connect_rbac_profile_signal():
    """Connect lazily to avoid importing RBAC models during app initialization."""
    from apps.rbac.models import UserProfile

    def sync_rbac_profile_to_employee_master(sender, instance, raw=False, **kwargs):
        if raw or instance.is_deleted:
            return
        from apps.hr_core.services import EmployeeService

        if EmployeeMaster.objects.filter(user=instance.user).exists():
            EmployeeService.sync_from_rbac_profile(instance)

    # The handler is local to keep app imports lazy, so connect it strongly.
    post_save.connect(
        sync_rbac_profile_to_employee_master,
        sender=UserProfile,
        dispatch_uid='hr_rbac_sync_master',
        weak=False,
    )


connect_rbac_profile_signal()


def _link_domain_extensions(employee):
    """Attach compatibility/domain rows without copying identity back into master."""
    from django.db.models import Q
    from apps.finance.salary_models import EmployeeSalaryInfo
    from apps.onboarding.models import OnboardingRecord, OffboardingRecord, ProbationPerformanceReport
    from apps.payroll_engine.models import PayrollEmployee

    codes = [code for code in (employee.employee_number, employee.employee_code, employee.emp_code) if code]
    code_query = Q()
    for code in codes:
        code_query |= Q(employee_id__iexact=code)
    finance_query = Q(user_id=employee.user_id) if employee.user_id else Q(pk__in=[])
    if code_query:
        finance_query |= code_query
    EmployeeSalaryInfo.objects.filter(finance_query, canonical_employee=None).update(canonical_employee=employee)

    payroll_query = Q(user_id=employee.user_id) if employee.user_id else Q(pk__in=[])
    for code in codes:
        payroll_query |= Q(employee_no__iexact=code)
    PayrollEmployee.objects.filter(payroll_query, employee=None).update(employee=employee)

    lifecycle_query = Q(user_id=employee.user_id) if employee.user_id else Q(pk__in=[])
    if employee.email:
        lifecycle_query |= Q(employee_email__iexact=employee.email)
    for code in codes:
        lifecycle_query |= Q(employee_id__iexact=code)
    OnboardingRecord.objects.filter(lifecycle_query, canonical_employee=None).update(canonical_employee=employee)
    OffboardingRecord.objects.filter(lifecycle_query, canonical_employee=None).update(canonical_employee=employee)
    if employee.user_id:
        ProbationPerformanceReport.objects.filter(
            employee_id=employee.user_id, canonical_employee=None,
        ).update(canonical_employee=employee)


def connect_domain_extension_signals():
    """Resolve new extension records to EmployeeMaster at write time."""
    from apps.finance.salary_models import EmployeeSalaryInfo
    from apps.onboarding.models import OnboardingRecord, OffboardingRecord, ProbationPerformanceReport
    from apps.payroll_engine.models import PayrollEmployee
    from apps.hr_core.identity import EmployeeIdentityService

    def link(sender, instance, raw=False, **kwargs):
        if raw:
            return
        canonical_field = 'employee' if sender is PayrollEmployee else 'canonical_employee'
        if getattr(instance, f'{canonical_field}_id', None):
            return
        identifiers = []
        if sender is PayrollEmployee:
            identifiers = [instance.user_id, instance.employee_no]
        elif sender is EmployeeSalaryInfo:
            identifiers = [instance.user_id, instance.employee_id]
        elif sender is ProbationPerformanceReport:
            identifiers = [instance.employee_id]
        else:
            identifiers = [instance.user_id, instance.employee_email, instance.employee_id]
        employee = None
        for identifier in identifiers:
            if identifier:
                employee = EmployeeIdentityService.resolve(identifier)
                if employee:
                    break
        if employee:
            sender.objects.filter(pk=instance.pk).update(**{canonical_field: employee})

    for model in (EmployeeSalaryInfo, PayrollEmployee, OnboardingRecord, OffboardingRecord, ProbationPerformanceReport):
        post_save.connect(link, sender=model, dispatch_uid=f'hr_link_{model._meta.label_lower}', weak=False)


connect_domain_extension_signals()
