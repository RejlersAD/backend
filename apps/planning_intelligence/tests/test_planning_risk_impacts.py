"""Quantified risk inputs retain unknowns, commercial scope and source history."""
from datetime import timedelta
from decimal import Decimal

from django.utils import timezone
from rest_framework.test import APIClient

from ..models import PlanningAuditEvent, PlanningRiskRecord, ScheduleVersion
from ..services.planning_registers import clone_risks, risk_snapshot
from .test_scheduling_engine import ScheduleAPIFixture


class PlanningRiskImpactTests(ScheduleAPIFixture):
    def setUp(self):
        super().setUp()
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/risk-register/'

    def create(self, **extra):
        return self.client.post(self.url, {'version_id': self.version.pk, 'title': 'Supplier capacity',
            'description': 'Supplier capacity may delay the package.', 'reason': 'Reviewed with supplier.', **extra}, format='json')

    def update(self, item, **extra):
        return self.client.patch(self.url, {'version_id': self.version.pk, 'item_id': item['id'],
            'revision': item['revision'], 'reason': 'Weekly risk review.', **extra}, format='json')

    def commercial(self):
        self.owner.is_staff = True
        self.owner.save(update_fields=['is_staff'])

    def test_missing_assessments_stay_unknown_and_explicit_zero_stays_zero(self):
        self.commercial()
        result = self.create()
        self.assertEqual(result.status_code, 201, result.data)
        item = result.data['item']
        self.assertIsNone(item['probability_percent'])
        self.assertIsNone(item['expected_cost_impact'])
        self.assertEqual(result.data['analysis']['expected_cost_by_currency'], {})
        updated = self.update(item, probability_percent='0', cost_impact='15000', impact_currency='AED',
            schedule_impact_days='0', impact_basis='Explicit assessed zero likelihood for current scope.')
        self.assertEqual(updated.status_code, 200, updated.data)
        self.assertEqual(updated.data['item']['expected_cost_impact'], '0.00')
        self.assertEqual(updated.data['analysis']['expected_cost_by_currency'], {'AED': '0.00'})
        self.assertEqual(updated.data['analysis']['schedule_assessed'], 1)

    def test_cost_exposure_uses_explicit_probability_and_separate_currencies(self):
        self.commercial()
        first = self.create(probability_percent='25', cost_impact='10000', impact_currency='AED',
            schedule_impact_days='12.5', impact_basis='Supplier review estimates.')
        self.assertEqual(first.status_code, 201, first.data)
        self.assertEqual(first.data['item']['expected_cost_impact'], '2500.00')
        second = self.create(probability_percent='50', cost_impact='1000', impact_currency='USD', impact_basis='Second supplier.')
        self.assertEqual(second.data['analysis']['expected_cost_by_currency'], {'AED': '2500.00', 'USD': '500.00'})
        third = self.create(cost_impact='8000', impact_currency='AED', impact_basis='Cost known; probability unassessed.')
        self.assertIsNone(third.data['item']['expected_cost_impact'])
        self.assertEqual(third.data['analysis']['cost_assessed'], 2)
        closed = self.update(first.data['item'], status='closed', resolution='Alternative supplier confirmed.')
        self.assertEqual(closed.data['analysis']['expected_cost_by_currency'], {'USD': '500.00'})

    def test_invalid_numbers_currency_and_missing_basis_are_rejected(self):
        self.commercial()
        for extra in ({'probability_percent': '-1'}, {'probability_percent': '100.01'},
                      {'probability_percent': 'NaN'}, {'cost_impact': 'Infinity'}, {'cost_impact': '-1'},
                      {'schedule_impact_days': '-1'}, {'impact_currency': 'aed'}, {'cost_impact': '50'}):
            with self.subTest(extra=extra):
                response = self.create(impact_basis='Reviewed estimate.', **extra)
                self.assertEqual(response.status_code, 400, response.data)
        self.assertEqual(self.create(probability_percent='25').status_code, 400)
        self.assertEqual(PlanningRiskRecord.objects.count(), 0)

    def test_partial_updates_preserve_assessment_and_explicit_null_clears_it(self):
        self.commercial()
        response = self.create(probability_percent='40', cost_impact='200', impact_currency='AED',
            schedule_impact_days='3', impact_basis='Reviewed estimate.')
        self.assertEqual(response.status_code, 201, response.data)
        changed = self.update(response.data['item'], probability_percent='50')
        self.assertEqual(changed.data['item']['expected_cost_impact'], '100.00')
        cleared = self.update(changed.data['item'], probability_percent=None, schedule_impact_days=None)
        self.assertEqual(cleared.status_code, 200, cleared.data)
        self.assertIsNone(cleared.data['item']['expected_cost_impact'])
        self.assertIsNone(cleared.data['item']['schedule_impact_days'])
        self.assertEqual(cleared.data['item']['cost_impact'], '200.00')

    def test_restricted_reads_writes_and_mitigation_edits_preserve_hidden_costs(self):
        self.assertEqual(self.create(cost_impact='100', impact_currency='AED', impact_basis='Reviewed.').status_code, 403)
        self.commercial()
        item = self.create(probability_percent='25', cost_impact='123456', impact_currency='AED',
            impact_basis='Supplier estimate.').data['item']
        self.owner.is_staff = False
        self.owner.save(update_fields=['is_staff'])
        response = self.client.get(self.url, {'version_id': self.version.pk})
        self.assertFalse(response.data['permissions']['can_view_costs'])
        self.assertIsNone(response.data['items'][0]['cost_impact'])
        self.assertIsNone(response.data['analysis']['expected_cost_by_currency'])
        self.assertEqual(self.update(item, cost_impact=None).status_code, 403)
        changed = self.update(item, response='Obtain alternate capacity.', mitigation_status='in_progress')
        self.assertEqual(changed.status_code, 200, changed.data)
        record = PlanningRiskRecord.objects.get(pk=item['id'])
        self.assertEqual(record.cost_impact, Decimal('123456'))
        self.assertEqual(record.probability_percent, Decimal('25'))
        self.assertIsNone(changed.data['item']['cost_impact'])

    def test_mitigation_plan_due_date_and_completion_are_validated_and_counted(self):
        yesterday = (timezone.localdate() - timedelta(days=1)).isoformat()
        self.assertEqual(self.create(mitigation_status='in_progress').status_code, 400)
        self.assertEqual(self.create(mitigation_due_date=yesterday).status_code, 400)
        created = self.create(response='Confirm alternate capacity.', mitigation_status='planned', mitigation_due_date=yesterday)
        self.assertEqual(created.status_code, 201, created.data)
        self.assertEqual(created.data['analysis']['overdue_mitigations'], 1)
        completed = self.update(created.data['item'], mitigation_status='completed')
        self.assertEqual(completed.status_code, 200, completed.data)
        self.assertEqual(completed.data['analysis']['overdue_mitigations'], 0)
        self.assertEqual(completed.data['item']['mitigation_due_date'], yesterday)

    def test_shared_snapshots_and_audit_are_redacted_but_revisions_copy_full_assessment(self):
        self.commercial()
        created = self.create(probability_percent='25', cost_impact='123456', impact_currency='AED',
            schedule_impact_days='4', impact_basis='Supplier review.', response='Find alternate supplier.',
            mitigation_status='in_progress', mitigation_due_date='2026-12-01')
        self.assertEqual(created.status_code, 201, created.data)
        snapshot = risk_snapshot(self.version)
        self.assertIsNone(snapshot[0]['cost_impact'])
        self.assertTrue(snapshot[0]['cost_data_restricted'])
        audit = PlanningAuditEvent.objects.get(action='planning.risk_created')
        self.assertIsNone(audit.after['cost_impact'])
        self.assertIsNone(audit.after['impact_currency'])
        self.assertIsNone(audit.after['expected_cost_impact'])
        self.assertEqual(audit.metadata, {'reason': 'Reviewed with supplier.', 'cost_assessment_updated': True})
        self.assertNotIn('cost_assessment_fingerprint', audit.metadata)
        self.assertNotIn('123456', str(audit.after))
        target = ScheduleVersion.objects.create(schedule=self.schedule, version=2, created_by=self.owner)
        clone_risks(self.version, target)
        copied = target.planning_risks.get()
        self.assertEqual(copied.cost_impact, Decimal('123456'))
        self.assertEqual(copied.schedule_impact_days, Decimal('4'))
        self.assertEqual(copied.mitigation_due_date.isoformat(), '2026-12-01')
        self.assertEqual(copied.mitigation_status, 'in_progress')
        self.assertEqual(copied.description, created.data['item']['description'])

    def test_stale_review_source_mutation_and_outsider_access_are_rejected(self):
        created = self.create()
        self.assertEqual(created.status_code, 201, created.data)
        item = created.data['item']
        self.assertEqual(self.update(item, description='Changed source').status_code, 400)
        self.assertEqual(self.update(item, schedule_impact_days='5', impact_basis='Reviewed delay.').status_code, 200)
        self.assertEqual(self.update(item, schedule_impact_days='6', impact_basis='Stale delay.').status_code, 400)
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(self.url, {'version_id': self.version.pk}).status_code, 404)
        self.assertEqual(self.update(item, mitigation_status='completed').status_code, 404)
