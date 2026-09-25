"""Planning-package proposals add source scope without changing its authority."""
from copy import deepcopy
import hashlib
from unittest import TestCase

from ..services.planning_package_sources import build_planning_package_sources


def source(text, identifier=1, **changes):
    return {'id': identifier, 'project_id': 4, 'filename': 'scope.pdf', 'category': 'sow',
            'parse_status': 'done', 'text': text, **changes}


def fact(text, title, quote=None, *, identifier=1, start=None, **changes):
    quote = quote or title
    start = text.index(quote) if start is None else start
    return {'id': identifier, 'fact_type': 'deliverable', 'status': 'detected',
            'extraction_method': 'ai', 'source_file_id': 1,
            'value': {'name': title, 'discipline': 'not_specified'}, 'source_excerpt': quote,
            'source_locator': {'character_start': start, 'character_end': start + len(quote), 'quote': quote,
                               'extracted_text_sha256': hashlib.sha256(text.encode()).hexdigest()}, **changes}


MATRIX = ('Table 1: Applicable Deliverables for Work Packages\n'
          'S. No. Discipline Document / Deliverable Description Work Packages Remarks\n'
          '4.2 General\n4.2.1 General Survey dossier X X Common report\n'
          '4.2.2 General Optional drawing\n4.2.3 General Shelter report X If required\n'
          '4.2.4 General Release register X\n')


