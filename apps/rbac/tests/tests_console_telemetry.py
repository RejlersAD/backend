from unittest.mock import patch, MagicMock
from types import SimpleNamespace
from datetime import timedelta
from uuid import uuid4

from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.http import JsonResponse
from django.test import TestCase, RequestFactory, override_settings
from django.urls import path
from django.core.cache import cache
from django.utils import timezone
from rest_framework.response import Response
from rest_framework.test import APIRequestFactory, APIClient, force_authenticate

from apps.usage_tracking.middleware import UsageTrackingMiddleware
from apps.usage_tracking.models import UsageLog
from apps.rbac.analytics_collectors import collect_health_check, collect_system_metrics
from apps.rbac.analytics_serializers import SystemHealthCheckSerializer
from apps.rbac.analytics_models import SecurityAlert
from apps.rbac.console_telemetry import console_overview
from apps.rbac.models import AuditLog, Organization, UserProfile, Role
from apps.rbac.views import AnalyticsDashboardViewSet, SecurityAlertViewSet, RoleViewSet, AuditLogViewSet
from apps.rbac.middleware import RBACMiddleware
from apps.rbac.utils import create_audit_log
from apps.rbac.audit_context import current_audits
from apps.core.storage_telemetry import get_admin_s3_snapshot

urlpatterns = [path('api/v1/rbac/roles/<uuid:pk>/', RoleViewSet.as_view({'patch': 'partial_update'}))]


