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
