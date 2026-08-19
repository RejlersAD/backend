"""
Notification Signals - Auto-create notifications for events
QHSE and User modules actively monitored
Uses soft-coded conditional registration to prevent errors when apps are disabled
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from django.apps import apps
from .services import NotificationService
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


def is_app_installed(app_label):
    """Check if a Django app is installed and available"""
    return apps.is_installed(app_label)


def _get_users_by_role(role_name):
    """Smart helper to get users by role"""
    try:
        return User.objects.filter(role=role_name, is_active=True)
    except:
        try:
            return User.objects.filter(
                roles__name__icontains=role_name,
                is_active=True
            ).distinct()
        except:
            return User.objects.none()


def _get_users_by_role(role_name):
    """Smart helper to get users by role"""
    try:
        return User.objects.filter(role=role_name, is_active=True)
    except:
        try:
            return User.objects.filter(
                roles__name__icontains=role_name,
                is_active=True
            ).distinct()
        except:
            return User.objects.none()


# ✅ SMART SIGNAL REGISTRATION - Only register if QHSE app is installed
if is_app_installed('apps.qhse'):
    @receiver(post_save, sender='qhse.QHSESpotCheckRegister')
    def notify_qhse_spot_check_created(sender, instance, created, **kwargs):
        """Auto-notify when new spot check is created"""
        if created:
            try:
                observation_type = getattr(instance, 'observation_type', None) or getattr(instance, 'category', None)
                if observation_type in ['CAR', 'NCR', 'MAJOR']:
                    qhse_users = _get_users_by_role('qhse')
                    for user in qhse_users:
                        NotificationService.create_notification(
                            recipient=user,
                            template_key='QHSE_SPOT_CHECK_CREATED',
                            project_no=instance.project_no,
                            observation_type=observation_type,
                            action_url=f'/qhse/general/spotcheck'
                        )
                        logger.info(f"[AUTO-NOTIFY] QHSE spot check notification sent to {user.username}")
            except Exception as e:
                logger.error(f"[AUTO-NOTIFY] QHSE spot check notification failed: {e}")


    @receiver(post_save, sender='qhse.QHSEAudit')
    def notify_qhse_audit_events(sender, instance, created, **kwargs):
        """Auto-notify on audit creation"""
        if created:
            try:
                if hasattr(instance, 'auditor') and instance.auditor:
                    NotificationService.create_notification(
                        recipient=instance.auditor,
                        title=f"New Audit Assigned: {instance.project_no}",
                        message=f"You have been assigned to audit {instance.project_no}. Audit date: {instance.audit_date}",
                        category_name='QHSE',
                        priority='HIGH',
                        send_email=True,
                        action_url=f'/qhse/audits/{instance.id}'
                    )
                    logger.info(f"[AUTO-NOTIFY] QHSE audit notification sent")
            except Exception as e:
                logger.error(f"[AUTO-NOTIFY] QHSE audit notification failed: {e}")
    
    logger.info("[SIGNALS] ✅ QHSE signal handlers registered")
else:
    logger.info("[SIGNALS] ⚠️ QHSE app not installed - skipping QHSE signal handlers")


@receiver(post_save, sender=User)
def notify_user_created(sender, instance, created, **kwargs):
    """Auto-notify when new user is created"""
    if created:
        try:
            NotificationService.create_notification(
                recipient=instance,
                title="Welcome to RAD AI Platform!",
                message=f"Your account has been created successfully. Please update your profile and change your default password.",
                category_name='USER',
                priority='NORMAL',
                send_email=True,
                action_url='/profile/settings'
            )
            
            for admin in User.objects.filter(is_staff=True, is_active=True).exclude(id=instance.id):
                NotificationService.create_notification(
                    recipient=admin,
                    title=f"New User Registered: {instance.username}",
                    message=f"User {instance.get_full_name() or instance.username} has been registered.",
                    category_name='ADMIN',
                    priority='LOW',
                    action_url=f'/admin/users/{instance.id}'
                )
            logger.info(f"[AUTO-NOTIFY] User creation notifications sent for {instance.username}")
        except Exception as e:
            logger.error(f"[AUTO-NOTIFY] User notification failed: {e}")
