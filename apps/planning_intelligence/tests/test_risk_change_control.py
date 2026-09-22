"""Persisted risk assessments and existing governance permissions."""
from decimal import Decimal

from rest_framework.test import APIClient

from apps.core.project_models import Project, ProjectMember
from apps.users.models import User

from ..models import GovernanceItem, PlanningAuditEvent, ScheduleVersion
from .test_scheduling_engine import ScheduleFixture, grant_planning_test_actions


class RiskChangeControlAPITests(ScheduleFixture):
    def setUp(self):
        super().setUp()
        enterprise = Project.objects.create(name='Risk controlled project', code='RISK-001', owner=self.owner)
        self.reviewer = User.objects.create_user(username='risk-reviewer', email='risk-reviewer@example.com', password='test')
        grant_planning_test_actions((self.owner, self.reviewer, self.outsider), ('read', 'create', 'update'))
        grant_planning_test_actions((self.outsider,), ('approve',))
        ProjectMember.objects.create(project=enterprise, user=self.reviewer, role='reviewer')
        self.project.enterprise_project = enterprise
        self.project.save(update_fields=['enterprise_project', 'updated_at'])
        self.activity_row = self.activity('RISK-A', 2)
        self.client = APIClient()
        self.client.force_authenticate(self.owner)
        self.base = f'/api/v1/planning-intelligence/schedule-versions/{self.version.pk}'

    def create(self, **extra):
        return self.client.post(f'{self.base}/governance-items/', {
            'item_type': 'risk', 'title': 'Vendor capacity risk', 'owner': self.reviewer.id,
            'activity': self.activity_row.id, **extra,
        }, format='json')

    def update(self, item_id, **extra):
        return self.client.patch(f'{self.base}/governance-item/', {'item_id': item_id, **extra}, format='json')

    def assessment(self, **extra):
        return {'risk_control': {
            'category': 'Procurement', 'cause': 'Limited vendor capacity',
            'event': 'Package delivery delay', 'effect': 'Later site release',
            'response_strategy': 'reduce', 'inherent_probability': 4, 'inherent_impact': 5,
            'residual_probability': 2, 'residual_impact': 3,
            'inherent_cost_exposure': '25000.00', 'residual_cost_exposure': '10000.00',
            'currency': 'AED', 'cost_impact_assessed': False, 'schedule_impact_assessed': True,
            **extra,
        }}

    def test_create_persists_explicit_assessment_and_full_audit(self):
        response = self.create(metadata=self.assessment(), due_date='2026-09-20', schedule_impact_days=4)
        self.assertEqual(response.status_code, 201, response.data)
        item = GovernanceItem.objects.get(pk=response.data['id'])
        self.assertEqual(item.metadata['risk_control']['inherent_cost_exposure'], '25000.00')
        self.assertEqual(item.metadata['risk_control']['inherent_probability'], 4)
        self.assertEqual(item.status, 'open')
        event = PlanningAuditEvent.objects.get(action='governance.item_created', entity_id=str(item.pk))
        self.assertEqual(event.after['metadata'], item.metadata)
        self.assertEqual(event.after['due_date'], '2026-09-20')
        self.assertEqual(event.after['schedule_impact_days'], '4.00')

    def test_update_merges_assessment_without_destroying_other_metadata(self):
        created = self.create(metadata=self.assessment())
        item = GovernanceItem.objects.get(pk=created.data['id'])
        item.metadata['source_reference'] = {'id': 'protected-existing-source'}
        item.save(update_fields=['metadata'])
        response = self.update(item.pk, title='Revised risk title', description='Recorded review detail',
                               metadata={'risk_control': {'residual_probability': 1}},
                               due_date='2026-09-25', cost_impact='250.00', schedule_impact_days='2.50')
        self.assertEqual(response.status_code, 200, response.data)
        item.refresh_from_db()
        self.assertEqual(item.title, 'Revised risk title')
        self.assertEqual(item.metadata['source_reference']['id'], 'protected-existing-source')
        self.assertEqual(item.metadata['risk_control']['residual_probability'], 1)
        self.assertEqual(item.metadata['risk_control']['residual_impact'], 3)
        self.assertEqual(item.cost_impact, Decimal('250.00'))
        event = PlanningAuditEvent.objects.filter(action='governance.item_updated', entity_id=str(item.pk)).latest('created_at')
        self.assertEqual(event.before['metadata']['risk_control']['residual_probability'], 2)
        self.assertEqual(event.after['metadata']['risk_control']['residual_probability'], 1)
        self.assertEqual(event.after['due_date'], '2026-09-25')

    def test_invalid_assessments_are_rejected_without_creating_records(self):
        invalid = [
            {'risk_control': {'inherent_probability': 6, 'inherent_impact': 2}},
            {'risk_control': {'inherent_probability': 2}},
            {'risk_control': {'residual_probability': None, 'residual_impact': 2}},
            {'risk_control': {'inherent_cost_exposure': '-1.00', 'currency': 'AED'}},
            {'risk_control': {'residual_cost_exposure': '0.00'}},
            {'risk_control': {'currency': 'aed'}},
            {'risk_control': {'cost_impact_assessed': True}},
            {'risk_control': {'derived_exposure': 2000}},
            {'source_reference': 'client cannot overwrite provenance'},
            {'risk_control': None},
        ]
        for metadata in invalid:
            with self.subTest(metadata=metadata):
                self.assertEqual(self.create(metadata=metadata).status_code, 400)
        self.assertEqual(GovernanceItem.objects.count(), 0)

    def test_explicit_zero_assessment_survives_create_and_read(self):
        response = self.create(cost_impact=0, schedule_impact_days=0, metadata=self.assessment(
            inherent_cost_exposure='0.00', residual_cost_exposure='0.00', cost_impact_assessed=True,
        ))
        self.assertEqual(response.status_code, 201, response.data)
        dashboard = self.client.get(f'{self.base}/governance/')
        row = dashboard.data['items'][0]
        self.assertEqual(row['cost_impact'], '0.00')
        self.assertTrue(row['metadata']['risk_control']['cost_impact_assessed'])
        self.assertEqual(row['metadata']['risk_control']['inherent_cost_exposure'], '0.00')

    def test_clear_assessment_requires_both_rating_fields(self):
        created = self.create(metadata=self.assessment())
        invalid = self.update(created.data['id'], metadata={'risk_control': {'inherent_probability': None}})
        self.assertEqual(invalid.status_code, 400)
        response = self.update(created.data['id'], metadata={'risk_control': {'inherent_probability': None, 'inherent_impact': None}})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNone(response.data['metadata']['risk_control']['inherent_probability'])
        self.assertIsNone(response.data['metadata']['risk_control']['inherent_impact'])

    def test_currency_cannot_be_cleared_while_exposure_remains(self):
        created = self.create(metadata=self.assessment())
        response = self.update(created.data['id'], metadata={'risk_control': {'currency': ''}})
        self.assertEqual(response.status_code, 400)

    def test_activity_update_rejects_other_version_and_allows_clear(self):
        created = self.create()
        other_version = ScheduleVersion.objects.create(schedule=self.schedule, version=2, created_by=self.owner)
        other_activity = other_version.activities.create(external_id='OTHER', name='Other version', calendar=self.calendar, duration_days=2)
        response = self.update(created.data['id'], title='Must not save', activity=other_activity.pk)
        self.assertEqual(response.status_code, 400)
        item = GovernanceItem.objects.get(pk=created.data['id'])
        self.assertEqual(item.title, 'Vendor capacity risk')
        self.assertEqual(item.activity_id, self.activity_row.pk)
        cleared = self.update(item.pk, activity=None)
        self.assertEqual(cleared.status_code, 200)
        self.assertIsNone(cleared.data['activity'])

    def test_reviewer_can_comment_but_cannot_mutate_assessments(self):
        created = self.create(metadata=self.assessment())
        self.client.force_authenticate(self.reviewer)
        dashboard = self.client.get(f'{self.base}/governance/')
        self.assertFalse(dashboard.data['can_manage'])
        self.assertEqual(self.create().status_code, 403)
        self.assertEqual(self.update(created.data['id'], metadata=self.assessment()).status_code, 403)
        response = self.client.post(f'{self.base}/governance-comments/', {'item': created.data['id'], 'body': 'Review comment from project reviewer.'}, format='json')
        self.assertEqual(response.status_code, 201, response.data)

    def test_outsider_cannot_access_or_mutate_register(self):
        created = self.create()
        self.client.force_authenticate(self.outsider)
        self.assertEqual(self.client.get(f'{self.base}/governance/').status_code, 404)
        self.assertEqual(self.update(created.data['id'], status='approved').status_code, 404)

    def test_existing_status_transition_records_actor_and_resolution(self):
        created = self.create(item_type='change_request')
        response = self.update(created.data['id'], status='implemented', resolution='Change applied following project review.')
        self.assertEqual(response.status_code, 200, response.data)
        self.assertIsNotNone(response.data['closed_at'])
        event = PlanningAuditEvent.objects.filter(action='governance.item_updated').latest('created_at')
        self.assertEqual(event.actor_id, self.owner.id)
        self.assertEqual(event.after['status'], 'implemented')
        self.assertEqual(event.after['resolution'], 'Change applied following project review.')
        reopened = self.update(created.data['id'], status='open')
        self.assertIsNone(reopened.data['closed_at'])
