"""
Payroll App Configuration
==========================
Soft-coded settings for the payroll module.

BEAT_SCHEDULE: Celery periodic task definitions.
"""
from celery.schedules import crontab

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED SCHEDULE CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

# Monthly leave accrual schedule
# Target: 00:05 Abu Dhabi time (UTC+4) on the 1st of every month
#       = 20:05 UTC on the LAST day of the previous month.
# crontab() has no "last day of month" expression, so this fires daily on
# 28-31 at 20:05 UTC; the task itself (run_monthly_leave_accrual) re-checks
# the real Asia/Dubai date and only does real work when tomorrow is the 1st
# — so it's correct regardless of what CELERY_TIMEZONE is actually set to.
MONTHLY_ACCRUAL_DAYS_OF_MONTH = '28-31'
MONTHLY_ACCRUAL_HOUR_UTC = 20
MONTHLY_ACCRUAL_MINUTE_UTC = 5

# ─────────────────────────────────────────────────────────────────────────────
# CELERY BEAT SCHEDULE
# Exported to config/celery.py for registration
# ─────────────────────────────────────────────────────────────────────────────

BEAT_SCHEDULE = {
    # Automated monthly leave accrual — adds annual_entitlement/12 days to
    # every current-year EmployeeLeaveRecord at the start of each month.
    'payroll-monthly-leave-accrual': {
        'task': 'payroll.run_monthly_leave_accrual',
        'schedule': crontab(
            day_of_month=MONTHLY_ACCRUAL_DAYS_OF_MONTH,
            hour=MONTHLY_ACCRUAL_HOUR_UTC,
            minute=MONTHLY_ACCRUAL_MINUTE_UTC,
        ),
        'kwargs': {
            'triggered_by': 'celery_beat',
        },
        'options': {
            'expires': 3600 * 6,  # Task expires after 6 hours if not executed
        },
    },
}
