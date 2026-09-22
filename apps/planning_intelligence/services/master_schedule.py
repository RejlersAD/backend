"""One explicit current schedule selection, shared by the planning canvas.

Opening a version never changes the current selection or the preserved working
draft. Commands use the existing CPM, assurance and business approval services.
"""
from copy import deepcopy

from django.db import transaction
from django.db.models import Max
from django.http import Http404
from django.utils import timezone

from apps.rbac.action_policy import module_action_allowed
from ..access import can_write_project, proposal_approver_users
from ..models import (PlanningProject, ScheduleVersion, ScheduleReview, ScheduleReviewDecision,
                      ScheduleBaseline, ScheduleWBSNode, ScheduleActivity, ActivityRelationship, ActivityAssignment)
from .audit import record_event
from .cpm import calculate_schedule_version
from .operational_jobs import canonical_fingerprint, schedule_state_fingerprint, assurance_state_fingerprint
from .planning_boundaries import accepted_input_validation, calculation_inputs_current
from .schedule_approval import (ScheduleApprovalError, can_baseline_schedule, can_decide_schedule_review,
                                current_schedule_version, decide_schedule_review, require_schedule_authority)
from .trustworthy_scheduling import current_assurance, run_schedule_assurance, approve_schedule_assurance


def _error(message, code='master_schedule_conflict', **details):
    raise ScheduleApprovalError(message, code=code, **details)


def _write(project, actor):
    return can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update')


def _version(project, version_id, *, lock=False):
    query = ScheduleVersion.objects.filter(pk=version_id, schedule__project=project,
                                           schedule__is_deleted=False, is_deleted=False)
    if lock:
        query = query.select_for_update()
    result = query.first()
    if result is None:
        raise Http404
    return result


def _revision(project, version):
    # 48-bit opaque integer is exactly representable by JavaScript. It changes
    # for alternate API edits too, not only commands through this canvas.
    return int(canonical_fingerprint({
        'selection': project.master_schedule_revision, 'version': version.pk,
        'inputs': schedule_state_fingerprint(version), 'assurance': assurance_state_fingerprint(version),
        'updated_at': version.updated_at, 'status': version.status,
        'reviews': list(version.governance_reviews.filter(is_deleted=False).values('id', 'status', 'updated_at')),
    })[:12], 16)


def _review(version):
    return version.governance_reviews.filter(is_deleted=False).order_by('-requested_at', '-pk').first()


