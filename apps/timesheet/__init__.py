"""Public service selection for the Time Sheet application.

Every consumer must use :func:`get_service` so Analytics, employee
self-service and Payroll all honour the same manual/biometric and
SQL-Server/mirror settings.
"""

default_app_config = 'apps.timesheet.apps.TimesheetConfig'


def get_service():
    """Return the configured attendance service module."""
    from . import config

    if config.INPUT_MODE in ('manual', 'hybrid') or config.DATA_SOURCE == 'mirror':
        from . import mirror_services
        return mirror_services

    from . import services
    return services
