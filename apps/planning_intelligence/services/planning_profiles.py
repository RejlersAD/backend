"""Version and approve project planning rules without generating a schedule."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json

from django.db import transaction
from django.db.models import Max, Q
from django.utils import timezone
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from ..access import accessible_projects, can_final_approve_defaults, can_write_project
from ..models import PlanningProject, WorkCalendar
from ..planning_profile_models import PlanningProfile, ProjectPlanningProfileSelection
from ..workflow_models import WorkflowTemplate, EngineeringDependencyTemplate
from .audit import record_event
from .evidence_schema import validate_value


SCHEMA = 'planning-profile/1'
LEVELS = {'project', 'phase', 'area', 'package', 'discipline', 'deliverable', 'workflow_stage'}
CONFIG_FIELDS = {'workflow_template_id', 'dependency_template_id', 'final_gate_label', 'approved_dependency_rule_ids',
                 'stage_duration_overrides', 'wbs_convention', 'calendar_policy', 'progress_policy', 'resource_policy'}


class ProfileConflict(ValidationError):
    status_code = 409


def _conflict(message, code='planning_profile_revision_conflict'):
    raise ProfileConflict({'code': code, 'detail': message})


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode('utf-8')).hexdigest()


def _fingerprint(profile):
    return _digest({'schema': SCHEMA, 'project_id': profile.project_id, 'code': profile.code,
                    'name': profile.name, 'version': profile.version, 'definition': profile.definition})


def _permission(project, actor, *, approve=False):
    action = 'approve' if approve else 'update'
    if not accessible_projects(actor).filter(pk=project.pk).exists() or not module_action_allowed(actor, 'planning_package', action):
        raise PermissionDenied('Your access does not permit this planning profile action.')
    if not (can_final_approve_defaults(actor, project) if approve else can_write_project(actor, project)):
        raise PermissionDenied('An accountable project owner or manager must approve and select planning profiles.' if approve
                               else 'Your project role permits viewing, but not editing planning profiles.')


def _object(value, field, allowed):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValidationError({field: 'Supply an object containing only the supported policy fields.'})
    return deepcopy(value)


def _quantity(value, field):
    error = validate_value('duration', value)
    if error:
        raise ValidationError({field: error})
    if set(value) != {'value', 'unit'}:
        raise ValidationError({field: 'A quantity contains value and unit only.'})
    if isinstance(value['value'], str):
        raise ValidationError({field: 'Use a numeric quantity rather than a text value.'})
    return deepcopy(value)


def _workflow_snapshot(template):
    return {'id': template.pk, 'code': template.code, 'name': template.name, 'version': template.version,
            'project_id': template.project_id, 'stages': [{
                'id': stage.pk, 'code': stage.code, 'name': stage.name, 'sequence': stage.sequence,
                'activity_name_template': stage.activity_name_template,
                'duration': {'value': float(stage.duration_days), 'unit': 'working_days'},
                'role': stage.responsible_party, 'activity_type': stage.activity_type,
                'relationship_to_previous': stage.relationship_to_previous, 'lag_days': float(stage.lag_days),
                'progress_weight': float(stage.progress_weight), 'is_release_gate': stage.is_release_gate,
            } for stage in template.stages.filter(is_deleted=False).order_by('sequence')]}


def _dependency_snapshot(template):
    if template is None:
        return None
    return {'id': template.pk, 'code': template.code, 'name': template.name, 'version': template.version,
            'project_id': template.project_id, 'rules': [{
                'id': rule.pk, 'predecessor_code': rule.predecessor_code, 'predecessor_stage_code': rule.predecessor_stage_code,
                'predecessor_name': rule.predecessor_name, 'successor_name': rule.successor_name,
                'successor_code': rule.successor_code, 'successor_stage_code': rule.successor_stage_code,
                'relationship_type': rule.relationship_type, 'lag': {'value': float(rule.lag_days), 'unit': 'working_days'},
                'rationale': rule.rationale, 'source_reference': rule.source_reference, 'requires_confirmation': rule.requires_confirmation,
            } for rule in template.rules.filter(is_deleted=False).order_by('sequence', 'pk')]}


def _compile(project, raw):
    config = {key: deepcopy(value) for key, value in raw.items() if key in CONFIG_FIELDS}
    template = WorkflowTemplate.objects.filter(pk=config.get('workflow_template_id'), status='active', is_deleted=False).filter(
        Q(project=project) | Q(project__isnull=True)).first()
    if not template:
        raise ValidationError({'workflow_template_id': 'Select an active workflow belonging to this project or the corporate library.'})
    workflow = _workflow_snapshot(template)
    stages = workflow['stages']
    if len(stages) != 5 or [stage['code'] for stage in stages[:4]] != ['IFR', 'COMPANY_REVIEW', 'IFA', 'COMPANY_APPROVAL']:
        raise ValidationError({'workflow_template_id': 'Select a five-stage IFR, Company Review, IFA, Company Approval and final release workflow.'})
    codes = {stage['code'] for stage in stages}
    if len(codes) != 5:
        raise ValidationError({'workflow_template_id': 'Workflow stage identities must be unique.'})
    label = config.get('final_gate_label', stages[-1]['name'])
    if not isinstance(label, str) or not label.strip() or len(label) > 120:
        raise ValidationError({'final_gate_label': 'Provide an explicit final release label, such as the project-approved IFT/IFM gate.'})
    config['final_gate_label'] = label
    overrides = _object(config.get('stage_duration_overrides', {}), 'stage_duration_overrides', codes)
    rules = []
    for index, stage in enumerate(stages):
        stage['duration'] = _quantity(overrides.get(stage['code'], stage['duration']), 'stage_duration_overrides')
        if stage['activity_type'] in {'start_milestone', 'finish_milestone'} and stage['duration']['value'] != 0:
            raise ValidationError({'stage_duration_overrides': 'A configured milestone must have zero duration.'})
        if stage['activity_type'] not in {'start_milestone', 'finish_milestone'} and stage['duration']['value'] <= 0:
            raise ValidationError({'stage_duration_overrides': 'A configured task duration must be positive.'})
        rules.append({'id': f'workflow:{template.pk}:v{template.version}:stage:{stage["id"]}:duration',
                      'kind': 'stage_duration', 'stage_code': stage['code'], 'value': deepcopy(stage['duration']),
                      'basis': 'project_override' if stage['code'] in overrides else 'configured_workflow',
                      'provenance_type': 'planning_rule', 'source_fact': False})
        if validate_value('activity_type', stage['activity_type']):
            raise ValidationError({'workflow_template_id': 'Every stage requires a valid activity type.'})
        if index and stage['relationship_to_previous']:
            link = {'predecessor_id': stages[index - 1]['code'], 'type': stage['relationship_to_previous'],
                    'lag': stage['lag_days'], 'lag_unit': 'working_days'}
            if validate_value('dependencies', [link]):
                raise ValidationError({'workflow_template_id': 'Every configured stage relationship requires a valid type and lag.'})
            rules.append({'id': f'workflow:{template.pk}:v{template.version}:stage:{stage["id"]}:relationship',
                          'kind': 'workflow_relationship', 'stage_code': stage['code'], 'value': link,
                          'provenance_type': 'planning_rule', 'source_fact': False})
    stages[-1]['name'] = label
    config['stage_duration_overrides'] = overrides
    dependency_id = config.get('dependency_template_id')
    dependency = None
    if dependency_id is not None:
        dependency = EngineeringDependencyTemplate.objects.filter(pk=dependency_id, status='active', is_deleted=False).filter(
            Q(project=project) | Q(project__isnull=True)).first()
        if not dependency:
            raise ValidationError({'dependency_template_id': 'Select an active dependency template in this project or the corporate library.'})
    dependency_snapshot = _dependency_snapshot(dependency)
    selected = config.get('approved_dependency_rule_ids', [])
    available = {row['id']: row for row in (dependency_snapshot or {}).get('rules', [])}
    if len(set(selected)) != len(selected) or set(selected) - set(available):
        raise ValidationError({'approved_dependency_rule_ids': 'Select distinct rule IDs from the selected dependency template.'})
    config['approved_dependency_rule_ids'] = sorted(selected)
    for rule_id in config['approved_dependency_rule_ids']:
        rule = available[rule_id]
        if rule['predecessor_stage_code'] not in codes or rule['successor_stage_code'] not in codes:
            raise ValidationError({'approved_dependency_rule_ids': 'Selected dependency gates must exist in the chosen workflow.'})
        if validate_value('dependencies', [{'predecessor_id': rule['predecessor_code'], 'type': rule['relationship_type'],
                                           'lag': rule['lag']['value'], 'lag_unit': rule['lag']['unit']}]) or not rule['successor_code']:
            raise ValidationError({'approved_dependency_rule_ids': 'Selected dependencies require exact endpoints, type and numeric lag.'})
        rules.append({'id': f'dependency:{dependency.pk}:v{dependency.version}:rule:{rule_id}', 'kind': 'dependency',
                      'value': deepcopy(rule), 'provenance_type': 'planning_rule', 'source_fact': False})

    wbs = _object(config.get('wbs_convention', {}), 'wbs_convention', {'levels', 'code_separator'})
    levels = wbs.get('levels', [])
    if not isinstance(levels, list) or any(not isinstance(level, str) or level not in LEVELS for level in levels) or len(set(levels)) != len(levels):
        raise ValidationError({'wbs_convention': 'Use distinct supported hierarchy levels.'})
    if 'code_separator' in wbs and (not isinstance(wbs['code_separator'], str) or len(wbs['code_separator']) > 4 or not wbs['code_separator']):
        raise ValidationError({'wbs_convention': 'Use a nonempty separator of up to four characters.'})
    wbs['levels'] = levels
    config['wbs_convention'] = wbs
    calendar_policy = _object(config.get('calendar_policy', {'mode': 'not_specified'}), 'calendar_policy', {'mode', 'calendar_id', 'calendar'})
    calendar_mode = calendar_policy.get('mode')
    if calendar_mode == 'project_calendar':
        if set(calendar_policy) != {'mode', 'calendar_id'}:
            raise ValidationError({'calendar_policy': 'Project calendar policy requires only mode and calendar_id.'})
        if type(calendar_policy['calendar_id']) is not int or calendar_policy['calendar_id'] < 1:
            raise ValidationError({'calendar_policy': 'Supply a positive integer project calendar ID.'})
        calendar = WorkCalendar.objects.filter(pk=calendar_policy.get('calendar_id'), project=project, is_deleted=False).first()
        if not calendar:
            raise ValidationError({'calendar_policy': 'Select a current calendar from this project.'})
        calendar_value = {'working_weekdays': calendar.working_weekdays, 'hours_per_day': float(calendar.hours_per_day),
                          'working_times': calendar.working_times,
                          'timezone': calendar.timezone, 'exceptions': [{
                              'date': row.date.isoformat(), 'is_working': row.is_working,
                              **({'working_hours': float(row.working_hours)} if row.working_hours is not None else {}),
                              **({'working_times': row.working_times} if row.working_times else {}),
                          } for row in calendar.exceptions.filter(is_deleted=False).order_by('date')]}
    elif calendar_mode == 'explicit':
        if set(calendar_policy) != {'mode', 'calendar'}:
            raise ValidationError({'calendar_policy': 'Explicit calendar policy requires only mode and calendar.'})
        calendar_value = calendar_policy.get('calendar')
    elif calendar_mode == 'not_specified':
        if set(calendar_policy) != {'mode'}:
            raise ValidationError({'calendar_policy': 'An unspecified calendar cannot contain configured calendar values.'})
        calendar_value = None
    else:
        raise ValidationError({'calendar_policy': 'Choose not_specified, project_calendar or explicit.'})
    if calendar_value is not None:
        _object(calendar_value, 'calendar_policy', {'working_weekdays', 'hours_per_day', 'timezone', 'exceptions', 'working_times'})
        error = validate_value('calendar', calendar_value)
        if error:
            raise ValidationError({'calendar_policy': error})
        for exception in calendar_value['exceptions']:
            _object(exception, 'calendar_policy', {'date', 'is_working', 'working_hours', 'working_times'})
    config['calendar_policy'] = calendar_policy
    progress = _object(config.get('progress_policy', {'mode': 'not_specified'}), 'progress_policy', {'mode', 'weights'})
    if progress.get('mode') not in {'not_specified', 'workflow_weights', 'explicit_weights'}:
        raise ValidationError({'progress_policy': 'Choose not_specified, workflow_weights or explicit_weights.'})
    if progress.get('mode') == 'workflow_weights' and set(progress) != {'mode'}:
        raise ValidationError({'progress_policy': 'Workflow weights cannot include a second, conflicting weights configuration.'})
    weights = {stage['code']: stage['progress_weight'] for stage in stages} if progress.get('mode') == 'workflow_weights' else progress.get('weights')
    if progress.get('mode') != 'not_specified':
        try:
            valid_weights = isinstance(weights, dict) and set(weights) == codes and all(
                not isinstance(value, bool) and Decimal(str(value)).is_finite() and Decimal(str(value)) >= 0 for value in weights.values()) and sum(Decimal(str(value)) for value in weights.values()) == 100
        except (InvalidOperation, ValueError, TypeError):
            valid_weights = False
        if not valid_weights:
            raise ValidationError({'progress_policy': 'Provide a nonnegative weight for every stage, totaling exactly 100 percent.'})
    elif set(progress) != {'mode'}:
        raise ValidationError({'progress_policy': 'Unspecified progress cannot include weights.'})
    config['progress_policy'] = progress
    resource = _object(config.get('resource_policy', {'mode': 'not_specified'}), 'resource_policy', {'mode', 'roles'})
    if resource.get('mode') not in {'not_specified', 'workflow_roles', 'explicit_roles'}:
        raise ValidationError({'resource_policy': 'Choose not_specified, workflow_roles or explicit_roles.'})
    if resource.get('mode') == 'workflow_roles' and set(resource) != {'mode'}:
        raise ValidationError({'resource_policy': 'Workflow roles cannot include a second, conflicting roles configuration.'})
    roles = {stage['code']: stage['role'] for stage in stages} if resource.get('mode') == 'workflow_roles' else resource.get('roles')
    if resource.get('mode') != 'not_specified':
        if not isinstance(roles, dict) or set(roles) != codes or any(not isinstance(value, str) or not value.strip() or len(value) > 120 for value in roles.values()):
            raise ValidationError({'resource_policy': 'Provide an explicit role label for every stage. Roles do not assign employees.'})
    elif set(resource) != {'mode'}:
        raise ValidationError({'resource_policy': 'Unspecified resource policy cannot include roles.'})
    config['resource_policy'] = resource
    return {'schema_version': SCHEMA, 'configuration': config, 'workflow': workflow, 'dependency_template': dependency_snapshot,
            'rules': rules, 'wbs_convention': wbs, 'calendar_policy': {**calendar_policy, 'snapshot': calendar_value},
            'progress_policy': {**progress, 'weights': weights}, 'resource_policy': {**resource, 'roles': roles},
            'provenance_type': 'planning_policy', 'source_fact': False}


def profile_permissions(profile, actor):
    write = bool(actor and can_write_project(actor, profile.project) and module_action_allowed(actor, 'planning_package', 'update'))
    approve = bool(actor and can_final_approve_defaults(actor, profile.project))
    return {'can_edit': write and profile.status == 'draft', 'can_propose': write and profile.status == 'draft',
            'can_approve': approve and profile.status == 'proposed', 'can_reject': approve and profile.status == 'proposed',
            'can_revise': write and profile.status in {'approved', 'rejected'}}


def serialize_profile(profile, actor=None):
    return {key: getattr(profile, key) for key in ('id', 'project_id', 'code', 'name', 'version', 'revision', 'status',
        'supersedes_id', 'definition', 'content_fingerprint', 'approved_snapshot', 'created_by_id', 'proposed_by_id',
        'approved_by_id', 'decided_by_id', 'proposal_reason', 'decision_reason')} | {
        key: getattr(profile, key).isoformat() if getattr(profile, key) else None
        for key in ('created_at', 'updated_at', 'proposed_at', 'approved_at', 'decided_at')} | {'permissions': profile_permissions(profile, actor)}


def _valid_approved(profile):
    snapshot = profile.approved_snapshot
    return bool(profile.status == 'approved' and isinstance(snapshot, dict)
                and profile.content_fingerprint == _fingerprint(profile)
                and snapshot.get('content_fingerprint') == profile.content_fingerprint
                and snapshot.get('definition') == profile.definition
                and snapshot.get('schema_version') == SCHEMA
                and snapshot.get('profile_id') == profile.pk
                and snapshot.get('project_id') == profile.project_id
                and snapshot.get('profile_version') == profile.version
                and profile.approved_at and profile.approved_by_id
                and snapshot.get('approval') == {'actor_id': profile.approved_by_id,
                    'reason': profile.decision_reason, 'approved_at': profile.approved_at.isoformat(),
                    'profile_revision': profile.revision})


def planning_profile_selection(project):
    """Read-only selected immutable profile contract; invalid snapshots fail closed."""
    selected = ProjectPlanningProfileSelection.objects.filter(project=project).select_related('profile').first()
    if not selected:
        return {'revision': 0, 'profile_id': None, 'snapshot': None, 'content_fingerprint': None, 'valid': False}
    profile = selected.profile
    valid = (profile.project_id == project.pk and _valid_approved(profile)
             and selected.approved_snapshot == profile.approved_snapshot and selected.content_fingerprint == profile.content_fingerprint)
    return {'revision': selected.revision, 'profile_id': profile.pk, 'profile_version': profile.version,
            'snapshot': deepcopy(selected.approved_snapshot) if valid else None, 'valid': valid,
            'content_fingerprint': selected.content_fingerprint if valid else None,
            'selected_by_id': selected.selected_by_id, 'selected_at': selected.selected_at.isoformat(), 'reason': selected.reason}


def selected_approved_rule(project, rule_id, *, content_fingerprint=None):
    selection = planning_profile_selection(project)
    if not selection['valid'] or (content_fingerprint is not None and content_fingerprint != selection['content_fingerprint']):
        return None
    matches = [rule for rule in selection['snapshot']['definition']['rules'] if rule['id'] == rule_id]
    return {'rule': deepcopy(matches[0]), 'profile_id': selection['profile_id'], 'profile_version': selection['profile_version'],
            'content_fingerprint': selection['content_fingerprint'], 'approval': deepcopy(selection['snapshot']['approval']),
            'provenance_type': 'approved_planning_rule', 'source_fact': False} if len(matches) == 1 else None


def profile_collection(project, actor):
    scope = Q(project=project) | Q(project__isnull=True)
    return {'profiles': [serialize_profile(row, actor) for row in PlanningProfile.objects.filter(project=project).select_related('project')],
            'selection': planning_profile_selection(project),
            'options': {'workflow_templates': [_workflow_snapshot(row) for row in WorkflowTemplate.objects.filter(scope, status='active', is_deleted=False)],
                        'dependency_templates': [_dependency_snapshot(row) for row in EngineeringDependencyTemplate.objects.filter(scope, status='active', is_deleted=False)],
                        'calendars': list(WorkCalendar.objects.filter(project=project, is_deleted=False).values('id', 'name')),
                        'wbs_levels': sorted(LEVELS)},
            'permissions': {'can_create': can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update'),
                            'can_approve': can_final_approve_defaults(actor, project), 'can_select': can_final_approve_defaults(actor, project)},
            'capabilities': {'profile_management': True, 'schedule_generation': False, 'automatic_baseline': False}}


def _locked(project, profile_id, revision):
    profile = PlanningProfile.objects.select_for_update().filter(pk=profile_id, project=project).first()
    if not profile:
        raise NotFound('Planning profile not found in this project.')
    if profile.revision != revision:
        _conflict('The profile changed. Refresh before continuing.')
    return profile


def _save(profile, actor, action, before=None):
    profile.content_fingerprint = _fingerprint(profile)
    profile.save()
    record_event(project=profile.project, actor=actor, action=f'planning_profile.{action}', entity=profile,
                 before=before or {}, after=serialize_profile(profile))
    return profile


@transaction.atomic
def create_profile(project, actor, data):
    _permission(project, actor)
    project = PlanningProject.objects.select_for_update().get(pk=project.pk)
    if any(not data.get(key) for key in ('code', 'name', 'workflow_template_id')):
        raise ValidationError('Code, name and workflow_template_id are required.')
    if PlanningProfile.objects.filter(project=project, code=data['code']).exists():
        _conflict('This profile code already exists. Create a revision of its existing version.', 'planning_profile_code_exists')
    profile = PlanningProfile(project=project, code=data['code'], name=data['name'], version=1,
                              definition=_compile(project, data), created_by=actor)
    return _save(profile, actor, 'created')


@transaction.atomic
def update_profile(project, actor, profile_id, data):
    _permission(project, actor)
    PlanningProject.objects.select_for_update().get(pk=project.pk)
    profile = _locked(project, profile_id, data.get('revision'))
    if profile.status != 'draft':
        _conflict('Only draft profiles can be edited. Create a new version.', 'planning_profile_immutable')
    if 'code' in data and data['code'] != profile.code:
        raise ValidationError({'code': 'Profile identity is immutable.'})
    before = serialize_profile(profile)
    profile.name = data.get('name', profile.name)
    profile.definition = _compile(project, {**profile.definition['configuration'], **data})
    profile.revision += 1
    return _save(profile, actor, 'updated', before)


@transaction.atomic
def decide_profile(project, actor, profile_id, action, *, revision, reason):
    _permission(project, actor, approve=action in {'approve', 'reject'})
    PlanningProject.objects.select_for_update().get(pk=project.pk)
    profile = _locked(project, profile_id, revision)
    reason = reason.strip()
    if not reason:
        raise ValidationError({'reason': 'Record the reason for this profile decision.'})
    before = serialize_profile(profile)
    if action == 'revise':
        if profile.status not in {'approved', 'rejected'}:
            _conflict('Only reviewed profiles can be revised.', 'planning_profile_state')
        number = PlanningProfile.objects.filter(project=project, code=profile.code).aggregate(value=Max('version'))['value'] + 1
        return _save(PlanningProfile(project=project, code=profile.code, name=profile.name, version=number,
                                    supersedes=profile, definition=deepcopy(profile.definition), created_by=actor,
                                    proposal_reason=reason), actor, 'revised', before)
    expected = 'draft' if action == 'propose' else 'proposed'
    if profile.status != expected:
        _conflict(f'This action requires a {expected} profile.', 'planning_profile_state')
    if action in {'propose', 'approve'} and _compile(project, profile.definition['configuration']) != profile.definition:
        _conflict('Referenced policy inputs changed. Refresh the draft before proposing it.', 'planning_profile_sources_changed')
    now = timezone.now()
    profile.revision += 1
    if action == 'propose':
        profile.status, profile.proposed_by, profile.proposed_at, profile.proposal_reason = 'proposed', actor, now, reason
    elif action in {'approve', 'reject'}:
        profile.status = 'approved' if action == 'approve' else 'rejected'
        profile.decided_by, profile.decided_at, profile.decision_reason = actor, now, reason
        if action == 'approve':
            profile.approved_by, profile.approved_at = actor, now
            profile.approved_snapshot = {'schema_version': SCHEMA, 'profile_id': profile.pk, 'project_id': project.pk,
                'profile_version': profile.version, 'content_fingerprint': _fingerprint(profile),
                'definition': deepcopy(profile.definition), 'approval': {'actor_id': actor.pk, 'reason': reason,
                'approved_at': now.isoformat(), 'profile_revision': profile.revision},
                'provenance_type': 'approved_planning_rule', 'source_fact': False}
    else:
        raise ValidationError('Unknown profile decision.')
    return _save(profile, actor, action, before)


@transaction.atomic
def select_profile(project, actor, *, profile_id, selection_revision, reason):
    _permission(project, actor, approve=True)
    PlanningProject.objects.select_for_update().get(pk=project.pk)
    profile = PlanningProfile.objects.select_for_update().filter(project=project, pk=profile_id).first()
    if not profile:
        raise NotFound('Planning profile not found in this project.')
    if not _valid_approved(profile):
        _conflict('Select an intact approved profile version.', 'planning_profile_not_approved')
    if not reason.strip():
        raise ValidationError({'reason': 'Record why this planning profile is selected.'})
    selection = ProjectPlanningProfileSelection.objects.select_for_update().filter(project=project).first()
    if selection_revision != (selection.revision if selection else 0):
        _conflict('The selected project profile changed. Refresh before selecting another.', 'planning_profile_selection_conflict')
    before = planning_profile_selection(project)
    selection = selection or ProjectPlanningProfileSelection(project=project)
    selection.revision = selection_revision + 1
    selection.profile, selection.selected_by, selection.reason = profile, actor, reason.strip()
    selection.approved_snapshot = deepcopy(profile.approved_snapshot)
    selection.content_fingerprint = profile.content_fingerprint
    selection.save()
    record_event(project=project, actor=actor, action='planning_profile.selected', entity=selection,
                 before=before, after=planning_profile_selection(project))
    return profile