def master_plan_state(project, actor, *, version_id=None):
    from .simple_planning import plan_state
    from .planning_provenance import annotate_plan_provenance
    from .planning_profiles import planning_profile_selection
    selected_id = version_id or project.master_schedule_version_id
    if selected_id:
        published_version = _version(project, selected_id)
        published_baseline = published_version.baselines.filter(is_deleted=False, approved_at__isnull=False).order_by('-approved_at', '-pk').first()
        if published_baseline:
            from .published_schedule import published_plan_state
            from .planning_registers import risk_snapshot
            state = published_plan_state(published_baseline)
            canonical = project.master_schedule_version_id == published_version.pk
            writable = _write(project, actor)
            state.update(project_id=project.pk, revision=_revision(project, published_version),
                master_revision=project.master_schedule_revision, master_version_id=project.master_schedule_version_id,
                canonical_version=canonical, viewing_history=not canonical, legacy_read_only=not canonical,
                risk_register=risk_snapshot(published_version),
                permissions={'can_edit': False, 'can_assign': False, 'can_calculate': False, 'can_validate': False,
                    'can_submit': False, 'can_approve_publish': False,
                    'can_restore_working_draft': writable and bool(project.master_schedule_version_id),
                    'can_select_version': writable and current_schedule_version(published_version),
                    'can_reopen': writable and canonical and current_schedule_version(published_version)})
            state['schedule_versions'] = [{'id': item.pk, 'schedule_id': item.schedule_id, 'version': item.version,
                'version_number': item.version, 'status': item.status, 'created_at': item.created_at.isoformat(),
                'label': f'{item.schedule.name} · v{item.version} · {item.get_status_display()}'}
                for item in ScheduleVersion.objects.filter(schedule__project=project, schedule__is_deleted=False, is_deleted=False)
                .select_related('schedule').order_by('-created_at', '-pk')[:100]]
            state['versions'] = state['schedule_versions']
            return state
    state = plan_state(project, actor, version_id=selected_id)
    state['master_revision'] = project.master_schedule_revision
    state['master_version_id'] = project.master_schedule_version_id
    state['planning_profile'] = planning_profile_selection(project)
    state['permissions']['can_restore_working_draft'] = _write(project, actor) and bool(project.master_schedule_version_id)
    state['canonical_version'] = bool(project.master_schedule_version_id and selected_id == project.master_schedule_version_id)
    if selected_id:
        version = _version(project, selected_id)
        from .planning_registers import risk_snapshot
        state['resource_requirements'] = deepcopy(version.planning_build.plan.get('resources', [])) if version.planning_build_id else []
        state['risk_register'] = risk_snapshot(version)
        state['planning_build_id'] = str(version.planning_build_id) if version.planning_build_id else None
        state['permissions']['can_select_version'] = _write(project, actor) and current_schedule_version(version)
        # History must describe the selected version, not the current JSON draft.
        review = _review(version)
        baseline = version.baselines.filter(is_deleted=False).first()
        state.update(state='baselined' if baseline else 'submitted' if review and review.status in {'pending', 'approved'} else 'review',
                     review={'id': review.pk, 'status': review.status} if review else None,
                     review_id=review.pk if review else None,
                     baseline={'id': baseline.pk, 'name': baseline.name, 'version_id': version.pk,
                               'approved_at': baseline.approved_at.isoformat() if baseline.approved_at else None} if baseline else None)
        if state['canonical_version']:
            readiness = accepted_input_validation(version)
            calculated = bool(version.calculated_at and calculation_inputs_current(version))
            assurance = current_assurance(version) if calculated else None
            pending = bool(review and review.status == 'pending')
            editable = _write(project, actor) and current_schedule_version(version) and version.status in {'draft', 'calculated'}
            blockers = list(readiness['issues']) + (list(assurance.blockers or []) if assurance else [])
            if not calculated:
                blockers.append({'code': 'calculation_required', 'message': 'Calculate this accepted schedule version.'})
            elif not assurance:
                blockers.append({'code': 'validation_required', 'message': 'Validate this schedule before submitting it.'})
            state.update(revision=_revision(project, version), viewing_history=False, legacy_read_only=False,
                         current_version_id=version.pk, schedule_id=version.schedule_id, blockers=blockers,
                         stale_inputs=not readiness['ready_for_calculation'], assumptions=[],
                         read_only_reason='This version uses accepted planning inputs. Review changes in Evidence.',
                         warnings=list(assurance.warnings or []) if assurance else [],
                         accepted_input_readiness=readiness,
                         calculation_available=calculated, source_verification={
                             'status': 'verified' if readiness['ready_for_approval'] else 'unverified',
                             'issues': readiness['issues'], 'policy': readiness['policy']})
            state['permissions'].update(
                can_edit=False, can_assign=False,
                can_calculate=editable and not pending and readiness['ready_for_calculation'],
                can_validate=editable and not pending and calculated,
                can_submit=editable and not pending and not blockers,
                can_approve_publish=bool(not blockers and review and (
                    can_decide_schedule_review(review, actor) or can_baseline_schedule(version, actor))),
                can_reopen=_write(project, actor) and bool(baseline) and current_schedule_version(version),
            )
            if not calculated:
                for task in state['tasks']:
                    task.update(calculated=False, calculation_basis=None, total_float_days=None, free_float_days=None,
                                is_critical=None, planned_start_date=None, planned_finish_date=None,
                                early_start=None, early_finish=None, late_start=None, late_finish=None)
                from .simple_planning import _canvas_metadata
                activities = {row.external_id: row for row in version.activities.filter(is_deleted=False).select_related('wbs_node')}
                _canvas_metadata(project, state, version, activities)
            if readiness['ready_for_calculation'] and version.evidence_graph_id:
                state['calendar'].update(source_verified=True, evidence_status='Accepted planning input')
    annotate_plan_provenance(project, state)
    if selected_id:
        from .source_schedule_import import enrich_imported_state
        enrich_imported_state(version, state)
        from .source_schedule_logic import enrich_logic_state
        enrich_logic_state(version, state)
        from .intelligent_sequence import enrich_sequence_state
        enrich_sequence_state(version, state)
    from .intelligent_sequence import can_propose_sequence
    state['permissions']['can_propose_sequence'] = (can_propose_sequence(project, actor)
        and not state.get('viewing_history') and state.get('state') not in {'baselined', 'submitted'})
    state['permissions']['can_build_source_logic'] = bool(state.get('source_import') and not state.get('viewing_history')
        and not state.get('stale_inputs') and _write(project, actor))
    from .gantt_editing import can_edit_gantt, enrich_gantt_state
    state['permissions']['can_edit_gantt'] = bool(not state.get('viewing_history') and can_edit_gantt(project, actor, version if selected_id else None))
    if selected_id:
        enrich_gantt_state(version, state)
    return state


