"""Log retention: old usage_log / system_activity rows roll into daily summaries, reports stay identical.

    python manage.py test apps.usage_tracking.tests_retention --settings=config.settings_usage_retention_test
"""
from datetime import datetime, time, timedelta
from io import StringIO
from unittest import mock

from django.contrib.auth import get_user_model
from django.core.management import CommandError, call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.activity.models import SystemActivity
from apps.usage_tracking import config as cfg
from apps.usage_tracking import tasks, views
from apps.usage_tracking.models import ActivityDailySummary, UsageDailySummary, UsageLog
from apps.usage_tracking.retention import apply_retention


def at(day, hour, minute=0):
    return timezone.make_aware(datetime.combine(day, time(hour, minute)), timezone.get_current_timezone())


class RetentionTests(TestCase):
    def setUp(self):
        today = timezone.localdate()
        # A month well outside every retention window, and one recent day.
        self.old_month = (today - timedelta(days=200)).replace(day=1)
        self.old_days = [self.old_month + timedelta(days=d) for d in (0, 1, 14, 27)]
        self.recent = today - timedelta(days=5)
        self.admin = get_user_model().objects.create_superuser(username='ret-admin', email='ret-admin@example.test', password=None)
        for i, day in enumerate(self.old_days):
            for user, disc, ms, ok, n in (('a@example.test', 'pid', 120, True, 3), ('a@example.test', 'sales', None, True, 1),
                                          ('b@example.test', 'pid', 300 + i, False, 2), ('b@example.test', 'finance', 80, True, 4)):
                for k in range(n):
                    UsageLog.objects.create(user_email=user, user_full_name=user.split('@')[0].upper(),
                                            discipline_key=disc, discipline_label=disc.title(),
                                            request_path=f'/api/v1/{disc}/', response_status=200 if ok else 500,
                                            response_time_ms=ms, success=ok, timestamp=at(day, 9 + k, i))
            # Last minute of the day and first minute of the next: must land on the right days.
            UsageLog.objects.create(user_email='c@example.test', discipline_key='qhse', discipline_label='QHSE',
                                    request_path='/api/v1/qhse/', response_time_ms=50, timestamp=at(day, 23, 59))
            for kind, cat in (('api_request', 'api'), ('api_request', 'api'), ('ai_analysis', 'ai'), ('user_login', 'auth')):
                SystemActivity.objects.create(activity_type=kind, category=cat, user_email='a@example.test',
                                              description='x', timestamp=at(day, 11))
        UsageLog.objects.create(user_email='a@example.test', discipline_key='pid', discipline_label='Pid',
                                request_path='/api/v1/pid/', timestamp=at(self.recent, 10))
        SystemActivity.objects.create(activity_type='api_request', category='api', user_email='a@example.test',
                                      description='recent', timestamp=at(self.recent, 10))

    def report(self, period='monthly', anchor=None):
        request = APIRequestFactory().get('/', {'period': period, 'anchor': anchor or self.old_month.strftime('%Y-%m')})
        force_authenticate(request, self.admin)
        data = views.UtilisationReportView.as_view()(request).data
        data['report_meta'].pop('generated_at')
        for key in ('by_user', 'by_discipline'):
            data[key] = sorted(data[key], key=lambda r: (r.get('email') or r.get('discipline_key')))
        return data

    def db_events(self, rng='90d'):
        request = APIRequestFactory().get('/', {'range': rng})
        force_authenticate(request, self.admin)
        data = views.DbEventsView.as_view()(request).data
        return data['total_in_period'], sorted((c['category'], c['count']) for c in data['category_summary'])

    def test_reports_are_identical_after_old_rows_are_summarised(self):
        monthly, quarterly, events = self.report(), self.report('quarterly'), self.db_events('90d')
        result = apply_retention()
        self.assertFalse(UsageLog.objects.filter(timestamp__lt=at(self.recent, 0)).exists())
        self.assertEqual(self.report(), monthly)
        self.assertEqual(self.report('quarterly'), quarterly)
        self.assertEqual(self.db_events('90d'), events)
        # independent checks, so both paths cannot be wrong the same way
        users = {u['email']: u for u in monthly['by_user']}
        self.assertEqual(users['a@example.test']['requests'], 16)       # (3 + 1) x 4 days
        self.assertEqual(users['b@example.test']['avg_response_ms'], round((sum(300 + i for i in range(4)) * 2 + 80 * 16) / 24))
        self.assertEqual(users['a@example.test']['ai_calls'], 4)
        self.assertEqual(monthly['summary']['success_rate'], round((16 + 16 + 4) / 44 * 100, 1))
        self.assertEqual([t['requests'] for t in self.report('daily', None)['trends']], [1])  # recent raw row only
        self.assertEqual(result[0]['rows_deleted'], 44)

    def test_event_totals_are_unchanged_on_the_first_day_of_the_window(self):
        # Rows early and late on the day the 90-day window starts, older than the 30-day noise window.
        edge = timezone.localdate() - timedelta(days=90)
        for hour in (1, 23):
            SystemActivity.objects.create(activity_type='api_request', category='api', user_email='a@example.test',
                                          description='edge', timestamp=at(edge, hour))
        before = self.db_events('90d')
        self.assertEqual(before[0], 3)   # both edge rows + the recent one
        apply_retention()
        self.assertEqual(self.db_events('90d'), before)

    def test_day_boundaries_follow_the_local_calendar(self):
        apply_retention()
        late = UsageDailySummary.objects.get(user_email='c@example.test', date=self.old_days[0])
        self.assertEqual(late.requests, 1)
        self.assertEqual(UsageDailySummary.objects.filter(user_email='c@example.test').count(), 4)

    def test_recent_rows_and_real_actions_are_kept(self):
        apply_retention()
        self.assertTrue(UsageLog.objects.filter(timestamp__gte=at(self.recent, 0)).exists())
        self.assertEqual(SystemActivity.objects.filter(activity_type='api_request').count(), 1)  # only the recent one
        self.assertEqual(SystemActivity.objects.exclude(activity_type='api_request').count(), 8)  # ACTIVITY_RETENTION_DAYS=0
        self.assertEqual(ActivityDailySummary.objects.filter(activity_type='api_request').count(), 4)

    def test_other_activity_types_are_pruned_only_when_configured(self):
        with mock.patch.object(cfg, 'ACTIVITY_RETENTION_DAYS', 120):
            apply_retention()
        self.assertFalse(SystemActivity.objects.filter(activity_type='user_login').exists())
        ai = ActivityDailySummary.objects.filter(activity_type='ai_analysis')
        self.assertEqual(sum(ai.values_list('count', flat=True)), 4)

    def test_dry_run_changes_nothing(self):
        before = (UsageLog.objects.count(), SystemActivity.objects.count())
        result = apply_retention(dry_run=True)
        self.assertEqual((UsageLog.objects.count(), SystemActivity.objects.count()), before)
        self.assertFalse(UsageDailySummary.objects.exists())
        self.assertEqual(result[0]['rows_summarised'], 44)

    def test_rerun_and_late_rows_accumulate(self):
        apply_retention()
        self.assertEqual(apply_retention()[0]['rows_deleted'], 0)
        UsageLog.objects.create(user_email='a@example.test', discipline_key='pid', discipline_label='Pid',
                                request_path='/api/v1/pid/', response_time_ms=60, timestamp=at(self.old_days[0], 15))
        apply_retention()
        row = UsageDailySummary.objects.get(user_email='a@example.test', discipline_key='pid', date=self.old_days[0])
        self.assertEqual((row.requests, row.response_ms_sum, row.response_ms_count), (4, 420, 4))

    def test_max_days_limits_one_run(self):
        result = apply_retention(max_days=2)
        self.assertEqual(result[0]['days_processed'], 2)

    def test_task_and_command_respect_the_switch(self):
        with mock.patch.object(cfg, 'LOG_RETENTION_ENABLED', False):
            self.assertEqual(tasks.apply_log_retention(), {'skipped': 'disabled'})
            with self.assertRaisesMessage(CommandError, 'LOG_RETENTION_ENABLED'):
                call_command('apply_log_retention')
            out = StringIO()
            call_command('apply_log_retention', '--dry-run', stdout=out)
            self.assertIn('usage_log: keep 100 days', out.getvalue())
            self.assertEqual(UsageLog.objects.count(), 45)
        with mock.patch.object(cfg, 'LOG_RETENTION_ENABLED', True):
            self.assertIn('results', tasks.apply_log_retention())
        self.assertEqual(UsageLog.objects.count(), 1)
