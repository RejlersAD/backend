"""Bulk decisions preserve originals, prior reviews and business schedules."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
import tempfile
from unittest.mock import patch
from uuid import uuid4

from django.core.files.base import ContentFile
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext

from apps.core.project_models import Project
from apps.users.models import User
from ..evidence_models import EvidenceDecision, EvidenceDocumentVersion, EvidenceEdge, EvidenceGraph, EvidenceNode
from ..models import PlanningFile, PlanningJob, PlanningProject, ScheduleVersion, ScheduleBaseline, WorkCalendar
from ..services.evidence_bulk import bulk_review_summary, issue_group_key, run_bulk_evidence_review
from ..services.evidence_graph import EvidenceError, _source, input_fingerprint, record_evidence_decision, source_manifest
from ..services.evidence_schema import RULE_VERSION, SCHEMA_VERSION, validate_value
from .test_scheduling_engine import grant_planning_test_actions


AI = {'available': True, 'provider': 'anthropic', 'model': 'project-test-model', 'reason': ''}


class BulkEvidenceTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        setting = override_settings(MEDIA_ROOT=self.media.name)
        setting.enable()
        self.addCleanup(setting.disable)
        self.actor = User.objects.create_user(username='bulk-planner', email='bulk@example.test')
        grant_planning_test_actions((self.actor,), ('read', 'update', 'create'))
        enterprise = Project.objects.create(name='Source review', code='BULK-1', owner=self.actor)
        self.project = PlanningProject.objects.create(name='Source review', created_by=self.actor, enterprise_project=enterprise,
            simple_planning_state={'revision': 7, 'tasks': []})

    def prepare(self, rows, *, document_text=None, raw_bytes=None, filename='source.txt'):
        """Rows: (entity, property, value[, exact quote]); no source value edits."""
        quotes = [row[3] if len(row) > 3 else json.dumps({row[1]: row[2]}, separators=(',', ':')) for row in rows]
        text = document_text if document_text is not None else '\n'.join(quotes)
        raw_bytes = text.encode() if raw_bytes is None else raw_bytes
        file = PlanningFile(project=self.project, original_filename=filename, category='other',
                            parse_status='done', extracted_text=text, uploaded_by=self.actor)
        file.file.save(filename, ContentFile(raw_bytes), save=True)
        self.file = file
        self.graph = EvidenceGraph.objects.create(project=self.project, revision=1,
            schema_version=SCHEMA_VERSION, rule_version=RULE_VERSION,
            source_manifest=source_manifest(self.project), source_fingerprint=input_fingerprint(self.project))
        self.document = EvidenceDocumentVersion.objects.create(id=uuid4(), graph=self.graph, source_file=file,
            filename=file.original_filename, storage_name=file.file.name, extracted_text=text,
            file_sha256=sha256(raw_bytes).hexdigest(), text_sha256=sha256(text.encode()).hexdigest(), integrity_status='verified')
        nodes, position = [], 0
        for (entity, prop, value, *_), quote in zip(rows, quotes):
            if document_text is not None:
                position = text.index(quote)
            source = _source(self.document, {'excerpt': quote, 'locator': {
                'character_start': position, 'character_end': position + len(quote)}})
            node = EvidenceNode(id=uuid4(), graph=self.graph, document_version=self.document, kind='fact',
                entity_id=entity, entity_name=entity, property=prop, value=value, sources=[source],
                provenance_type='document_evidence', status='missing' if value is None else 'detected',
                validation={'quote_verified': True, 'error': validate_value(prop, value), 'schema': SCHEMA_VERSION},
                confidence={'human_validation': 'pending'})
            nodes.append(node)
            position += len(quote) + 1
        EvidenceNode.objects.bulk_create(nodes, batch_size=400)
        return nodes

    def run_review(self, *, mode='verified', revision=None, job=None, callback=None, actor=None):
        return run_bulk_evidence_review(self.project, actor or self.actor,
            {'revision': self.graph.revision if revision is None else revision, 'reason': 'Authorized complete source review.', 'mode': mode},
            job=job, progress_callback=callback)

    @staticmethod
    def choose(groups, project, actor, progress_callback=None):
        group = groups[0]
        fact = group['candidates'][0]
        return {'provider': 'anthropic', 'model': 'project-test-model', 'warnings': [], 'unresolved': [], 'decisions': [{
            'group_key': group['key'], 'fact_id': fact['id'], 'confidence': 'high',
            'reason': 'The exact selected source explicitly states this quantity for the scoped item.',
            'source_fact_ids': [fact['id']],
            'evidence_quotes': [{'fact_id': fact['id'], 'source_index': 0, 'quote': fact['sources'][0]['verbatim']}]}]}

    def test_verified_accepts_grounded_facts_and_keeps_unknowns_without_business_writes(self):
        nodes = self.prepare([('a', 'identity', 'Pump A'), ('a', 'duration', {'value': 7, 'unit': 'working_days'}),
                              ('a', 'calendar', None), ('b', 'duration', {'value': 99, 'unit': 'working_days'}, 'The source says7 working days.')])
        before = deepcopy(self.project.simple_planning_state)
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 2)
        self.assertEqual(result['counts']['unresolved'], 2)
        self.assertFalse(result['review_complete'])
        self.assertFalse(result['calculation_ready'])
        self.assertEqual(result['counts']['total'], 4)
        self.assertEqual(result['count_basis']['accepted_verified'], 'facts')
        self.assertEqual(self.graph.decisions.count(), 2)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertIsNone(self.project.master_schedule_version_id)
        for model in (ScheduleVersion, ScheduleBaseline, WorkCalendar):
            self.assertFalse(model.objects.exists())
        self.assertEqual(self.graph.documents.get().extracted_text, self.document.extracted_text)
        self.assertEqual(EvidenceNode.objects.get(pk=nodes[-1].pk).value, nodes[-1].value)
        self.assertTrue(all(decision.actor_id == self.actor.pk for decision in self.graph.decisions.all()))
        self.assertEqual(issue_group_key({'code': 'missing_input', 'field': 'calendar'}), 'missing_input:calendar')

    def test_typed_table_values_are_verified_against_the_correct_column(self):
        # Header + row are one exact quote; parser row evidence must match the
        # locator, so a number elsewhere in the document cannot establish it.
        row = 'Task|Duration (working days)|Start|Finish\nPump|7|2026-01-06|2026-01-14'
        nodes = self.prepare([('a', 'duration', {'value': 7, 'unit': 'working_days'}, row),
                              ('b', 'duration', {'value': 14, 'unit': 'working_days'}, row)])
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 0)
        self.assertEqual(result['counts']['unresolved'], 2)
        self.assertFalse(self.graph.decisions.exists())

    def test_typed_values_accept_explicit_header_units_and_preserve_date_columns(self):
        row = 'Pump|7|2026-01-06|2026-01-14'
        source = 'Task|Duration (working days)|Start|Finish\n' + row
        self.prepare([('a', 'duration', {'value': 7, 'unit': 'working_days'}, row),
                      ('a', 'start_date', '2026-01-06', row),
                      ('b', 'start_date', '2026-01-14', row)], document_text=source)
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 2)
        self.assertEqual(result['counts']['unresolved'], 1)

    def test_changed_original_bytes_and_wrong_quotes_never_auto_accept(self):
        nodes = self.prepare([('a', 'identity', 'Pump A')])
        with self.file.file.open('wb') as stream:
            stream.write(b'Replaced outside metadata')
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 0)
        self.assertEqual(result['counts']['unresolved'], 1)
        self.assertFalse(self.graph.decisions.exists())
        self.assertEqual(EvidenceNode.objects.get(pk=nodes[0].pk).status, 'detected')

    def test_manual_rejected_entity_and_reviewed_groups_are_preserved(self):
        nodes = self.prepare([('a', 'identity', 'Excluded scope'), ('a', 'duration', {'value': 7, 'unit': 'working_days'}),
                              ('b', 'identity', 'Manual accepted'), ('c', 'identity', 'Unreviewed')])
        record_evidence_decision(self.project, self.actor, {'revision': 1, 'fact_id': str(nodes[0].pk),
            'action': 'reject', 'reason': 'Explicitly excluded scope.'})
        record_evidence_decision(self.project, self.actor, {'revision': 2, 'fact_id': str(nodes[2].pk),
            'action': 'accept', 'reason': 'Previously reviewed exact source.'})
        self.graph.refresh_from_db()
        before = list(self.graph.decisions.order_by('id').values_list('id', 'value', 'reason'))
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 1)
        self.assertEqual(list(self.graph.decisions.filter(pk__in=[row[0] for row in before]).order_by('id').values_list('id', 'value', 'reason')), before)
        self.assertEqual(EvidenceNode.objects.get(pk=nodes[1].pk).status, 'detected')
        self.assertEqual(EvidenceNode.objects.get(pk=nodes[2].pk).confidence['human_validation'], 'accepted')

    @patch('apps.planning_intelligence.services.evidence_bulk_ai.ai_availability', return_value=AI)
    @patch('apps.planning_intelligence.services.evidence_bulk_ai.resolve_conflicts')
    def test_ai_only_selects_existing_verified_conflicts_and_records_its_evidence(self, resolve, availability):
        nodes = self.prepare([('a', 'duration', {'value': 7, 'unit': 'working_days'}),
                              ('a', 'duration', {'value': 9, 'unit': 'working_days'})])
        resolve.side_effect = self.choose
        result = self.run_review(mode='ai_verified')
        self.assertEqual(result['counts']['accepted_ai'], 1)
        self.assertEqual(result['counts']['rejected_alternatives'], 1)
        self.assertEqual(self.graph.decisions.count(), 2)
        selected = self.graph.nodes.get(status='accepted')
        self.assertIn(selected.value, [node.value for node in nodes])
        self.assertEqual(selected.confidence['human_validation'], 'bulk_authorized')
        self.assertIn('evidence_quotes', self.graph.decisions.get(action='accept').reason)
        self.assertIn('project-test-model', self.graph.decisions.get(action='accept').reason)

    @patch('apps.planning_intelligence.services.evidence_bulk_ai.ai_availability', return_value=AI)
    @patch('apps.planning_intelligence.services.evidence_bulk_ai.resolve_conflicts')
    def test_ai_out_of_group_or_false_quote_selection_abstains(self, resolve, availability):
        self.prepare([('a', 'duration', {'value': 7, 'unit': 'working_days'}),
                      ('a', 'duration', {'value': 9, 'unit': 'working_days'})])
        def invalid(groups, project, actor, progress_callback=None):
            result = self.choose(groups, project, actor)
            result['decisions'][0]['evidence_quotes'][0]['quote'] = 'An invented source quote'
            return result
        resolve.side_effect = invalid
        result = self.run_review(mode='ai_verified')
        self.assertEqual(result['counts']['accepted_ai'], 0)
        self.assertEqual(result['counts']['unresolved'], 1)
        self.assertFalse(self.graph.decisions.exists())

    def test_job_replay_uses_immutable_audit_even_if_progress_overwrote_result(self):
        self.prepare([('a', 'identity', 'Pump A')])
        job = PlanningJob.objects.create(project=self.project, requested_by=self.actor, job_type='evidence_bulk')
        first = self.run_review(job=job)
        PlanningJob.objects.filter(pk=job.pk).update(result_data={'phase': 'stale worker progress'})
        repeated = self.run_review(job=job)
        self.assertEqual(first, repeated)
        self.assertEqual(self.graph.decisions.count(), 1)
        self.assertEqual(self.project.audit_events.filter(action='evidence.bulk_reviewed').count(), 1)
        with self.assertRaises(EvidenceError):
            run_bulk_evidence_review(self.project, self.actor, {'revision': 1, 'mode': 'verified', 'reason': 'Different command'}, job=job)

    def test_revision_or_source_change_during_external_work_rolls_back_all_decisions(self):
        self.prepare([('a', 'identity', 'Pump A')])
        def alter(progress):
            if progress['phase'] == 'commit':
                EvidenceGraph.objects.filter(pk=self.graph.pk).update(revision=2)
        with self.assertRaises(EvidenceError) as error:
            self.run_review(callback=alter)
        self.assertEqual(error.exception.payload['code'], 'evidence_revision_conflict')
        self.assertFalse(self.graph.decisions.exists())

    def test_permissions_rechecked_after_external_work(self):
        self.prepare([('a', 'identity', 'Pump A')])
        def revoke(progress):
            if progress['phase'] == 'commit':
                User.objects.filter(pk=self.actor.pk).update(is_active=False)
        with self.assertRaises(EvidenceError) as error:
            self.run_review(callback=revoke)
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(self.graph.decisions.exists())

    def test_same_path_source_replacement_during_review_prevents_any_acceptance(self):
        self.prepare([('a', 'identity', 'Pump A')])
        def replace(progress):
            if progress['phase'] == 'commit':
                with self.file.file.storage.open(self.file.file.name, 'wb') as stream:
                    stream.write(b'A different original document at the same key')
        with self.assertRaises(EvidenceError) as error:
            self.run_review(callback=replace)
        self.assertEqual(error.exception.payload['code'], 'evidence_sources_changed')
        self.assertFalse(self.graph.decisions.exists())

    def test_cached_rbac_profile_revocation_is_rechecked_before_commit(self):
        self.prepare([('a', 'identity', 'Pump A')])
        profile = self.actor.rbac_profile  # Intentionally populate the cache.
        def revoke(progress):
            if progress['phase'] == 'commit':
                type(profile).objects.filter(pk=profile.pk).update(status='inactive')
        with self.assertRaises(EvidenceError) as error:
            self.run_review(callback=revoke)
        self.assertEqual(error.exception.status_code, 403)
        self.assertTrue(User.objects.get(pk=self.actor.pk).is_active)
        self.assertFalse(self.graph.decisions.exists())

    def test_source_metadata_change_during_review_prevents_any_acceptance(self):
        self.prepare([('a', 'identity', 'Pump A')])
        def replace(progress):
            if progress['phase'] == 'commit':
                PlanningFile.objects.filter(pk=self.file.pk).update(extracted_text='Changed extraction')
        with self.assertRaises(EvidenceError) as error:
            self.run_review(callback=replace)
        self.assertEqual(error.exception.payload['code'], 'evidence_revision_conflict')
        self.assertFalse(self.graph.decisions.exists())

    def test_corrected_value_and_original_assertion_are_not_revisited(self):
        nodes = self.prepare([('a', 'identity', 'Original title')])
        record_evidence_decision(self.project, self.actor, {'revision': 1, 'fact_id': str(nodes[0].pk),
            'action': 'correct', 'value': 'Planner corrected title', 'reason': 'Authoritative corrected scope name.'})
        self.graph.refresh_from_db()
        before = list(self.graph.nodes.order_by('id').values('id', 'value', 'status', 'sources'))
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 0)
        self.assertEqual(list(self.graph.nodes.order_by('id').values('id', 'value', 'status', 'sources')), before)
        self.assertEqual(self.graph.decisions.count(), 1)

    def prepare_pdf_candidates(self):
        from ..services.parsers import _extract_pdf
        from ..services.reference_schedule_geometry import parse_reference_schedule_pdf
        from .test_reference_schedule_geometry import pdf_document
        binary = pdf_document([[
            (1, 'summary', 'Original project', 4, 25, '06-Jan-26', '30-Jan-26', 0),
            (2, 'ACT_1', 'Prepare drawing - IFA', 14, 7.5, '07-Jan-26', '22-Jan-26', 0),
        ]])
        text = _extract_pdf(BytesIO(binary))
        self.prepare([], document_text=text, raw_bytes=binary, filename='schedule.pdf')
        row = parse_reference_schedule_pdf(BytesIO(binary))['activities'][0]
        values = {'identity': row['title'], 'start_date': row['planned_start_date'],
                  'finish_date': row['planned_finish_date'], 'duration': {'value': 7.5, 'unit': None}}
        source = _source(self.document, {'excerpt': row['raw_text'], 'locator': row['source_locator']})
        self.assertFalse(source['quote_verified'])
        nodes = [EvidenceNode(id=uuid4(), graph=self.graph, document_version=self.document,
            kind='fact', entity_id='ACT_1', entity_name=row['title'], property=prop, value=value,
            sources=[deepcopy(source)], provenance_type='document_evidence', status='detected',
            validation={'quote_verified': False, 'error': validate_value(prop, value), 'schema': SCHEMA_VERSION},
            confidence={'human_validation': 'pending'}) for prop, value in values.items()]
        EvidenceNode.objects.bulk_create(nodes)
        return nodes

    def test_original_pdf_repairs_append_new_facts_without_mutating_assertions_or_inventing_units(self):
        originals = self.prepare_pdf_candidates()
        assertions = {row.pk: (deepcopy(row.value), deepcopy(row.sources)) for row in originals}
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 3)
        self.assertEqual(result['counts']['citations_repaired'], 3)
        self.assertEqual(result['counts']['unresolved'], 1)
        self.assertFalse(result['calculation_ready'])
        self.assertEqual(self.graph.nodes.count(), 7)
        self.assertEqual(self.graph.decisions.count(), 3)
        edges = EvidenceEdge.objects.filter(graph=self.graph, relationship='supersedes')
        self.assertEqual(edges.count(), 3)
        for original in originals:
            original.refresh_from_db()
            self.assertEqual((original.value, original.sources), assertions[original.pk])
            if original.property == 'duration':
                self.assertEqual(original.status, 'detected')
                self.assertIsNone(original.value['unit'])
                continue
            self.assertEqual(original.status, 'superseded')
            replacement = edges.get(target=original).source
            self.assertEqual(replacement.value, original.value)
            self.assertEqual(replacement.status, 'accepted')
            self.assertEqual(replacement.provenance_type, 'document_evidence')
            self.assertTrue(replacement.validation['quote_verified'])
            self.assertEqual(replacement.rule['original_fact_id'], str(original.pk))
            self.assertEqual(replacement.rule['proofs'][0]['file_sha256'], self.document.file_sha256)
            citation = replacement.sources[0]
            start, end = [citation['locator'][key] for key in ('character_start', 'character_end')]
            self.assertEqual(self.document.extracted_text[start:end], citation['verbatim'])
            self.assertTrue(_source(self.document, {'excerpt': citation['verbatim'], 'locator': citation['locator']})['quote_verified'])
            self.assertTrue(self.graph.decisions.filter(fact=replacement, action='accept', actor=self.actor).exists())
        repeated = self.run_review(revision=result['result_revision'])
        self.assertEqual(repeated['counts']['citations_repaired'], 0)
        self.assertEqual(self.graph.nodes.count(), 7)
        self.assertEqual(self.graph.decisions.count(), 3)
        self.assertFalse(ScheduleVersion.objects.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_pdf_repair_is_not_persisted_when_original_bytes_change_before_commit(self):
        originals = self.prepare_pdf_candidates()
        def replace(progress):
            if progress['phase'] == 'commit':
                with self.file.file.storage.open(self.file.file.name, 'wb') as stream:
                    stream.write(b'A changed PDF at the same object key')
        with self.assertRaises(EvidenceError) as error:
            self.run_review(callback=replace)
        self.assertEqual(error.exception.payload['code'], 'evidence_sources_changed')
        self.assertEqual(self.graph.nodes.count(), len(originals))
        self.assertFalse(self.graph.decisions.exists())
        self.assertFalse(EvidenceEdge.objects.filter(graph=self.graph).exists())

    def test_outsider_even_with_module_permission_cannot_apply(self):
        self.prepare([('a', 'identity', 'Pump A')])
        other = User.objects.create_user(username='bulk-outsider', email='bulk-outsider@example.test')
        grant_planning_test_actions((other,), ('read', 'update'))
        with self.assertRaises(EvidenceError) as error:
            self.run_review(actor=other)
        self.assertEqual(error.exception.status_code, 403)
        self.assertFalse(self.graph.decisions.exists())

    def test_cycles_and_dangling_dependencies_stay_unresolved(self):
        rows = []
        for entity, pred in [('a', 'b'), ('b', 'a'), ('c', 'missing')]:
            rows.extend([(entity, 'identity', entity), (entity, 'duration', {'value': 1, 'unit': 'working_days'}),
                         (entity, 'dependencies', [{'predecessor_id': pred, 'type': 'FS', 'lag': 0, 'lag_unit': 'working_days'}])])
        self.prepare(rows)
        result = self.run_review()
        self.assertEqual(result['counts']['accepted_verified'], 6)
        self.assertFalse(self.graph.nodes.filter(property='dependencies', status='accepted').exists())
        self.assertEqual(result['counts']['unresolved'], 3)
        self.assertFalse(result['calculation_ready'])

    @patch('apps.planning_intelligence.services.evidence_bulk_ai.ai_availability', return_value=AI)
    @patch('apps.planning_intelligence.services.evidence_bulk_ai.resolve_conflicts')
    def test_linked_entities_never_select_cross_source_precedence(self, resolve, availability):
        nodes = self.prepare([('register', 'identity', 'Pump'), ('schedule', 'identity', 'Pump'),
            ('register', 'duration', {'value': 7, 'unit': 'working_days'}),
            ('schedule', 'duration', {'value': 9, 'unit': 'working_days'})])
        EvidenceEdge.objects.create(id=uuid4(), graph=self.graph, source=nodes[0], target=nodes[1],
            relationship='same_as', provenance={'type': 'approved_planning_input'})
        result = self.run_review(mode='ai_verified')
        self.assertEqual(result['counts']['accepted_verified'], 2)
        self.assertEqual(result['counts']['accepted_ai'], 0)
        self.assertFalse(self.graph.nodes.filter(property='duration', status='accepted').exists())
        self.assertIn('linked_source_conflict:duration', {row['key'] for row in result['unresolved_groups']})
        resolve.assert_not_called()

    def test_cancellation_before_commit_leaves_decisions_and_graph_revision_unchanged(self):
        self.prepare([('a', 'identity', 'Pump A')])
        job = PlanningJob.objects.create(project=self.project, requested_by=self.actor, job_type='evidence_bulk')
        def cancel(progress):
            if progress['phase'] == 'commit':
                PlanningJob.objects.filter(pk=job.pk).update(status='cancelled')
        with self.assertRaises(EvidenceError) as error:
            self.run_review(job=job, callback=cancel)
        self.assertEqual(error.exception.payload['code'], 'evidence_bulk_cancelled')
        self.graph.refresh_from_db()
        self.assertEqual(self.graph.revision, 1)
        self.assertFalse(self.graph.decisions.exists())
        self.assertFalse(self.project.audit_events.filter(action='evidence.bulk_reviewed').exists())

    @patch('apps.planning_intelligence.services.evidence_bulk_ai.ai_availability', return_value=AI)
    def test_summary_is_read_only_and_uses_the_entire_precomputed_queue(self, availability):
        self.prepare([('a', 'identity', 'Pump A'), ('b', 'calendar', None)])
        from ..services.evidence_graph import _knowledge
        nodes, _, issues = _knowledge(self.graph)
        with CaptureQueriesContext(connection) as queries:
            summary = bulk_review_summary(self.project, nodes=nodes, issues=issues)
        self.assertEqual(summary['total_open'], 2)
        self.assertEqual(summary['verified_unambiguous'], 1)
        self.assertTrue(summary['ai_available'])
        self.assertIn('missing_input:calendar', {row['key'] for row in summary['unresolved_groups']})
        self.assertFalse(any(row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE')) for row in queries))

    def test_ten_thousand_queue_items_are_accepted_with_bounded_database_batches(self):
        self.prepare([(f'entity:{i}', 'identity', f'Document {i}') for i in range(10_000)])
        with CaptureQueriesContext(connection) as queries:
            result = self.run_review()
        self.assertEqual(result['counts']['total'], 10_000)
        self.assertEqual(result['counts']['processed'], 10_000)
        self.assertEqual(result['counts']['accepted_verified'], 10_000)
        self.assertEqual(self.graph.decisions.count(), 10_000)
        self.assertEqual(self.graph.nodes.filter(status='accepted').count(), 10_000)
        self.assertLess(len(queries), 600, f'Bulk review used {len(queries)} SQL queries for10,000 facts')
        updates = [row for row in queries if row['sql'].lstrip().upper().startswith('UPDATE')]
        self.assertLess(len(updates), 100)
        self.assertEqual(self.project.audit_events.filter(action='evidence.bulk_reviewed').count(), 1)
