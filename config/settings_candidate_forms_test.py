"""Isolated candidate-form and HR action regressions, including lifecycle relations."""
from .settings_leave_test import *  # noqa: F403

# User/profile querysets prefetch existing onboarding/offboarding relations.
INSTALLED_APPS = [*INSTALLED_APPS, 'apps.onboarding']  # noqa: F405
