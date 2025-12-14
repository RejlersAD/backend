"""
RBAC Signals
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from .models import UserProfile, Organization
from .utils import create_audit_log

User = get_user_model()


@receiver(post_save, sender=User)
def log_user_login(sender, instance, created, **kwargs):
    """
    Log user login events
    """
    if not created and instance.last_login:
        try:
            profile = instance.rbac_profile
            profile.last_login_at = instance.last_login
            profile.failed_login_attempts = 0  # Reset on successful login
            profile.save(update_fields=['last_login_at', 'failed_login_attempts'])
        except UserProfile.DoesNotExist:
            pass
