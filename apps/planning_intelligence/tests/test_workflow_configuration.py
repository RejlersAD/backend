from django.test import TestCase
from rest_framework.test import APIClient
from unittest.mock import patch

from apps.users.models import User

from ..models import (
    EngineeringDependencyRule, EngineeringDependencyTemplate, PlanningProject,
    ProjectScheduleConfiguration, WorkflowStage, WorkflowTemplate, WorkflowTemplateOverride,
    ScheduleDefaultProposal,
)
from ..services.workflow_configuration import (
    ensure_project_schedule_configuration, resolve_workflow_template,
)
from ..services.activity_generator import build_activities
from ..services.pipeline import preview_schedule


def add_five_stages(template):
    rows = [
        ('IFR', 'IFR', 30, ''),
        ('COMPANY_REVIEW', 'COMPANY REVIEW', 20, 'FS'),
        ('IFA', 'IFA', 25, 'FS'),
        ('COMPANY_APPROVAL', 'COMPANY APPROVAL', 15, 'FS'),
        ('FINAL_ISSUE', 'FINAL ISSUE', 10, 'FS'),
    ]
    for sequence, (code, name, weight, relationship) in enumerate(rows, 1):
        WorkflowStage.objects.create(
            template=template, sequence=sequence, code=code, name=name,
            activity_name_template='{deliverable} - {stage}', duration_days=1,
            relationship_to_previous=relationship, progress_weight=weight,
        )


class WorkflowConfigurationTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username='workflow-owner', email='workflow@example.com', password='test')
        self.outsider = User.objects.create_user(username='workflow-outsider', email='outside@example.com', password='test')
        self.project = PlanningProject.objects.create(name='Workflow Project', created_by=self.owner)
        self.standard = WorkflowTemplate.objects.create(
            code='STANDARD_5_STAGE', name='Standard Five Stage', version=1,
            status='active', is_system=True, is_default=True,
        )
        add_five_stages(self.standard)
        self.dependencies = EngineeringDependencyTemplate.objects.create(
            code='PROCESS_ENGINEERING_V1', name='Process Network', discipline='process',
            version=1, status='active', is_system=True, is_default=True,
        )
        EngineeringDependencyRule.objects.create(
            template=self.dependencies, sequence=1,
            predecessor_code='HEAT_MASS_BALANCE', predecessor_name='Heat and Mass Balance',
            predecessor_stage_code='FINAL_ISSUE', successor_code='PROCESS_FLOW_DIAGRAM',
            successor_name='Process Flow Diagram', successor_stage_code='IFR',
            relationship_type='FS', requires_confirmation=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(self.owner)

    def test_standard_configuration_has_exact_five_stage_order(self):
        configuration, created = ensure_project_schedule_configuration(self.project, actor=self.owner)
        self.assertTrue(created)
        self.assertEqual(configuration.standard_task_count, 5)
        self.assertEqual(
            list(configuration.workflow_template.stages.values_list('code', flat=True)),
            ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE'],
        )
        self.assertEqual(configuration.settings['date_authority'], 'cpm')

    def test_system_workflow_can_be_cloned_but_not_edited(self):
        denied = self.client.patch(
            f'/api/v1/planning-intelligence/workflow-templates/{self.standard.id}/',
            {'name': 'Changed corporate template'}, format='json',
        )
        self.assertEqual(denied.status_code, 403)

        response = self.client.post(
            f'/api/v1/planning-intelligence/workflow-templates/{self.standard.id}/clone/',
            {'project': self.project.id}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['status'], 'draft')
        self.assertEqual(response.data['stage_count'], 5)
        self.assertEqual(response.data['project'], self.project.id)

    def test_configuration_rejects_task_count_that_disagrees_with_template(self):
        response = self.client.post(
            '/api/v1/planning-intelligence/schedule-configurations/',
            {
                'project': self.project.id, 'workflow_template': self.standard.id,
                'dependency_template': self.dependencies.id, 'standard_task_count': 6,
            }, format='json',
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn('standard_task_count', response.data)

    def test_default_proposal_is_tested_and_non_effective_until_final_approval(self):
        configuration, _ = ensure_project_schedule_configuration(self.project, actor=self.owner)
        rule = self.dependencies.rules.get()
        response = self.client.post(
            '/api/v1/planning-intelligence/schedule-default-proposals/',
            {
                'project': self.project.id,
                'title': 'Process workflow baseline',
                'rationale': 'Engineering review complete.',
                'workflow_template': self.standard.id,
                'dependency_template': self.dependencies.id,
                'confirmed_dependency_rule_ids': [rule.id],
            }, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertTrue(response.data['tests_passed'])
        self.assertEqual(len(response.data['test_results']), 5)
        configuration.refresh_from_db()
        self.assertEqual(configuration.configuration_version, 1)
        self.assertEqual(configuration.settings.get('confirmed_dependency_rule_ids'), None)

        approved = self.client.post(
            f"/api/v1/planning-intelligence/schedule-default-proposals/{response.data['id']}/decision/",
            {'decision': 'approved', 'comment': 'Final engineering authority approval.'}, format='json',
        )
        self.assertEqual(approved.status_code, 200, approved.data)
        configuration.refresh_from_db()
        self.assertEqual(configuration.configuration_version, 2)
        self.assertEqual(configuration.settings['confirmed_dependency_rule_ids'], [rule.id])

    def test_direct_default_update_is_blocked_and_outsider_cannot_approve(self):
        configuration, _ = ensure_project_schedule_configuration(self.project, actor=self.owner)
        blocked = self.client.patch(
            f'/api/v1/planning-intelligence/schedule-configurations/{configuration.id}/',
            {'settings': {'date_authority': 'cpm'}}, format='json',
        )
        self.assertEqual(blocked.status_code, 400)
        proposal = ScheduleDefaultProposal.objects.create(
            project=self.project, configuration=configuration, title='Protected change',
            base_configuration_version=configuration.configuration_version,
            proposed_values={}, test_results=[{'code': 'test', 'status': 'passed'}],
            proposed_by=self.owner,
        )
        outsider = APIClient()
        outsider.force_authenticate(self.outsider)
        denied = outsider.post(
            f'/api/v1/planning-intelligence/schedule-default-proposals/{proposal.id}/decision/',
            {'decision': 'approved'}, format='json',
        )
        self.assertIn(denied.status_code, (403, 404))

    def test_stale_default_proposal_cannot_overwrite_newer_configuration(self):
        configuration, _ = ensure_project_schedule_configuration(self.project, actor=self.owner)
        proposal = ScheduleDefaultProposal.objects.create(
            project=self.project, configuration=configuration, title='Stale change',
            base_configuration_version=configuration.configuration_version,
            proposed_values={
                'workflow_template': self.standard.id, 'dependency_template': self.dependencies.id,
                'standard_task_count': 5, 'settings': {'date_authority': 'relational_cpm'},
            }, test_results=[{'code': 'contract', 'status': 'passed'}], proposed_by=self.owner,
        )
        configuration.configuration_version += 1
        configuration.save(update_fields=['configuration_version', 'updated_at'])
        response = self.client.post(
            f'/api/v1/planning-intelligence/schedule-default-proposals/{proposal.id}/decision/',
            {'decision': 'approved'}, format='json',
        )
        self.assertEqual(response.status_code, 400)
        proposal.refresh_from_db()
        self.assertEqual(proposal.status, 'proposed')

    def test_deliverable_override_takes_precedence_over_discipline(self):
        configuration, _ = ensure_project_schedule_configuration(self.project, actor=self.owner)
        discipline_template = WorkflowTemplate.objects.create(
            project=self.project, code='MECHANICAL_4', name='Mechanical Four Stage',
            version=1, status='active', created_by=self.owner,
        )
        deliverable_template = WorkflowTemplate.objects.create(
            project=self.project, code='DESIGN_BASIS_3', name='Design Basis Three Stage',
            version=1, status='active', created_by=self.owner,
        )
        WorkflowTemplateOverride.objects.create(
            configuration=configuration, scope_type='discipline', scope_key='mechanical',
            workflow_template=discipline_template,
        )
        WorkflowTemplateOverride.objects.create(
            configuration=configuration, scope_type='deliverable', scope_key='Mechanical Design Basis',
            workflow_template=deliverable_template,
        )
        resolved = resolve_workflow_template(
            configuration, discipline='mechanical', deliverable='Mechanical Design Basis',
        )
        self.assertEqual(resolved, deliverable_template)

    def test_dependency_clone_preserves_stage_release_gates(self):
        response = self.client.post(
            f'/api/v1/planning-intelligence/dependency-templates/{self.dependencies.id}/clone/',
            {'project': self.project.id}, format='json',
        )
        self.assertEqual(response.status_code, 201, response.data)
        self.assertEqual(response.data['rule_count'], 1)
        rule = response.data['rules'][0]
        self.assertEqual(rule['predecessor_stage_code'], 'FINAL_ISSUE')
        self.assertEqual(rule['successor_stage_code'], 'IFR')
        self.assertTrue(rule['requires_confirmation'])

    def test_outsider_cannot_clone_into_another_users_project(self):
        client = APIClient()
        client.force_authenticate(self.outsider)
        response = client.post(
            f'/api/v1/planning-intelligence/workflow-templates/{self.standard.id}/clone/',
            {'project': self.project.id}, format='json',
        )
        self.assertEqual(response.status_code, 400)

    def test_engine_expands_five_stages_and_applies_process_release_gate(self):
        ensure_project_schedule_configuration(self.project, actor=self.owner)
        result = build_activities(self.project, [], {
            'disciplines': {
                'process': {
                    'in_scope': True,
                    'deliverables': ['Heat & Material Balance', 'Process Flow Diagram (PFD)'],
                },
            },
            'hse_studies': [],
        })
        deliverable_activities = [
            item for item in result['activities'] if item.get('workflow_template_code')
        ]
        heat = [item for item in deliverable_activities if item['deliverable'] == 'Heat & Material Balance']
        flow = [item for item in deliverable_activities if item['deliverable'] == 'Process Flow Diagram (PFD)']

        self.assertEqual(
            [item['workflow_stage_code'] for item in heat],
            ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE'],
        )
        standard_groups = {}
        for item in deliverable_activities:
            standard_groups.setdefault((item['discipline'], item['deliverable']), []).append(item)
        self.assertEqual(len(standard_groups), 4)
        self.assertTrue(all(len(items) == 5 for items in standard_groups.values()))
        self.assertNotIn('Start', [item.get('workflow_status') for item in deliverable_activities])
        self.assertEqual(len(flow), 5)
        self.assertEqual(flow[0]['name'], 'Process Flow Diagram (PFD) - IFR')
        self.assertIn(heat[-1]['id'], [item['id'] for item in flow[0]['predecessors']])
        template_link = next(
            item for item in flow[0]['predecessors'] if item['id'] == heat[-1]['id']
        )
        self.assertEqual(template_link['source'], 'dependency_template')
        self.assertTrue(template_link['requires_confirmation'])
        self.assertEqual(result['date_authority'], 'relational_cpm')

    def test_wizard_confirmation_resolves_dependency_warning(self):
        configuration, _ = ensure_project_schedule_configuration(self.project, actor=self.owner)
        rule = self.dependencies.rules.get()
        configuration.settings = {
            **configuration.settings,
            'confirmed_dependency_rule_ids': [rule.id],
        }
        configuration.save(update_fields=['settings', 'updated_at'])

        result = build_activities(self.project, [], {
            'disciplines': {'process': {
                'in_scope': True,
                'deliverables': ['Heat & Material Balance', 'Process Flow Diagram (PFD)'],
            }},
            'hse_studies': [],
        })
        flow_ifr = next(
            item for item in result['activities']
            if item.get('deliverable') == 'Process Flow Diagram (PFD)'
            and item.get('workflow_stage_code') == 'IFR'
        )
        template_link = next(
            item for item in flow_ifr['predecessors'] if item.get('rule_id') == rule.id
        )
        self.assertFalse(template_link['requires_confirmation'])

    @patch('apps.planning_intelligence.services.pipeline.analyze_documents')
    def test_generation_wizard_preview_does_not_persist_generation(self, analyze):
        ensure_project_schedule_configuration(self.project, actor=self.owner)
        analyze.return_value = {
            'disciplines': {'process': {
                'in_scope': True,
                'deliverables': ['Heat & Material Balance', 'Process Flow Diagram (PFD)'],
            }},
            'hse_studies': [],
        }
        result = preview_schedule(self.project, user=self.owner)

        self.assertEqual(result['deliverable_count'], 4)  # Process + PDR + EPC + survey workflow output
        self.assertGreater(result['activity_count'], result['configured_workflow_activity_count'])
        self.assertEqual(result['date_authority'], 'relational_cpm')
        self.assertEqual(self.project.generations.count(), 0)

    def test_generation_rejects_a_stale_wizard_configuration(self):
        configuration, _ = ensure_project_schedule_configuration(self.project, actor=self.owner)
        response = self.client.post(
            f'/api/v1/planning-intelligence/projects/{self.project.id}/generate/',
            {'generation_options': {
                'expected_configuration_version': configuration.configuration_version + 1,
            }},
            format='json',
        )
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data['code'], 'configuration_conflict')
