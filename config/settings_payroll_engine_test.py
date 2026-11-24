"""Isolated SQLite settings for Payroll Engine tests; no production services.

    python manage.py test apps.payroll_engine.tests_analytics --settings=config.settings_payroll_engine_test

The base registry already installs rbac, hr_core, payroll, payroll_engine and
timesheet (plus crs, which payroll-adjacent models reference).
"""
from .settings_pid_checker_security_test import *  # noqa: F403,F401

ROOT_URLCONF = 'config.urls_payroll_engine_test'
