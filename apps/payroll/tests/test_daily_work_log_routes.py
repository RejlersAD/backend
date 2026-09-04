from django.test import SimpleTestCase
from django.urls import resolve, reverse


class DailyWorkLogRouteTests(SimpleTestCase):
    def test_summary_is_registered_as_a_collection_action(self):
        url = reverse('payroll:daily-log-summary')
        match = resolve(url)

        self.assertEqual(url, '/api/v1/payroll/daily-logs/summary/')
        self.assertEqual(match.url_name, 'daily-log-summary')
        self.assertEqual(match.func.actions, {'get': 'summary'})

    def test_all_documented_custom_actions_are_registered(self):
        expected_routes = {
            'payroll:daily-log-export-to-s3': {'get': 'export_to_s3'},
            'payroll:daily-log-team': {'get': 'team'},
            'payroll:daily-log-approve': {'post': 'approve'},
            'payroll:daily-log-reject': {'post': 'reject'},
        }

        for route_name, actions in expected_routes.items():
            kwargs = {'pk': '00000000-0000-0000-0000-000000000001'} if route_name.endswith(('approve', 'reject')) else None
            match = resolve(reverse(route_name, kwargs=kwargs))
            self.assertEqual(match.func.actions, actions)
