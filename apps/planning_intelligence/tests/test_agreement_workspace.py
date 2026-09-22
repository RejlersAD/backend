"""Agreement intake shares supported facts without inventing project controls."""
from copy import deepcopy
from datetime import date
from decimal import Decimal
import hashlib
import tempfile
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.base import ContentFile
from django.test import TestCase, override_settings

from apps.core.project_models import Project, ProjectMilestone
from apps.rbac.models import Module, Permission
from apps.rbac.module_actions import ensure_module_actions
from apps.users.models import User
from ..agreement_models import AgreementWorkspace
from ..models import PlanningAuditEvent, PlanningFile, PlanningJob, PlanningProject
from ..schedule_models import Schedule, ScheduleVersion
from ..services.agreement_workspace import (accept_agreement_workspace, agreement_workspace_summary,
    analyze_agreement_workspace, serialize_workspace)
from ..services.evidence_graph import EvidenceError


EXTRACT = 'apps.planning_intelligence.services.agreement_extraction.extract_agreement_workspace'


class AgreementWorkspaceTests(TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.temp.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.actor = User.objects.create_user(username='agreement-owner', email='agreement@example.test',
                                              is_staff=True, is_superuser=True)
        for code in ['planning_package', 'project_control']:
            module, _ = Module.objects.get_or_create(code=code, defaults={'name': code})
            ensure_module_actions(Module, Permission, module_ids=[module.pk])
        from .test_business_approval_gates import grant_test_approval
        grant_test_approval((self.actor,))
        self.enterprise = Project.objects.create(code='AGR-1', name='Existing project', owner=self.actor)
        self.project = PlanningProject.objects.create(name='Existing project', created_by=self.actor,
                                                       enterprise_project=self.enterprise)
        self.text = 'Client ADNOC. Commencement 1 December 2025. Contract USD 877512.79. Site survey report.'
        self.source = PlanningFile.objects.create(project=self.project, original_filename='agreement.txt',
            file=ContentFile(self.text.encode(), name='agreement.txt'), category='other', parse_status='done',
            extracted_text=self.text, uploaded_by=self.actor)
        self.manifest = [{'file_id': self.source.pk, 'filename': self.source.original_filename,
            'sha256': hashlib.sha256(self.text.encode()).hexdigest(),
            'text_sha256': hashlib.sha256(self.text.encode()).hexdigest(),
            'size_bytes': self.source.size_bytes, 'page_count': 1, 'category': self.source.category,
            'storage_name': self.source.file.name, 'updated_at': self.source.updated_at.isoformat()}]

    def candidate(self, field, value, *, tab=None, basis='document_fact', identity=None):
        tabs = {'client': 'overview', 'project_name': 'overview', 'scope_summary': 'overview',
                'contract_value': 'commercials', 'payment_term': 'commercials', 'milestone': 'milestones',
                'date_constraint': 'schedule', 'duration_requirement': 'schedule', 'risk': 'risks',
                'estimate_requirement': 'estimates', 'deliverable': 'documents'}
        return {'id': identity or field, 'tab': tab or tabs[field], 'field': field,
                'label': value.get('name') or field.replace('_', ' ').title(), 'entity_key': identity or field,
                'value': value, 'basis': basis, 'confidence': 'high',
                'sources': [{'file_id': self.source.pk, 'filename': 'agreement.txt',
                    'sha256': self.manifest[0]['sha256'], 'text_sha256': self.manifest[0]['text_sha256'],
                    'page': 1, 'quote': self.text, 'char_start': 0, 'char_end': len(self.text), 'quote_verified': True}]}

    def analyze(self, candidates, *, warnings=None, job=None):
        result = {'candidates': candidates, 'document_manifest': deepcopy(self.manifest),
                  'coverage': {'semantic_coverage_verified': False}, 'warnings': warnings or []}
        with patch(EXTRACT, return_value=result):
            return analyze_agreement_workspace(self.project, self.actor, file_ids=[self.source.pk], job=job)

    def accept(self, workspace, **kwargs):
        return accept_agreement_workspace(self.project, self.actor, workspace_id=workspace.pk,
                                           revision=workspace.revision, **kwargs)

    def test_supported_inputs_populate_shared_tabs_and_draft_without_budget_or_actuals(self):
        workspace = self.analyze([
            self.candidate('client', {'text': 'ADNOC'}),
            self.candidate('contract_value', {'amount': '877512.79', 'currency': 'USD'}),
            self.candidate('date_constraint', {'event': 'Commencement', 'date': '2025-12-01', 'anchor': 'Agreement'}),
            self.candidate('deliverable', {'name': 'Site survey report', 'discipline': 'General', 'stage': 'FEED'}),
            self.candidate('milestone', {'name': 'Final package', 'date': None, 'offset_amount': 28,
                                          'offset_unit': 'weeks', 'anchor': 'effective award'}),
            self.candidate('risk', {'name': 'Approval delay', 'description': 'Client response risk'}),
            self.candidate('estimate_requirement', {'name': 'EPC estimate', 'accuracy_percent': 15, 'stage': 'EPC'}),
        ])
        self.assertEqual(PlanningAuditEvent.objects.filter(action='agreement.analyzed').count(), 1)
        self.assertEqual(set(workspace.projection), {'overview', 'schedule', 'commercials', 'milestones',
                                                    'risks', 'estimates', 'documents', 'activity'})
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, {})
        result = self.accept(workspace)
        self.project.refresh_from_db()
        self.enterprise.refresh_from_db()
        self.assertEqual(self.project.client, 'ADNOC')
        self.assertEqual(self.enterprise.contract_value, Decimal('877512.79'))
        self.assertEqual(self.enterprise.currency, 'USD')
        self.assertIsNone(self.enterprise.budget)
        self.assertEqual(self.enterprise.spent, 0)
        self.assertEqual(self.enterprise.progress, 0)
        self.assertEqual(self.enterprise.start_date, date(2025, 12, 1))
        self.assertEqual(self.project.effective_date, date(2025, 12, 1))
        task = self.project.simple_planning_state['tasks'][0]
        self.assertLessEqual(len(task['id']), 64)
        self.assertEqual(task['title'], 'Site survey report')
        self.assertIsNone(task['duration_days'])
        self.assertEqual(task['depends_on'], [])
        self.assertEqual(task['dependency_status'], 'missing')
        self.assertEqual(task['source_references'][0]['checksum_sha256'], self.manifest[0]['sha256'])
        self.assertFalse(ScheduleVersion.objects.exists())
        self.assertFalse(ProjectMilestone.objects.exists())
        self.assertEqual(result['counts']['accepted'], 6)
        self.assertEqual(result['projection']['risks']['items'][0]['status'], 'proposed')
        self.assertFalse(result['projection']['schedule']['calculation_ready'])
        self.assertEqual(result['projection']['overview']['items'][1]['id'], 'contract_value')

    def test_contractual_timing_events_and_anchors_are_never_collapsed(self):
        workspace = self.analyze([
            self.candidate('milestone', {'name': 'FEED completion', 'date': None, 'offset_amount': 28,
                'offset_unit': 'weeks', 'anchor': 'effective award'}, identity='feed'),
            self.candidate('milestone', {'name': 'Provisional acceptance', 'date': None, 'offset_amount': 8,
                'offset_unit': 'months', 'anchor': 'commencement'}, identity='pac'),
        ])
        result = self.accept(workspace)
        self.assertEqual(result['counts']['accepted'], 2)
        self.assertFalse(ProjectMilestone.objects.exists())
        self.assertIn('timing_basis', {item['code'] for item in result['exceptions']})
        self.assertNotIn('source_conflicts', {item['code'] for item in result['exceptions']})

    def test_conflicts_require_explicit_one_value_selection_and_reason(self):
        workspace = self.analyze([
            self.candidate('contract_value', {'amount': '100', 'currency': 'USD'}, identity='fee-a'),
            self.candidate('contract_value', {'amount': '200', 'currency': 'USD'}, identity='fee-b'),
            self.candidate('client', {'text': 'ADNOC'}),
        ])
        with self.assertRaises(EvidenceError) as error:
            self.accept(workspace, selected_fact_ids=['fee-a'])
        self.assertEqual(error.exception.payload['code'], 'agreement_selection_reason_required')
        with self.assertRaises(EvidenceError):
            self.accept(workspace, selected_fact_ids=['fee-a', 'fee-b'], reason='Choose source')
        result = self.accept(workspace, selected_fact_ids=['fee-a'], reason='The signed amendment governs this amount.')
        self.enterprise.refresh_from_db()
        self.assertEqual(self.enterprise.contract_value, Decimal('100'))
        self.assertEqual(result['counts']['accepted'], 2)
        self.assertEqual({item['id']: item['status'] for item in result['projection']['commercials']['items']},
                         {'fee-a': 'accepted', 'fee-b': 'not_selected'})
        self.assertNotIn('source_conflicts', {item['code'] for item in result['exceptions']})

    def test_equivalent_money_precision_does_not_require_conflict_decision(self):
        workspace = self.analyze([
            self.candidate('contract_value', {'amount': '100.00', 'currency': 'USD'}, identity='fee-a'),
            self.candidate('contract_value', {'amount': '100', 'currency': 'USD'}, identity='fee-b'),
        ])
        result = self.accept(workspace)
        self.assertEqual(result['counts']['accepted'], 2)
        self.assertNotIn('source_conflicts', {item['code'] for item in result['exceptions']})
        self.enterprise.refresh_from_db()
        self.assertEqual(self.enterprise.contract_value, Decimal('100.00'))

    def test_unresolved_conflict_can_be_decided_after_initial_bulk_acceptance(self):
        workspace = self.analyze([
            self.candidate('contract_value', {'amount': '100', 'currency': 'USD'}, identity='fee-a'),
            self.candidate('contract_value', {'amount': '200', 'currency': 'USD'}, identity='fee-b'),
        ])
        first = self.accept(workspace)
        self.assertEqual(first['counts']['accepted'], 0)
        workspace.refresh_from_db()
        second = self.accept(workspace, selected_fact_ids=['fee-b'], reason='Signed contract page applies.')
        self.assertEqual(second['counts']['accepted'], 1)
        activities = second['projection']['activity']['items']
        self.assertEqual(len({row['id'] for row in activities}), len(activities))
        self.assertEqual(PlanningAuditEvent.objects.filter(action='agreement.accepted').first().before['status'], 'accepted')
        workspace.refresh_from_db()
        with self.assertRaises(EvidenceError):
            self.accept(workspace, selected_fact_ids=['fee-a'], reason='Try to silently reverse acceptance.')

    def test_existing_project_values_manual_draft_and_actuals_are_preserved(self):
        self.project.client = 'Existing client'
        self.project.simple_planning_state = {'revision': 12, 'tasks': [{'title': 'Manual work'}]}
        self.project.save()
        self.enterprise.contract_value = Decimal('50')
        self.enterprise.budget = Decimal('30')
        self.enterprise.spent = Decimal('10')
        self.enterprise.progress = 40
        self.enterprise.save()
        workspace = self.analyze([self.candidate('client', {'text': 'ADNOC'}),
            self.candidate('contract_value', {'amount': '100', 'currency': 'USD'}),
            self.candidate('deliverable', {'name': 'Site survey report'})])
        result = self.accept(workspace)
        self.project.refresh_from_db()
        self.enterprise.refresh_from_db()
        self.assertEqual(self.project.client, 'Existing client')
        self.assertEqual(self.project.simple_planning_state['revision'], 12)
        self.assertEqual(self.enterprise.contract_value, Decimal('50'))
        self.assertEqual(self.enterprise.budget, Decimal('30'))
        self.assertEqual(self.enterprise.spent, Decimal('10'))
        self.assertEqual(self.enterprise.progress, 40)
        self.assertIn('existing_values_preserved', {item['code'] for item in result['exceptions']})

    def test_approved_schedule_blocks_even_empty_metadata_and_new_milestones(self):
        schedule = Schedule.objects.create(project=self.project, name='Approved', code='MASTER', planned_start=date(2026, 1, 1))
        ScheduleVersion.objects.create(schedule=schedule, status='approved')
        workspace = self.analyze([self.candidate('client', {'text': 'ADNOC'}),
            self.candidate('milestone', {'name': 'New contractual gate', 'date': '2026-06-01'})])
        result = self.accept(workspace)
        self.project.refresh_from_db()
        self.assertEqual(self.project.client, '')
        self.assertFalse(ProjectMilestone.objects.exists())
        self.assertTrue(result['materialization']['approved_schedule_preserved'])

    def test_duplicate_acceptance_is_idempotent_for_audit_and_milestones(self):
        workspace = self.analyze([self.candidate('milestone', {'name': 'Start gate', 'date': '2026-01-01'})])
        first = self.accept(workspace)
        second = self.accept(workspace)
        self.assertEqual(first['revision'], second['revision'])
        self.assertEqual(ProjectMilestone.objects.count(), 1)
        self.assertEqual(PlanningAuditEvent.objects.filter(action='agreement.accepted').count(), 1)

    def test_same_path_original_file_change_blocks_all_acceptance(self):
        workspace = self.analyze([self.candidate('client', {'text': 'ADNOC'})])
        with self.source.file.storage.open(self.source.file.name, 'wb') as stream:
            stream.write(b'A different agreement')
        with self.assertRaises(EvidenceError) as error:
            self.accept(workspace)
        self.assertEqual(error.exception.payload['code'], 'agreement_sources_changed')
        self.project.refresh_from_db()
        self.assertEqual(self.project.client, '')
        self.assertFalse(PlanningAuditEvent.objects.filter(action='agreement.accepted').exists())

    def test_removed_or_changed_source_and_wrong_revision_are_rejected(self):
        workspace = self.analyze([self.candidate('client', {'text': 'ADNOC'})])
        with self.assertRaises(EvidenceError):
            accept_agreement_workspace(self.project, self.actor, workspace_id=workspace.pk, revision=42)
        self.source.original_filename = 'replacement.txt'
        self.source.save()
        self.assertTrue(serialize_workspace(workspace)['stale'])
        with self.assertRaises(EvidenceError):
            self.accept(workspace)

    def test_analysis_replay_uses_durable_job_without_second_extraction(self):
        job = PlanningJob.objects.create(project=self.project, job_type='agreement_setup', requested_by=self.actor)
        first = self.analyze([self.candidate('client', {'text': 'ADNOC'})], job=job)
        with patch(EXTRACT) as extract:
            second = analyze_agreement_workspace(self.project, self.actor, file_ids=[self.source.pk], job=job)
        self.assertEqual(first.pk, second.pk)
        extract.assert_not_called()
        self.assertEqual(AgreementWorkspace.objects.count(), 1)

    def test_source_mutation_during_analysis_rolls_back_result(self):
        def extract(*args, **kwargs):
            self.source.original_filename = 'changed.txt'
            self.source.save()
            return {'candidates': [self.candidate('client', {'text': 'ADNOC'})],
                    'document_manifest': self.manifest, 'coverage': {}, 'warnings': []}
        with patch(EXTRACT, side_effect=extract), self.assertRaises(EvidenceError):
            analyze_agreement_workspace(self.project, self.actor, file_ids=[self.source.pk])
        self.assertFalse(AgreementWorkspace.objects.exists())

    def test_unverified_quotes_and_risk_assessments_are_never_accepted(self):
        unverified = self.candidate('client', {'text': 'ADNOC'})
        unverified['sources'][0]['quote_verified'] = False
        workspace = self.analyze([unverified, self.candidate('risk', {'name': 'Concern'})])
        result = self.accept(workspace)
        self.assertEqual(result['counts']['accepted'], 0)
        self.assertEqual(result['counts']['proposals'], 2)
        self.assertEqual(result['projection']['risks']['items'][0]['basis'], 'ai_proposal')

    def test_newer_analysis_invalidates_old_draft_and_sources_are_immutable(self):
        old = self.analyze([self.candidate('client', {'text': 'ADNOC'})])
        latest = self.analyze([self.candidate('client', {'text': 'ADNOC'})])
        self.assertEqual(agreement_workspace_summary(self.project)['id'], str(latest.pk))
        with self.assertRaises(EvidenceError):
            self.accept(old)
        old.candidates = []
        with self.assertRaises(ValidationError):
            old.save()

    def test_unauthorized_actor_cannot_accept_or_analyze(self):
        other = User.objects.create_user(username='agreement-outsider', email='outsider@example.test')
        workspace = self.analyze([self.candidate('client', {'text': 'ADNOC'})])
        with self.assertRaises(EvidenceError) as error:
            accept_agreement_workspace(self.project, other, workspace_id=workspace.pk, revision=1)
        self.assertEqual(error.exception.status_code, 403)
        with self.assertRaises(EvidenceError), patch(EXTRACT) as extract:
            analyze_agreement_workspace(self.project, other, file_ids=[self.source.pk])
        extract.assert_not_called()

    def test_core_metadata_requires_project_control_permission(self):
        workspace = self.analyze([self.candidate('contract_value', {'amount': '100', 'currency': 'USD'})])
        with patch('apps.rbac.action_policy.module_action_allowed', side_effect=lambda user, module, action: module == 'planning_package'):
            result = self.accept(workspace)
        self.enterprise.refresh_from_db()
        self.assertIsNone(self.enterprise.contract_value)
        self.assertFalse(result['materialization']['core_fields_permitted'])

    def test_parsed_text_is_reused_once_with_current_manifest_and_profile(self):
        self.source.extracted_text = ''
        self.source.parse_status = 'pending'
        self.source.save()
        result = {'candidates': [self.candidate('client', {'text': 'ADNOC'})],
                  'document_manifest': deepcopy(self.manifest), 'coverage': {}, 'warnings': [],
                  'parsed_files': [{'file_id': self.source.pk, 'text': self.text, 'confidence': 0.9,
                                    'coverage': {'status': 'complete', 'ocr_pages': [1]}}]}
        with patch(EXTRACT, return_value=result):
            workspace = analyze_agreement_workspace(self.project, self.actor, file_ids=[self.source.pk])
        self.source.refresh_from_db()
        self.assertEqual(self.source.extracted_text, self.text)
        self.assertEqual(self.source.parse_status, 'done')
        self.assertEqual(self.source.document_profile.checksum_sha256, self.manifest[0]['sha256'])
        self.assertFalse(serialize_workspace(workspace)['stale'])
        self.assertEqual(workspace.source_manifest[0]['updated_at'], self.source.updated_at.isoformat())
        self.assertEqual(self.accept(workspace)['counts']['accepted'], 1)

    def test_partial_source_omission_is_explicit_and_keeps_available_inputs_usable(self):
        second = PlanningFile.objects.create(project=self.project, original_filename='missing.pdf',
                                             file='missing.pdf', category='other')
        result = {'candidates': [self.candidate('client', {'text': 'ADNOC'})],
                  'document_manifest': self.manifest, 'coverage': {'status': 'partial'},
                  'warnings': [{'code': 'source_unreadable', 'message': 'One source is unavailable.', 'file_id': second.pk}]}
        with patch(EXTRACT, return_value=result):
            workspace = analyze_agreement_workspace(self.project, self.actor, file_ids=[self.source.pk, second.pk])
        self.assertEqual(workspace.status, 'partial')
        self.assertFalse(serialize_workspace(workspace)['stale'])
        self.assertEqual(self.accept(workspace)['counts']['accepted'], 1)
        self.assertIn('source_unreadable', {row['code'] for row in workspace.exceptions})

    def test_invalid_parsed_hash_and_revoked_actor_are_not_persisted(self):
        result = {'candidates': [self.candidate('client', {'text': 'ADNOC'})],
                  'document_manifest': self.manifest, 'coverage': {}, 'warnings': [],
                  'parsed_files': [{'file_id': self.source.pk, 'text': 'Not the cited text', 'confidence': 0.9}]}
        with patch(EXTRACT, return_value=result), self.assertRaises(EvidenceError):
            analyze_agreement_workspace(self.project, self.actor, file_ids=[self.source.pk])
        self.assertFalse(AgreementWorkspace.objects.exists())

        def revoke(*args, **kwargs):
            User.objects.filter(pk=self.actor.pk).update(is_active=False)
            return dict(result, parsed_files=[])
        with patch(EXTRACT, side_effect=revoke), self.assertRaises(EvidenceError) as error:
            analyze_agreement_workspace(self.project, self.actor, file_ids=[self.source.pk])
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(AgreementWorkspace.objects.exists())