class PlanningPackageSourcesTests(TestCase):
    def test_ai_scope_is_added_when_a_register_already_exists(self):
        text = 'Item|Discipline|Deliverable\n1|Process|Process balance\n\nAdditional scope: Site survey\n'
        data = build_planning_package_sources([source(text)], [fact(text, 'Site survey')])
        self.assertEqual([item['title'] for item in data['deliverables']], ['Process balance', 'Site survey'])
        self.assertEqual(data['deliverables'][0]['discipline'], 'process')
        self.assertEqual(data['deliverables'][1]['discipline'], 'not_specified')

    def test_exact_ai_row_match_merges_citations_and_retains_recorded_discipline(self):
        text = 'Item|Discipline|Deliverable\n1|Process|Process balance\n'
        claims = [fact(text, 'Process balance', '1|Process|Process balance',
                       value={'name': 'Process balance', 'discipline': 'process'})]
        data = build_planning_package_sources([source(text)], claims)
        self.assertEqual(len(data['deliverables']), 1)
        item = data['deliverables'][0]
        self.assertEqual(item['source_fact_ids'], [1])
        self.assertEqual(len(item['source_references']), 2)
        self.assertEqual(item['discipline_basis'], 'source_register')

    def test_same_title_register_occurrences_remain_distinct(self):
        text = 'Table 1: Deliverables\nS. No. Description\nPackage One\n1 Survey dossier\nPackage Two\n1 Survey dossier\n'
        start = text.rindex('Survey dossier')
        data = build_planning_package_sources([source(text)], [fact(text, 'Survey dossier', start=start)])
        self.assertEqual(len(data['deliverables']), 2)
        self.assertEqual(len({row['id'] for row in data['deliverables']}), 2)
        self.assertEqual([row['source_group'] for row in data['deliverables']], ['Package One', 'Package Two'])
        self.assertEqual([row['source_fact_ids'] for row in data['deliverables']], [[], [1]])

    def test_matrix_unmarked_and_conditional_rows_cannot_be_reintroduced_by_ai(self):
        data = build_planning_package_sources([source(MATRIX)], [
            fact(MATRIX, 'Optional drawing'), fact(MATRIX, 'Shelter report', identifier=2)])
        self.assertEqual([row['title'] for row in data['deliverables']], ['Survey dossier', 'Release register'])
        self.assertEqual(len(data['excluded_inventory']), 4)
        self.assertEqual(sum(row['reason'] == 'ai_claim_requires_register_review' for row in data['excluded_inventory']), 2)

    def test_ambiguous_matrix_title_shortened_by_ai_is_still_quarantined(self):
        text = MATRIX.replace('4.2.4 General Release register X', '4.2.4 Release register X')
        data = build_planning_package_sources([source(text)], [fact(text, 'Release register')])
        self.assertNotIn('Release register', [row['title'] for row in data['deliverables']])
        self.assertTrue(any(row['reason'] == 'ai_claim_requires_register_review' for row in data['excluded_inventory']))

    def test_rejected_register_fact_suppresses_reparsed_row(self):
        text = 'Item|Discipline|Deliverable\n1|Process|Process balance\n'
        rejection = fact(text, 'Process balance', '1|Process|Process balance', status='rejected',
                         extraction_method='deterministic', value={'name': 'Process balance', 'source_register': True})
        data = build_planning_package_sources([source(text)], [rejection])
        self.assertEqual(data['deliverables'], [])
        self.assertEqual(data['excluded_inventory'][0]['reason'], 'source_review_excluded')

    def test_rejected_or_conflicted_ai_scope_is_not_added(self):
        text = 'Survey dossier\nProcess balance'
        for status in ('rejected', 'conflicted', 'superseded'):
            with self.subTest(status=status):
                data = build_planning_package_sources([source(text)], [fact(text, 'Survey dossier', status=status)])
                self.assertEqual(data['deliverables'], [])

    def test_other_scalar_conflict_does_not_drop_valid_deliverables(self):
        text = 'Client: Party One\nSurvey dossier'
        data = build_planning_package_sources([source(text)], [fact(text, 'Survey dossier'),
            fact(text, 'Party One', identifier=2, fact_type='client', status='conflicted')])
        self.assertEqual([row['title'] for row in data['deliverables']], ['Survey dossier'])

    def test_foreign_deleted_sample_and_changed_source_citations_are_not_scope(self):
        text = 'Survey dossier'
        claim = fact(text, text)
        for files, changed in [([source(text)], {'source_file_id': 9}),
                               ([source(text, is_deleted=True)], {}),
                               ([source(text, category='output_schedule_sample')], {}),
                               ([source(text + ' changed')], {})]:
            with self.subTest(files=files):
                self.assertEqual(build_planning_package_sources(files, [{**claim, **changed}])['deliverables'], [])

    def test_duplicate_ai_occurrence_enriches_discipline_without_duplicate_scope(self):
        text = 'Process Survey dossier'
        first = fact(text, 'Survey dossier', text)
        second = fact(text, 'Survey dossier', text, identifier=2,
                      value={'name': 'Survey dossier', 'discipline': 'process'})
        data = build_planning_package_sources([source(text)], [first, second])
        self.assertEqual(len(data['deliverables']), 1)
        self.assertEqual(data['deliverables'][0]['discipline'], 'process')
        self.assertEqual(data['deliverables'][0]['source_fact_ids'], [1, 2])

    def test_exact_unique_explicit_dependency_retains_type_lag_and_source(self):
        text = 'Survey dossier\nProcess balance\nSurvey dossier FS Process balance lag 0 working_days'
        quote = text.splitlines()[-1]
        claims = [fact(text, 'Survey dossier'), fact(text, 'Process balance', identifier=2),
                  fact(text, '', quote, identifier=3, fact_type='dependency', value={
                      'predecessor': 'Survey dossier', 'successor': 'Process balance',
                      'relationship_type': 'FS', 'lag': 0, 'lag_unit': 'working_days'})]
        data = build_planning_package_sources([source(text)], claims)
        self.assertEqual(len(data['dependencies']), 1)
        self.assertEqual(data['unresolved_dependencies'], [])
        link = data['dependencies'][0]
        self.assertEqual(link['predecessor_id'], data['deliverables'][0]['id'])
        self.assertEqual(link['successor_id'], data['deliverables'][1]['id'])
        self.assertEqual((link['type'], link['lag_days'], link['lag_unit']), ('FS', 0, 'working_days'))
        self.assertEqual(link['source_fact_ids'], [3])

    def test_prose_dependency_retained_without_guessing_fs_or_zero_lag(self):
        text = 'Survey dossier\nProcess balance\nSurvey dossier before Process balance'
        claims = [fact(text, 'Survey dossier'), fact(text, 'Process balance', identifier=2),
                  fact(text, '', text.splitlines()[-1], identifier=3, fact_type='dependency',
                       value={'predecessor': 'Survey dossier', 'successor': 'Process balance'})]
        data = build_planning_package_sources([source(text)], claims)
        self.assertEqual(data['dependencies'], [])
        self.assertEqual(data['unresolved_dependencies'][0]['reason'], 'dependency_type_or_working_day_lag_not_specified')

    def test_repeated_title_dependency_endpoint_requires_review(self):
        text = 'Item|Discipline|Deliverable\n1|Process|Survey dossier\n2|Piping|Survey dossier\n3|Process|Process balance\n\nSurvey dossier FS Process balance lag 0 working_days'
        claim = fact(text, '', text.splitlines()[-1], fact_type='dependency', value={
            'predecessor': 'Survey dossier', 'successor': 'Process balance',
            'relationship_type': 'FS', 'lag': 0, 'lag_unit': 'working_days'})
        data = build_planning_package_sources([source(text)], [claim])
        self.assertEqual(data['dependencies'], [])
        self.assertEqual(data['unresolved_dependencies'][0]['reason'], 'dependency_endpoints_not_unique')

    def test_stable_identity_and_no_mutation_or_arbitrary_item_cap(self):
        text = '\n'.join(f'Item {index}' for index in range(250))
        files = [source(text)]
        claims = [fact(text, f'Item {index}', identifier=index + 1) for index in range(250)]
        original = deepcopy((files, claims))
        first = build_planning_package_sources(files, claims)
        second = build_planning_package_sources(files, claims)
        self.assertEqual(len(first['deliverables']), 250)
        self.assertEqual(first, second)
        self.assertEqual((files, claims), original)