class ConsoleTelemetryTests(TestCase):
    def setUp(self):
        snapshot = patch('apps.core.storage_telemetry.get_admin_s3_snapshot', return_value={
            'status': 'connected', 'total_files': 23261, 'total_size_gb': 32.15,
            'bucket': 'production-test', 'region': 'test-region', 'checked_at': timezone.now().isoformat(),
        })
        self.s3_snapshot = snapshot.start()
        self.addCleanup(snapshot.stop)
        self.user = get_user_model().objects.create_user('console-admin', email='console@example.test', is_superuser=True)
        organization = Organization.objects.create(name='Console test', code='console-test')
        self.profile, _ = UserProfile.objects.get_or_create(user=self.user, defaults={'organization': organization})
        self.profile.is_mfa_enabled = True
        self.profile.save()

    def log(self, status=200, elapsed=40, path='/api/v1/designiq/jobs/'):
        return UsageLog.objects.create(user=self.user, request_path=path, discipline_key='designiq',
                                       response_status=status, response_time_ms=elapsed, success=status < 400)

    def test_read_activity_counts_without_login_and_security_fields_reach_api(self):
        self.log()
        with patch('apps.rbac.analytics_collectors.ensure_fresh'):
            request = APIRequestFactory().get('/overview/')
            force_authenticate(request, self.user)
            response = AnalyticsDashboardViewSet.as_view({'get': 'overview'})(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['active_users_today'], 1)
        self.assertEqual(response.data['mfa_adoption_percentage'], 100)
        self.assertEqual(response.data['privileged_admins'], 1)
        self.assertEqual(response.data['api_requests_count'], 1)

    @patch('apps.rbac.analytics_collectors._safe_psutil', return_value=None)
    def test_metrics_use_request_pipeline_not_audit_write_outcomes(self, _):
        self.log(200, 20)
        self.log(500, 80)
        AuditLog.objects.create(user=self.user, user_email=self.user.email, action='update', resource_type='test', success=True)
        metric = collect_system_metrics(force=True)
        self.assertEqual(metric.api_requests_count, 2)
        self.assertEqual(metric.success_rate_percentage, 50)
        self.assertEqual(metric.avg_response_time_ms, 50)
        self.assertEqual(metric.peak_response_time_ms, 80)

    @patch('apps.rbac.analytics_collectors._ping_celery', return_value=True)
    @patch('apps.rbac.analytics_collectors._ping_redis', return_value=True)
    @patch('apps.rbac.analytics_collectors.shutil.disk_usage', return_value=(100 * 1024**3, 25 * 1024**3, 75 * 1024**3))
    def test_health_measures_probes_and_propagates_api_failures(self, *_):
        self.log(500, 90)
        health = collect_health_check(force=True)
        payload = SystemHealthCheckSerializer(health).data
        self.assertEqual(payload['overall_status'], 'degraded')
        self.assertEqual(payload['api_status'], 'degraded')
        self.assertEqual(payload['ai_status'], 'degraded')
        self.assertEqual(payload['authentication_status'], 'unknown')
        self.assertEqual(payload['error_rates']['api'], 100)
        self.assertEqual(payload['response_times']['api'], 90)
        self.assertGreaterEqual(payload['response_times']['database'], 0)
        overview = console_overview(health)
        self.assertEqual(overview['disk_total_gb'], 100)
        self.assertEqual(overview['disk_used_gb'], 25)
        self.assertEqual(overview['storage_used_gb'], 32.15)
        self.assertIsNone(overview['storage_total_gb'])
        self.assertEqual(payload['storage_status'], 'healthy')

    def test_failed_anonymous_auth_is_logged_without_credentials(self):
        request = RequestFactory().post('/api/v1/auth/login/', {'password': 'never-store'})
        request.user = AnonymousUser()
        UsageTrackingMiddleware(lambda _: JsonResponse({}, status=401))(request)
        row = UsageLog.objects.get()
        self.assertIsNone(row.user_id)
        self.assertEqual(row.user_email, '')
        self.assertEqual(row.response_status, 401)
        self.assertNotIn('never-store', str(row.__dict__))

    def test_active_security_filter_includes_investigations_before_pagination(self):
        for status in ['new', 'investigating', 'resolved', 'false_positive']:
            SecurityAlert.objects.create(status=status, title=status, alert_type='suspicious_activity')
        request = APIRequestFactory().get('/alerts/?active=true')
        force_authenticate(request, self.user)
        response = SecurityAlertViewSet.as_view({'get': 'list'})(request)
        self.assertEqual(response.status_code, 200)
        rows = response.data['results'] if isinstance(response.data, dict) else response.data
        self.assertEqual({row['status'] for row in rows}, {'new', 'investigating'})

    @patch('apps.rbac.analytics_collectors._ping_celery', return_value=True)
    @patch('apps.rbac.analytics_collectors._ping_redis', return_value=True)
    def test_no_request_samples_do_not_report_healthy_api(self, *_):
        health = collect_health_check(force=True)
        self.assertEqual(health.api_status, 'unknown')
        self.assertNotIn('api', health.error_rates)

    def test_s3_failure_never_falls_back_to_server_disk(self):
        self.s3_snapshot.return_value = {'status': 'offline', 'checked_at': timezone.now().isoformat()}
        overview = console_overview(SimpleNamespace(resource_usage={'disk_used_gb': 350.22}))
        self.assertIsNone(overview['storage_used_gb'])
        self.assertEqual(overview['disk_used_gb'], 350.22)

    def middleware_request(self, path, action='create', method='post', pk=None):
        request = getattr(RequestFactory(), method)(path)
        request.user = self.user
        callback = SimpleNamespace(cls=SimpleNamespace(queryset=get_user_model().objects.all()), actions={method: action})
        request.resolver_match = SimpleNamespace(func=callback, kwargs={'pk': pk} if pk is not None else {}, url_name='user-detail')
        middleware = RBACMiddleware(lambda _: Response())
        middleware.process_view(request, callback, (), {})
        return middleware, request

    def test_tracking_posts_do_not_create_administrative_audits(self):
        for endpoint in ['activity', 'ai-usage']:
            middleware, request = self.middleware_request(f'/api/v1/rbac/ai-champion/track/{endpoint}/')
            middleware.process_response(request, Response({}, status=201))
        self.assertEqual(AuditLog.objects.count(), 0)
        self.assertIsNone(current_audits.get())

    def test_explicit_view_event_is_preserved_without_duplicate(self):
        middleware, request = self.middleware_request('/api/v1/rbac/roles/assign/', action='assign')
        entry = create_audit_log(self.user, 'role_assign', 'Role', resource_id=uuid4(),
                                 resource_repr='Engineering administrator', changes={'role': 'assigned'})
        middleware.process_response(request, Response({}, status=200))
        self.assertEqual(AuditLog.objects.count(), 1)
        entry.refresh_from_db()
        self.assertEqual(entry.action, 'role_assign')
        self.assertEqual(entry.resource_repr, 'Engineering administrator')
        self.assertEqual(entry.metadata['request_path'], request.path)
        self.assertEqual(entry.changes, {'role': 'assigned'})
        self.assertIsNone(current_audits.get())

    @override_settings(ROOT_URLCONF=__name__, MIDDLEWARE=[
        'django.contrib.sessions.middleware.SessionMiddleware',
        'django.contrib.auth.middleware.AuthenticationMiddleware',
        'apps.rbac.middleware.RBACMiddleware',
    ])
    def test_real_role_update_records_one_semantic_event_through_middleware(self):
        role = Role.objects.create(code='console-integration-test', name='Original role', level=4)
        client = APIClient()
        client.force_authenticate(self.user)
        response = client.patch(f'/api/v1/rbac/roles/{role.pk}/', {'name': 'Updated role'}, format='json')
        self.assertEqual(response.status_code, 200, response.data)
        entries = AuditLog.objects.filter(user=self.user)
        self.assertEqual(entries.count(), 1)
        self.assertEqual(entries.get().resource_id, role.pk)
        self.assertEqual(entries.get().action, 'update')
        self.assertEqual(entries.get().metadata['request_method'], 'PATCH')
        self.assertIsNone(current_audits.get())

    def test_custom_action_and_integer_target_are_preserved(self):
        middleware, request = self.middleware_request('/api/v1/rbac/users/42/activate/', action='activate', pk=42)
        middleware.process_response(request, Response({}, status=403))
        row = AuditLog.objects.get()
        self.assertEqual(row.metadata['action_label'], 'Activate User')
        self.assertEqual(row.resource_repr, 'User #42')
        self.assertEqual(row.metadata['target_id'], '42')
        self.assertIsNone(row.resource_id)
        self.assertFalse(row.success)

    def test_fallback_create_uses_response_id_and_delete_uses_route_uuid(self):
        middleware, request = self.middleware_request('/api/v1/rbac/users/')
        middleware.process_response(request, Response({'id': 42}, status=201))
        self.assertEqual(AuditLog.objects.get().resource_repr, 'User #42')
        pk = uuid4()
        middleware, request = self.middleware_request(f'/api/v1/rbac/users/{pk}/', action='destroy', method='delete', pk=pk)
        middleware.process_response(request, Response(status=204))
        self.assertEqual(AuditLog.objects.get(action='delete').resource_id, pk)

    def feed(self, params=''):
        request = APIRequestFactory().get(f'/activity/?{params}')
        force_authenticate(request, self.user)
        return AnalyticsDashboardViewSet.as_view({'get': 'real_time_activity'})(request)

    def test_feed_filters_legacy_tracking_before_limit_and_keeps_real_actor(self):
        actor = get_user_model().objects.create_user('other-admin', email='other@example.test', first_name='Other', last_name='Admin')
        event = create_audit_log(actor, 'role_assign', 'Role', resource_repr='Engineering', changes={'module': 'added'})
        for _ in range(8):
            create_audit_log(self.user, 'create', 'track')
        create_audit_log(self.user, 'create', 'Role', metadata={'request_path': '/api/v1/rbac/ai-champion/track/activity/'})
        create_audit_log(self.user, 'create', 'Vendor')
        response = self.feed('limit=1')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['id'], str(event.id))
        self.assertEqual(response.data[0]['user_email'], actor.email)
        self.assertEqual(response.data[0]['actor_name'], 'Other Admin')
        self.assertEqual(response.data[0]['target'], 'Engineering')
        self.assertEqual(response.data[0]['description'], 'Assign role')
        self.assertEqual(AuditLog.objects.filter(resource_type='track').count(), 8)

    def test_feed_applies_selected_period_and_rejects_invalid_query(self):
        event = create_audit_log(self.user, 'update', 'Role', resource_repr='Old role')
        AuditLog.objects.filter(pk=event.pk).update(timestamp=timezone.now() - timedelta(days=2))
        self.assertEqual(len(self.feed('hours=24').data), 0)
        self.assertEqual(len(self.feed('hours=168').data), 1)
        self.assertEqual(self.feed('limit=bad').status_code, 400)