@transaction.atomic
def select_master_version(project, actor, *, revision, version_id):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    if not _write(project, actor):
        _error('Your project access does not permit schedule selection.', status_code=403)
    if revision != project.master_schedule_revision:
        _error('The current schedule selection changed. Refresh before selecting a version.', 'master_schedule_revision_conflict')
    version = _version(project, version_id, lock=True) if version_id else None
    if version and not current_schedule_version(version):
        _error('This version has been superseded. Select the latest version of this schedule.', 'master_schedule_version_stale')
    before = project.master_schedule_version_id
    if before != version_id:
        project.master_schedule_version = version
        project.master_schedule_revision += 1
        project.save(update_fields=['master_schedule_version', 'master_schedule_revision'])
        record_event(project=project, actor=actor, action='master_schedule.selected', entity=project,
                     before={'version_id': before}, after={'version_id': version_id, 'revision': project.master_schedule_revision})
    return master_plan_state(project, actor)


def _locked(project, actor, revision, operation):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    if not project.master_schedule_version_id:
        _error('Select a saved schedule version first.', 'master_schedule_not_selected')
    version = _version(project, project.master_schedule_version_id, lock=True)
    if operation == 'approve-publish':
        require_schedule_authority(version, actor)
    elif not _write(project, actor):
        _error('Your access does not permit changes to this schedule.', status_code=403)
    if revision != _revision(project, version):
        _error('The schedule or its evidence changed. Refresh before continuing.', 'master_schedule_revision_conflict')
    if not current_schedule_version(version):
        _error('This schedule version is superseded. Select its latest version.', 'master_schedule_version_stale')
    return project, version


def _publish(version, actor, name):
    review = _review(version)
    already_approved = review and review.status == 'approved' and version.status == 'approved'
    if not review or (not already_approved and not can_decide_schedule_review(review, actor)):
        _error('Complete the assigned schedule review before publishing.', 'master_schedule_review_required', status_code=403)
    assurance = current_assurance(version)
    if not assurance or assurance.blockers:
        _error('Validate and resolve the blocking findings before publishing.', 'master_schedule_validation_required')
    if not already_approved:
        if assurance.status != 'approved':
            assurance = approve_schedule_assurance(version, actor)
        decide_schedule_review(version, review.pk, actor, decision='approved')
    version.refresh_from_db()
    if not can_baseline_schedule(version, actor):
        _error('Complete current evidence, assurance and schedule reviews before publishing.', 'master_schedule_baseline_blocked')
    baseline_name = (name or f'{version.schedule.project.name} — Baseline v{version.version}')[:255]
    if ScheduleBaseline.objects.filter(schedule=version.schedule, name=baseline_name).exists():
        _error('Choose a different baseline name; that name already exists.', 'master_schedule_baseline_name_exists')
    from ..schedule_serializers import (ActivityRelationshipSerializer, ScheduleActivitySerializer,
        ScheduleAssuranceReviewSerializer, ScheduleVersionSerializer, ScheduleWBSNodeSerializer)
    from .planning_boundaries import freeze_schedule_inputs
    from .planning_registers import risk_snapshot
    baseline = ScheduleBaseline.objects.create(schedule=version.schedule, source_version=version, name=baseline_name,
        data_date=version.schedule.data_date, approved_by=actor, approved_at=timezone.now(), snapshot={
            'version': ScheduleVersionSerializer(version).data,
            'wbs': ScheduleWBSNodeSerializer(version.wbs_nodes.filter(is_deleted=False), many=True).data,
            'activities': ScheduleActivitySerializer(version.activities.filter(is_deleted=False), many=True).data,
            'relationships': ActivityRelationshipSerializer(version.relationships.filter(is_deleted=False), many=True).data,
            'schedule_assurance': ScheduleAssuranceReviewSerializer(assurance).data,
            'accepted_inputs': freeze_schedule_inputs(version),
            'risk_register': risk_snapshot(version),
        })
    version.status = 'baselined'
    version.save(update_fields=['status', 'updated_at'])
    record_event(project=version.schedule.project, actor=actor, action='schedule.baselined', entity=baseline,
                 after={'version': version.version}, metadata={'source': 'master_schedule'})


