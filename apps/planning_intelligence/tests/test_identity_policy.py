"""Identity is an explicit, versioned source reference, never a title guess."""
from copy import deepcopy
import hashlib
from types import SimpleNamespace

from django.test import SimpleTestCase

from ..services.document_plan import build_document_plan
from ..services.identity_policy import (
    exact_identifier, identifier_key, identity_candidates, occurrence_key, same_source_location,
)
from ..services.schedule_basis import _deliverable_rows, _scalar
from ..services.source_timing_constraints import source_timing_evidence


def upload(text, identifier=12, **values):
    return {'id': identifier, 'filename': 'arbitrary.csv', 'parse_status': 'done',
            'category': 'other', 'text': text, **values}


def reference(source, **values):
    return {'file_id': source['id'], 'project_id': source.get('project_id'),
            'document_version': source.get('document_version'), 'namespace': source.get('namespace'),
            'document_revision': source.get('document_revision'),
            'extracted_text_sha256': hashlib.sha256(source['text'].encode('utf-8')).hexdigest(), **values}


class _Facts(list):
    def filter(self, **kwargs):
        rows = list(self)
        for key, value in kwargs.items():
            if key == 'value__source_register':
                rows = [row for row in rows if row.value.get('source_register') == value]
            elif key == 'source_file__isnull':
                rows = [row for row in rows if (row.source_file_id is None) == value]
            elif key.endswith('__in'):
                rows = [row for row in rows if getattr(row, key[:-4]) in value]
            else:
                rows = [row for row in rows if getattr(row, key) == value]
        return _Facts(rows)

    def exclude(self, **kwargs):
        excluded = self.filter(**kwargs)
        return _Facts(row for row in self if row not in excluded)

    def select_related(self, *_):
        return self

    def order_by(self, *_):
        return self

    def values_list(self, key, flat=False):
        return [getattr(row, key) for row in self]


def fact_run(names, *, register=False, generic=False, revisions=None):
    run = SimpleNamespace(id=9, pk=9, project_id=4, summary={'base_intelligence': {
        'document_driven': generic, 'deliverable_source': 'register' if register else 'source',
    }})
    source = SimpleNamespace(original_filename='Register.xlsx', category='mdr')
    run.facts = _Facts(SimpleNamespace(
        id=index + 1, pk=index + 1, run=run, fact_type='deliverable', is_deleted=False,
        source_file_id=3, source_file=source, extraction_method='deterministic',
        key=f'item-{index + 1}', confidence=.9, status='confirmed',
        source_locator={'row': index + 2, 'sheet': 'Scope'}, source_excerpt=name,
        value={'name': name, 'original_title': name, 'discipline': 'civil',
               'document_number': 'D-001', 'document_revision': (revisions or ['A'] * len(names))[index],
               'source_register': register, 'register_item': 1},
    ) for index, name in enumerate(names))
    return run