class SharedS3InventoryTests(TestCase):
    def setUp(self):
        cache.clear()

    @patch('apps.core.storage_telemetry.get_s3_service')
    def test_one_paginated_inventory_is_shared_with_existing_aws_endpoint(self, factory):
        from apps.api.views import aws_status
        service = MagicMock(bucket_name='production-test', region='test-region')
        service.s3_client.get_paginator.return_value.paginate.return_value = [
            {'Contents': [{'Key': 'reports/a.pdf', 'Size': 1024**3}]},
            {'Contents': [{'Key': 'reports/b.xlsx', 'Size': 2 * 1024**3}]},
        ]
        factory.return_value = service
        first = get_admin_s3_snapshot()
        self.assertEqual(first['total_size_gb'], 3)
        self.assertEqual(first['total_files'], 2)
        self.assertEqual({row['type'] for row in first['file_breakdown']}, {'PDF', 'XLSX'})
        user = get_user_model().objects.create_user('aws-admin', is_superuser=True)
        request = APIRequestFactory().get('/dashboard/aws-status/')
        force_authenticate(request, user)
        with patch('apps.core.s3_service.get_s3_service', return_value=service):
            response = aws_status(request)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['total_size_gb'], first['total_size_gb'])
        self.assertEqual(response.data['checked_at'], first['checked_at'])
        service.s3_client.get_paginator.assert_called_once_with('list_objects_v2')
        service.s3_client.get_paginator.return_value.paginate.assert_called_once_with(Bucket='production-test')

    @patch('apps.core.storage_telemetry.get_s3_service')
    def test_failed_inventory_is_offline_without_partial_totals(self, factory):
        service = MagicMock(bucket_name='production-test', region='test-region')
        service.s3_client.get_paginator.side_effect = RuntimeError('provider secret error')
        factory.return_value = service
        result = get_admin_s3_snapshot()
        self.assertEqual(result['status'], 'offline')
        self.assertNotIn('total_size_gb', result)
        self.assertNotIn('provider secret error', str(result))


