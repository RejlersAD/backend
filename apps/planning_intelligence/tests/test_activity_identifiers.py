"""Business activity identifiers remain independent of row and WBS position."""
from copy import deepcopy

from django.test import SimpleTestCase, TestCase, override_settings

from ..models import ScheduleActivity
from ..services.simple_planning import _draft, _retain_saved_work, _seed_task, _version_tasks
from ..services.activity_identifiers import assign_activity_identifiers
from ..services.work_breakdown import materialize_work_breakdown
from . import test_simple_planning as fixture


@override_settings(ROOT_URLCONF=fixture.__name__)
class ActivityIdentifierPersistenceTests(TestCase):
    setUp = fixture.SimplePlanningTests.setUp
    read = fixture.SimplePlanningTests.read
    save = fixture.SimplePlanningTests.save
    task = fixture.SimplePlanningTests.task

    def generated_task(self, index=1, **updates):
        statement = f'The CONTRACTOR shall prepare source report {index}.'
        title = f'Prepare source report {index}'
        return _seed_task(self.task(
            f'requirement-{index}', title=title, planning_activity_id=f'FEED-REQ-{index * 10:04d}',
            source_title=statement, requirement_value=statement, requirement_id=index,
            activity_name_basis='requirement_action', activity_name_original=title,
            activity_naming_version=1, **updates,
        ))

    def seed_draft(self, tasks):
        state = _draft(self.project)
        state.update(tasks=tasks, state='review', method='programmatic_requirements',
                     activity_id_registry={'prefix': 'FEED-REQ', 'next_sequence': 10, 'allocated': {}},
                     disciplines=[{'code': 'testing', 'name': 'Testing'}])
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])
        return state

    def test_reordering_deleting_and_adding_rows_does_not_renumber_existing_activities(self):
        self.seed_draft([self.generated_task(index) for index in (1, 2, 3)])
        first = self.read()
        self.assertEqual([task['activity_code'] for task in first['tasks']],
                         ['FEED-REQ-0010', 'FEED-REQ-0020', 'FEED-REQ-0030'])
        saved = self.save(tasks=[self.task('requirement-3'), self.task('requirement-1'), self.task('new-manual')])
        codes = {task['id']: task['activity_code'] for task in saved['tasks']}
        self.assertEqual(codes['requirement-1'], 'FEED-REQ-0010')
        self.assertEqual(codes['requirement-3'], 'FEED-REQ-0030')
        self.assertNotIn('requirement-2', codes)
        self.assertEqual(codes['new-manual'], 'FEED-REQ-0040')
        reopened = self.read()
        self.assertEqual({task['id']: task['activity_code'] for task in reopened['tasks']}, codes)
        # The WBS position can change without changing a business identifier.
        self.assertEqual(reopened['tasks'][0]['wbs_code'], '1.1')
        self.assertEqual(reopened['tasks'][0]['activity_code_source'], 'planning_activity_id')

    def test_save_keeps_server_held_identifier_and_name_provenance(self):
        original = self.generated_task()
        self.seed_draft([original])
        saved = self.save(tasks=[self.task(
            original['id'], title='Prepare revised planner report', planning_activity_id='FORGED-ID',
            source_title='Forged source text', activity_name_original='Forged baseline',
            activity_name_basis='forged', activity_naming_version=999,
        )])
        task = saved['tasks'][0]
        self.assertEqual(task['title'], 'Prepare revised planner report')
        for key in ('planning_activity_id', 'source_title', 'activity_name_original',
                    'activity_name_basis', 'activity_naming_version'):
            self.assertEqual(task[key], original[key], key)
        self.assertEqual(task['id'], original['id'])

    def test_materialization_and_version_reads_keep_identifiers_and_source_metadata(self):
        tasks = [self.generated_task(), self.generated_task(2, depends_on=['requirement-1'])]
        state = self.seed_draft(tasks)
        version = materialize_work_breakdown(
            self.project, state, actor=self.owner, start=self.project.effective_date,
            token=state['assignment_token'],
        )
        activities = list(version.activities.order_by('sort_order'))
        self.assertEqual([row.external_id for row in activities], ['requirement-1', 'requirement-2'])
        version_tasks = _version_tasks(version)
        for task, original in zip(version_tasks, tasks):
            for key in ('planning_activity_id', 'source_title', 'activity_name_original',
                        'activity_name_basis', 'activity_naming_version'):
                self.assertEqual(task[key], original[key], key)
            self.assertEqual(task['activity_code'], original['planning_activity_id'])
        self.assertEqual(version_tasks[1]['depends_on'], ['requirement-1'])
        self.assertEqual(version.relationships.get().predecessor_id, activities[0].pk)
        response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual([task['activity_code'] for task in response.data['tasks']],
                         ['FEED-REQ-0010', 'FEED-REQ-0020'])
        self.assertEqual([task['external_id'] for task in response.data['tasks']],
                         ['requirement-1', 'requirement-2'])

    def test_imported_activity_identity_takes_priority_over_generated_identifier(self):
        original = self.generated_task(source_activity_id='SOURCE-A010', document_number='DOC-020')
        state = self.seed_draft([original])
        self.assertEqual(self.read()['tasks'][0]['activity_code'], 'SOURCE-A010')
        version = materialize_work_breakdown(
            self.project, state, actor=self.owner, start=self.project.effective_date,
            token=state['assignment_token'],
        )
        activity = ScheduleActivity.objects.get(version=version)
        before = deepcopy(activity.metadata)
        response = self.client.get(self.url, {'version_id': version.pk})
        self.assertEqual(response.status_code, 200, response.data)
        self.assertEqual(response.data['tasks'][0]['activity_code'], 'SOURCE-A010')
        self.assertEqual(response.data['tasks'][0]['source_activity_id'], 'SOURCE-A010')
        self.assertEqual(response.data['tasks'][0]['document_number'], 'DOC-020')
        self.assertEqual(activity.external_id, original['id'])
        activity.refresh_from_db()
        self.assertEqual(activity.metadata, before)

    def test_source_title_is_not_replaced_by_derived_activity_title_on_rebuild(self):
        previous = self.generated_task()
        proposed = self.generated_task()
        _retain_saved_work([previous], [proposed])
        self.assertEqual(proposed['source_title'], previous['source_title'])
        self.assertEqual(proposed['planning_activity_id'], previous['planning_activity_id'])

    def test_rebuild_can_update_automatic_name_without_treating_it_as_planner_edit(self):
        previous = self.generated_task()
        proposed = self.generated_task()
        proposed.update(title='Prepare detailed source report 1',
                        activity_name_original='Prepare detailed source report 1', activity_naming_version=2)
        _retain_saved_work([previous], [proposed])
        self.assertEqual(proposed['title'], 'Prepare detailed source report 1')
        self.assertEqual(proposed['activity_naming_version'], 2)
        self.assertEqual(proposed['source_title'], previous['source_title'])

    def test_rebuild_keeps_planner_rename_and_its_original_generated_baseline(self):
        previous = self.generated_task()
        previous['title'] = 'Prepare agreed site survey report'
        proposed = self.generated_task()
        proposed.update(title='Prepare detailed source report 1',
                        activity_name_original='Prepare detailed source report 1', activity_naming_version=2)
        _retain_saved_work([previous], [proposed])
        self.assertEqual(proposed['title'], previous['title'])
        self.assertEqual(proposed['activity_name_original'], previous['activity_name_original'])
        self.assertEqual(proposed['source_title'], previous['source_title'])

    def test_rebuild_retains_source_context_name_only_for_the_same_source_occurrence(self):
        previous = self.generated_task()
        previous.update(
            title='Prepare Train 1 Fire and Gas Mapping Report',
            activity_name_original='Prepare Train 1 Fire and Gas Mapping Report',
            activity_name_basis='source_context_review',
            source_references=[{'file_id': 37, 'extracted_text_sha256': 'source-version-hash',
                                'locator': {'page': 12, 'line': 7}, 'excerpt': previous['source_title']}],
        )
        for same_occurrence in (True, False):
            with self.subTest(same_occurrence=same_occurrence):
                proposed = self.generated_task()
                proposed.update(id='requirement-new-run', source_references=deepcopy(previous['source_references']))
                if not same_occurrence:
                    proposed['source_references'][0]['locator']['line'] = 8
                generic = deepcopy(proposed)
                _retain_saved_work([previous], [proposed])
                if same_occurrence:
                    self.assertEqual(proposed['id'], previous['id'])
                    self.assertEqual(proposed['title'], previous['title'])
                    self.assertEqual(proposed['activity_name_original'], previous['activity_name_original'])
                    self.assertEqual(proposed['activity_name_basis'], 'source_context_review')
                else:
                    self.assertEqual(proposed['id'], generic['id'])
                    self.assertEqual(proposed['title'], generic['title'])
                    self.assertEqual(proposed['activity_name_basis'], generic['activity_name_basis'])
                self.assertEqual(proposed['source_title'], previous['source_title'])


