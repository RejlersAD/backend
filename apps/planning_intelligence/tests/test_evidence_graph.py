"""Knowledge is persisted, reviewed and invalidated independently of a schedule."""
from copy import deepcopy
from datetime import date
from hashlib import sha256
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.db import DatabaseError, connection, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext

from ..evidence_models import EvidenceDecision, EvidenceDocumentVersion, EvidenceGraph, EvidenceNode
from ..models import PlanningFile, PlanningProject
from ..services.evidence_graph import (
    EvidenceError, _knowledge, _source, accepted_document_plan, evidence_graph_snapshot,
    evidence_review, materialize_accepted_plan, record_evidence_decision, refresh_evidence_graph, validate_schedule_inputs,
)
from ..services.evidence_schema import network_issues, validate_value
from . import test_document_evidence_api as api_fixture


class EvidenceGraphTests(TestCase):
    def setUp(self):
        api_fixture.DocumentEvidenceAPITests.setUp(self)
        self.file.file.save('requirements.csv', ContentFile(b'Preserved original file bytes'), save=True)

    def refresh(self):
        self.graph = refresh_evidence_graph(self.project, self.user)
        return self.graph

    def test_exact_locator_quote_is_verified_instead_of_normalized_context(self):
        self.refresh()
        document = self.graph.documents.get()
        quote = 'Qualify supplier|7'
        start = document.extracted_text.index(quote)
        source = _source(document, {'excerpt': 'Normalized display context that differs from the exact row.',
                                   'locator': {'quote': quote, 'line': 2, 'character_start': start, 'character_end': start + len(quote)}})
        self.assertTrue(source['quote_verified'])
        self.assertEqual(source['excerpt'], quote)
        self.assertIn('Normalized display context', source['context_excerpt'])
        source = _source(document, {'excerpt': quote, 'locator': {
            'quote': quote, 'line': 2, 'character_start': start + 1, 'character_end': start + len(quote) + 1}})
        self.assertFalse(source['quote_verified'])

    def fact(self, prop, *, entity=None):
        rows = self.graph.nodes.filter(current=True, kind='fact', property=prop)
        if entity:
            rows = rows.filter(entity_id=entity)
        return rows.exclude(entity_id='project').first() if entity is None else rows.first()

    def decide(self, fact, action='accept', **data):
        self.graph.refresh_from_db()
        return record_evidence_decision(self.project, self.user, {
            'revision': self.graph.revision, 'fact_id': str(fact.pk), 'action': action,
            'reason': 'Reviewed the exact project requirement.', **data})

    def test_refresh_is_idempotent_read_is_side_effect_free_and_original_bytes_are_hashed(self):
        before = deepcopy(self.project.simple_planning_state)
        graph = self.refresh()
        count, revision = graph.nodes.count(), graph.revision
        self.refresh()
        self.assertEqual(self.graph.revision, revision)
        self.assertEqual(self.graph.nodes.count(), count)
        self.assertEqual(self.graph.documents.count(), 1)
        doc = self.graph.documents.get()
        self.assertEqual(doc.file_sha256, sha256(b'Preserved original file bytes').hexdigest())
        self.assertNotEqual(doc.file_sha256, doc.text_sha256)
        with CaptureQueriesContext(connection) as queries:
            review = evidence_review(self.project)
        self.assertFalse([query for query in queries if query['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertFalse(review['readiness']['calculation']['ready'])
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)

    def test_correction_records_actor_and_reason_without_rewriting_source(self):
        self.refresh()
        node = self.fact('duration')
        original = deepcopy(node.value)
        decision = self.decide(node, 'correct', value={'value': 12, 'unit': 'working_days'})
        node.refresh_from_db()
        self.assertEqual(node.value, original)
        self.assertEqual(node.status, 'superseded')
        corrected = self.graph.nodes.get(rule__decision_id=str(decision.pk))
        self.assertEqual(corrected.provenance_type, 'approved_planning_input')
        self.assertEqual(corrected.status, 'accepted')
        self.assertEqual(decision.actor, self.user)
        self.assertTrue(decision.reason)

    def test_explicit_refresh_rechecks_restored_original_storage(self):
        with patch.object(self.file.file.storage, 'open', side_effect=OSError('Storage temporarily unavailable')):
            self.refresh()
        original_revision = self.graph.revision
        self.assertTrue(any(item['code'] == 'source_integrity_unverified' for item in _knowledge(self.graph)[2]))
        self.refresh()
        self.assertGreater(self.graph.revision, original_revision)
        self.assertEqual(self.graph.documents.count(), 2)
        self.assertFalse(any(item['code'] == 'source_integrity_unverified' for item in _knowledge(self.graph)[2]))

    def test_wrong_locator_does_not_validate_a_quote_found_elsewhere(self):
        self.refresh()
        doc = self.graph.documents.get()
        ref = {'locator': {'line': 1}, 'excerpt': 'Qualify supplier|7'}
        self.assertFalse(_source(doc, ref)['quote_verified'])
        ref['locator']['line'] = 2
        self.assertTrue(_source(doc, ref)['quote_verified'])
        ref['locator'] = {'made_up_page': 1}
        self.assertFalse(_source(doc, ref)['quote_verified'])

    def test_rejecting_required_inputs_does_not_remove_the_blocker(self):
        self.refresh()
        node = self.fact('scope_complete', entity='project')
        self.decide(node, 'reject')
        _, _, issues = _knowledge(self.graph)
        self.assertTrue(any(item.get('field') == 'scope_complete' for item in issues))

    def test_source_revision_invalidates_prior_decisions_and_keeps_old_assertions(self):
        self.refresh()
        node = self.fact('identity')
        self.decide(node)
        old_id = node.pk
        self.file.extracted_text += 'Approve permit|2\n'
        self.file.save(update_fields=['extracted_text', 'updated_at'])
        self.assertTrue(evidence_review(self.project)['readiness']['stale'])
        with self.assertRaises(EvidenceError):
            self.decide(node, 'reject')
        self.refresh()
        node.refresh_from_db()
        self.assertFalse(node.current)
        self.assertTrue(EvidenceNode.objects.filter(pk=old_id).exists())
        self.assertEqual(self.graph.documents.count(), 2)

    def test_conflicts_require_recorded_resolution_not_high_confidence(self):
        self.refresh()
        node = self.fact('duration')
        from uuid import uuid4
        other = EvidenceNode.objects.create(id=uuid4(), graph=self.graph, kind='fact', entity_id=node.entity_id,
            entity_name=node.entity_name, property='duration', value={'value': 99, 'unit': 'working_days'},
            provenance_type='document_evidence', sources=node.sources,
            validation={'quote_verified': True}, confidence={'heuristic_score': 1})
        _, _, issues = _knowledge(self.graph)
        self.assertTrue(any(item['code'] == 'conflicting_values' for item in issues))
        self.decide(node)
        other.refresh_from_db()
        self.assertEqual(other.status, 'rejected')
        self.assertEqual(self.graph.decisions.count(), 1)

    def test_new_analysis_and_bulk_fact_reviews_invalidate_unchanged_uploads(self):
        from django.utils import timezone
        from ..intelligence_models import DocumentIntelligenceRun, IntelligenceFact
        self.refresh()
        run = DocumentIntelligenceRun.objects.create(project=self.project, status='succeeded',
            started_at=timezone.now(), engine_version='test-evidence-version')
        fact = IntelligenceFact.objects.create(run=run, source_file=self.file, fact_type='requirement',
            key='source-requirement', value='Qualify supplier', source_excerpt='Qualify supplier|7',
            source_locator={'line': 2})
        self.assertTrue(evidence_review(self.project)['readiness']['stale'])
        self.refresh()
        self.assertFalse(evidence_review(self.project)['readiness']['stale'])
        assertion = self.graph.nodes.get(current=True, property='requirement')
        self.assertEqual(assertion.sources[0]['extraction_run_id'], str(run.pk))
        self.assertEqual(assertion.sources[0]['extraction_method'], 'deterministic')
        IntelligenceFact.objects.filter(pk=fact.pk).update(status='rejected')
        self.assertTrue(evidence_review(self.project)['readiness']['stale'])
        self.refresh()
        self.assertFalse(self.graph.nodes.filter(current=True, property='requirement').exists())

    def test_duplicate_names_remain_distinct_and_link_does_not_merge(self):
        self.file.extracted_text = 'Task|Duration (working days)\nInspect foundations|3\nInspect foundations|8\n'
        self.file.save(update_fields=['extracted_text', 'updated_at'])
        self.refresh()
        identities = list(self.graph.nodes.filter(kind='fact', property='identity'))
        self.assertEqual(len(identities), 2)
        self.assertNotEqual(identities[0].entity_id, identities[1].entity_id)
        self.decide(identities[0], 'link', target_fact_id=str(identities[1].pk))
        self.assertEqual(self.graph.nodes.filter(kind='fact', property='identity').count(), 2)
        self.assertTrue(any(item['code'] == 'linked_source_conflict' for item in _knowledge(self.graph)[2]))

    def test_missing_values_cannot_be_accepted_or_turn_independent_without_a_decision(self):
        self.refresh()
        node = self.fact('dependencies')
        self.assertIsNone(node.value)
        with self.assertRaises(EvidenceError):
            self.decide(node)
        self.decide(node, 'correct', value=[])
        self.assertEqual(_knowledge(self.graph)[1][node.entity_id]['dependencies'], [])

    def test_repeated_corrections_survive_unrelated_source_refresh(self):
        self.refresh()
        first = self.fact('duration')
        one = self.decide(first, 'correct', value={'value': 12, 'unit': 'working_days'})
        second = self.graph.nodes.get(rule__decision_id=str(one.pk))
        two = self.decide(second, 'correct', value={'value': 13, 'unit': 'working_days'})
        # Different source added; original assertion remains the exact same version.
        file = PlanningFile.objects.create(project=self.project, original_filename='notes.txt', category='other', parse_status='done', extracted_text='Unrelated note')
        file.file.save('notes.txt', ContentFile(b'Unrelated note'), save=True)
        self.refresh()
        corrected = self.graph.nodes.get(rule__decision_id=str(two.pk))
        self.assertTrue(corrected.current)
        self.assertEqual(corrected.status, 'accepted')
        self.assertEqual(_knowledge(self.graph)[1][first.entity_id]['duration']['value'], 13)

    def test_api_is_project_scoped_revision_guarded_and_requires_reason(self):
        self.refresh()
        node = self.fact('identity')
        url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/evidence-review/decisions/'
        response = self.client.post(url, {'revision': self.graph.revision, 'action': 'accept', 'fact_id': str(node.pk)}, format='json')
        self.assertEqual(response.status_code, 400)
        response = self.client.post(url, {'revision': 0, 'action': 'accept', 'fact_id': str(node.pk), 'reason': 'Reviewed'}, format='json')
        self.assertEqual(response.status_code, 409)
        from apps.users.models import User
        from .test_business_approval_gates import grant_test_approval
        other = User.objects.create_user(username='graph-other', email='graph-other@example.test')
        grant_test_approval((other,))
        self.client.force_authenticate(other)
        self.assertEqual(self.client.get(url.replace('decisions/', '')).status_code, 404)

    def test_source_versions_decisions_and_assertions_resist_bulk_update(self):
        if connection.vendor != 'postgresql':
            self.skipTest('Database immutability trigger is PostgreSQL-specific.')
        self.refresh()
        node = self.fact('identity')
        decision = self.decide(node)
        operations = [lambda: EvidenceDecision.objects.filter(pk=decision.pk).update(reason='tampered'),
                      lambda: EvidenceDocumentVersion.objects.filter(graph=self.graph).update(extracted_text='tampered'),
                      lambda: EvidenceNode.objects.filter(pk=node.pk).update(value='tampered')]
        for operation in operations:
            with self.assertRaises(DatabaseError), transaction.atomic():
                operation()

    def test_no_calendar_duration_or_relationship_defaults_and_cycles_are_explicit(self):
        self.assertIsNotNone(validate_value('duration', {'value': 4}))
        self.assertIsNotNone(validate_value('calendar', {}))
        self.assertIsNotNone(validate_value('dependencies', [{'predecessor_id': 'A'}]))
        cycle = {'A': {'dependencies': [{'predecessor_id': 'B'}]}, 'B': {'dependencies': [{'predecessor_id': 'A'}]}}
        self.assertTrue(any(item['code'] == 'dependency_cycle' for item in network_issues(cycle)))

    def test_accepted_projection_is_deterministic_and_preserves_property_provenance(self):
        self.refresh()
        identity = self.fact('identity')
        self.decide(identity)
        first, second = accepted_document_plan(self.project), accepted_document_plan(self.project)
        self.assertEqual(first, second)
        row = next(item for item in first['activities'] if item['id'] == identity.entity_id)
        self.assertEqual(row['property_provenance']['identity'], str(identity.pk))
        self.assertIsNone(row['duration'])
        self.assertFalse(first['ready_for_calculation'])

    def approve_inputs(self):
        self.project.effective_date, self.project.planned_end_date = date(2026, 9, 1), date(2026, 9, 30)
        self.project.save(update_fields=['effective_date', 'planned_end_date'])
        self.refresh()
        calendar = {'working_weekdays': [0, 1, 2, 3, 4], 'hours_per_day': 8, 'timezone': 'Asia/Dubai', 'exceptions': []}
        for node in list(self.graph.nodes.filter(kind='fact')):
            if node.property in {'identity', 'duration'}:
                self.decide(node)
            elif node.property in {'project_start', 'project_finish'}:
                self.decide(node, 'correct', value=node.value)
            elif node.property == 'calendar':
                self.decide(node, 'correct', value=calendar)
            elif node.property == 'activity_type':
                self.decide(node, 'correct', value='task')
            elif node.property in {'dependencies', 'constraints'}:
                self.decide(node, 'correct', value=[])
            elif node.property == 'scope_complete':
                self.decide(node, 'correct', value=True)
        self.graph.refresh_from_db()
        self.assertTrue(evidence_review(self.project)['readiness']['calculation']['ready'], evidence_review(self.project)['issues'])

    def test_accepted_inputs_materialize_idempotently_and_calculate_reproducibly(self):
        from ..models import ScheduleVersion
        from ..services.cpm import calculate_schedule_version
        self.approve_inputs()
        before = deepcopy(self.project.simple_planning_state)
        result = materialize_accepted_plan(self.project, self.user, revision=self.graph.revision)
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        self.assertFalse(result['calculated'])
        self.assertEqual(version.status, 'draft')
        self.assertEqual(version.activities.count(), 2)
        self.assertEqual(validate_schedule_inputs(version), [])
        repeat = materialize_accepted_plan(self.project, self.user, revision=self.graph.revision)
        self.assertFalse(repeat['created'])
        self.assertEqual(repeat['schedule_version_id'], version.pk)
        calculate_schedule_version(version, requested_by=self.user)
        dates = list(version.activities.order_by('pk').values_list('planned_start', 'planned_finish', 'total_float_days'))
        calculate_schedule_version(version, requested_by=self.user)
        self.assertEqual(list(version.activities.order_by('pk').values_list('planned_start', 'planned_finish', 'total_float_days')), dates)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(version.baselines.exists())

    def test_deleting_or_duplicating_accepted_scope_blocks_schedule_calculation(self):
        from ..models import ScheduleVersion
        self.approve_inputs()
        result = materialize_accepted_plan(self.project, self.user, revision=self.graph.revision)
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        activity = version.activities.first()
        activity.is_deleted = True
        activity.save(update_fields=['is_deleted'])
        self.assertTrue(any(item['code'] == 'accepted_scope_mismatch' for item in validate_schedule_inputs(version)))
        with self.assertRaises(EvidenceError):
            materialize_accepted_plan(self.project, self.user, revision=self.graph.revision)
        activity.is_deleted = False
        activity.save(update_fields=['is_deleted'])
        activity.pk = None
        activity.external_id = 'EXTRA'
        activity.save()
        self.assertTrue(any(item['code'] == 'accepted_scope_mismatch' for item in validate_schedule_inputs(version)))

    def test_register_link_to_excluded_activity_does_not_satisfy_scope(self):
        from uuid import uuid4
        self.approve_inputs()
        activity = self.graph.nodes.filter(kind='fact', property='identity', status='accepted').first()
        register = EvidenceNode.objects.create(id=uuid4(), graph=self.graph, kind='fact',
            entity_id='register:required-deliverable', entity_name='Required deliverable',
            property='identity', value='Required deliverable')
        EvidenceNode.objects.create(id=uuid4(), graph=self.graph, kind='scope_link',
            entity_id=register.entity_id, entity_name=register.entity_name, value={'source_register': True})
        decision = self.decide(register, 'correct', value=register.value)
        accepted_register = self.graph.nodes.get(rule__decision_id=str(decision.pk))
        self.decide(accepted_register, 'link', target_fact_id=str(activity.pk))
        self.assertFalse(any(item['code'] == 'scope_link_missing' for item in _knowledge(self.graph)[2]))
        self.decide(activity, 'reject')
        self.assertTrue(any(item['code'] == 'scope_link_missing' and item['entity_id'] == register.entity_id
                            for item in _knowledge(self.graph)[2]))
        self.assertFalse(evidence_review(self.project)['readiness']['calculation']['ready'])
        # An explicit exclusion of the corrected identity must not be defeated
        # by the preserved, superseded source assertion.
        self.decide(accepted_register, 'reject')
        self.assertFalse(any(item.get('entity_id') == register.entity_id for item in _knowledge(self.graph)[2]))
        self.assertNotIn(register.entity_id, _knowledge(self.graph)[1])

    def test_calendar_precision_is_preserved_instead_of_rounded_by_storage(self):
        from ..models import ScheduleVersion, WorkCalendar
        self.approve_inputs()
        calendar = {'working_weekdays': [0, 1, 2, 3, 4], 'hours_per_day': 8.125,
                    'timezone': 'Asia/Dubai', 'exceptions': []}
        for node in list(self.graph.nodes.filter(kind='fact', property='calendar', current=True, status='accepted')):
            self.decide(node, 'correct', value=calendar)
        self.graph.refresh_from_db()
        before_versions = ScheduleVersion.objects.count()
        before_calendars = WorkCalendar.objects.count()
        with self.assertRaises(EvidenceError) as error:
            materialize_accepted_plan(self.project, self.user, revision=self.graph.revision)
        self.assertEqual(error.exception.payload['code'], 'calendar_precision_unsupported')
        self.assertEqual(ScheduleVersion.objects.count(), before_versions)
        self.assertEqual(WorkCalendar.objects.count(), before_calendars)
        self.assertEqual(_knowledge(self.graph)[1]['project']['calendar']['hours_per_day'], 8.125)

    def test_project_read_only_reviewer_cannot_record_planning_decisions(self):
        from apps.core.project_models import ProjectMember
        from apps.users.models import User
        from .test_business_approval_gates import grant_test_approval
        self.refresh()
        reviewer = User.objects.create_user(username='evidence-review-only', email='evidence-review-only@example.test')
        grant_test_approval((reviewer,))
        ProjectMember.objects.create(project=self.project.enterprise_project, user=reviewer, role='reviewer')
        self.client.force_authenticate(reviewer)
        url = f'/api/v1/planning-intelligence/projects/{self.project.pk}/evidence-review/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.data['permissions']['can_review'])
        self.assertEqual(self.client.post(url + 'refresh/', {}, format='json').status_code, 403)
        self.assertEqual(self.client.post(url + 'materialize/', {'revision': self.graph.revision}, format='json').status_code, 403)

    def test_postgres_protects_approved_baseline_snapshot_from_bulk_update(self):
        if connection.vendor != 'postgresql':
            self.skipTest('PostgreSQL trigger coverage.')
        from django.utils import timezone
        from ..models import ScheduleBaseline, ScheduleVersion
        self.approve_inputs()
        result = materialize_accepted_plan(self.project, self.user, revision=self.graph.revision)
        version = ScheduleVersion.objects.get(pk=result['schedule_version_id'])
        baseline = ScheduleBaseline.objects.create(schedule=version.schedule, source_version=version,
            name='Immutable snapshot', snapshot={'source_versions': ['original']}, approved_by=self.user, approved_at=timezone.now())
        with self.assertRaises(DatabaseError), transaction.atomic():
            ScheduleBaseline.objects.filter(pk=baseline.pk).update(snapshot={'source_versions': ['changed']})
