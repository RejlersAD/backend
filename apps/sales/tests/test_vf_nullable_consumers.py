"""Unknown registration facts remain unknown in existing Sales consumers."""

from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.test import TestCase
from django.utils import timezone
from rest_framework.exceptions import ValidationError

from apps.dashboard.executive import _pipeline_stages, _sales_pipeline_metric
from apps.sales.ai_service import SalesAIService
from apps.sales.models import Client, Deal, SalesForecast


class VFNullableConsumerTests(TestCase):
    def setUp(self):
        self.account = Client.objects.create(
            client_code='VF-NULL-CLIENT', company_name='Synthetic registration client',
            industry_type='other',
        )

    def deal(self, **changes):
        values = {
            'deal_code': f'VF-NULL-{Deal.objects.count() + 1}',
            'deal_name': 'Synthetic registration', 'client': self.account,
            'estimated_value': None, 'currency': '', 'expected_close_date': None,
        }
        return Deal.objects.create(**(values | changes))

    def test_next_action_does_not_invent_an_award_date(self):
        deal = self.deal()
        result = SalesAIService.recommend_next_action(deal)
        self.assertEqual(result['primary_action']['action'], 'Schedule Discovery Call')
        self.assertFalse(any(row['action'] == 'Accelerate Close' for row in result['alternative_actions']))
        deal.refresh_from_db()
        self.assertIsNone(deal.expected_close_date)

    def test_unknown_scoring_inputs_are_not_zero_or_low_priority(self):
        score = SalesAIService.calculate_lead_score({
            'estimated_value': None, 'expected_close_date': None,
        })
        self.assertIsNone(score['total_score'])
        self.assertEqual(score['status'], 'incomplete')
        self.assertEqual(score['missing_fields'], ['estimated_value', 'expected_close_date'])
        self.assertIsNone(score['score_breakdown']['budget_match'])
        self.assertIsNone(score['score_breakdown']['urgency'])
        self.assertEqual(score['grade'], 'Not scored')

    def test_complete_scoring_and_real_zero_remain_supported(self):
        score = SalesAIService.calculate_lead_score({
            'estimated_value': Decimal('0'),
            'expected_close_date': timezone.localdate() + timedelta(days=45),
        })
        self.assertIsNotNone(score['total_score'])
        self.assertEqual(score['score_breakdown']['budget_match'], 40)
        self.assertEqual(score['score_breakdown']['urgency'], 75)

    def test_forecast_rejects_unknown_inputs_without_a_snapshot(self):
        self.deal()
        with self.assertRaises(ValidationError) as caught:
            SalesAIService.generate_sales_forecast('2026-Q4')
        self.assertEqual(str(caught.exception.detail['code']), 'forecast_inputs_incomplete')
        self.assertEqual(set(caught.exception.detail['missing_inputs']),
                         {'estimated_value', 'weighted_value', 'currency'})
        self.assertEqual(str(caught.exception.detail['incomplete_opportunity_count']), '1')
        self.assertFalse(SalesForecast.objects.exists())

    def test_forecast_does_not_silently_drop_unpriced_subset(self):
        self.deal(estimated_value=Decimal('1000'), currency='AED')
        self.deal(currency='AED')
        with self.assertRaises(ValidationError) as caught:
            SalesAIService.generate_sales_forecast('2026-Q4')
        self.assertNotIn('currency', caught.exception.detail['missing_inputs'])
        self.assertEqual(str(caught.exception.detail['incomplete_opportunity_count']), '1')

    def test_forecast_complete_amounts_still_work_without_an_award_date(self):
        self.deal(estimated_value=Decimal('1000'), currency='AED', service_categories=['engineering'])
        result = SalesAIService.generate_sales_forecast('2026-Q4')
        self.assertEqual(result['predicted_revenue'], 70.0)
        self.assertEqual(result['forecast_by_service'], {'engineering': 100.0})
        self.assertEqual(result['top_deals_considered'][0]['weighted_value'], 100.0)

    def test_service_breakdown_propagates_missing_amount_in_either_order(self):
        known = SimpleNamespace(weighted_value=Decimal('100'), currency='AED', service_categories=['engineering', 'design'])
        unknown = SimpleNamespace(weighted_value=None, currency='AED', service_categories=['engineering'])
        for rows in ([known, unknown], [unknown, known]):
            self.assertEqual(SalesAIService._calculate_service_breakdown(rows),
                             {'engineering': None, 'design': 100.0})

    def test_client_insight_does_not_claim_a_partial_amount_is_complete(self):
        self.deal(stage='qualified', currency='AED')
        self.deal(stage='qualified', estimated_value=Decimal('1000'), currency='AED')
        insight = next(row for row in SalesAIService.generate_insights_summary(self.account)
                       if row['type'] == 'revenue_potential')
        self.assertIsNone(insight['value'])
        self.assertEqual(insight['status'], 'incomplete')
        self.assertEqual(insight['incomplete_opportunity_count'], 1)

    def test_executive_all_unknown_group_keeps_count_without_crashing(self):
        self.deal()
        stage = _pipeline_stages(Deal.objects.all())[0]
        self.assertEqual(stage['count'], 1)
        self.assertEqual(stage['by_currency'], [
            {'currency': 'UNSPECIFIED', 'amount': None, 'weighted_amount': None},
        ])
        metric = _sales_pipeline_metric(Deal.objects.all())
        self.assertEqual(metric['status'], 'partial')
        self.assertEqual(metric['by_currency'], [])
        self.assertEqual(metric['missing_value_count'], 1)

    def test_executive_withholds_only_incomplete_currency_and_preserves_zero(self):
        self.deal(estimated_value=Decimal('1000'), currency='AED')
        self.deal(currency='AED')
        self.deal(estimated_value=Decimal('0'), currency='USD')
        stage = _pipeline_stages(Deal.objects.all())[0]
        amounts = {row['currency']: row for row in stage['by_currency']}
        self.assertIsNone(amounts['AED']['amount'])
        self.assertIsNone(amounts['AED']['weighted_amount'])
        self.assertEqual(amounts['USD']['amount'], '0.00')
        metric = _sales_pipeline_metric(Deal.objects.all())
        self.assertEqual(metric['by_currency'], [{'currency': 'USD', 'amount': '0.00'}])
        self.assertEqual(metric['incomplete_currencies'], ['AED'])
        self.assertEqual(metric['status'], 'partial')

    def test_executive_known_amount_without_currency_is_not_real_money(self):
        self.deal(estimated_value=Decimal('1000'))
        metric = _sales_pipeline_metric(Deal.objects.all())
        self.assertEqual(metric['by_currency'], [])
        self.assertEqual(metric['incomplete_currencies'], ['UNSPECIFIED'])
        self.assertIsNone(_pipeline_stages(Deal.objects.all())[0]['by_currency'][0]['amount'])

    def test_executive_currency_normalization_does_not_hide_missing_rows(self):
        self.deal(estimated_value=Decimal('1000'), currency='AED')
        self.deal(currency='aed')
        self.assertIsNone(_pipeline_stages(Deal.objects.all())[0]['by_currency'][0]['amount'])
        metric = _sales_pipeline_metric(Deal.objects.all())
        self.assertEqual(metric['by_currency'], [])
        self.assertEqual(metric['incomplete_currencies'], ['AED'])
