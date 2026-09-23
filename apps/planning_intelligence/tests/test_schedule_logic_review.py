"""Reviewed parallel assumptions cannot mutate or approve schedule inputs."""
from copy import deepcopy
from datetime import date

from django.core.exceptions import ValidationError
from django.db import connection
from django.test import TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.urls import path
from django.utils import timezone

from apps.rbac.route_guard import secure_module_endpoints
from ..models import (PlanningAuditEvent, PlanningProject, ScheduleBaseline, ScheduleLogicReview,
                      ScheduleReview, ScheduleReviewDecision, ScheduleVersion, WorkCalendar)
from ..services.cpm import calculate_schedule_version
from ..services.schedule_approval import (ScheduleApprovalError, can_approve_schedule, can_baseline_schedule,
                                        decide_schedule_review)
from ..services.schedule_logic_review import (carry_logic_reviews, logic_quality, version_logic_quality)
from ..services.simple_planning import _dated_tasks, _draft, _fingerprint
from ..services.trustworthy_scheduling import (approve_schedule_assurance, current_assurance, run_schedule_assurance)
from ..services.work_breakdown import materialize_work_breakdown
from ..simple_planning_views import SimplePlanningView
from . import test_simple_planning as fixture


urlpatterns = [path('api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/', SimplePlanningView.as_view()),
    *[path(f'api/v1/planning-intelligence/projects/<int:project_id>/simple-plan/{operation}/',
           SimplePlanningView.as_view(operation=operation)) for operation in
      ('confirm-parallel-logic', 'edit-activity', 'submit', 'approve-publish', 'reopen', 'calculate', 'validate')]]
secure_module_endpoints(urlpatterns)


