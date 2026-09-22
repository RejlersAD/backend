"""Deterministic, reviewable planning builds; source facts and policy stay distinct."""
from collections import Counter, defaultdict
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hashlib
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db import transaction
from django.db.models import Exists, OuterRef
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.rbac.action_policy import module_action_allowed
from ..access import accessible_projects, can_write_project
from ..evidence_models import EvidenceGraph
from ..models import (PlanningProject, WorkCalendar, CalendarException, Schedule, ScheduleVersion,
                      ScheduleWBSNode, ScheduleActivity, ActivityRelationship)
from ..planning_build_models import PlanningBuild
from .audit import record_event
from .evidence_graph import evidence_graph_snapshot, input_fingerprint
from .evidence_schema import network_issues, validate_value
from .planning_profiles import planning_profile_selection


RULE_VERSION = 'accepted-evidence-approved-policy/1'
OPTION_FIELDS = {'deliverable_entity_ids', 'source_activity_entity_ids', 'dependency_bindings', 'independent_entity_ids'}


class PlanningBuildError(ValidationError):
    status_code = 409


def _error(message, code='planning_build_conflict'):
    raise PlanningBuildError({'code': code, 'detail': message})


def _hash(value):
    return hashlib.sha256(json.dumps(value, cls=DjangoJSONEncoder, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()


def _write(project, actor):
    if not accessible_projects(actor).filter(pk=project.pk).exists():
        raise NotFound('Planning project not found.')
    if not can_write_project(actor, project) or not module_action_allowed(actor, 'planning_package', 'update'):
        raise PermissionDenied('Your project role cannot prepare or apply a planning build.')


def _issue(code, message, entity='', field='', *, warning=False):
    return {'code': code, 'message': message, 'entity_id': entity, 'field': field,
            'severity': 'warning' if warning else 'error', 'blocks': [] if warning else ['apply', 'calculation', 'approval', 'export']}


def _scope(project, snapshot):
    inputs = snapshot.get('accepted_inputs') or {}
    facts = snapshot.get('facts') or []
    activity_ids = {fact['entity_id'] for fact in facts if fact['property'] == 'duration' and inputs.get(fact['entity_id'], {}).get('identity')}
    register_ids = set()
    if snapshot.get('id'):
        register_ids = set(project.evidence_graph.nodes.filter(current=True, kind__in=['register_scope', 'scope_link']).values_list('entity_id', flat=True))
    # Register-only fallback rows have missing timing placeholders. They are
    # deliverable scope, not a source schedule merely because a placeholder
    # duration node exists. Any explicit timing remains a source activity.
    timed_ids = {fact['entity_id'] for fact in facts if fact['property'] in {
        'duration', 'start_date', 'finish_date', 'constraints', 'dependencies', 'activity_type', 'calendar'}
        and fact['value'] is not None and fact['status'] not in {'rejected', 'superseded'}}
    registers = register_ids & {entity for entity, values in inputs.items() if values.get('identity')} - timed_ids
    return sorted(registers), sorted(activity_ids - registers)


def _fact_index(snapshot):
    inputs = snapshot.get('accepted_inputs') or {}
    return {(fact['entity_id'], fact['property']): fact for fact in snapshot.get('facts') or []
            if fact['status'] == 'accepted' and inputs.get(fact['entity_id'], {}).get(fact['property']) == fact['value']}


def _source_lineage(fact, activity_id, prop, value):
    return {'type': fact['provenance_type'], 'activity_id': activity_id, 'property': prop, 'value': deepcopy(value),
            'fact_ids': [fact['id']], 'source_references': deepcopy(fact.get('sources') or []),
            'decision': deepcopy(fact.get('rule') or {}), 'source_fact': fact['provenance_type'] == 'document_evidence'}


def _calendar_normal(value):
    if not isinstance(value, dict):
        return value
    result = deepcopy(value)
    result.setdefault('working_times', {})
    result['exceptions'] = sorted([{**item, 'working_times': item.get('working_times') or []}
                                   for item in result.get('exceptions') or []], key=lambda item: item['date'])
    return result


def _rule_lineage(profile, activity_id, prop, value, *, rule_id=None, path=None):
    return {'type': 'approved_planning_rule', 'activity_id': activity_id, 'property': prop, 'value': deepcopy(value),
            'profile_id': profile['profile_id'], 'profile_version': profile['profile_version'],
            'profile_fingerprint': profile['content_fingerprint'], 'rule_id': rule_id,
            'policy_path': path, 'approval': deepcopy(profile['snapshot']['approval']), 'source_fact': False}


def _selected(options, key, eligible):
    values = options.get(key)
    if values is None:
        return list(eligible)
    if not isinstance(values, list) or any(not isinstance(value, str) for value in values) or len(values) != len(set(values)):
        raise ValidationError({key: 'Supply distinct exact entity IDs.'})
    if set(values) - set(eligible):
        raise ValidationError({key: 'Selection contains an unaccepted, foreign or inapplicable entity.'})
    return sorted(values)


def _compile(project, snapshot, profile, options, actor, reason):
    if not isinstance(options, dict) or set(options) - OPTION_FIELDS:
        raise ValidationError({'options': 'Supply only supported explicit applicability options.'})
    inputs = snapshot.get('accepted_inputs') or {}
    facts = _fact_index(snapshot)
    register_ids, source_ids = _scope(project, snapshot)
    registers = _selected(options, 'deliverable_entity_ids', register_ids)
    sources = _selected(options, 'source_activity_entity_ids', source_ids)
    selected = set(registers + sources)
    independent = _selected({'independent_entity_ids': options.get('independent_entity_ids', [])}, 'independent_entity_ids', registers)
    bindings = options.get('dependency_bindings', {})
    if not isinstance(bindings, dict) or any(not isinstance(key, str) or not isinstance(value, str) or value not in registers for key, value in bindings.items()):
        raise ValidationError({'dependency_bindings': 'Bind exact approved rule endpoint codes to selected deliverable entity IDs.'})
    canonical_options = {'deliverable_entity_ids': registers, 'source_activity_entity_ids': sources,
                         'independent_entity_ids': independent, 'dependency_bindings': dict(sorted(bindings.items()))}
    definition = profile['snapshot']['definition']
    issues = []
    for issue in snapshot.get('issues') or []:
        entity = issue.get('entity_id', '')
        if issue.get('code') == 'scope_link_missing' and entity in registers:
            continue  # Explicit policy application accounts for this exact register item.
        if entity in registers and issue.get('code') == 'missing_input' and issue.get('field') in {
                'duration', 'calendar', 'constraints', 'activity_type', 'dependencies'}:
            continue  # The selected approved workflow supplies its own inputs.
        if entity in selected or entity == 'project' or entity.startswith('document:') or issue.get('code') in {'source_integrity_unverified', 'linked_source_conflict'}:
            # Missing project calendar can be supplied by an explicitly approved policy.
            if entity == 'project' and issue.get('field') == 'calendar' and issue.get('code') == 'missing_input' and definition['calendar_policy'].get('snapshot'):
                continue
            issues.append(_issue(issue['code'], issue['message'], entity, issue.get('field', '')))
    if not selected:
        issues.append(_issue('planning_scope_empty', 'Select accepted source activities or deliverables.'))
    excluded = (set(register_ids) | set(source_ids)) - selected
    if excluded:
        issues.append(_issue('planning_scope_excluded', f'{len(excluded)} accepted scope entities were explicitly excluded from this build.', warning=True))
    project_inputs = deepcopy(inputs.get('project', {}))
    for prop in ('project_start', 'project_finish', 'scope_complete'):
        if validate_value(prop, project_inputs.get(prop)):
            issues.append(_issue('project_input_missing', f'Accept the project {prop.replace("_", " ")} in Evidence before applying.', 'project', prop))
    if project_inputs.get('project_start') and project_inputs.get('project_finish') and project_inputs['project_start'] > project_inputs['project_finish']:
        issues.append(_issue('project_window_invalid', 'The accepted project finish precedes its start.', 'project', 'project_finish'))
    if str(project.planned_end_date) != str(project_inputs.get('project_finish')):
        issues.append(_issue('project_finish_conflict', 'The registered project finish must match the accepted contractual finish. It will not be changed by this build.', 'project', 'project_finish'))
    calendar = project_inputs.get('calendar')
    policy_calendar = definition['calendar_policy'].get('snapshot')
    if calendar is not None and policy_calendar is not None and _calendar_normal(calendar) != _calendar_normal(policy_calendar):
        issues.append(_issue('calendar_policy_conflict', 'The accepted source calendar and selected profile calendar differ; neither is overwritten.', 'project', 'calendar'))
    if calendar is None:
        calendar = deepcopy(policy_calendar)
        project_inputs['calendar'] = calendar
    if validate_value('calendar', calendar):
        issues.append(_issue('calendar_not_specified', 'Accept a calendar or select a profile containing an approved calendar.', 'project', 'calendar'))
    profile_rules = {rule['id']: rule for rule in definition['rules']}
    duration_rules = {rule['stage_code']: rule for rule in profile_rules.values() if rule['kind'] == 'stage_duration'}
    relationship_rules = {rule['stage_code']: rule for rule in profile_rules.values() if rule['kind'] == 'workflow_relationship'}
    stages = definition['workflow']['stages']
    roles = definition['resource_policy'].get('roles') or {}
    weights = definition['progress_policy'].get('weights') or {}
    activities, relationships = [], []
    source_map, stage_map = {}, {}
    for entity in sources:
        values = inputs[entity]
        key = 'PB-' + _hash({'entity': entity, 'kind': 'source'})[:36]
        source_map[entity] = key
        row = {'id': key, 'source_entity_id': entity, 'name': values['identity'], 'kind': 'source_activity',
               **{prop: deepcopy(values.get(prop)) for prop in ('duration', 'calendar', 'constraints', 'activity_type', 'start_date', 'finish_date')},
               'dependencies': deepcopy(values.get('dependencies')), 'responsible_role': None, 'progress_weight': None,
               'property_lineage': {prop: _source_lineage(facts[(entity, prop)], key, prop, value)
                                    for prop, value in values.items() if (entity, prop) in facts}}
        activities.append(row)
        for prop in ('duration', 'calendar', 'constraints', 'activity_type', 'dependencies'):
            if validate_value(prop, row.get(prop)):
                issues.append(_issue('source_activity_input_missing', f'Accept the exact source activity {prop}; profile defaults never replace missing source activity inputs.', entity, prop))
    for entity in registers:
        values = inputs[entity]
        if any(values.get(prop) is not None for prop in ('duration', 'start_date', 'finish_date', 'constraints')):
            issues.append(_issue('source_package_timing_requires_mapping', 'This deliverable has explicit timing or constraints. Map them to source activities; they cannot be distributed across workflow stages.', entity, 'duration'))
        for stage in stages:
            key = 'PB-' + _hash({'entity': entity, 'stage': stage['code'], 'kind': 'workflow'})[:36]
            stage_map[(entity, stage['code'])] = key
            duration = deepcopy(stage['duration'])
            name = stage['activity_name_template'].replace('{deliverable}', values['identity']).replace('{stage}', stage['name'])
            if '{discipline}' in name:
                discipline = values.get('discipline')
                discipline = discipline.get('name') if isinstance(discipline, dict) else discipline
                if not discipline:
                    issues.append(_issue('workflow_name_input_missing', 'The approved stage naming convention requires an accepted discipline.', entity, 'discipline'))
                name = name.replace('{discipline}', discipline or 'Not Specified')
            if '{' in name or '}' in name:
                issues.append(_issue('workflow_name_template_invalid', 'The workflow name contains an unsupported substitution.', entity, 'identity'))
            row = {'id': key, 'source_entity_id': entity, 'name': name, 'kind': 'workflow_activity',
                   'workflow_stage_code': stage['code'], 'workflow_stage_name': stage['name'], 'duration': duration,
                   'calendar': deepcopy(calendar), 'constraints': [], 'activity_type': stage['activity_type'],
                   'start_date': None, 'finish_date': None, 'dependencies': [],
                   'responsible_role': roles.get(stage['code']), 'progress_weight': weights.get(stage['code']), 'property_lineage': {}}
            lineage = row['property_lineage']
            lineage['identity'] = {'type': 'deterministic_derivation', 'activity_id': key, 'property': 'identity', 'value': name,
                'fact_ids': [facts[(entity, 'identity')]['id']], 'source_references': deepcopy(facts[(entity, 'identity')].get('sources') or []),
                'rule': RULE_VERSION, 'profile_fingerprint': profile['content_fingerprint'], 'policy_path': 'workflow.stages', 'source_fact': False}
            lineage['duration'] = _rule_lineage(profile, key, 'duration', duration, rule_id=duration_rules[stage['code']]['id'])
            for prop in ('activity_type', 'constraints'):
                lineage[prop] = _rule_lineage(profile, key, prop, row[prop], path=f'workflow.stages.{stage["code"]}')
            lineage['calendar'] = (_source_lineage(facts[('project', 'calendar')], key, 'calendar', calendar) if ('project', 'calendar') in facts
                                   else _rule_lineage(profile, key, 'calendar', calendar, path='calendar_policy.snapshot'))
            if row['responsible_role']:
                lineage['responsible_role'] = _rule_lineage(profile, key, 'responsible_role', row['responsible_role'], path='resource_policy.roles')
            if row['progress_weight'] is not None:
                lineage['progress_weight'] = _rule_lineage(profile, key, 'progress_weight', row['progress_weight'], path='progress_policy.weights')
            activities.append(row)
        for stage in stages:
            rule = relationship_rules.get(stage['code'])
            if rule:
                value = rule['value']
                relationships.append({'predecessor_id': stage_map[(entity, value['predecessor_id'])],
                    'successor_id': stage_map[(entity, stage['code'])], 'type': value['type'], 'lag': value['lag'], 'lag_unit': value['lag_unit'],
                    'lineage': _rule_lineage(profile, stage_map[(entity, stage['code'])], 'dependencies', deepcopy(value), rule_id=rule['id'])})
    # Identity links are evidence of shared scope, never authorization to duplicate it.
    identity_facts = {fact['id']: fact['entity_id'] for fact in snapshot.get('facts') or [] if fact['property'] == 'identity'}
    for edge in snapshot.get('relationships') or []:
        if edge['type'] == 'same_as' and {identity_facts.get(edge['source']), identity_facts.get(edge['target'])} <= selected:
            issues.append(_issue('duplicate_linked_scope', 'Two selected identities are explicitly linked. Preserve the source activities or expand the register scope, not both.'))
    known_codes = set()
    for rule in profile_rules.values():
        if rule['kind'] != 'dependency':
            continue
        value = rule['value']
        codes = (value['predecessor_code'], value['successor_code'])
        known_codes.update(codes)
        endpoints = [bindings.get(code) for code in codes]
        if not all(endpoints):
            issues.append(_issue('dependency_rule_binding_missing', f'Bind exact endpoint codes {codes[0]} and {codes[1]} before applying this selected rule.', field='dependencies'))
            continue
        predecessor = stage_map[(endpoints[0], value['predecessor_stage_code'])]
        successor = stage_map[(endpoints[1], value['successor_stage_code'])]
        if predecessor == successor or endpoints[0] == endpoints[1]:
            issues.append(_issue('dependency_rule_self_link', 'An inter-deliverable rule cannot connect the same deliverable to itself.', endpoints[1], 'dependencies'))
            continue
        link = {'predecessor_id': predecessor, 'successor_id': successor, 'type': value['relationship_type'],
                'lag': value['lag']['value'], 'lag_unit': value['lag']['unit']}
        relationships.append({**link, 'lineage': _rule_lineage(profile, successor, 'dependencies', link, rule_id=rule['id'])})
    if set(bindings) - known_codes:
        raise ValidationError({'dependency_bindings': 'An endpoint code is not present in the selected approved rules.'})
    for row in activities:
        if row['kind'] != 'source_activity':
            continue
        for link in row['dependencies'] or []:
            predecessor = source_map.get(link['predecessor_id'])
            if not predecessor:
                issues.append(_issue('source_dependency_outside_scope', 'A source dependency does not resolve to a selected source activity. No workflow gate was guessed.', row['source_entity_id'], 'dependencies'))
                continue
            relationships.append({'predecessor_id': predecessor, 'successor_id': row['id'], 'type': link['type'],
                'lag': link['lag'], 'lag_unit': link['lag_unit'], 'lineage': deepcopy(row['property_lineage']['dependencies'])})
    by_id = {row['id']: row for row in activities}
    incoming = defaultdict(list)
    incoming_lineage = defaultdict(list)
    link_keys = set()
    for link in relationships:
        link_key = (link['predecessor_id'], link['successor_id'], link['type'])
        if link_key in link_keys:
            issues.append(_issue('duplicate_build_relationship', 'Multiple selected facts or rules produce the same relationship. Resolve the duplicate or conflicting rule applications.', link['successor_id'], 'dependencies'))
        link_keys.add(link_key)
        incoming[link['successor_id']].append({key: deepcopy(link[key]) for key in ('predecessor_id', 'type', 'lag', 'lag_unit')})
        incoming_lineage[link['successor_id']].append(deepcopy(link['lineage']))
    for row in activities:
        row['dependencies'] = incoming[row['id']]
        if row['kind'] == 'source_activity' and 'dependencies' in row['property_lineage']:
            original = row['property_lineage']['dependencies']
            row['property_lineage']['dependencies'] = {'type': 'deterministic_derivation', 'activity_id': row['id'], 'property': 'dependencies',
                'value': deepcopy(row['dependencies']), 'rule': 'exact_source_entity_to_build_activity',
                'fact_ids': original.get('fact_ids', []), 'source_references': original.get('source_references', []),
                'applications': [original], 'source_fact': False}
        if row['kind'] == 'workflow_activity':
            if not row['dependencies']:
                if row['source_entity_id'] not in independent:
                    issues.append(_issue('workflow_root_logic_not_confirmed', 'Confirm this workflow root is independent or bind its incoming approved relationship.', row['source_entity_id'], 'dependencies'))
                row['property_lineage']['dependencies'] = {'type': 'planner_input', 'activity_id': row['id'], 'property': 'dependencies',
                    'value': [], 'decision': {'actor_id': str(actor.pk), 'reason': reason, 'input': 'independent_entity_ids'}, 'source_fact': False}
            else:
                row['property_lineage']['dependencies'] = {'type': 'deterministic_derivation', 'activity_id': row['id'], 'property': 'dependencies',
                    'value': deepcopy(row['dependencies']), 'rule': RULE_VERSION, 'applications': incoming_lineage[row['id']], 'source_fact': False}
    for entity in independent:
        if any(link['successor_id'] == stage_map[(entity, stages[0]['code'])] for link in relationships):
            issues.append(_issue('independence_conflict', 'This deliverable was declared independent but has an incoming approved relationship.', entity, 'dependencies'))
    issues.extend(_issue(item['code'], item['message'], item['entity_id'], item['field'])
                  for item in network_issues({key: {'dependencies': row['dependencies']} for key, row in by_id.items()}))
    wbs = _wbs(definition, profile, activities, inputs, facts, issues, project)
    resources = [{'id': 'ROLE-' + row['id'], 'activity_id': row['id'], 'source_entity_id': row['source_entity_id'],
                  'role': row['responsible_role'], 'quantity': None, 'unit': None, 'assigned_employee_id': None,
                  'lineage': deepcopy(row['property_lineage']['responsible_role'])} for row in activities if row['responsible_role']]
    risks = []
    for (entity, prop), fact in facts.items():
        if prop in {'resource_requirement', 'responsibility', 'risk'}:
            record = {'id': fact['id'], 'source_entity_id': entity, 'value': deepcopy(fact['value']),
                      'lineage': _source_lineage(fact, '', prop, fact['value']), 'activity_id': None}
            (risks if prop == 'risk' else resources).append(record)
    if not roles and registers:
        issues.append(_issue('resource_roles_not_specified', 'The profile has no approved resource-role policy. Employee assignments and quantities remain unspecified.', field='resources', warning=True))
    if not weights and registers:
        issues.append(_issue('progress_weights_not_specified', 'Progress weights are not specified in the selected profile.', field='progress', warning=True))
    plan = {'schema_version': 'planning-build/1', 'project_inputs': project_inputs, 'activities': activities,
            'relationships': relationships, 'wbs': wbs, 'resources': resources, 'risks': risks,
            'source_entity_ids': sorted(selected), 'rule_version': RULE_VERSION}
    issues.extend(_adapter_issues(plan))
    # Stable ordering makes identical evidence/options produce identical outputs.
    unique = {_hash(issue): issue for issue in issues}
    return plan, list(unique.values()), canonical_options


def _wbs(definition, profile, activities, inputs, facts, issues, project):
    convention = definition['wbs_convention']
    levels = convention.get('levels') or []
    if not levels:
        issues.append(_issue('wbs_convention_not_specified', 'Select an approved WBS convention before applying the build.', field='wbs'))
    nodes, by_path, by_id, siblings = [], {}, {}, defaultdict(int)
    for activity in activities:
        parent, labels = None, []
        entity = activity['source_entity_id']
        for dimension in levels:
            value = None
            fact = None
            if dimension == 'project':
                value = project.name
            elif dimension == 'deliverable':
                value, fact = inputs[entity]['identity'], facts.get((entity, 'identity'))
            elif dimension == 'workflow_stage':
                if activity['kind'] != 'workflow_activity':
                    continue
                value = activity['workflow_stage_name']
            else:
                raw = inputs[entity].get(dimension)
                value = raw.get('name') if isinstance(raw, dict) else raw
                fact = facts.get((entity, dimension))
            if not value:
                issues.append(_issue('wbs_dimension_not_specified', f'Accept this entity\'s {dimension} before applying the selected WBS convention.', entity, dimension))
                value = 'Not Specified'
            # Scope identity prevents equal titles from merging distinct deliverables.
            component = entity if dimension == 'deliverable' else activity.get('workflow_stage_code') if dimension == 'workflow_stage' else value
            labels.append((dimension, component))
            signature = _hash(labels)
            if signature not in by_path:
                key = 'W-' + signature[:32]
                siblings[parent] += 1
                code = ((by_id[parent]['code'] + convention['code_separator']) if parent else '') + str(siblings[parent]) if convention.get('code_separator') else key
                node = {'id': key, 'parent_id': parent, 'code': code, 'name': str(value), 'level': len(labels) - 1,
                        'dimension': dimension, 'sort_order': len(nodes), 'discipline': '',
                        'lineage': _rule_lineage(profile, activity['id'], 'wbs', list(labels), path='wbs_convention')}
                if fact:
                    node['lineage']['fact_ids'] = [fact['id']]
                nodes.append(node)
                by_path[signature] = key
                by_id[key] = node
            parent = by_path[signature]
        activity['wbs_id'] = parent
    return nodes


def _adapter_issues(plan):
    issues = []
    calendar = plan['project_inputs'].get('calendar')
    for row in plan['activities']:
        quantity = row.get('duration')
        try:
            value = Decimal(str((quantity or {}).get('value')))
            supported = value.is_finite() and 0 <= value <= Decimal('99999999.99') and value == value.to_integral_value()
        except InvalidOperation:
            supported = False
        if not supported or (quantity or {}).get('unit') != 'working_days':
            issues.append(_issue('duration_resolution_unsupported', 'The current schedule adapter requires whole working days; this quantity was not converted or rounded.', row['source_entity_id'], 'duration'))
        if _calendar_normal(row.get('calendar')) != _calendar_normal(calendar):
            issues.append(_issue('mixed_calendars_unsupported', 'This schedule adapter cannot preserve multiple activity calendars.', row['source_entity_id'], 'calendar'))
        if len(row.get('constraints') or []) > 1:
            issues.append(_issue('multiple_constraints_unsupported', 'The schedule adapter supports one explicit constraint per activity.', row['source_entity_id'], 'constraints'))
        if row.get('activity_type') in {'start_milestone', 'finish_milestone'} and supported and value != 0:
            issues.append(_issue('milestone_duration_conflict', 'An explicit milestone cannot have a nonzero duration.', row['source_entity_id'], 'duration'))
        if row.get('activity_type') == 'level_of_effort':
            issues.append(_issue('level_of_effort_unsupported', 'Dynamic level of effort is not supported by the current calculation adapter.', row['source_entity_id'], 'activity_type'))
        if len(row['name']) > 500:
            issues.append(_issue('activity_name_limit', 'This activity name exceeds the schedule adapter limit; it was not truncated.', row['source_entity_id'], 'identity'))
    for link in plan['relationships']:
        value = Decimal(str(link['lag']))
        if not value.is_finite() or value != value.to_integral_value() or abs(value) > Decimal('999999.99') or link['lag_unit'] != 'working_days':
            issues.append(_issue('lag_resolution_unsupported', 'The schedule adapter requires whole working-day lags; the source value was not changed.', link['successor_id'], 'dependencies'))
    for node in plan['wbs']:
        if len(node['name']) > 255 or len(node['code']) > 128:
            issues.append(_issue('wbs_value_limit', 'An approved WBS name/code exceeds the relational adapter limit.', node['id'], 'wbs'))
    if calendar and not validate_value('calendar', calendar):
        hours = [calendar['hours_per_day']] + [item['working_hours'] for item in calendar['exceptions'] if item.get('working_hours') is not None]
        if any(Decimal(str(value)) != Decimal(str(value)).quantize(Decimal('0.01')) for value in hours):
            issues.append(_issue('calendar_precision_unsupported', 'Calendar hours require at most two decimal places; values are preserved.', 'project', 'calendar'))
        if any(item['is_working'] and item.get('working_hours') is not None and item['working_hours'] != calendar['hours_per_day'] for item in calendar['exceptions']):
            issues.append(_issue('partial_day_calendar_unsupported', 'The current working-day adapter cannot preserve partial-day calendar exceptions.', 'project', 'calendar'))
    return issues


def _payload(build):
    return {'project_id': build.project_id, 'evidence_graph_id': str(build.evidence_graph_id), 'evidence_revision': build.evidence_revision,
            'profile_id': build.profile_id, 'profile_selection_revision': build.profile_selection_revision,
            'source_fingerprint': build.source_fingerprint, 'profile_fingerprint': build.profile_fingerprint,
            'rule_version': build.rule_version, 'options': build.options, 'evidence_snapshot': build.evidence_snapshot,
            'profile_snapshot': build.profile_snapshot, 'plan': build.plan, 'issues': build.issues,
            'created_by_id': str(build.created_by_id), 'reason': build.reason}


def serialize_planning_build(build):
    version = build.schedule_versions.filter(is_deleted=False, schedule__is_deleted=False).order_by('created_at', 'pk').first()
    return {'id': str(build.pk), 'revision': 1, 'fingerprint': build.fingerprint, 'created_at': build.created_at.isoformat(),
            'created_by_id': str(build.created_by_id), 'reason': build.reason, 'status': 'applied' if version else 'preview',
            'profile': {**deepcopy(build.profile_snapshot), 'name': build.profile.name, 'code': build.profile.code}, 'graph_revision': build.evidence_revision,
            'profile_selection_revision': build.profile_selection_revision, 'options': deepcopy(build.options),
            'plan': deepcopy(build.plan), 'issues': deepcopy(build.issues),
            'ready_to_apply': not any('apply' in issue.get('blocks', []) for issue in build.issues),
            'summary': {key: len(build.plan.get(key) or []) for key in ('activities', 'relationships', 'wbs', 'resources', 'risks')},
            'schedule_version_id': version.pk if version else None}


def planning_build_collection(project, actor):
    snapshot = evidence_graph_snapshot(project)
    profile = planning_profile_selection(project)
    if profile.get('profile_id'):
        display = project.planning_profiles.filter(pk=profile['profile_id']).values('name', 'code').first()
        if display:
            profile = {**profile, **display}
    registers, activities = _scope(project, snapshot)
    facts = _fact_index(snapshot)
    def rows(ids):
        return [{'entity_id': entity, 'name': facts[(entity, 'identity')]['value'], 'fact_id': facts[(entity, 'identity')]['id'],
                 'source_references': deepcopy(facts[(entity, 'identity')].get('sources') or [])} for entity in ids]
    # Collection history is intentionally compact. Fetch the reviewed snapshot
    # through the detail endpoint only when the user opens that build.
    history = project.planning_builds.select_related('profile').defer(
        'evidence_snapshot', 'profile_snapshot', 'options', 'reason').annotate(
        applied=Exists(ScheduleVersion.objects.filter(planning_build_id=OuterRef('pk'), is_deleted=False, schedule__is_deleted=False)))[:25]
    builds = [{'id': str(build.pk), 'status': 'applied' if build.applied else 'preview', 'created_at': build.created_at.isoformat(),
               'profile': {'profile_id': build.profile_id, 'profile_version': build.profile.version,
                           'name': build.profile.name, 'code': build.profile.code},
               'summary': {key: len(build.plan.get(key) or []) for key in ('activities', 'relationships', 'wbs', 'resources', 'risks')},
               'ready_to_apply': not any('apply' in issue.get('blocks', []) for issue in build.issues)} for build in history]
    return {'builds': builds,
            'options': {'graph_revision': snapshot.get('revision', 0), 'profile_selection_revision': profile['revision'], 'profile': profile,
                        'deliverables': rows(registers), 'source_activities': rows(activities),
                        'dependency_rules': [rule for rule in profile['snapshot']['definition']['rules'] if rule['kind'] == 'dependency'] if profile['valid'] else []},
            'permissions': {'can_preview': can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update'),
                            'can_apply': can_write_project(actor, project) and module_action_allowed(actor, 'planning_package', 'update')}}


@transaction.atomic
def preview_planning_build(project, actor, *, evidence_revision, profile_selection_revision, options, reason):
    project = PlanningProject.objects.select_for_update().get(pk=project.pk, is_deleted=False)
    _write(project, actor)
    reason = reason.strip()
    if not reason:
        raise ValidationError({'reason': 'Record the reason and scope for this build.'})
    snapshot = evidence_graph_snapshot(project)
    profile = planning_profile_selection(project)
    if snapshot.get('revision') != evidence_revision or snapshot.get('readiness', {}).get('stale') or not snapshot.get('id'):
        _error('Evidence changed or has not been reviewed. Refresh the source review first.', 'planning_build_evidence_stale')
    if not profile['valid'] or profile['revision'] != profile_selection_revision:
        _error('Select the current approved planning profile before previewing.', 'planning_build_profile_stale')
    plan, issues, options = _compile(project, snapshot, profile, options, actor, reason)
    build = PlanningBuild(project=project, evidence_graph_id=snapshot['id'], evidence_revision=evidence_revision,
        profile_id=profile['profile_id'], profile_selection_revision=profile_selection_revision,
        source_fingerprint=snapshot['source_fingerprint'], profile_fingerprint=profile['content_fingerprint'],
        rule_version=RULE_VERSION, options=options, evidence_snapshot=snapshot, profile_snapshot=profile['snapshot'],
        plan=plan, issues=issues, created_by=actor, reason=reason)
    build.fingerprint = _hash(_payload(build))
    build.save()
    record_event(project=project, actor=actor, action='planning_build.previewed', entity=build,
                 after={'fingerprint': build.fingerprint, 'activities': len(plan['activities']), 'issues': len(issues)})
    return build


@transaction.atomic
def apply_planning_build(build, actor, *, fingerprint, reason=''):
    project = PlanningProject.objects.select_for_update().get(pk=build.project_id, is_deleted=False)
    _write(project, actor)
    build = PlanningBuild.objects.select_for_update().get(pk=build.pk, project=project)
    if fingerprint != build.fingerprint or build.fingerprint != _hash(_payload(build)):
        _error('The reviewed build fingerprint does not match.', 'planning_build_fingerprint_conflict')
    profile = planning_profile_selection(project)
    graph = EvidenceGraph.objects.get(pk=build.evidence_graph_id)
    if graph.revision != build.evidence_revision or input_fingerprint(project) != build.source_fingerprint:
        _error('Source evidence changed after preview. Prepare a new build.', 'planning_build_evidence_stale')
    if not profile['valid'] or profile['revision'] != build.profile_selection_revision or profile['content_fingerprint'] != build.profile_fingerprint:
        _error('The selected approved profile changed after preview.', 'planning_build_profile_stale')
    if any('apply' in issue.get('blocks', []) for issue in build.issues):
        _error('Resolve this preview\'s blocking findings before applying.', 'planning_build_blocked')
    existing = build.schedule_versions.order_by('created_at', 'pk').first()
    if existing:
        if existing.is_deleted or existing.schedule.is_deleted:
            _error('This applied build was archived. Prepare a new preview.', 'planning_build_archived')
        return existing
    plan = build.plan
    calendar_value = plan['project_inputs']['calendar']
    calendar = WorkCalendar.objects.create(project=project, name=f'Planning build {str(build.pk)}',
        working_weekdays=calendar_value['working_weekdays'], hours_per_day=calendar_value['hours_per_day'], timezone=calendar_value['timezone'],
        working_times=deepcopy(calendar_value.get('working_times') or {}))
    CalendarException.objects.bulk_create([CalendarException(calendar=calendar, date=item['date'], is_working=item['is_working'],
        working_hours=item.get('working_hours'), working_times=deepcopy(item.get('working_times') or []),
        name='Planning build approved calendar') for item in calendar_value['exceptions']])
    # A new immutable schedule input container avoids mutating calendars/start
    # that existing draft or baselined versions share. Master selection is separate.
    schedule = Schedule.objects.create(project=project, code='BUILD-' + build.pk.hex, name=project.name,
        planned_start=plan['project_inputs']['project_start'], default_calendar=calendar, created_by=actor)
    version = ScheduleVersion.objects.create(schedule=schedule, version=1, status='draft', planning_build=build,
        evidence_graph=graph, evidence_graph_revision=build.evidence_revision, evidence_input_snapshot=build.evidence_snapshot,
        change_summary=f'Approved profile application {build.profile_id}; {RULE_VERSION}', created_by=actor)
    nodes = {}
    for row in plan['wbs']:
        nodes[row['id']] = ScheduleWBSNode.objects.create(version=version, parent=nodes.get(row['parent_id']), code=row['code'],
            name=row['name'], level=row['level'], sort_order=row['sort_order'], discipline=row['discipline'])
    activities = {}
    for index, row in enumerate(plan['activities']):
        constraint = row['constraints'][0] if row['constraints'] else None
        activities[row['id']] = ScheduleActivity.objects.create(version=version, wbs_node=nodes.get(row['wbs_id']), calendar=calendar,
            external_id=row['id'], name=row['name'], activity_type=row['activity_type'], duration_days=row['duration']['value'],
            responsible_role=row['responsible_role'] or '', constraint_type=constraint['type'] if constraint else 'none',
            constraint_date=constraint['date'] if constraint else None, sort_order=index,
            metadata={'evidence_policy': 'approved_profile_application', 'evidence_entity_id': row['source_entity_id'],
                      'planning_build_id': str(build.pk), 'planning_build_fingerprint': build.fingerprint,
                      'planning_build_activity_id': row['id'], 'property_lineage': deepcopy(row['property_lineage']),
                      'workflow_stage_code': row.get('workflow_stage_code'), 'workflow_progress_weight': row.get('progress_weight'),
                      'source_planned_start': row.get('start_date'), 'source_planned_finish': row.get('finish_date'),
                      'duration_source': ('approved_planning_rule' if row['kind'] == 'workflow_activity' else
                                          'planner' if row['property_lineage'].get('duration', {}).get('type') == 'approved_planning_input' else 'source_document'),
                      'property_provenance': {prop: value['fact_ids'][0] for prop, value in row['property_lineage'].items()
                                              if value.get('fact_ids') and value['type'] in {'document_evidence', 'approved_planning_input'}}})
    for row in plan['relationships']:
        ActivityRelationship.objects.create(version=version, predecessor=activities[row['predecessor_id']], successor=activities[row['successor_id']],
            relationship_type=row['type'], lag_days=row['lag'], metadata={'planning_build_id': str(build.pk),
                'planning_build_fingerprint': build.fingerprint, 'lineage': deepcopy(row['lineage']), 'lag_unit': row['lag_unit']})
    record_event(project=project, actor=actor, action='planning_build.applied', entity=build,
                 after={'version_id': version.pk, 'fingerprint': build.fingerprint, 'reason': reason or build.reason})
    return version


def _calendar_record(calendar):
    if calendar is None:
        return None
    return {'working_weekdays': calendar.working_weekdays, 'hours_per_day': float(calendar.hours_per_day),
            'timezone': calendar.timezone, 'working_times': calendar.working_times,
            'exceptions': [{'date': str(item.date), 'is_working': item.is_working, 'working_times': item.working_times,
                            **({'working_hours': float(item.working_hours)} if item.working_hours is not None else {})}
                           for item in calendar.exceptions.filter(is_deleted=False).order_by('date')]}


def build_input_validation(version):
    """Verify actual inputs against the immutable applied build, never metadata claims."""
    build = version.planning_build
    if not build:
        return [_issue('planning_build_not_attached', 'This version has no persisted planning build.')]
    issues = deepcopy(build.issues)
    project = version.schedule.project
    if build.project_id != project.pk or build.fingerprint != _hash(_payload(build)):
        return [_issue('planning_build_integrity', 'The build identity or immutable snapshot does not match this project.')]
    graph = EvidenceGraph.objects.filter(pk=build.evidence_graph_id, project=project).first()
    if not graph or graph.revision != build.evidence_revision or graph.source_fingerprint != build.source_fingerprint or input_fingerprint(project) != build.source_fingerprint:
        issues.append(_issue('planning_build_evidence_stale', 'Evidence changed since this planning build. Review the impact and prepare a new build.'))
    profile = planning_profile_selection(project)
    if not profile['valid'] or profile['revision'] != build.profile_selection_revision or profile['content_fingerprint'] != build.profile_fingerprint:
        issues.append(_issue('planning_build_profile_stale', 'The selected approved profile differs from the build snapshot.'))
    if version.evidence_graph_id != build.evidence_graph_id or version.evidence_graph_revision != build.evidence_revision:
        issues.append(_issue('planning_build_evidence_binding', 'The schedule evidence binding differs from its build.'))
    plan = build.plan
    calendar_cache = {}
    def calendar_value(calendar):
        key = calendar.pk if calendar else None
        if key not in calendar_cache:
            calendar_cache[key] = _calendar_normal(_calendar_record(calendar))
        return calendar_cache[key]
    if str(version.schedule.planned_start) != plan['project_inputs'].get('project_start') or str(project.planned_end_date) != plan['project_inputs'].get('project_finish'):
        issues.append(_issue('planning_build_project_dates_changed', 'Registered or schedule dates differ from the accepted build window.', 'project', 'project_start'))
    if calendar_value(version.schedule.default_calendar) != _calendar_normal(plan['project_inputs'].get('calendar')):
        issues.append(_issue('planning_build_calendar_changed', 'The calendar differs from the approved build snapshot.', 'project', 'calendar'))
    expected_nodes = {node['id']: node for node in plan['wbs']}
    expected_by_code = {node['code']: node for node in plan['wbs']}
    nodes = list(version.wbs_nodes.filter(is_deleted=False).select_related('parent'))
    if Counter(node.code for node in nodes) != Counter(expected_by_code.keys()):
        issues.append(_issue('planning_build_wbs_scope_changed', 'WBS scope differs from the immutable build.', field='wbs'))
    for node in nodes:
        expected = expected_by_code.get(node.code)
        if not expected:
            continue
        expected_parent = expected_nodes.get(expected['parent_id'])
        if (node.name, node.level, node.sort_order, node.discipline, node.parent.code if node.parent else None) != (
                expected['name'], expected['level'], expected['sort_order'], expected['discipline'], expected_parent['code'] if expected_parent else None):
            issues.append(_issue('planning_build_wbs_changed', 'WBS identity, hierarchy or ordering differs from its build.', str(node.pk), 'wbs'))
    expected_activities = {row['id']: row for row in plan['activities']}
    actual_activities = list(version.activities.filter(is_deleted=False).select_related('wbs_node', 'calendar'))
    if Counter(row.external_id for row in actual_activities) != Counter(expected_activities.keys()):
        issues.append(_issue('planning_build_activity_scope_changed', 'The schedule activity set differs from the immutable build.', field='identity'))
    for activity in actual_activities:
        row = expected_activities.get(activity.external_id)
        if not row:
            continue
        constraints = [] if activity.constraint_type == 'none' else [{'type': activity.constraint_type, 'date': str(activity.constraint_date)}]
        actual = {'identity': activity.name, 'duration': {'value': float(activity.duration_days), 'unit': 'working_days'},
                  'activity_type': activity.activity_type, 'constraints': constraints, 'responsible_role': activity.responsible_role or None,
                  'calendar': calendar_value(activity.calendar or version.schedule.default_calendar),
                  'wbs': activity.wbs_node.code if activity.wbs_node else None}
        expected = {'identity': row['name'], 'duration': {key: row['duration'].get(key) for key in ('value', 'unit')},
                    'activity_type': row['activity_type'], 'constraints': row['constraints'], 'responsible_role': row['responsible_role'],
                    'calendar': _calendar_normal(row['calendar']), 'wbs': expected_nodes[row['wbs_id']]['code'] if row['wbs_id'] else None}
        for prop, value in expected.items():
            if actual[prop] != value:
                issues.append(_issue('planning_build_input_changed', f'The activity {prop} differs from its accepted source or approved policy application.', row['id'], prop))
        metadata = activity.metadata or {}
        if (metadata.get('planning_build_id') != str(build.pk) or metadata.get('planning_build_fingerprint') != build.fingerprint
                or metadata.get('planning_build_activity_id') != row['id'] or metadata.get('property_lineage') != row['property_lineage']
                or metadata.get('source_planned_start') != row.get('start_date') or metadata.get('source_planned_finish') != row.get('finish_date')):
            issues.append(_issue('planning_build_lineage_changed', 'Activity provenance differs from the immutable field bindings.', row['id'], 'provenance'))
    def key(link):
        return (link['predecessor_id'], link['successor_id'], link['type'], str(Decimal(str(link['lag'])).normalize()), link['lag_unit'])
    actual_links = list(version.relationships.filter(is_deleted=False).select_related('predecessor', 'successor'))
    actual_values = [{'predecessor_id': link.predecessor.external_id, 'successor_id': link.successor.external_id,
                      'type': link.relationship_type, 'lag': link.lag_days, 'lag_unit': (link.metadata or {}).get('lag_unit')} for link in actual_links]
    if Counter(key(link) for link in actual_values) != Counter(key(link) for link in plan['relationships']):
        issues.append(_issue('planning_build_relationships_changed', 'The dependency network differs from the immutable build.', field='dependencies'))
    expected_links = {key(link): link for link in plan['relationships']}
    for link, values in zip(actual_links, actual_values):
        expected = expected_links.get(key(values))
        if expected and (link.metadata.get('lineage') != expected['lineage'] or link.metadata.get('planning_build_fingerprint') != build.fingerprint):
            issues.append(_issue('planning_build_lineage_changed', 'Relationship provenance differs from the immutable rule/fact binding.', str(link.pk), 'dependencies'))
    return issues


def build_manifest(version):
    build = version.planning_build
    return {'id': str(build.pk), 'fingerprint': build.fingerprint, **deepcopy(_payload(build))} if build else None


def build_provenance(version):
    """Only verified persisted build fields may receive an approved-rule badge."""
    if not version.planning_build_id or any(issue.get('severity') != 'warning' for issue in build_input_validation(version)):
        return {}
    return {row['id']: deepcopy(row['property_lineage']) for row in version.planning_build.plan['activities']}
