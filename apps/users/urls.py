"""
URLs for user management and authentication
"""
from django.urls import path
from .views_password import (
    check_first_login,
    reset_first_login_password,
    validate_email,
    change_password
)

urlpatterns = [
    path('check-first-login/', check_first_login, name='check-first-login'),
    path('reset-first-login-password/', reset_first_login_password, name='reset-first-login-password'),
    path('validate-email/', validate_email, name='validate-email'),
    path('change-password/', change_password, name='change-password'),
]
