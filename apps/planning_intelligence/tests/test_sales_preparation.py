"""The Sales bridge consumes exact, reviewed Planning evidence and no money."""
import datetime as dt
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError
from rest_framework.test import APIClient

from apps.core.project_models import Project
from apps.rbac.models import Organization, Permission, UserPermissionOverride, UserProfile
from apps.sales.models import BidPreparation, Client, Deal
from apps.sales.tests.access_fixtures import grant_sales_actions
from apps.users.models import User
from ..models import (ActivityAssignment, PlanningAuditEvent, PlanningGeneration, PlanningProject, PlanningRiskRecord,
                      Schedule, ScheduleActivity, ScheduleResource, ScheduleVersion, TechnicalProposal, WorkCalendar)
from ..proposal_serializers import TechnicalProposalSerializer
from ..sales_preparation import (eligible_projects, require_project, source_candidates, source_snapshot,
                                validate_bound_enterprise_project)
from ..services.operational_jobs import canonical_fingerprint
from .test_scheduling_engine import grant_planning_test_actions


class SalesPreparationSourceTests(TestCase):
    def setUp(self):
        self.actor = User.objects.create_user(username='bid-planner', email='bid-planner@example.test')
        grant_sales_actions(self.actor, 'sales_opportunities', 'sales_proposals', 'sales_clients')
        grant_planning_test_actions((self.actor,), ('read', 'create', 'update', 'delete', 'export'))
        self.customer = Client.objects.create(company_name='Preparation Client', client_code='BID-CLIENT', account_manager=self.actor)
        self.deal = Deal.objects.create(deal_code='VF-PREP', deal_name='Preparation scope', client=self.customer,
                                       owner=self.actor, stage='proposal', bid_decision='bid', client_reference='RFP-123')
        self.project = PlanningProject.objects.create(name='Bid planning', client=self.customer.company_name,
                                                      duration_months=3, created_by=self.actor)
        self.binding = BidPreparation.objects.create(opportunity=self.deal, planning_project=self.project,
                                                     created_by=self.actor, reason='Known opportunity')
        self.calendar = WorkCalendar.objects.create(project=self.project, name='Known calendar', is_default=True)
        self.schedule = Schedule.objects.create(project=self.project, name='Bid schedule', code='BID',
                                                planned_start=dt.date(2026, 10, 1), default_calendar=self.calendar)
        self.generation = PlanningGeneration.objects.create(project=self.project, version=1,
            manhours={'grand_total_man_hours': 999}, intelligence={'scope': 'Generator text'})
        self.version = ScheduleVersion.objects.create(schedule=self.schedule, version=1, source_generation=self.generation)
        self.activity = ScheduleActivity.objects.create(version=self.version, calendar=self.calendar,
            external_id='A-01', name='Engineering', duration_days=4)
        self.resource = ScheduleResource.objects.create(project=self.project, code='ENGINEER', name='Engineering role',
                                                        role='Engineer', unit_cost=Decimal('12345.67'))
        self.assignment = ActivityAssignment.objects.create(activity=self.activity, resource=self.resource,
            planned_units=4, budgeted_hours=32, budgeted_cost=Decimal('98765.43'))
        self.risk = PlanningRiskRecord.objects.create(version=self.version, source_key='source-risk', title='Late data',
            description='Client input may arrive late', probability_percent=25, cost_impact=Decimal('87654.32'),
            impact_currency='AED', schedule_impact_days=2, response='Confirm delivery date',
            provenance={'source': {'private_cost': 'confidential financial evidence'}})
        self.proposal = TechnicalProposal.objects.create(project=self.project, schedule_version=self.version,
            source_generation=self.generation, proposal_number='TP-001', revision=1, title='Technical scope',
            client_name=self.customer.company_name, opportunity_reference=self.deal.deal_code,
            client_reference=self.deal.client_reference, created_by=self.actor,
            sections=[{'key': 'scope', 'title': 'Scope', 'included': True, 'content': 'Planner authored scope'},
                      {'key': 'deliverables', 'title': 'Deliverables', 'included': True,
                       'data': [{'document_number': 'D-01', 'title': 'Reviewed drawing', 'cost': 'secret'}]},
                      {'key': 'assumptions', 'title': 'Assumptions', 'included': True, 'content': 'Client provides input'},
                      {'key': 'exclusions', 'title': 'Exclusions', 'included': False, 'content': 'Excluded section'},
                      {'key': 'risk_mitigation', 'title': 'Risk', 'included': True, 'content': 'Review input dates'},
                      {'key': 'key_personnel', 'title': 'CV', 'content': 'Private personnel details'}],
            snapshot={'captured_at': '2026-10-01T00:00:00Z',
                      'schedule': {'version_id': self.version.pk, 'id': self.schedule.pk, 'version': 1},
                      'generation': {'id': self.generation.pk, 'version': 1},
                      'disciplines': [{'name': 'mechanical'}],
                      'resources': [{'code': 'ENGINEER', 'name': 'Original role', 'unit_cost': 'hidden'}],
                      'manhours': {'grand_total_man_hours': '32.00', 'total_cost': 'private',
                                   'basis': {'hours_per_day': 8, 'hourly_rate': 'private'},
                                   'by_discipline': [{'discipline': 'mechanical', 'man_hours': '32.00', 'cost': 99}]}})
        self.client = APIClient()
        self.client.force_authenticate(self.actor)

    def bundle(self):
        return source_snapshot(self.actor, self.deal, self.project, self.proposal.pk)

    def deny(self, action, module='planning_package'):
        permission = Permission.objects.filter(module__code=module, action=action).first()
        UserPermissionOverride.objects.create(user_profile=UserProfile.objects.get(user=self.actor),
                                             permission=permission, allowed=False)

    def test_exact_frozen_authored_mapping_and_current_nonfinancial_evidence(self):
        bundle = self.bundle()
        self.assertEqual(bundle['proposed_fields']['scope'], 'Planner authored scope')
        self.assertEqual(bundle['proposed_fields']['estimated_hours']['total'], '32.00')
        self.assertEqual(bundle['proposed_fields']['deliverables'], [{'document_number': 'D-01', 'title': 'Reviewed drawing'}])
        self.assertNotIn('exclusions', bundle['proposed_fields'])
        self.assertEqual(bundle['source']['generation_id'], self.generation.pk)
        self.assertEqual(bundle['evidence']['assignments'][0]['budgeted_hours'], '32.00')
        encoded = str(bundle)
        for private in ('12345.67', '98765.43', '87654.32', 'hourly_rate', 'total_cost', 'Private personnel', 'confidential financial'):
            self.assertNotIn(private, encoded)
        self.assertIn('current', bundle['warnings'][-1].lower())

    def test_same_source_fingerprint_stable_and_current_risk_change_invalidates_it(self):
        first = canonical_fingerprint(self.bundle()['fingerprint'])
        self.assertEqual(first, canonical_fingerprint(self.bundle()['fingerprint']))
        self.risk.response = 'New mitigation'
        self.risk.revision += 1
        self.risk.save()
        self.assertNotEqual(first, canonical_fingerprint(self.bundle()['fingerprint']))

    def test_resource_edit_and_authored_section_edit_change_digest_without_replacing_frozen_data(self):
        first = canonical_fingerprint(self.bundle()['fingerprint'])
        self.resource.name = 'Revised resource role'
        self.resource.save()
        second = self.bundle()
        self.assertNotEqual(first, canonical_fingerprint(second['fingerprint']))
        self.assertEqual(second['evidence']['frozen_resources'][0]['name'], 'Original role')
        self.proposal.sections[0]['content'] = 'Revised authored scope'
        self.proposal.save()
        self.assertNotEqual(canonical_fingerprint(second['fingerprint']), canonical_fingerprint(self.bundle()['fingerprint']))

    def test_unknown_effort_and_boilerplate_are_not_proposed_as_known_values(self):
        self.proposal.snapshot['manhours'] = {'grand_total_man_hours': 0, 'by_discipline': []}
        self.proposal.sections[0]['content'] = 'Not specified in the available project references.'
        self.proposal.save()
        bundle = self.bundle()
        self.assertNotIn('estimated_hours', bundle['proposed_fields'])
        self.assertNotIn('scope', bundle['proposed_fields'])
        self.assertIsNone(bundle['evidence']['effort'])

    def test_no_latest_generation_fallback_or_foreign_source(self):
        later = PlanningGeneration.objects.create(project=self.project, version=2, manhours={'grand_total_man_hours': 500})
        self.assertEqual(self.bundle()['source']['generation_id'], self.generation.pk)
        self.proposal.source_generation = later
        self.proposal.save()
        with self.assertRaises(ValidationError):
            self.bundle()

    def test_foreign_snapshot_identity_rejected(self):
        self.proposal.snapshot['schedule']['version_id'] = self.version.pk + 999
        self.proposal.save()
        with self.assertRaises(ValidationError):
            self.bundle()

    def test_planning_and_sales_read_permissions_both_required(self):
        for module in ('planning_package', 'sales_opportunities'):
            with self.subTest(module=module):
                self.deny('read', module)
                with self.assertRaises(PermissionDenied):
                    self.bundle()
                UserPermissionOverride.objects.all().delete()

    def test_foreign_organization_and_unrelated_enterprise_scope_denied(self):
        foreign = Organization.objects.create(code='FOREIGN-BID', name='Foreign')
        UserProfile.objects.filter(user=self.actor).update(organization=foreign)
        other = User.objects.create_user(username='foreign-bid-owner', email='foreign-bid@example.test')
        grant_sales_actions(other, 'sales_opportunities')
        self.project.created_by = other
        self.project.save()
        with self.assertRaises(NotFound):
            self.bundle()
        self.project.created_by = self.actor
        self.project.enterprise_project = Project.objects.create(code='OTHER-EXECUTION', name='Other', owner=self.actor, client=self.customer)
        self.project.save()
        with self.assertRaises(NotFound):
            self.bundle()

    def test_candidates_are_scoped_paginated_and_do_not_create_any_record(self):
        before = TechnicalProposal.objects.count()
        response = source_candidates(self.actor, self.deal, self.project, search='TP-001')
        self.assertEqual(response['count'], 1)
        self.assertEqual(response['results'][0]['technical_proposal_id'], self.proposal.pk)
        self.assertEqual(TechnicalProposal.objects.count(), before)
        with self.assertRaises(NotFound):
            source_snapshot(self.actor, self.deal, self.project, self.proposal.pk + 999)

    def test_existing_connection_does_not_grant_write_or_planning_read(self):
        self.deny('update')
        with self.assertRaises(PermissionDenied):
            require_project(self.actor, self.deal, self.project.pk, write=True)
        self.assertTrue(eligible_projects(self.actor, self.deal).exists())
        self.deny('read')
        self.assertFalse(eligible_projects(self.actor, self.deal).exists())

    def test_bound_technical_create_prefills_canonical_sales_identity_without_approval(self):
        response = self.client.post('/api/v1/planning-intelligence/technical-proposals/',
            {'project': self.project.pk, 'schedule_version': self.version.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['opportunity_reference'], self.deal.deal_code)
        self.assertEqual(response.data['client_reference'], self.deal.client_reference)
        self.assertEqual(response.data['sales_origin']['client_id'], str(self.customer.pk))
        self.assertEqual(response.data['status'], 'draft')
        self.assertIsNone(response.data['approved_by'])
        self.assertFalse(response.data['workflow_permissions']['can_approve'])

    def test_create_rejects_conflicting_canonical_client(self):
        response = self.client.post('/api/v1/planning-intelligence/technical-proposals/',
            {'project': self.project.pk, 'schedule_version': self.version.pk, 'client_name': 'Another client'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_generic_technical_mutations_cannot_reassign_identity_or_manufacture_approval(self):
        for payload in ({'project': self.project.pk}, {'status': 'approved'}, {'snapshot': {}},
                        {'approved_by': self.actor.pk}, {'client_name': 'Other'}, {'opportunity_reference': 'Other'}):
            with self.subTest(payload=payload):
                response = self.client.patch(f'/api/v1/planning-intelligence/technical-proposals/{self.proposal.pk}/', payload, format='json')
                # The production guard may reject an approval-shaped payload
                # before serializer validation; both paths must preserve state.
                self.assertIn(response.status_code, (400, 403), response.data)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.status, 'draft')
        self.assertEqual(self.proposal.client_name, self.customer.company_name)

    def test_late_approval_blocks_draft_write_after_initial_validation(self):
        serializer = TechnicalProposalSerializer(self.proposal, data={'title': 'Stale draft edit'}, partial=True,
                                                context={'request': SimpleNamespace(user=self.actor)})
        self.assertTrue(serializer.is_valid(), serializer.errors)
        TechnicalProposal.objects.filter(pk=self.proposal.pk).update(status='approved')
        from ..proposal_views import TechnicalProposalViewSet
        view = TechnicalProposalViewSet()
        view.request = SimpleNamespace(user=self.actor)
        with self.assertRaises(ValidationError):
            view.perform_update(serializer)
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.title, 'Technical scope')

    def test_update_and_audit_are_atomic(self):
        with patch('apps.planning_intelligence.proposal_views.record_event', side_effect=RuntimeError('Audit unavailable')):
            with self.assertRaises(RuntimeError):
                self.client.patch(f'/api/v1/planning-intelligence/technical-proposals/{self.proposal.pk}/',
                                  {'title': 'Must roll back'}, format='json')
        self.proposal.refresh_from_db()
        self.assertEqual(self.proposal.title, 'Technical scope')

    def test_saved_sales_origin_hidden_when_sales_scope_revoked(self):
        self.proposal.snapshot['sales_origin'] = {'opportunity_id': str(self.deal.pk)}
        self.proposal.save()
        self.deny('read', 'sales_opportunities')
        data = TechnicalProposalSerializer(self.proposal, context={'request': SimpleNamespace(user=self.actor)}).data
        self.assertIsNone(data['sales_origin'])
        self.assertNotIn('sales_origin', data['snapshot'])

    def test_bound_workspace_cannot_become_an_unrelated_execution_project(self):
        target = Project.objects.create(code='NOT-AWARDED', name='Unrelated project', owner=self.actor)
        with self.assertRaises(ValidationError):
            validate_bound_enterprise_project(self.project, target)
        self.deal.converted_project = target
        self.deal.save()
        validate_bound_enterprise_project(self.project, target)

    def test_unlinked_schedule_creation_does_not_use_latest_generation(self):
        self.binding.delete()
        version = ScheduleVersion.objects.create(schedule=self.schedule, version=2)
        response = self.client.post('/api/v1/planning-intelligence/technical-proposals/',
            {'project': self.project.pk, 'schedule_version': version.pk}, format='json')
        self.assertEqual(response.status_code, 201, response.data)
        self.assertIsNone(response.data['source_generation'])
        self.assertEqual(response.data['snapshot']['manhours'], {})

    def test_bound_workspace_archive_denied_and_source_stays_available(self):
        response = self.client.delete(f'/api/v1/planning-intelligence/projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 400, response.data)
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_deleted)
        self.assertTrue(BidPreparation.objects.filter(pk=self.binding.pk).exists())
        self.assertEqual(self.bundle()['source']['technical_proposal_id'], self.proposal.pk)
        self.assertFalse(PlanningAuditEvent.objects.filter(project=self.project, action='project.archived').exists())

    def test_unbound_standalone_workspace_retains_explicit_archive_behavior(self):
        self.binding.delete()
        response = self.client.delete(f'/api/v1/planning-intelligence/projects/{self.project.pk}/')
        self.assertEqual(response.status_code, 204, response.data)
        self.project.refresh_from_db()
        self.assertTrue(self.project.is_deleted)
        self.assertEqual(PlanningAuditEvent.objects.filter(project=self.project, action='project.archived').count(), 1)

    def test_archive_rechecks_current_project_write_scope_after_initial_lookup(self):
        self.binding.delete()
        other = User.objects.create_user(username='new-bid-owner', email='new-bid-owner@example.test')
        PlanningProject.objects.filter(pk=self.project.pk).update(created_by=other)
        from ..views import PlanningProjectViewSet
        view = PlanningProjectViewSet()
        view.request = SimpleNamespace(user=self.actor)
        with self.assertRaises(PermissionDenied):
            view.perform_destroy(self.project)
        self.project.refresh_from_db()
        self.assertFalse(self.project.is_deleted)

    def test_bound_technical_create_denied_after_sales_access_revoked(self):
        self.deny('read', 'sales_opportunities')
        response = self.client.post('/api/v1/planning-intelligence/technical-proposals/',
            {'project': self.project.pk, 'schedule_version': self.version.pk}, format='json')
        self.assertEqual(response.status_code, 403, response.data)
        self.assertEqual(TechnicalProposal.objects.filter(project=self.project).count(), 1)
