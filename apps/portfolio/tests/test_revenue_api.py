from django.test import TestCase

from apps.core.project_models import Project
from . import test_reporting


class PortfolioRevenueApiTests(TestCase):
    grant = test_reporting.PortfolioReportingTests.grant
    source = test_reporting.PortfolioReportingTests.source
    row = test_reporting.PortfolioReportingTests.row

    def setUp(self):
        test_reporting.PortfolioReportingTests.setUp(self)
        self.url = '/api/v1/dashboard/executive/portfolio-workbook/revenue/'

    def test_endpoint_permission_and_source_permission_are_separate(self):
        self.assertEqual(self.client.get(self.url).status_code, 403)
        self.grant('executive_dashboard')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['status'], 'restricted')
        self.assertTrue(response.data['enabled'])
        self.assertIsNone(response.data['source'])
        self.assertEqual(response['Cache-Control'], 'private, no-store')

    def test_missing_source_and_invalid_queries(self):
        self.grant('executive_dashboard', 'project_control')
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['enabled'])
        self.assertEqual(response.data['status'], 'unavailable')
        for query in ({'limit': 201}, {'limit': 0}, {'offset': -1}):
            self.assertEqual(self.client.get(self.url, query).status_code, 400)

    def test_filtered_aggregates_do_not_change_with_pagination(self):
        self.grant('executive_dashboard', 'project_control')
        Project.objects.create(code='P', name='Visible project', owner=self.user)
        _, snapshot = self.source()
        facts = {'executive': {'current_forecast_aed': '300', 'pm_forecast_aed': '250',
                               'forecast_variance_aed': '50'}}
        self.row(snapshot, pm='PM1', business_unit='FEED', client='Client A',
                 period_revenue_aed='100', extra=facts)
        self.row(snapshot, 'P-2', pm='PM1', business_unit='FEED', client='Client A',
                 period_revenue_aed='200', extra=facts)
        self.row(snapshot, 'P-3', pm='PM2', business_unit='DE', client='Client B',
                 period_revenue_aed='900', extra=facts)
        response = self.client.get(self.url, {'pm': 'pm1', 'business_unit': 'FEED',
                                             'client': 'Client A', 'limit': 1, 'offset': 1})
        self.assertEqual(response.status_code, 200)
        data = response.data
        self.assertTrue(data['enabled'])
        self.assertEqual(data['projects']['total_rows'], 2)
        self.assertEqual(data['projects']['returned_rows'], 1)
        self.assertEqual(data['projects']['rows'][0]['subproject_code'], 'P-2')
        values = {item['id']: item['value'] for item in data['kpis']}
        self.assertEqual(values['total_revenue_actual'], '300.00')
        self.assertEqual(values['current_forecast'], '600.00')
        self.assertEqual(values['pm_forecast'], '500.00')
        self.assertEqual(values['variance'], '100.00')
        self.assertEqual(data['capacity']['status'], 'restricted_scope')
        self.assertEqual(data['scope']['row_count'], 2)

    def test_route_is_read_only_and_requires_authentication(self):
        self.grant('executive_dashboard', 'project_control')
        self.assertIn(self.client.post(self.url, {}).status_code, (403, 405))
        self.client.force_authenticate(None)
        self.assertIn(self.client.get(self.url).status_code, (401, 403))
