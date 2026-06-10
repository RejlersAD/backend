"""
DRF views for the Time Sheet Analytics feature.

All endpoints are mounted under /api/v1/timesheet/ and require an
authenticated RAD AI user (uses the default JWTAuthentication +
IsAuthenticated from REST_FRAMEWORK).

Endpoints:
    GET /health/                    Connection + driver + config snapshot
    GET /discovery/databases/       List databases on the server
    GET /discovery/tables/          List tables in a database
    GET /discovery/columns/         List columns in a table
    GET /discovery/preview/         TOP N rows of any table (for column mapping)
    GET /live/                      Current punches today + IN/OUT/late summary
    GET /daily/?date=YYYY-MM-DD     Per-user hours for a date
    GET /monthly/?year=&month=      Per-user roll-up for a month
    GET /user/?employee_code=&from=&to=     Per-user history
    GET /export/daily/?date=        Excel download
    GET /export/monthly/?year=&month=       Excel download
    GET /export/monthly/pdf/?year=&month=   PDF download
"""
from __future__ import annotations

import logging

from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from rest_framework.response import Response

from . import config as ts_config
from . import discovery as ts_discovery
from . import exports as ts_exports
from . import services as ts_services
from . import sqlserver as ts_sql
from .sqlserver import TimesheetConnectionError, TimesheetDriverError

logger = logging.getLogger(__name__)


# Soft-coded copy returned when the SQL Server can't be reached from this
# environment (e.g. Railway production has no route to the office LAN IP).
# Frontend treats `configured: False` as the trigger for the 'Not Configured'
# banner, so connection failures are presented gracefully instead of red.
_UNREACHABLE_MESSAGE = (
    'Time Sheet biometric server is not reachable from this environment. '
    'This feature is only available on the office network.'
)
_DRIVER_MISSING_MESSAGE = (
    'SQL Server driver is not installed on this environment.'
)


def _graceful_unavailable(exc: Exception, *, extra_keys: dict | None = None):
    """Return a 200 OK payload that mirrors the not-configured response shape
    so the existing frontend banner handles it without changes."""
    if isinstance(exc, TimesheetDriverError):
        reason, message = 'driver_missing', _DRIVER_MISSING_MESSAGE
    else:
        reason, message = 'unreachable', _UNREACHABLE_MESSAGE
    logger.info('[timesheet] gracefully degrading (%s): %s', reason, exc)
    payload = {
        'configured': False,
        'reason': reason,
        'message': message,
        'rows': [],
        'summary': {},
    }
    if extra_keys:
        payload.update(extra_keys)
    return Response(payload)


# ─────────────────────────────────────────────────────────────────────────────
# Health + Discovery (Setup wizard)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def health(request):
    """Aggregated health snapshot used by the Setup wizard."""
    drv = ts_sql.driver_status()
    cfg = ts_config.configuration_status()
    ping = ts_sql.health_check() if cfg['configured'] or drv['driver_in_use'] else {'ok': False}
    return Response({
        'driver': drv,
        'config': cfg,
        'ping': ping,
        'sqlserver_host': ts_config.SQLSERVER['host'],
        'sqlserver_port': ts_config.SQLSERVER['port'],
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def databases(request):
    try:
        return Response({'databases': ts_discovery.list_databases()})
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def tables(request):
    db = request.GET.get('database') or ts_config.SQLSERVER['database']
    if not db:
        return Response({'error': 'database query param required'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response({'database': db, 'tables': ts_discovery.list_tables(db)})
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def columns(request):
    db = request.GET.get('database') or ts_config.SQLSERVER['database']
    tbl = request.GET.get('table')
    if not (db and tbl):
        return Response({'error': 'database + table query params required'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response({
            'database': db,
            'table': tbl,
            'columns': ts_discovery.list_columns(db, tbl),
        })
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def preview(request):
    db = request.GET.get('database') or ts_config.SQLSERVER['database']
    tbl = request.GET.get('table')
    limit = int(request.GET.get('limit') or 5)
    if not (db and tbl):
        return Response({'error': 'database + table query params required'},
                        status=status.HTTP_400_BAD_REQUEST)
    try:
        return Response({
            'database': db,
            'table': tbl,
            'rows': ts_discovery.preview_table(db, tbl, limit),
        })
    except Exception as exc:
        return _error_response(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Reports
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def live(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': [], 'summary': {}})
    try:
        return Response({'configured': True, **ts_services.live_status()})
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def daily(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': []})
    try:
        return Response({'configured': True, **ts_services.daily_report(request.GET.get('date'))})
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def monthly(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': []})
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return Response({
            'configured': True,
            **ts_services.monthly_report(int(y) if y else None, int(m) if m else None),
        })
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def user_drill(request):
    if not ts_config.is_configured():
        return Response({'configured': False, 'rows': []})
    try:
        return Response({
            'configured': True,
            **ts_services.user_history(
                employee_code=request.GET.get('employee_code'),
                email=request.GET.get('email'),
                from_date=request.GET.get('from'),
                to_date=request.GET.get('to'),
            ),
        })
    except (TimesheetConnectionError, TimesheetDriverError) as exc:
        return _graceful_unavailable(exc)
    except Exception as exc:
        return _error_response(exc)


# ─────────────────────────────────────────────────────────────────────────────
# Exports (Excel + PDF)
# ─────────────────────────────────────────────────────────────────────────────
@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_daily_excel(request):
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        return ts_exports.export_daily_excel(request.GET.get('date'))
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_monthly_excel(request):
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return ts_exports.export_monthly_excel(int(y) if y else None,
                                               int(m) if m else None)
    except Exception as exc:
        return _error_response(exc)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def export_monthly_pdf(request):
    if not ts_config.is_configured():
        return Response({'error': 'Time sheet not configured'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    y = request.GET.get('year')
    m = request.GET.get('month')
    try:
        return ts_exports.export_monthly_pdf(int(y) if y else None,
                                             int(m) if m else None)
    except Exception as exc:
        return _error_response(exc)


# ─────────────────────────────────────────────────────────────────────────────
def _error_response(exc: Exception):
    kind = type(exc).__name__
    code = status.HTTP_503_SERVICE_UNAVAILABLE if kind in (
        'TimesheetDriverError', 'TimesheetConnectionError'
    ) else status.HTTP_500_INTERNAL_SERVER_ERROR
    logger.warning('[timesheet] %s: %s', kind, exc)
    return Response({'error': str(exc), 'kind': kind}, status=code)