class AuditLogPaginationTests(TestCase):
    setUp = ConsoleTelemetryTests.setUp

    def test_cards_filter_all_records_and_paginate(self):
        AuditLog.objects.all().delete()
        for index in range(25):
            AuditLog.objects.create(user=self.user, user_email=self.user.email,
                                    action='update', resource_type='Role', success=index >= 7)
        old = AuditLog.objects.order_by('timestamp').first()
        AuditLog.objects.filter(pk=old.pk).update(timestamp=timezone.now() - timedelta(days=2))

        def fetch(params):
            request = APIRequestFactory().get('/audit-logs/', params)
            force_authenticate(request, self.user)
            return AuditLogViewSet.as_view({'get': 'list'})(request)

        response = fetch({'page_size': 10, 'page': 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data['results']), 5)
        self.assertEqual(response.data['page_size'], 10)
        self.assertEqual(response.data['summary'], {'total': 25, 'today': 24, 'failed': 7})
        self.assertEqual(fetch({'scope': 'today'}).data['count'], 24)
        self.assertEqual(fetch({'scope': 'failed'}).data['count'], 7)
        self.assertEqual(fetch({'scope': 'invalid'}).status_code, 400)
        self.assertEqual(fetch({'action': 'login'}).data['summary']['total'], 0)
