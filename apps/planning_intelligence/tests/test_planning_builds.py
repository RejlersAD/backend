"""Approved policy applications preserve exact source inputs and immutable lineage."""
from copy import deepcopy
from datetime import date

from django.core.exceptions import ValidationError as ModelValidationError
from django.core.files.base import ContentFile
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.project_models import ProjectTask
from ..models import PlanningFile, ScheduleVersion, ScheduleBaseline
from ..planning_build_models import PlanningBuild
from ..services.evidence_graph import refresh_evidence_graph, record_evidence_decision, evidence_graph_snapshot
from ..services.planning_builds import (PlanningBuildError, preview_planning_build, apply_planning_build,
    planning_build_collection, serialize_planning_build, build_input_validation, build_provenance, build_manifest)
from ..services.planning_profiles import create_profile, decide_profile, select_profile
from . import test_planning_profiles as profile_fixture


class PlanningBuildTests(TestCase):
    def setUp(self):
        profile_fixture.PlanningProfileTests.setUp(self)
        self.project.effective_date = date(2026, 11, 2)
        self.project.planned_end_date = date(2026, 12, 31)
        self.project.save(update_fields=['effective_date', 'planned_end_date'])
        self.text = '--- Sheet: MDR ---\nSL. NO.|DISCIPLINE|DOCUMENT TITLE\n1|PROCUREMENT|Supplier inspection dossier\n2|COMMISSIONING|Control room acceptance record\n'
        self.file = PlanningFile.objects.create(project=self.project, category='mdr', original_filename='scope.csv',
                                               parse_status='done', extracted_text=self.text, uploaded_by=self.owner)
        self.file.file.save('scope.csv', ContentFile(self.text.encode()), save=True)
        self.graph = refresh_evidence_graph(self.project, self.owner)
        for fact in list(self.graph.nodes.filter(current=True, kind='fact', property__in=['identity', 'discipline'])):
            self.decision(fact)
        for prop, value in [('project_start', '2026-11-02'), ('project_finish', '2026-12-31'), ('scope_complete', True)]:
            self.decision(self.graph.nodes.get(current=True, kind='fact', entity_id='project', property=prop), 'correct', value)
        self.profile = create_profile(self.project, self.owner, {
            'code': 'BUILD-POLICY', 'name': 'Reviewed engineering release policy', 'workflow_template_id': self.workflow.pk,
            'final_gate_label': 'IFT / IFM', 'wbs_convention': {'levels': ['project', 'deliverable', 'workflow_stage'], 'code_separator': '.'},
            'calendar_policy': {'mode': 'project_calendar', 'calendar_id': self.calendar.pk},
            'progress_policy': {'mode': 'workflow_weights'}, 'resource_policy': {'mode': 'workflow_roles'},
        })
        self.profile = decide_profile(self.project, self.owner, self.profile.pk, 'propose', revision=1, reason='Review project policy.')
        self.profile = decide_profile(self.project, self.owner, self.profile.pk, 'approve', revision=2, reason='Approved for selected project scope.')
        select_profile(self.project, self.owner, profile_id=self.profile.pk, selection_revision=0, reason='Use reviewed policy.')
        self.scope = planning_build_collection(self.project, self.owner)['options']['deliverables']
        self.assertEqual(len(self.scope), 2)

    def decision(self, fact, action='accept', value=None):
        self.graph.refresh_from_db()
        return record_evidence_decision(self.project, self.owner, {'revision': self.graph.revision,
            'fact_id': str(fact.pk), 'action': action, 'reason': 'Reviewed exact fixture evidence.', 'value': value})

    def preview(self, **options):
        self.graph.refresh_from_db()
        return preview_planning_build(self.project, self.owner, evidence_revision=self.graph.revision,
            profile_selection_revision=1, options={'independent_entity_ids': [row['entity_id'] for row in self.scope], **options},
            reason='Apply the approved policy only to the explicitly reviewed scope.')

    def apply(self, build):
        return apply_planning_build(build, self.owner, fingerprint=build.fingerprint)

    def test_preview_expands_accepted_scope_with_exact_rules_and_does_not_modify_work(self):
        before = deepcopy(self.project.simple_planning_state)
        build = self.preview()
        self.assertTrue(serialize_planning_build(build)['ready_to_apply'], build.issues)
        self.assertEqual(len(build.plan['activities']), 10)
        self.assertEqual(len(build.plan['relationships']), 8)
        self.assertEqual(len(build.plan['wbs']), 13)
        self.assertEqual(len(build.plan['resources']), 10)
        self.assertEqual(build.plan['risks'], [])
        first = build.plan['activities'][0]
        self.assertEqual(first['duration'], {'value': 2.0, 'unit': 'working_days'})
        self.assertEqual(first['property_lineage']['duration']['type'], 'approved_planning_rule')
        self.assertFalse(first['property_lineage']['duration']['source_fact'])
        self.assertEqual(first['property_lineage']['duration']['value'], first['duration'])
        self.assertTrue(first['property_lineage']['identity']['fact_ids'])
        self.assertTrue(all(row['quantity'] is None and row['assigned_employee_id'] is None for row in build.plan['resources']))
        self.assertTrue(all(row['start_date'] is None and row['finish_date'] is None for row in build.plan['activities']))
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ProjectTask.objects.exists())
        self.assertFalse(ScheduleVersion.objects.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_apply_creates_exact_draft_and_repeated_apply_is_idempotent(self):
        build = self.preview()
        version = self.apply(build)
        self.assertEqual(version.status, 'draft')
        self.assertEqual(version.planning_build_id, build.pk)
        self.assertIsNone(version.calculated_at)
        self.assertEqual(version.activities.count(), 10)
        self.assertEqual(version.relationships.count(), 8)
        self.assertEqual(version.wbs_nodes.count(), 13)
        self.assertEqual(self.apply(build).pk, version.pk)
        self.assertEqual(ScheduleVersion.objects.count(), 1)
        self.assertEqual([issue for issue in build_input_validation(version) if issue['severity'] == 'error'], [])
        self.assertEqual(len(build_provenance(version)), 10)
        self.assertEqual(build_manifest(version)['fingerprint'], build.fingerprint)
        self.project.refresh_from_db()
        self.assertIsNone(self.project.master_schedule_version_id)
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertFalse(ProjectTask.objects.exists())

    def test_same_evidence_profile_and_options_produce_identical_plan_and_fingerprint(self):
        first, second = self.preview(), self.preview()
        self.assertEqual(first.plan, second.plan)
        self.assertEqual(first.fingerprint, second.fingerprint)
        self.assertNotEqual(first.pk, second.pk)
        history = planning_build_collection(self.project, self.owner)['builds']
        self.assertEqual(len(history), 2)
        self.assertEqual(history[0]['summary']['activities'], 10)
        self.assertNotIn('plan', history[0])
        self.assertNotIn('snapshot', history[0]['profile'])

    def test_workflow_roots_require_explicit_independence_and_unbound_rules_are_not_guessed(self):
        build = self.preview(independent_entity_ids=[])
        self.assertFalse(serialize_planning_build(build)['ready_to_apply'])
        self.assertEqual(sum(issue['code'] == 'workflow_root_logic_not_confirmed' for issue in build.issues), 2)
        with self.assertRaises(PlanningBuildError):
            self.apply(build)
        self.assertFalse(ScheduleVersion.objects.exists())
        with self.assertRaises(ValidationError):
            self.preview(dependency_bindings={'Supplier inspection dossier': self.scope[0]['entity_id']})

    def test_fuzzy_titles_and_foreign_ids_cannot_establish_workflow_applicability(self):
        for values in (['Supplier inspection dossier'], [self.scope[0]['entity_id'].upper()], ['other-project-entity']):
            with self.assertRaises(ValidationError):
                self.preview(deliverable_entity_ids=values)
        with self.assertRaises(NotFound):
            preview_planning_build(self.project, self.outsider, evidence_revision=self.graph.revision,
                profile_selection_revision=1, options={}, reason='Foreign access')
        with self.assertRaises(PermissionDenied):
            preview_planning_build(self.project, self.reviewer, evidence_revision=self.graph.revision,
                profile_selection_revision=1, options={}, reason='Read only')

    def test_read_only_member_cannot_apply_an_owners_ready_build(self):
        build = self.preview()
        self.assertTrue(serialize_planning_build(build)['ready_to_apply'], build.issues)
        self.client.force_authenticate(self.reviewer)
        response = self.client.post(
            f'/api/v1/planning-intelligence/projects/{self.project.pk}/planning-builds/{build.pk}/apply/',
            {'fingerprint': build.fingerprint, 'master_revision': 0, 'reason': 'Attempt by read-only member.'},
            format='json',
        )
        self.assertEqual(response.status_code, 403, response.data)
        self.assertFalse(ScheduleVersion.objects.filter(schedule__project=self.project).exists())
        self.project.refresh_from_db()
        self.assertIsNone(self.project.master_schedule_version_id)
        self.assertEqual(self.project.master_schedule_revision, 0)

    def test_changed_source_or_selected_profile_prevents_apply(self):
        build = self.preview()
        self.file.extracted_text += '\n3|PROCUREMENT|Additional package'
        self.file.save(update_fields=['extracted_text', 'updated_at'])
        with self.assertRaises(PlanningBuildError) as failure:
            self.apply(build)
        self.assertEqual(str(failure.exception.detail['code']), 'planning_build_evidence_stale')
        self.file.extracted_text = self.text
        self.file.save(update_fields=['extracted_text', 'updated_at'])
        self.graph = refresh_evidence_graph(self.project, self.owner)
        # Even without altered file contents, the refreshed source version is a new review boundary.
        with self.assertRaises(PlanningBuildError):
            self.apply(build)
        self.assertFalse(ScheduleVersion.objects.exists())

    def test_immutable_build_and_tampered_schedule_provenance_fail_closed(self):
        build = self.preview()
        with self.assertRaises(ModelValidationError):
            build.save()
        if connection.vendor == 'postgresql':
            with self.assertRaises(DatabaseError), transaction.atomic():
                PlanningBuild.objects.filter(pk=build.pk).update(reason='Tampered')
        version = self.apply(build)
        activity = version.activities.first()
        activity.duration_days = 88
        activity.save(update_fields=['duration_days'])
        self.assertTrue(any(issue['code'] == 'planning_build_input_changed' and issue['field'] == 'duration' for issue in build_input_validation(version)))
        self.assertEqual(build_provenance(version), {})

    def test_reselected_profile_is_a_new_selection_revision(self):
        build = self.preview()
        select_profile(self.project, self.owner, profile_id=self.profile.pk, selection_revision=1, reason='Recorded new policy selection decision.')
        with self.assertRaises(PlanningBuildError) as failure:
            self.apply(build)
        self.assertEqual(str(failure.exception.detail['code']), 'planning_build_profile_stale')

    def test_source_activity_duration_and_logic_survive_without_profile_expansion(self):
        self.file.extracted_text = 'Task|Duration (working days)\nQualify supplier|7\nInspect excavation|3\n'
        self.file.category = 'other'
        self.file.file.save('timing.csv', ContentFile(self.file.extracted_text.encode()), save=True)
        self.file.save(update_fields=['category', 'extracted_text'])
        self.graph = refresh_evidence_graph(self.project, self.owner)
        snapshot = evidence_graph_snapshot(self.project)
        source_ids = sorted({fact['entity_id'] for fact in snapshot['facts'] if fact['property'] == 'duration'})
        calendar = deepcopy(self.profile.definition['calendar_policy']['snapshot'])
        for entity in source_ids:
            for prop in ('identity', 'duration'):
                self.decision(self.graph.nodes.get(current=True, kind='fact', entity_id=entity, property=prop))
            for prop, value in [('calendar', calendar), ('constraints', []), ('activity_type', 'task'),
                                ('dependencies', [] if entity == source_ids[0] else [{'predecessor_id': source_ids[0], 'type': 'FS', 'lag': 2, 'lag_unit': 'working_days'}])]:
                self.decision(self.graph.nodes.get(current=True, kind='fact', entity_id=entity, property=prop), 'correct', value)
        for prop, value in [('project_start', '2026-11-02'), ('project_finish', '2026-12-31'), ('scope_complete', True)]:
            fact = self.graph.nodes.filter(current=True, kind='fact', entity_id='project', property=prop).exclude(status='superseded').first()
            if fact.status != 'accepted':
                self.decision(fact, 'correct', value)
        build = self.preview(deliverable_entity_ids=[], independent_entity_ids=[])
        self.assertTrue(serialize_planning_build(build)['ready_to_apply'], build.issues)
        self.assertEqual(len(build.plan['activities']), 2)
        self.assertEqual(sorted(row['duration']['value'] for row in build.plan['activities']), [3, 7])
        self.assertTrue(all(row['kind'] == 'source_activity' for row in build.plan['activities']))
        self.assertEqual(build.plan['relationships'][0]['lag'], 2)
        version = self.apply(build)
        self.assertEqual(version.relationships.get().lag_days, 2)
        self.assertEqual([issue for issue in build_input_validation(version) if issue['severity'] == 'error'], [])

    def test_register_dimensions_are_exact_cells_and_blank_rows_do_not_inherit_evidence(self):
        self.file.extracted_text = ('SL. NO.\tPHASE\tDISCIPLINE\tPACKAGE\tAREA\tDOCUMENT TITLE\n'
            '1\tExecution phase II\tCOMMISSIONING & HANDOVER\tVendor package 7\tNorth Area\tTurnover dossier\n'
            '2\t\t\t\t\tAcceptance certificate\n')
        self.file.file.save('dimensions.tsv', ContentFile(self.file.extracted_text.encode()), save=True)
        self.file.save(update_fields=['extracted_text'])
        self.graph = refresh_evidence_graph(self.project, self.owner)
        dimensions = list(self.graph.nodes.filter(current=True, kind='fact', property__in=['phase', 'discipline', 'package', 'area']))
        self.assertEqual(len(dimensions), 4)
        expected = {'phase': ('Execution phase II', 2), 'discipline': ({'name': 'COMMISSIONING & HANDOVER'}, 3),
                    'package': ({'name': 'Vendor package 7'}, 4), 'area': ('North Area', 5)}
        for fact in dimensions:
            self.assertEqual(fact.entity_name, 'Turnover dossier')
            self.assertEqual(fact.value, expected[fact.property][0])
            self.assertEqual(fact.sources[0]['locator']['column'], expected[fact.property][1])
            self.assertEqual(fact.sources[0]['locator']['line'], 2)
            self.assertTrue(fact.sources[0]['quote_verified'])
            self.assertEqual(fact.status, 'detected')
            self.decision(fact)
        for fact in list(self.graph.nodes.filter(current=True, kind='fact', property='identity')):
            self.decision(fact)
        profile = create_profile(self.project, self.owner, {
            'code': 'DIMENSION-POLICY', 'name': 'Explicit column WBS', 'workflow_template_id': self.workflow.pk,
            'final_gate_label': 'IFT / IFM', 'wbs_convention': {'levels': ['project', 'phase', 'discipline', 'package', 'area', 'deliverable'], 'code_separator': '.'},
            'calendar_policy': {'mode': 'project_calendar', 'calendar_id': self.calendar.pk},
            'progress_policy': {'mode': 'workflow_weights'}, 'resource_policy': {'mode': 'workflow_roles'},
        })
        decide_profile(self.project, self.owner, profile.pk, 'propose', revision=1, reason='Review exact dimensions.')
        decide_profile(self.project, self.owner, profile.pk, 'approve', revision=2, reason='Approved explicit WBS.')
        select_profile(self.project, self.owner, profile_id=profile.pk, selection_revision=1, reason='Use exact source cells.')
        self.graph.refresh_from_db()
        scope = planning_build_collection(self.project, self.owner)['options']['deliverables']
        build = preview_planning_build(self.project, self.owner, evidence_revision=self.graph.revision,
            profile_selection_revision=2, options={'independent_entity_ids': [row['entity_id'] for row in scope]}, reason='Review exact WBS grouping.')
        wbs_values = {row['name'] for row in build.plan['wbs']}
        self.assertTrue({'Execution phase II', 'COMMISSIONING & HANDOVER', 'Vendor package 7', 'North Area'} <= wbs_values)
        missing = [issue for issue in build.issues if issue['code'] == 'wbs_dimension_not_specified']
        self.assertEqual({issue['field'] for issue in missing}, {'phase', 'discipline', 'package', 'area'})
        self.assertFalse(serialize_planning_build(build)['ready_to_apply'])

    def test_explicit_document_numbers_do_not_create_duplicate_phantom_schedule_scope(self):
        self.file.extracted_text = ('DOCUMENT NUMBER|REVISION|DISCIPLINE|DOCUMENT TITLE\n'
            'PK-001|A|PROCUREMENT|Supplier inspection dossier\n'
            'PK-002|B|COMMISSIONING|Control room acceptance record\n')
        self.file.file.save('numbered.csv', ContentFile(self.file.extracted_text.encode()), save=True)
        self.file.save(update_fields=['extracted_text'])
        self.graph = refresh_evidence_graph(self.project, self.owner)
        for fact in list(self.graph.nodes.filter(current=True, kind='fact', property__in=['identity', 'discipline'])):
            self.decision(fact)
        options = planning_build_collection(self.project, self.owner)['options']
        self.assertEqual(len(options['deliverables']), 2)
        self.assertEqual(options['source_activities'], [])
        self.assertEqual(self.graph.nodes.filter(current=True, kind='fact', property='identity').count(), 2)

    def test_approved_interdeliverable_rule_requires_exact_scope_binding(self):
        profile = create_profile(self.project, self.owner, {
            'code': 'BOUND-RELEASE', 'name': 'Two package release', 'workflow_template_id': self.workflow.pk,
            'dependency_template_id': self.dependencies.pk, 'approved_dependency_rule_ids': [self.link.pk],
            'final_gate_label': 'IFT / IFM', 'wbs_convention': {'levels': ['project', 'deliverable'], 'code_separator': '.'},
            'calendar_policy': {'mode': 'project_calendar', 'calendar_id': self.calendar.pk},
            'progress_policy': {'mode': 'workflow_weights'}, 'resource_policy': {'mode': 'workflow_roles'},
        })
        decide_profile(self.project, self.owner, profile.pk, 'propose', revision=1, reason='Review this explicit release logic.')
        decide_profile(self.project, self.owner, profile.pk, 'approve', revision=2, reason='Approved exact release gates.')
        select_profile(self.project, self.owner, profile_id=profile.pk, selection_revision=1, reason='Use selected dependency.')
        self.graph.refresh_from_db()
        left, right = [row['entity_id'] for row in self.scope]
        def build(options):
            return preview_planning_build(self.project, self.owner, evidence_revision=self.graph.revision,
                profile_selection_revision=2, options=options, reason='Reviewed exact two-package applicability.')
        unbound = build({'independent_entity_ids': [left]})
        self.assertIn('dependency_rule_binding_missing', {issue['code'] for issue in unbound.issues})
        options = {'independent_entity_ids': [left], 'dependency_bindings': {'PKG-A': left, 'PKG-B': right}}
        bound = build(options)
        self.assertTrue(serialize_planning_build(bound)['ready_to_apply'], bound.issues)
        activities = {row['id']: row for row in bound.plan['activities']}
        links = [row for row in bound.plan['relationships'] if activities[row['predecessor_id']]['source_entity_id'] != activities[row['successor_id']]['source_entity_id']]
        self.assertEqual(len(links), 1)
        link = links[0]
        self.assertEqual((activities[link['predecessor_id']]['workflow_stage_code'], activities[link['successor_id']]['workflow_stage_code']), ('FINAL_ISSUE', 'IFR'))
        self.assertEqual((link['type'], link['lag'], link['lag_unit']), ('FS', 2, 'working_days'))
        self.assertEqual(link['lineage']['type'], 'approved_planning_rule')
        self.assertIn(f':rule:{self.link.pk}', link['lineage']['rule_id'])
        version = self.apply(bound)
        self.assertEqual(version.relationships.count(), 9)
        self.assertEqual([issue for issue in build_input_validation(version) if issue['severity'] == 'error'], [])
        conflicting = build({**options, 'independent_entity_ids': [left, right]})
        self.assertIn('independence_conflict', {issue['code'] for issue in conflicting.issues})
