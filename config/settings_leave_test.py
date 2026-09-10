"""Isolated leave workflow tests: no production database or external delivery."""
from .settings_sales_test import *  # noqa
INSTALLED_APPS = [*INSTALLED_APPS, 'apps.payroll', 'apps.payroll_engine', 'apps.timesheet']
ROOT_URLCONF = 'config.urls_leave_test'