class IdentityPolicyTests(SimpleTestCase):
    def test_conflicting_scalars_ignore_confidence_and_global_document_priority(self):
        run = fact_run(['First', 'Second'])
        for fact, value in zip(run.facts, [5, 12]):
            fact.fact_type, fact.value, fact.status = 'duration_months', value, 'detected'
        run.facts[0].confidence = .999
        issues = []
        self.assertIsNone(_scalar(run, 'duration_months', 10, {('contract_dates', 'mdr'): 99}, issues=issues))
        self.assertEqual(issues[0]['code'], 'scalar_value_conflict')
        self.assertEqual(issues[0]['fact_ids'], [1, 2])
        self.assertIn('approval', issues[0]['blocks'])

    def test_scalar_acceptance_requires_recorded_reviewer_and_timestamp(self):
        run = fact_run(['First'])
        fact = run.facts[0]
        fact.fact_type, fact.value = 'duration_months', 5
        self.assertIsNone(_scalar(run, 'duration_months', 10, {}))
        fact.reviewed_by_id, fact.reviewed_at = 7, '2026-09-21T10:00:00Z'
        self.assertEqual(_scalar(run, 'duration_months', 10, {}), 5)

    def test_two_accepted_conflicting_scalars_remain_unresolved(self):
        run = fact_run(['First', 'Second'])
        for fact, value in zip(run.facts, [5, 12]):
            fact.fact_type, fact.value = 'duration_months', value
            fact.reviewed_by_id, fact.reviewed_at = 7, '2026-09-21T10:00:00Z'
        self.assertIsNone(_scalar(run, 'duration_months', 10, {}))

    def test_fact_retry_deduplication_preserves_separate_source_occurrences(self):
        from ..models import DocumentIntelligenceRun, PlanningFile
        from ..services.document_intelligence import _add_fact

        source = PlanningFile(id=7)
        rows = {'run': DocumentIntelligenceRun(id=4), 'facts': [], '_seen': set()}
        text = 'Prepare layout.\nPrepare layout.'
        for start, end in [(0, 15), (16, 31), (16, 31)]:
            _add_fact(rows, source, 'deliverable', 'layout', {'name': 'layout'}, .8, text, start, end)
        self.assertEqual(len(rows['facts']), 2)
        self.assertEqual([row.source_locator['character_start'] for row in rows['facts']], [0, 16])
        self.assertEqual(rows['facts'][0].source_locator['extracted_text_sha256'], hashlib.sha256(text.encode()).hexdigest())

    def test_saved_assignments_do_not_move_to_a_different_source_revision(self):
        from ..services.simple_planning import _retain_saved_work

        original = {'id': 'old', 'title': 'Inspection', 'assignee_id': 8, 'depends_on': [],
                    'source_references': [{'file_id': 3, 'extracted_text_sha256': 'v1', 'locator': {'line': 2}}]}
        proposed = {'id': 'new', 'title': 'Inspection', 'depends_on': [],
                    'source_references': [{'file_id': 3, 'extracted_text_sha256': 'v2', 'locator': {'line': 2}}]}
        before = deepcopy(original)
        _retain_saved_work([original], [proposed])
        self.assertNotIn('assignee_id', proposed)
        self.assertEqual(original, before)

    def test_duplicate_saved_source_identities_never_select_the_last_employee_assignment(self):
        from ..services.simple_planning import _retain_saved_work

        first = {'id': 'first', 'title': 'Inspection', 'assignee_id': 8, 'depends_on': [],
                 'source_references': [{'file_id': 3, 'extracted_text_sha256': 'v1', 'locator': {'line': 2}}]}
        second = {**first, 'id': 'second', 'assignee_id': 9}
        proposed = {'id': 'new', 'title': 'Inspection', 'depends_on': [], 'source_references': first['source_references']}
        _retain_saved_work([first, second], [proposed])
        self.assertNotIn('assignee_id', proposed)
        self.assertEqual(proposed['identity_review']['code'], 'duplicate_source_identity')

    def test_identifier_lookup_is_lossless_case_punctuation_and_leading_zeros_matter(self):
        identifiers = ['A-001', 'a-001', 'A001', 'A-1', ' A-001', 'A-001 ']
        ref = {'file_id': 1, 'document_version': 'A'}
        self.assertEqual([exact_identifier(value) for value in identifiers], identifiers)
        self.assertEqual(len({identifier_key(value, ref) for value in identifiers}), len(identifiers))
        self.assertIsNone(identifier_key('', ref))
        self.assertIsNone(identifier_key('A-001', {}))

    def test_identity_scopes_project_file_revision_namespace_and_version(self):
        ref = {'project_id': 1, 'file_id': 2, 'document_version': 'v1',
               'namespace': 'contract', 'document_revision': 'A', 'checksum_sha256': 'abc'}
        original = identifier_key('A1', ref)
        for field in ref:
            with self.subTest(field=field):
                self.assertNotEqual(original, identifier_key('A1', {**ref, field: 'different'}))
        self.assertNotEqual(original, identifier_key('A1', {**ref, 'document_version': None}))

    def test_duplicate_records_propose_review_and_never_merge(self):
        first = {'activity_id': 'A', 'title': 'Pump test', 'source_references': [{'file_id': 1, 'locator': {'row': 2}}]}
        second = deepcopy(first)
        second['source_references'][0]['locator']['row'] = 3
        records = [first, second]
        before = deepcopy(records)
        proposals = identity_candidates(records)
        self.assertEqual(proposals[0]['record_indexes'], [0, 1])
        self.assertFalse(proposals[0]['automatic_merge'])
        self.assertEqual(records, before)
        self.assertNotEqual(occurrence_key(first['source_references'][0]), occurrence_key(second['source_references'][0]))

    def test_namespace_only_locator_is_not_a_physical_source_row(self):
        ref = {'file_id': 1, 'locator': {'sheet': 'Register'}}
        self.assertFalse(same_source_location(ref, ref))

    def test_same_global_line_can_be_described_by_two_different_adapters(self):
        first = {'file_id': 1, 'locator': {'line': 7}}
        second = {'file_id': 1, 'locator': {'line': 7, 'line_end': 7, 'character_start': 80, 'table': 1}}
        self.assertTrue(same_source_location(first, second))
        self.assertFalse(same_source_location(first, {**second, 'file_id': 2}))

    def test_identical_or_similar_titles_never_copy_schedule_values(self):
        source = upload('ID|Task|Duration (days)\nA1|Foundation layout|7\n')
        tasks = [{'id': 'a', 'title': 'Foundation layout'}, {'id': 'b', 'title': 'Foundation layouts'}]
        result = source_timing_evidence(tasks, {'files': [source]})
        self.assertEqual(result['matched_tasks'], {})
        self.assertEqual(len(result['identity_issues']), 2)
        self.assertEqual(len(result['evidence_records']), 1)

    def test_explicit_versioned_identifier_retains_identity_after_user_renames_task(self):
        source = upload('ID|Task|Duration (days)\nA1|Foundation layout|7\n')
        task = {'id': 'a', 'title': 'Local display title', 'source_activity_id': 'A1',
                'source_references': [reference(source)]}
        result = source_timing_evidence([task], {'files': [source]})
        self.assertEqual(result['matched_tasks']['a']['values']['original_duration_days'], 7)

    def test_same_identifier_from_another_revision_cannot_replace_accepted_reference(self):
        old = upload('ID|Task|Duration (days)\nA1|Foundation layout|7\n')
        new = upload('ID|Task|Duration (days)\nA1|Foundation layout|12\n')
        task = {'id': 'a', 'title': 'Foundation layout', 'source_activity_id': 'A1',
                'source_references': [reference(old)]}
        self.assertEqual(source_timing_evidence([task], {'files': [new]})['matched_tasks'], {})

    def test_exact_physical_locator_can_resolve_a_source_row_without_identifier(self):
        source = upload('Task|Duration (days)\nWeld inspection|3\n')
        task = {'id': 'a', 'title': 'Inspection', 'source_references': [reference(source, locator={'line': 2})]}
        result = source_timing_evidence([task], {'files': [source]})
        self.assertEqual(result['matched_tasks']['a']['values']['original_duration_days'], 3)

    def test_conflicting_duplicate_identifiers_are_retained_and_block_timing_selection(self):
        source = upload('ID|Task|Duration (days)\nA1|Inspection|3\nA1|Inspection|9\n')
        task = {'id': 'a', 'title': 'Inspection', 'source_activity_id': 'A1', 'source_references': [reference(source)]}
        result = source_timing_evidence([task], {'files': [source]})
        self.assertEqual(result['matched_tasks'], {})
        self.assertEqual([row['values']['original_duration_days'] for row in result['evidence_records']], [3, 9])
        self.assertEqual(result['identity_issues'][0]['code'], 'ambiguous_source_identity')

    def test_task_supplied_approval_boolean_does_not_authorize_a_cross_document_link(self):
        source = upload('ID|Task|Duration (days)\nA1|Inspection|3\n')
        task = {'id': 'a', 'title': 'Inspection', 'source_activity_id': 'A1',
                'identity_link_approved': True, 'source_references': [{'file_id': 999}]}
        self.assertEqual(source_timing_evidence([task], {'files': [source]})['matched_tasks'], {})

    def test_legacy_and_generic_basis_keep_identical_source_records_distinct(self):
        for generic in (False, True):
            run = fact_run(['Foundation layout', 'Foundation layout'], generic=generic)
            rows = _deliverable_rows(run)
            self.assertEqual(len(rows), 2)
            self.assertNotEqual(rows[0]['source_identity'], rows[1]['source_identity'])
            self.assertEqual([row['fact_ids'] for row in rows], [[1], [2]])

    def test_register_duplicates_and_source_revisions_remain_distinct(self):
        run = fact_run(['Foundation layout'] * 3, register=True, revisions=['A', 'A', 'B'])
        rows = _deliverable_rows(run)
        self.assertEqual(len(rows), 3)
        self.assertEqual(len({row['source_identity'] for row in rows}), 3)
        self.assertEqual([row['document_revision'] for row in rows], ['A', 'A', 'B'])

    def test_legacy_basis_does_not_canonicalize_aliases_or_merge_similar_names(self):
        rows = _deliverable_rows(fact_run(['Foundation layout Area A', 'Foundation layout Area B', 'P&ID']))
        self.assertEqual([row['canonical_name'] for row in rows], ['Foundation layout Area A', 'Foundation layout Area B', 'P&ID'])

    def test_mdr_is_primary_scope_without_guessing_schedule_links(self):
        mdr = upload('Document Number|Document Title|Department\nD1|Inspection|Quality\nD2|Training|Operations\n', category='mdr')
        schedule = upload('ID|Task|Duration (days)\nD1|Inspection|4\n', 13)
        plan = build_document_plan([mdr, schedule], project_id=4)
        self.assertEqual(plan['scope_authority'], 'source_register')
        self.assertEqual(len(plan['deliverables']), 2)
        self.assertEqual(len(plan['activities']), 1)
        self.assertEqual(plan['scope_activity_ids'], [])
        self.assertEqual(len(plan['unmapped_source_schedule_activity_ids']), 1)
        issue = next(row for row in plan['validation'] if row['code'] == 'register_schedule_association_not_specified')
        self.assertIn('approval', issue['blocks'])

    def test_different_projects_and_versions_never_share_generated_identity(self):
        source = upload('ID|Task|Duration (days)\nA|Inspection|2\n')
        first = build_document_plan([source], project_id=1)['activities'][0]['id']
        second = build_document_plan([source], project_id=2)['activities'][0]['id']
        amended = build_document_plan([{**source, 'text': source['text'].replace('|2\n', '|3\n')}], project_id=1)['activities'][0]['id']
        self.assertEqual(len({first, second, amended}), 3)

    def test_repeated_processing_is_idempotent_without_dropping_duplicate_source_rows(self):
        source = upload('Document Number|Document Title\nA1|Inspection\nA1|Inspection\n', category='mdr')
        first = build_document_plan([source], project_id=1)
        self.assertEqual(first, build_document_plan([source], project_id=1))
        self.assertEqual(len(first['activities']), 2)
        self.assertEqual(len({row['id'] for row in first['activities']}), 2)
