"""Administrator inventory and audited, super-admin database maintenance."""
import logging

from django.conf import settings
from django.db import DatabaseError, connection, transaction
from django.utils import timezone
from rest_framework import serializers, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.exceptions import APIException, NotFound
from rest_framework.permissions import BasePermission, IsAuthenticated
from rest_framework.response import Response

from .audit_context import current_audits
from .database_maintenance import SUPPORTED_ENGINES, public_table, table_catalog
from .permissions import IsAdmin, IsSuperAdmin
from .models import UserProfile
from .utils import create_audit_log

logger = logging.getLogger(__name__)


class IsDatabaseMaintenanceEnabled(BasePermission):
    def has_permission(self, request, view):
        if not getattr(settings, 'DATABASE_MAINTENANCE_ENABLED', False):
            raise NotFound('Database cleaning is temporarily disabled.')
        return True


class IsActiveUser(BasePermission):
    message = 'An active administrator account is required.'

    def has_permission(self, request, view):
        if not request.user or not request.user.is_active:
            return False
        try:
            profile = request.user.rbac_profile
        except UserProfile.DoesNotExist:
            return True  # Django superuser fallback is checked by the role permission.
        return (
            profile.status == 'active' and not profile.is_deleted
            and (not profile.locked_until or profile.locked_until <= timezone.now())
        )


class StrictStringField(serializers.CharField):
    def to_internal_value(self, data):
        if not isinstance(data, str):
            self.fail('invalid')
        return super().to_internal_value(data)


class TableActionSerializer(serializers.Serializer):
    table = StrictStringField(max_length=255, trim_whitespace=False)
    action = serializers.ChoiceField(choices=['clear', 'drop'])
    confirmation = StrictStringField(max_length=255, trim_whitespace=False)

    def validate(self, attrs):
        if attrs['confirmation'] != attrs['table']:
            raise serializers.ValidationError('Type the exact table name to confirm this action.')
        return attrs


class MaintenanceConflict(APIException):
    status_code = status.HTTP_409_CONFLICT


class MaintenanceUnavailable(APIException):
    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_detail = 'Database maintenance is unavailable. No changes were made.'


def _find_action_table(cursor, name, action):
    table = table_catalog(cursor, can_manage=True, include_counts=False).get(name)
    if table is None:
        from rest_framework.exceptions import NotFound
        raise NotFound('This table no longer exists. Refresh the table list.')
    reason = table[f'{action}_blocked_reason']
    if reason:
        raise MaintenanceConflict({'detail': reason, 'referenced_by': table['referenced_by']})
    return table


@api_view(['GET'])
@permission_classes([IsDatabaseMaintenanceEnabled, IsAuthenticated, IsActiveUser, IsAdmin])
def database_tables(request):
    can_manage = IsSuperAdmin().has_permission(request, None)
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute("SET LOCAL statement_timeout = '15s'")
            tables = table_catalog(cursor, can_manage=can_manage)
    except DatabaseError:
        logger.exception('Unable to inspect database maintenance inventory')
        return Response({'detail': 'Unable to load database tables. Please try again.'}, status=503)
    response = Response({
        'database': connection.alias, 'engine': connection.vendor,
        'can_manage': can_manage, 'tables': [public_table(table) for table in tables.values()],
    })
    response['Cache-Control'] = 'no-store'
    return response


@api_view(['POST'])
@permission_classes([IsDatabaseMaintenanceEnabled, IsAuthenticated, IsActiveUser, IsSuperAdmin])
def database_table_action(request):
    serializer = TableActionSerializer(data=request.data)
    if not serializer.is_valid():
        return Response({
            'detail': 'Choose a table and action, and type the exact table name to confirm.',
            'errors': serializer.errors,
        }, status=status.HTTP_400_BAD_REQUEST)
    if connection.vendor not in SUPPORTED_ENGINES or not connection.features.can_rollback_ddl:
        raise MaintenanceUnavailable('This database does not support transactional table maintenance.')
    name = serializer.validated_data['table']
    action = serializer.validated_data['action']
    deleted_rows = None
    entries = current_audits.get()
    audit_count = len(entries) if entries is not None else 0
    try:
        with transaction.atomic(), connection.cursor() as cursor:
            if connection.vendor == 'postgresql':
                cursor.execute("SET LOCAL lock_timeout = '5s'")
                cursor.execute("SET LOCAL statement_timeout = '30s'")
                cursor.execute('SHOW transaction_isolation')
                if cursor.fetchone()[0] != 'read committed':
                    raise MaintenanceUnavailable('Database maintenance requires read committed transaction isolation.')
            table = _find_action_table(cursor, name, action)
            if connection.vendor == 'postgresql':
                cursor.execute(f'LOCK TABLE {table["_sql_name"]} IN ACCESS EXCLUSIVE MODE')
                # Relationships may have changed since the inventory was shown
                # or while this request waited for the table lock.
                locked_table = _find_action_table(cursor, name, action)
                if locked_table['_oid'] != table['_oid']:
                    raise MaintenanceConflict('The table changed. Refresh and confirm again.')
                table = locked_table
            if action == 'clear':
                only = 'ONLY ' if connection.vendor == 'postgresql' else ''
                cursor.execute(f'DELETE FROM {only}{table["_sql_name"]}')
                deleted_rows = max(0, cursor.rowcount)
            else:
                restrict = ' RESTRICT' if connection.vendor == 'postgresql' else ''
                cursor.execute(f'DROP TABLE {table["_sql_name"]}{restrict}')
            try:
                create_audit_log(
                    request.user, 'delete', 'DatabaseTable', resource_repr=name,
                    changes={'operation': action, 'deleted_rows': deleted_rows},
                    metadata={
                        'database': connection.alias, 'table': name,
                        'operation': action, 'request_path': request.path,
                        'action_label': 'Delete table data' if action == 'clear' else 'Delete database table',
                        'audit_source': 'database_maintenance',
                    },
                    ip_address=request.META.get('REMOTE_ADDR'),
                    user_agent=request.META.get('HTTP_USER_AGENT', ''),
                )
            except Exception as exc:
                logger.exception('Database maintenance audit failed; rolling back')
                raise MaintenanceUnavailable() from exc
    except Exception as exc:
        # An audit created before a failed commit must not be reused by the
        # response middleware as a successful event.
        if entries is not None:
            del entries[audit_count:]
        if isinstance(exc, DatabaseError):
            logger.warning('Database maintenance rolled back for %s', name, exc_info=True)
            return Response({
                'detail': 'The database could not complete this action. No changes were made. '
                          'Check dependencies and database permissions, then refresh and try again.',
            }, status=status.HTTP_409_CONFLICT)
        raise
    message = f'Deleted {deleted_rows:,} rows from {name}.' if action == 'clear' else f'Deleted table {name}.'
    return Response({'success': True, 'table': name, 'action': action, 'deleted_rows': deleted_rows, 'message': message})
