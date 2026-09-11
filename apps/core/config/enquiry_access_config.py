"""Enquiry operations require an active, explicit RBAC module grant."""
from rest_framework.permissions import BasePermission

ENQUIRY_MODULE_CODE = 'enquiry_management'
# Compatibility for the manual grant command; no identity-based runtime bypass.
ENQUIRY_SPECIAL_ACCESS_USERS = []
ENQUIRY_ADMIN_ROLES = ['super_admin', 'admin', 'ict_admin']


def user_has_enquiry_access(user):
    if not user or not user.is_authenticated or not user.is_active:
        return False
    if user.is_superuser:
        return True
    from apps.rbac.models import UserProfile
    try:
        profile = UserProfile.objects.get(user=user, is_deleted=False)
    except UserProfile.DoesNotExist:
        return False
    return profile.has_module_access(ENQUIRY_MODULE_CODE)


class CanManageEnquiries(BasePermission):
    message = 'Your role does not grant enquiry management access.'

    def has_permission(self, request, view):
        return user_has_enquiry_access(request.user)


def get_enquiry_admin_emails():
    from django.contrib.auth import get_user_model
    return sorted({
        user.email for user in get_user_model().objects.filter(is_active=True)
        if user_has_enquiry_access(user)
    })