def _clone(version, actor):
    """Revision preserves input identities, evidence, typed links and resources."""
    schedule = type(version.schedule).objects.select_for_update().get(pk=version.schedule_id)
    number = (schedule.versions.aggregate(value=Max('version'))['value'] or 0) + 1
    clone = ScheduleVersion.objects.create(schedule=schedule, version=number, parent_version=version,
        created_by=actor, change_summary='Master Schedule revision', evidence_graph_id=version.evidence_graph_id,
        planning_build_id=version.planning_build_id,
        evidence_graph_revision=version.evidence_graph_revision, evidence_input_snapshot=deepcopy(version.evidence_input_snapshot))
    nodes = list(version.wbs_nodes.filter(is_deleted=False))
    node_map = {row.pk: ScheduleWBSNode.objects.create(version=clone, **{key: getattr(row, key)
                for key in ('code', 'name', 'level', 'sort_order', 'discipline')}) for row in nodes}
    for row in nodes:
        if row.parent_id in node_map:
            node_map[row.pk].parent = node_map[row.parent_id]
            node_map[row.pk].save(update_fields=['parent'])
    activities = list(version.activities.filter(is_deleted=False))
    activity_map = {row.pk: ScheduleActivity(version=clone, wbs_node=node_map.get(row.wbs_node_id),
                    **{key: deepcopy(getattr(row, key)) for key in ('calendar_id', 'external_id', 'name', 'activity_type',
                    'duration_days', 'discipline', 'responsible_role', 'constraint_type', 'constraint_date', 'sort_order', 'metadata')}) for row in activities}
    ScheduleActivity.objects.bulk_create(list(activity_map.values()), batch_size=500)
    ActivityRelationship.objects.bulk_create([ActivityRelationship(version=clone,
        predecessor=activity_map[row.predecessor_id], successor=activity_map[row.successor_id],
        relationship_type=row.relationship_type, lag_days=row.lag_days, metadata=deepcopy(row.metadata))
        for row in version.relationships.filter(is_deleted=False)
        if row.predecessor_id in activity_map and row.successor_id in activity_map], batch_size=500)
    ActivityAssignment.objects.bulk_create([ActivityAssignment(activity=activity_map[row.activity_id],
        **{key: getattr(row, key) for key in ('resource_id', 'planned_units', 'budgeted_hours', 'budgeted_cost', 'planned_output_quantity')})
        for row in ActivityAssignment.objects.filter(activity__version=version, is_deleted=False)
        if row.activity_id in activity_map], batch_size=500)
    from .planning_registers import clone_risks
    clone_risks(version, clone)
    return clone


@transaction.atomic
def master_schedule_action(project, actor, *, operation, revision, approver_id=None, name=''):
    project, version = _locked(project, actor, revision, operation)
    if operation in {'calculate', 'validate', 'submit'}:
        if version.status not in {'draft', 'calculated'} or version.governance_reviews.filter(is_deleted=False, status='pending').exists():
            _error('Complete the current review or create a new revision before changing this schedule.', 'master_schedule_review_pending')
        if operation == 'calculate':
            calculate_schedule_version(version, requested_by=actor)
        else:
            if not version.calculated_at or not calculation_inputs_current(version):
                _error('Calculate the current accepted inputs first.', 'master_schedule_calculation_required')
            assurance = run_schedule_assurance(version, requested_by=actor)
            if operation == 'submit':
                if assurance.blockers:
                    _error('Resolve the blocking schedule checks before submitting.', 'master_schedule_validation_blocked', blockers=assurance.blockers)
                approvers = list(proposal_approver_users(project))
                approver = next((user for user in approvers if user.pk == approver_id), None) if approver_id else next(iter(approvers), None)
                if not approver:
                    _error('Select an eligible project approver.', 'master_schedule_approver_required')
                review = ScheduleReview.objects.create(version=version, title=f'{project.name} — Plan approval'[:255],
                    requested_by=actor, requested_at=timezone.now())
                ScheduleReviewDecision.objects.create(review=review, reviewer=approver)
    elif operation == 'approve-publish':
        _publish(version, actor, name)
    elif operation == 'reopen':
        if version.status != 'baselined':
            _error('Only a published baseline requires reopening.', 'master_schedule_state')
        version = _clone(version, actor)
        project.master_schedule_version = version
    else:
        _error('Review accepted inputs in Evidence, or open the preserved working draft to edit it.', 'master_schedule_accepted_inputs_read_only')
    project.master_schedule_revision += 1
    project.save(update_fields=['master_schedule_revision', 'master_schedule_version'])
    record_event(project=project, actor=actor, action=f'master_schedule.{operation}', entity=version,
                 after={'version_id': version.pk, 'master_revision': project.master_schedule_revision})
    project.refresh_from_db()
    return master_plan_state(project, actor)