@override_settings(ROOT_URLCONF=__name__)
class ScheduleLogicReviewTests(TestCase):
    read = fixture.SimplePlanningTests.read

    def setUp(self):
        fixture.SimplePlanningTests.setUp(self)
        self.project.planned_end_date = date(2027, 5, 1)
        self.project.save(update_fields=['planned_end_date'])
        tasks = [fixture.SimplePlanningTests.task(self, 'release', title='Reviewed release gate', duration_days=1,
                                                 duration_source='planner')]
        parents = []
        stages = ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL', 'FINAL_ISSUE']
        for number in range(6):
            parent = {'id': f'package-{number}', 'title': f'Package {number}', 'discipline': 'testing',
                      'workflow_task_ids': [f'package-{number}-{stage}' for stage in stages]}
            parents.append(parent)
            for index, stage in enumerate(stages):
                predecessor = parent['workflow_task_ids'][index - 1] if index else 'release'
                tasks.append(fixture.SimplePlanningTests.task(self, parent['workflow_task_ids'][index],
                    title=f'{parent["title"]} {stage}', duration_days=1, duration_source='planner',
                    parent_deliverable_id=parent['id'], source_deliverable=deepcopy(parent),
                    workflow_stage_code=stage, workflow_stage_sequence=index + 1,
                    depends_on=[predecessor], dependency_details=[{'task_id': predecessor, 'type': 'FS', 'lag_days': 0}],
                    source_references=[{'filename': 'MDR.xlsx', 'locator': {'row': number + 1}}]))
        state = _draft(self.project)
        for task in tasks:
            task.setdefault('source_references', [])
            task.setdefault('document_number', '')
            task.setdefault('document_revision', '')
        state.update(state='review', revision=4, tasks=tasks, deliverables=parents,
                     disciplines=[{'code': 'testing', 'name': 'Testing'}], input_fingerprint=_fingerprint(self.project))
        self.project.simple_planning_state = state
        self.project.save(update_fields=['simple_planning_state'])

    def quality(self, state):
        return state['logic_quality']

    def payload(self, state=None, **changes):
        state = state or self.read()
        group = next(row for row in self.quality(state)['groups'] if row['kind'] == 'parallel_workflow')
        return {'revision': state['revision'], 'fingerprint': self.quality(state)['fingerprint'], 'group_id': group['id'],
                'rationale': 'Independent reviewed package inputs permit these six deliverables to proceed together.',
                'capacity_basis': 'The planner reviewed the named team allocation and recorded six available package teams.',
                'duration_basis': 'The entered workflow durations were reviewed against the approved project workflow.',
                'max_parallel_deliverables': 6, **changes}

    def post(self, operation, body, status=200):
        response = self.client.post(self.url + operation + '/', body, format='json')
        self.assertEqual(response.status_code, status, response.data)
        return response.data

    def confirm(self, state=None, **changes):
        return self.post('confirm-parallel-logic', self.payload(state, **changes))

    def materialize(self):
        version = materialize_work_breakdown(self.project, _draft(self.project), actor=self.owner,
                                            start=self.project.effective_date, token='logic-review-test')
        originals = {row['id']: row for row in self.project.simple_planning_state['tasks']}
        for activity in version.activities.all():
            activity.metadata = {**activity.metadata, 'duration_source': originals[activity.external_id]['duration_source']}
            activity.save(update_fields=['metadata'])
        calculate_schedule_version(version, requested_by=self.owner)
        version.refresh_from_db()
        return version

    def activate(self, version):
        self.project.master_schedule_version = version
        self.project.master_schedule_revision += 1
        self.project.save(update_fields=['master_schedule_version', 'master_schedule_revision'])
        return self.read()

    def test_get_is_read_only_and_parallel_group_blocks_submission(self):
        before = deepcopy(self.project.simple_planning_state)
        with CaptureQueriesContext(connection) as queries:
            state = self.read()
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual(self.quality(state)['summary']['unreviewed_group_count'], 1)
        self.assertFalse(state['permissions']['can_submit'])
        blocker = next(row for row in state['blockers'] if row['code'] == 'parallel_workflow_review_required')
        self.assertEqual(blocker['severity'], 'critical')
        self.assertEqual(len(blocker['task_ids']), 6)
        self.post('submit', {'revision': state['revision']}, 409)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleLogicReview.objects.exists())
        self.assertFalse(ScheduleVersion.objects.exists())

    def test_recorded_assumption_changes_only_review_and_revision_not_schedule_or_approval(self):
        state = self.read()
        before = deepcopy(self.project.simple_planning_state)
        after = self.confirm(state)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state['tasks'], before['tasks'])
        self.assertEqual(self.project.simple_planning_state['deliverables'], before['deliverables'])
        self.assertEqual(after['tasks'], state['tasks'])
        self.assertEqual(after['revision'], state['revision'] + 1)
        self.assertEqual(self.quality(after)['summary']['unreviewed_group_count'], 0)
        group = next(row for row in self.quality(after)['groups'] if row['kind'] == 'parallel_workflow')
        self.assertEqual(group['status'], 'reviewed')
        self.assertEqual(group['review']['reviewed_by_id'], self.owner.pk)
        self.assertFalse(group['requires_review'])
        self.assertTrue(after['permissions']['can_submit'], after['blockers'])
        self.assertFalse(after['permissions']['can_approve_publish'])
        self.assertFalse(ScheduleVersion.objects.exists())
        self.assertFalse(ScheduleReview.objects.exists())
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertEqual(PlanningAuditEvent.objects.filter(action='schedule.parallel_work_reviewed').count(), 1)

    def test_stale_revision_fingerprint_unknown_group_short_reason_and_insufficient_capacity_fail_closed(self):
        state = self.read()
        before = deepcopy(self.project.simple_planning_state)
        for changes, status in [({'revision': state['revision'] - 1}, 409), ({'fingerprint': 'f' * 64}, 409),
                                ({'group_id': 'e' * 64}, 409), ({'rationale': 'fine'}, 400),
                                ({'capacity_basis': ' ' * 30}, 400), ({'duration_basis': ' ' * 30}, 400),
                                ({'max_parallel_deliverables': 5}, 400), ({'max_parallel_deliverables': 0}, 400)]:
            self.post('confirm-parallel-logic', self.payload(state, **changes), status)
        self.project.refresh_from_db()
        self.assertEqual(self.project.simple_planning_state, before)
        self.assertFalse(ScheduleLogicReview.objects.exists())

    def test_permissions_history_submitted_and_baseline_are_read_only(self):
        state = self.read()
        body = self.payload(state)
        self.client.force_authenticate(self.reviewer)
        self.post('confirm-parallel-logic', body, 403)
        self.client.force_authenticate(self.owner)
        response = self.client.post(self.url + 'confirm-parallel-logic/?version_id=1', body, format='json')
        self.assertEqual(response.status_code, 409, response.data)
        for status in ['submitted', 'baselined']:
            self.project.simple_planning_state['state'] = status
            self.project.save(update_fields=['simple_planning_state'])
            self.post('confirm-parallel-logic', body, 409)
        self.assertFalse(ScheduleLogicReview.objects.exists())

    def test_edit_invalidates_previous_review_and_cycle_rejection_is_atomic(self):
        state = self.confirm()
        old_group = next(row['id'] for row in self.quality(state)['groups'] if row['kind'] == 'parallel_workflow')
        edited = self.post('edit-activity', {'revision': state['revision'], 'task_id': 'release', 'duration_days': 2})
        self.assertEqual(self.quality(edited)['summary']['unreviewed_group_count'], 1)
        group = next(row for row in self.quality(edited)['groups'] if row['kind'] == 'parallel_workflow')
        self.assertEqual(group['id'], old_group)
        self.assertNotIn('review', group)
        self.assertEqual(ScheduleLogicReview.objects.count(), 1)
        before = deepcopy(edited['tasks'])
        self.post('edit-activity', {'revision': edited['revision'], 'task_id': 'release',
                  'dependency_details': [{'task_id': 'package-0-FINAL_ISSUE', 'type': 'FS', 'lag_days': 0}]}, 409)
        self.assertEqual(self.read()['tasks'], before)

    def test_review_is_project_scoped_and_append_only(self):
        state = self.confirm()
        review = ScheduleLogicReview.objects.get()
        other = PlanningProject.objects.create(name='Unrelated', created_by=self.owner)
        result = logic_quality(other, state['tasks'], state['deliverables'])
        self.assertEqual(result['summary']['unreviewed_group_count'], 1)
        review.rationale = 'Attempt to overwrite a previously recorded assumption.'
        with self.assertRaises(ValidationError):
            review.save()
        with self.assertRaises(ValidationError):
            review.delete()

    def test_canonical_review_matches_display_and_invalidates_assurance_without_recalculation(self):
        version = self.materialize()
        state = self.activate(version)
        self.assertEqual(version_logic_quality(version)['fingerprint'], self.quality(state)['fingerprint'])
        assurance = run_schedule_assurance(version, requested_by=self.owner)
        before = list(version.activities.order_by('pk').values())
        calculated_at = version.calculated_at
        run_count = version.calculation_runs.count()
        state = self.read()
        after = self.confirm(state)
        version.refresh_from_db(); assurance.refresh_from_db()
        self.assertEqual(version.calculated_at, calculated_at)
        self.assertEqual(version.calculation_runs.count(), run_count)
        self.assertEqual(list(version.activities.order_by('pk').values()), before)
        self.assertEqual(assurance.status, 'superseded')
        self.assertEqual(version_logic_quality(version)['summary']['unreviewed_group_count'], 0)
        self.assertEqual(self.quality(after)['summary']['unreviewed_group_count'], 0)
        self.assertFalse(after['permissions']['can_approve_publish'])
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_legacy_matched_source_dates_have_identical_display_and_review_fingerprints(self):
        version = self.materialize()
        for activity in version.activities.all():
            activity.metadata = {**activity.metadata, 'duration_evidence': {
                'activity_specific': True,
                'source_references': [{'file_id': 91, 'filename': 'Original schedule.pdf',
                                       'locator': {'page': 10, 'row': activity.external_id}}],
                'values': {'planned_start_date': '2026-04-15', 'planned_finish_date': '2026-05-27'},
            }}
            activity.save(update_fields=['metadata'])
        state = self.activate(version)
        self.assertTrue(all(row['source_start_date'] == '2026-04-15' for row in state['tasks']))
        self.assertEqual(version_logic_quality(version)['fingerprint'], self.quality(state)['fingerprint'])
        reviewed = self.confirm(state)
        self.assertEqual(self.quality(reviewed)['summary']['unreviewed_group_count'], 0)
        self.assertEqual(version_logic_quality(version)['summary']['unreviewed_group_count'], 0)

    def test_baseline_can_be_reopened_but_original_review_and_snapshot_remain_unchanged(self):
        version = self.materialize()
        state = self.activate(version)
        self.confirm(state)
        version.status = 'baselined'
        version.save(update_fields=['status'])
        baseline = ScheduleBaseline.objects.create(schedule=version.schedule, source_version=version,
            name='Frozen original', approved_by=self.owner, approved_at=timezone.now(), snapshot={'frozen': 'baseline evidence'})
        before = list(version.activities.order_by('pk').values())
        review_count = ScheduleLogicReview.objects.count()
        from ..services.master_schedule import _revision, master_schedule_action
        from ..services.schedule_logic_review import confirm_parallel_logic
        self.project.refresh_from_db()
        version.refresh_from_db()
        with self.assertRaisesRegex(ScheduleApprovalError, 'editable correction draft'):
            confirm_parallel_logic(self.project, self.owner, {'revision': _revision(self.project, version)})
        reopened = master_schedule_action(self.project, self.owner, operation='reopen', revision=_revision(self.project, version))
        self.assertNotEqual(reopened['version_id'], version.pk)
        self.assertEqual(self.quality(reopened)['summary']['unreviewed_group_count'], 1)
        self.assertEqual(ScheduleLogicReview.objects.count(), review_count)
        baseline.refresh_from_db()
        self.assertEqual(baseline.snapshot, {'frozen': 'baseline evidence'})
        self.assertEqual(list(version.activities.order_by('pk').values()), before)

    def test_draft_submission_carries_only_matching_review_and_retains_original_reviewer(self):
        state = self.confirm()
        original = ScheduleLogicReview.objects.get(version=None)
        submitted = self.post('submit', {'revision': state['revision'], 'approver_id': self.owner.pk})
        version = ScheduleVersion.objects.get(pk=submitted['version_id'])
        carried = ScheduleLogicReview.objects.get(version=version)
        self.assertEqual(carried.reviewed_by_id, original.reviewed_by_id)
        self.assertEqual(carried.rationale, original.rationale)
        self.assertEqual(version_logic_quality(version)['summary']['unreviewed_group_count'], 0)
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertEqual(ScheduleReview.objects.get(version=version).status, 'pending')
        event = PlanningAuditEvent.objects.get(action='schedule.logic_review_carried')
        self.assertEqual(event.after['source_review_id'], original.pk)
        published = self.post('approve-publish', {'revision': submitted['revision'], 'name': 'Reviewed logic baseline'})
        self.assertEqual(published['state'], 'baselined')
        self.assertEqual(ScheduleBaseline.objects.count(), 1)
        self.assertEqual(ScheduleLogicReview.objects.count(), 2)
        self.assertEqual(version_logic_quality(version)['summary']['unreviewed_group_count'], 0)

    def test_carry_refuses_changed_data_and_never_invents_confirmation(self):
        state = self.confirm()
        version = self.materialize()
        tasks = _dated_tasks(self.project, deepcopy(self.project.simple_planning_state['tasks']))
        tasks[0]['duration_days'] = 9
        self.assertEqual(carry_logic_reviews(self.project, tasks, state['deliverables'], version), 0)
        self.assertFalse(ScheduleLogicReview.objects.filter(version=version).exists())
        self.assertEqual(version_logic_quality(version)['summary']['unreviewed_group_count'], 1)

    def test_carry_rejects_changed_target_calendar_even_when_dates_match(self):
        state = self.confirm()
        version = self.materialize()
        source_tasks = deepcopy(state['tasks'])
        calendar = WorkCalendar.objects.create(project=self.project, name='Another explicit calendar',
                                               working_weekdays=[0, 1, 2, 3, 4], hours_per_day=8)
        activity = version.activities.get(external_id='release')
        activity.calendar = calendar
        activity.save(update_fields=['calendar'])
        calculate_schedule_version(version, requested_by=self.owner)
        version.refresh_from_db()
        self.assertEqual(carry_logic_reviews(self.project, source_tasks, state['deliverables'], version), 0)
        self.assertFalse(ScheduleLogicReview.objects.filter(version=version).exists())

    def test_partial_reviewer_cannot_approve_parallel_work_but_can_request_changes_or_reject(self):
        version = self.materialize()
        self.activate(version)
        for alternative in ['changes_requested', 'rejected']:
            review = ScheduleReview.objects.create(version=version, title='Legacy review', requested_by=self.owner,
                                                   requested_at=timezone.now())
            vote = ScheduleReviewDecision.objects.create(review=review, reviewer=self.reviewer)
            ScheduleReviewDecision.objects.create(review=review, reviewer=self.owner)
            with self.assertRaises(ScheduleApprovalError) as raised:
                decide_schedule_review(version, review.pk, self.reviewer, decision='approved', comment='Ready')
            self.assertEqual(raised.exception.payload['code'], 'schedule_logic_review_required')
            vote.refresh_from_db(); review.refresh_from_db()
            self.assertEqual(vote.status, 'pending')
            self.assertEqual(vote.comment, '')
            self.assertIsNone(vote.decided_at)
            self.assertEqual(review.status, 'pending')
            result = decide_schedule_review(version, review.pk, self.reviewer, decision=alternative,
                                            comment='Resolve the current parallel-work assumptions first.')
            self.assertEqual(result.status, alternative)
            vote.refresh_from_db()
            self.assertEqual(vote.status, alternative)
        self.assertFalse(ScheduleBaseline.objects.exists())

    def test_persisted_ready_assurance_cannot_bypass_unreviewed_parallel_work(self):
        version = self.materialize()
        self.activate(version)
        assurance = run_schedule_assurance(version, requested_by=self.owner)
        # Simulate a persisted review from before this check was introduced.
        assurance.status = 'ready'
        assurance.blockers = []
        assurance.save(update_fields=['status', 'blockers'])
        before = list(version.activities.order_by('pk').values())
        with CaptureQueriesContext(connection) as queries:
            current = current_assurance(version)
        self.assertFalse([row for row in queries if row['sql'].lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE'))])
        self.assertEqual(current.status, 'draft')
        self.assertIn('parallel_workflow_review_required', [row['code'] for row in current.blockers])
        self.assertFalse(can_approve_schedule(version, self.owner, allow_unapproved_assurance=True))
        with self.assertRaisesRegex(ValueError, 'critical Phase 3'):
            approve_schedule_assurance(version, self.owner)
        assurance.refresh_from_db()
        self.assertEqual(assurance.status, 'ready')
        self.assertEqual(assurance.blockers, [])
        version.status = 'approved'
        version.save(update_fields=['status'])
        assurance.status = 'approved'
        assurance.save(update_fields=['status'])
        self.assertFalse(can_baseline_schedule(version, self.owner))
        self.assertFalse(ScheduleBaseline.objects.exists())
        self.assertEqual(list(version.activities.order_by('pk').values()), before)

    def test_recording_parallel_assumption_does_not_clear_other_evidence_blockers(self):
        self.project.simple_planning_state['input_fingerprint'] = 'stale-evidence'
        self.project.save(update_fields=['simple_planning_state'])
        state = self.confirm()
        self.assertEqual(self.quality(state)['summary']['unreviewed_group_count'], 0)
        self.assertIn('inputs_changed', [row['code'] for row in state['blockers']])
        self.assertFalse(state['permissions']['can_submit'])