class ActivityIdentifierAllocationTests(SimpleTestCase):
    def test_reordered_new_row_cannot_take_an_existing_id(self):
        tasks = [{'id': 'new'}, {'id': 'saved', 'planning_activity_id': 'FEED-REQ-0010'}]
        registry = assign_activity_identifiers(tasks)
        self.assertEqual(tasks[0]['planning_activity_id'], 'FEED-REQ-0020')
        self.assertEqual(tasks[1]['planning_activity_id'], 'FEED-REQ-0010')
        self.assertEqual(registry['next_sequence'], 30)

    def test_deleted_and_high_suffix_ids_are_never_reused(self):
        tasks = [{'id': 'removed', 'planning_activity_id': 'FEED-REQ-0870'}, {'id': 'kept'}]
        registry = assign_activity_identifiers(tasks)
        kept_id = tasks[1]['planning_activity_id']
        replacement = [{'id': 'kept'}, {'id': 'new'}]
        updated = assign_activity_identifiers(replacement, registry)
        self.assertEqual(replacement[0]['planning_activity_id'], kept_id)
        self.assertEqual(replacement[1]['planning_activity_id'], 'FEED-REQ-0890')
        self.assertEqual(updated['allocated']['removed'], 'FEED-REQ-0870')
        self.assertNotIn('new', registry['allocated'])

    def test_source_and_document_ids_reserve_case_insensitive_numbers(self):
        tasks = [
            {'id': 'source', 'source_activity_id': 'feed-req-0010'},
            {'id': 'document', 'document_number': 'FEED-REQ-0020', 'external_id': 'FEED-REQ-0030'},
        ]
        assign_activity_identifiers(tasks)
        self.assertEqual([task['planning_activity_id'] for task in tasks], ['FEED-REQ-0040', 'FEED-REQ-0050'])
        self.assertEqual(tasks[0]['source_activity_id'], 'feed-req-0010')
        self.assertEqual(tasks[1]['document_number'], 'FEED-REQ-0020')
        self.assertEqual(tasks[1]['external_id'], 'FEED-REQ-0030')

    def test_case_insensitive_duplicate_saved_identifiers_are_rejected(self):
        with self.assertRaisesRegex(ValueError, 'unique planning ID'):
            assign_activity_identifiers([], {'allocated': {'one': 'FEED-REQ-0010', 'two': 'feed-req-0010'}})

    def test_conflicting_registry_identifier_cannot_replace_saved_id(self):
        with self.assertRaisesRegex(ValueError, 'conflicts with its saved identifier'):
            assign_activity_identifiers([{'id': 'one', 'planning_activity_id': 'FEED-REQ-0020'}],
                                        {'allocated': {'one': 'FEED-REQ-0010'}})

    def test_existing_business_id_cannot_collide_with_another_tasks_source_id(self):
        for existing_id in ('task_metadata', 'registry'):
            with self.subTest(existing_id=existing_id):
                tasks = [{'id': 'saved'}, {'id': 'imported', 'source_activity_id': 'feed-req-0010'}]
                registry = None
                if existing_id == 'task_metadata':
                    tasks[0]['planning_activity_id'] = 'FEED-REQ-0010'
                else:
                    registry = {'allocated': {'saved': 'FEED-REQ-0010'}}
                before_tasks, before_registry = deepcopy(tasks), deepcopy(registry)
                with self.assertRaisesRegex(ValueError, "source activity ID conflicts with another activity's saved planning ID"):
                    assign_activity_identifiers(tasks, registry)
                self.assertEqual(tasks, before_tasks)
                self.assertEqual(registry, before_registry)

    def test_source_id_may_alias_the_same_tasks_business_id(self):
        tasks = [{'id': 'same', 'planning_activity_id': 'FEED-REQ-0010', 'source_activity_id': 'feed-req-0010'}]
        before = deepcopy(tasks)
        registry = assign_activity_identifiers(tasks, {'allocated': {'same': 'FEED-REQ-0010'}})
        self.assertEqual(tasks, before)
        self.assertEqual(registry['allocated']['same'], 'FEED-REQ-0010')

    def test_duplicate_authoritative_source_ids_are_rejected_without_changes(self):
        tasks = [{'id': 'one', 'source_activity_id': 'SOURCE-A010'},
                 {'id': 'two', 'source_activity_id': 'source-a010'}]
        before = deepcopy(tasks)
        with self.assertRaisesRegex(ValueError, 'unique source activity ID'):
            assign_activity_identifiers(tasks)
        self.assertEqual(tasks, before)
