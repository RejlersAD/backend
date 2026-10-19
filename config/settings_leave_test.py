"""Isolated leave workflow tests: no production database or external delivery."""
from .settings_sales_test import *  # noqa
# Payroll/timesheet dependencies are included by the Sales test registry.
ROOT_URLCONF = 'config.urls_leave_test'
