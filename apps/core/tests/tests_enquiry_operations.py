"""Read-only enquiry operations filters and measured reporting cohorts."""
from datetime import datetime, timedelta, timezone as dt_timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone
from rest_framework.test import APIRequestFactory, force_authenticate

from apps.core.models import Enquiry
from apps.core.views_enquiry import enquiry_stats, list_enquiries
from apps.rbac.models import Module, Organization, Role, RoleModule, UserProfile, UserRole


class EnquiryOperationsTests(TestCase):
    now = datetime(2026, 9, 16, 12, tzinfo=dt_timezone.utc)

    def setUp(self):
        User = get_user_model()
        self.manager = User.objects.create_user('enquiry-operator', email='operator@example.test')
        self.other = User.objects.create_user('enquiry-other', email='other@example.test')
        organization, _ = Organization.objects.get_or_create(code='enquiry-api-tests', defaults={'name': 'API tests'})
        profile, _ = UserProfile.objects.get_or_create(user=self.manager, defaults={'organization': organization})
        profile.roles.clear()
        self.role = Role.objects.create(code='enquiry-api-reader', name='Enquiry operator', level=3)
        UserRole.objects.create(user_profile=profile, role=self.role)
        self.module, _ = Module.objects.get_or_create(code='enquiry_management', defaults={'name': 'Enquiries'})
        self.factory = APIRequestFactory()
        self.enterContext(timezone.override(ZoneInfo('Asia/Dubai')))

    def grant(self):
        RoleModule.objects.get_or_create(role=self.role, module=self.module)

    def enquiry(self, **fields):
        created = fields.pop('created_at', self.now - timedelta(days=2))
        row = Enquiry.objects.create(
            name='Example requester', email='requester@example.test', phone='12345678',
            subject=fields.pop('subject', 'Service assistance'), message='Please assist with this request.',
            **fields,
        )
        Enquiry.objects.filter(pk=row.pk).update(created_at=created)
        return row

    def get(self, view=list_enquiries, params=None, user=None, authenticated=True):
        request = self.factory.get('/enquiry/', params or {})
        if authenticated:
            force_authenticate(request, user=user or self.manager)
        with patch('apps.core.views_enquiry.timezone.now', return_value=self.now):
            return view(request)

    def ids(self, **params):
        response = self.get(params=params)
        self.assertEqual(response.status_code, 200, response.data)
        return [row['id'] for row in response.data['results']]

    def stats(self):
        response = self.get(enquiry_stats)
        self.assertEqual(response.status_code, 200, response.data)
        return response.data

    def test_authentication_and_module_access_required_even_for_assigned_owner(self):
        self.enquiry(assigned_to=self.manager)
        for view in (list_enquiries, enquiry_stats):
            self.assertIn(self.get(view, authenticated=False).status_code, (401, 403))
            self.assertEqual(self.get(view).status_code, 403)
        self.grant()
        self.assertEqual(self.get().status_code, 200)
        self.manager.is_active = False
        self.manager.save(update_fields=['is_active'])
        self.assertEqual(self.get().status_code, 403)

    def test_authorized_operator_sees_existing_full_scope_and_superuser_keeps_access(self):
        row = self.enquiry(assigned_to=self.other)
        self.grant()
        self.assertEqual(self.ids(), [row.pk])
        self.assertEqual(self.ids(assigned_to=str(self.other.pk)), [row.pk])
        self.assertEqual(self.ids(assigned_to=str(self.manager.pk)), [])
        self.assertEqual(self.ids(assigned_to='9' * 40), [])
        self.other.is_superuser = True
        self.other.save(update_fields=['is_superuser'])
        self.assertEqual(self.get(user=self.other).status_code, 200)

    def test_queues_intersect_owner_status_and_existing_filters(self):
        self.grant()
        mine = self.enquiry(assigned_to=self.manager, status='in_progress', urgency='high', department='Finance')
        pending = self.enquiry(assigned_to=self.manager, status='pending_confirmation')
        unassigned = self.enquiry(status='new')
        self.enquiry(status='closed', assigned_to=self.manager)
        self.enquiry(status='spam')
        self.enquiry(status='assigned', assigned_to=self.other)
        self.assertCountEqual(self.ids(queue='mine'), [mine.pk, pending.pk])
        self.assertEqual(self.ids(queue='mine', department='Finance', urgency='high'), [mine.pk])
        self.assertEqual(self.ids(queue='mine', assigned_to=str(self.other.pk)), [])
        self.assertEqual(self.ids(queue='unassigned'), [unassigned.pk])
        self.assertEqual(self.ids(queue='unassigned', status='closed'), [])
        # Preserve old assigned_to=unassigned behavior on the All queue.
        self.assertEqual(len(self.ids(assigned_to='unassigned')), 2)

    def test_overdue_and_on_track_exclude_completed_confirmation_and_unknown_deadlines(self):
        self.grant()
        overdue = self.enquiry(due_at=self.now - timedelta(seconds=1), assigned_to=self.manager)
        boundary = self.enquiry(due_at=self.now)
        future = self.enquiry(due_at=self.now + timedelta(days=1))
        unknown = self.enquiry()
        for status in ('resolved', 'closed', 'spam', 'pending_confirmation'):
            self.enquiry(status=status, due_at=self.now - timedelta(days=1))
            self.enquiry(status=status)
        self.assertEqual(self.ids(queue='at_risk'), [overdue.pk])
        self.assertEqual(self.ids(sla='overdue'), [overdue.pk])
        self.assertCountEqual(self.ids(sla='on_track'), [boundary.pk, future.pk])
        self.assertEqual(self.ids(sla='no_deadline'), [unknown.pk])
        self.assertEqual(self.ids(queue='mine', sla='overdue'), [overdue.pk])
        self.assertEqual(self.ids(queue='at_risk', sla='on_track'), [])

    def test_due_today_uses_local_day_and_includes_earlier_today(self):
        self.grant()
        midnight = timezone.localtime(self.now).replace(hour=0, minute=0, second=0, microsecond=0)
        first = self.enquiry(due_at=midnight)
        last = self.enquiry(due_at=midnight + timedelta(days=1, microseconds=-1))
        self.enquiry(due_at=midnight - timedelta(microseconds=1))
        self.enquiry(due_at=midnight + timedelta(days=1))
        self.enquiry(due_at=midnight, status='pending_confirmation')
        self.assertCountEqual(self.ids(sla='due_today'), [first.pk, last.pk])

    def test_reference_search_preserves_text_or_match_and_server_pagination(self):
        self.grant()
        row = self.enquiry()
        reference = f'ENQ-{row.pk:06d}'
        text_match = self.enquiry(subject=f'Follow up to {reference}', created_at=self.now)
        self.enquiry(subject='Unrelated request')
        response = self.get(params={'search': reference.lower(), 'page_size': 1, 'page': 1})
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(response.data['results'][0]['id'], text_match.pk)
        response = self.get(params={'search': reference, 'page_size': 1, 'page': 2})
        self.assertEqual(response.data['count'], 2)
        self.assertEqual(response.data['results'][0]['id'], row.pk)
        self.assertIn(row.pk, self.ids(search=str(row.pk)))
        self.assertEqual(self.ids(search='ENQ-' + '9' * 40), [])
        response = self.get(params={'page_size': 999})
        self.assertEqual(response.data['page_size'], 100)

    def test_no_response_cohort_is_unknown_not_zero_or_full_compliance(self):
        self.grant()
        self.enquiry(due_at=self.now - timedelta(hours=2))
        data = self.stats()
        self.assertIsNone(data['median_response_hours'])
        self.assertEqual(data['response_sample_count'], 0)
        self.assertIsNone(data['first_response_sla_compliance'])
        self.assertEqual(data['first_response_sla_sample_count'], 0)
        # The legacy fields are deliberately unchanged.
        self.assertEqual(data['average_response_hours'], 0)
        self.assertEqual(data['sla_compliance'], 100.0)

    def test_zero_duration_response_and_zero_percent_sla_are_observed_values(self):
        self.grant()
        created = self.now - timedelta(hours=3)
        self.enquiry(created_at=created, first_response_at=created)
        data = self.stats()
        self.assertEqual(data['median_response_hours'], 0)
        self.assertEqual(data['response_sample_count'], 1)
        self.assertIsNone(data['first_response_sla_compliance'])
        self.enquiry(created_at=created, due_at=created, first_response_at=created + timedelta(hours=1))
        data = self.stats()
        self.assertEqual(data['first_response_sla_compliance'], 0)
        self.assertEqual(data['first_response_sla_sample_count'], 1)

    def test_median_filters_negative_durations_and_is_not_the_mean(self):
        self.grant()
        created = self.now - timedelta(days=3)
        for hours in (1, 2, 30, -2):
            self.enquiry(created_at=created, first_response_at=created + timedelta(hours=hours))
        data = self.stats()
        self.assertEqual(data['median_response_hours'], 2)
        self.assertEqual(data['response_sample_count'], 3)
        self.assertNotEqual(data['median_response_hours'], data['average_response_hours'])

    def test_first_response_sla_is_separate_from_completion_and_validates_due_dates(self):
        self.grant()
        created = self.now - timedelta(days=3)
        due = created + timedelta(hours=4)
        # Exact deadline is met for first response; final completion is late.
        self.enquiry(created_at=created, due_at=due, first_response_at=due,
                     resolved_at=self.now, status='closed')
        self.enquiry(created_at=created, due_at=due, first_response_at=due + timedelta(seconds=1))
        self.enquiry(created_at=created, due_at=due)  # No response: outside measured cohort.
        self.enquiry(created_at=created, due_at=created - timedelta(seconds=1), first_response_at=created)
        self.enquiry(created_at=created, due_at=due, first_response_at=created - timedelta(seconds=1))
        data = self.stats()
        self.assertEqual(data['response_sample_count'], 3)
        self.assertEqual(data['first_response_sla_sample_count'], 2)
        self.assertEqual(data['first_response_sla_compliance'], 50)
        self.assertEqual(data['sla_compliance'], 0)

    def test_resolved_this_week_uses_monday_local_boundary_current_status_and_no_future(self):
        self.grant()
        monday = datetime(2026, 9, 14, tzinfo=ZoneInfo('Asia/Dubai'))
        self.enquiry(status='closed', resolved_at=monday)
        self.enquiry(status='resolved', resolved_at=self.now)
        self.enquiry(status='closed', resolved_at=monday - timedelta(microseconds=1))
        self.enquiry(status='reopened', resolved_at=monday)
        self.enquiry(status='closed', resolved_at=self.now + timedelta(seconds=1))
        self.enquiry(status='closed', closed_at=self.now)  # No recorded resolved timestamp.
        self.assertEqual(self.stats()['resolved_this_week'], 2)

    def test_open_counts_and_owner_options_include_historical_owners_without_exposing_directory(self):
        self.grant()
        self.other.is_active = False
        self.other.first_name, self.other.last_name = 'Former', 'Owner'
        self.other.save(update_fields=['is_active', 'first_name', 'last_name'])
        self.enquiry(assigned_to=self.other, status='closed')
        self.enquiry(assigned_to=self.other, status='assigned')
        self.enquiry(assigned_to=self.manager, status='pending_confirmation')
        self.enquiry(status='new')
        self.enquiry(status='spam')
        data = self.stats()
        self.assertEqual(data['open_count'], 3)
        self.assertEqual(data['active_unassigned'], 1)
        self.assertEqual(data['unassigned'], 2)
        self.assertEqual(data['assigned_to_me'], 1)
        by_owner = {row['id']: row for row in data['owners']}
        self.assertEqual(by_owner[self.other.pk], {'id': self.other.pk, 'name': 'Former Owner', 'count': 2})
        self.assertEqual(by_owner[self.manager.pk]['count'], 1)
        self.assertTrue(all(set(row) == {'id', 'name', 'count'} for row in data['owners']))

    def test_stats_remain_global_when_list_filters_are_supplied(self):
        self.grant()
        self.enquiry(status='closed', assigned_to=self.other)
        self.enquiry(status='new')
        response = self.get(enquiry_stats, {'queue': 'mine', 'status': 'new', 'department': 'No match'})
        self.assertEqual(response.data['total'], 2)
        self.assertEqual(response.data['open_count'], 1)
