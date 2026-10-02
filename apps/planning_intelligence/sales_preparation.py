"""Exact, permission-scoped Planning evidence for Sales proposal preparation.

This adapter never generates a plan or alters a proposal. Frozen technical
content and current version-scoped resource/risk evidence are labelled separately.
Money, employee identities and private corporate personnel evidence are excluded.
"""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json

from django.core.serializers.json import DjangoJSONEncoder
from django.db.models import F, Q
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.core.shared_record_targets import organization_id
from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects, can_write_project
from .models import PlanningGeneration, PlanningProject, ScheduleResource, ScheduleVersion, TechnicalProposal
from .services.operational_jobs import canonical_fingerprint
from .services.planning_registers import risk_snapshot
from .serializers import _json_safe


ADAPTER_VERSION = 'sales-preparation/1'
PAGE_SIZE = 25
NARRATIVE_KEYS = {'requirements', 'scope', 'solution_architecture', 'data_flow', 'methodology',
                  'assumptions', 'exclusions', 'risk_mitigation'}


def _json(value):
    return json.loads(json.dumps(_json_safe(value), cls=DjangoJSONEncoder, allow_nan=False))


def _sales_access(actor, deal):
    from apps.sales.opportunity_workspace import require_access
    require_access(actor, deal)


def _organization(deal):
    values = {value for value in (organization_id(deal.owner), organization_id(deal.client.account_manager)) if value}
    if len(values) > 1:
        raise PermissionDenied('The opportunity ownership needs an organization review before preparation.')
    return next(iter(values), None)


def eligible_projects(actor, deal):
    _sales_access(actor, deal)
    queryset = accessible_projects(actor)
    if not actor.is_active or not module_action_allowed(actor, 'planning_package', 'read'):
        return queryset.none()
    organization = _organization(deal)
    if not organization:
        return queryset.none()
    if not (actor.is_staff or actor.is_superuser) and organization_id(actor) != organization:
        return queryset.none()
    queryset = queryset.filter(
        Q(enterprise_project__isnull=True, created_by__rbac_profile__organization_id=organization,
          created_by__rbac_profile__is_deleted=False)
        | Q(enterprise_project__owner__rbac_profile__organization_id=organization,
            enterprise_project__owner__rbac_profile__is_deleted=False)
    )
    project_scope = Q(enterprise_project__isnull=True)
    if deal.converted_project_id:
        project_scope |= Q(enterprise_project_id=deal.converted_project_id)
    queryset = queryset.filter(project_scope).filter(
        Q(enterprise_project__client__isnull=True) | Q(enterprise_project__client_id=deal.client_id)
    )
    # The binding model is owned by Sales. It has no permission-granting effect.
    return queryset.filter(Q(sales_bid_preparation__isnull=True)
                           | Q(sales_bid_preparation__opportunity_id=deal.pk)).distinct()


def require_project(actor, deal, project_id, *, write=False, lock=False):
    _sales_access(actor, deal)
    if not module_action_allowed(actor, 'planning_package', 'read'):
        raise PermissionDenied('Planning read access is required for proposal preparation.')
    try:
        project_id = int(project_id)
    except (TypeError, ValueError):
        raise ValidationError({'planning_project_id': 'Select a valid planning workspace.'}) from None
    query = eligible_projects(actor, deal).filter(pk=project_id)
    # PostgreSQL cannot lock DISTINCT joins; lock the selected base row, then
    # repeat the actual scope query before returning it.
    if lock:
        project = PlanningProject.objects.select_for_update(no_key=True).filter(pk=project_id).first()
        if project is None or not query.exists():
            raise NotFound('This planning workspace is unavailable.')
    else:
        project = query.select_related('enterprise_project', 'created_by').first()
    if project is None:
        raise NotFound('This planning workspace is unavailable.')
    if write and (not module_action_allowed(actor, 'planning_package', 'update')
                  or not can_write_project(actor, project)):
        raise PermissionDenied('You cannot connect this planning workspace.')
    return project


def _source_data(proposal):
    version = proposal.schedule_version
    generation = proposal.source_generation
    project = proposal.project
    return {'id': proposal.pk, 'technical_proposal_id': proposal.pk,
            'proposal_number': proposal.proposal_number, 'revision': proposal.revision,
            'title': proposal.title, 'status': proposal.status, 'updated_at': proposal.updated_at.isoformat(),
            'planning_project_id': project.pk, 'planning_project_name': project.name,
            'schedule_id': version.schedule_id, 'schedule_version_id': version.pk,
            'schedule_version': version.version, 'schedule_status': version.status,
            'generation_id': generation.pk if generation else None,
            'generation_version': generation.version if generation else None,
            'links': {'planning': f'/planning-workspace/{project.pk}',
                      'technical': f'/proposal-workspace/{project.pk}?proposalId={proposal.pk}',
                      'schedule': f'/planning-workspace/{project.pk}'}}


def source_candidates(actor, deal, project, search='', page=1):
    project = require_project(actor, deal, project.pk)
    try:
        page = int(page)
    except (ValueError, TypeError):
        raise ValidationError({'page': 'Choose a positive page number.'}) from None
    if page < 1 or page > 100000:
        raise ValidationError({'page': 'Choose a positive page number.'})
    query = TechnicalProposal.objects.filter(project=project, is_deleted=False,
        schedule_version__is_deleted=False, schedule_version__schedule__is_deleted=False,
        schedule_version__schedule__project=project).filter(
            Q(source_generation__isnull=True, schedule_version__source_generation__isnull=True)
            | Q(source_generation__project=project, source_generation__is_deleted=False,
                source_generation_id=F('schedule_version__source_generation_id'))
        ).select_related('project', 'schedule_version', 'source_generation')
    if search:
        query = query.filter(Q(title__icontains=str(search)[:200]) | Q(proposal_number__icontains=str(search)[:200]))
    count = query.count()
    rows = query.order_by('-revision', '-pk')[(page - 1) * PAGE_SIZE:page * PAGE_SIZE]
    return {'results': [_source_data(row) for row in rows], 'count': count, 'page': page,
            'page_size': PAGE_SIZE, 'has_more': page * PAGE_SIZE < count}


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
        return str(number) if number.is_finite() and number >= 0 else None
    except (ValueError, TypeError, InvalidOperation):
        return None


def _effort(value):
    if not isinstance(value, dict):
        return None
    total = _number(value.get('grand_total_man_hours', value.get('total')))
    rows = []
    for row in value.get('by_discipline', []) if isinstance(value.get('by_discipline'), list) else []:
        if not isinstance(row, dict):
            continue
        hours = _number(row.get('man_hours'))
        if hours is None:
            continue
        rows.append({'discipline': str(row.get('discipline') or ''),
                     'discipline_name': str(row.get('discipline_name') or ''), 'man_hours': hours})
    if total is None or (Decimal(total) == 0 and not rows):
        return None
    basis = value.get('basis') if isinstance(value.get('basis'), dict) else {}
    safe_basis = {key: _number(basis[key]) for key in ('hours_per_day', 'man_days_per_month') if key in basis}
    if isinstance(basis.get('assumption'), str):
        safe_basis['assumption'] = basis['assumption']
    return {'total': total, 'by_discipline': rows, 'basis': safe_basis}


def _meaningful(value):
    if not isinstance(value, str) or not value.strip():
        return None
    value = value.strip()
    if value.startswith(('Not specified in the available project references.',
                         'The current controlled planning basis covers these disciplines:')):
        return None
    return value


def _list_items(value, allowed):
    if not isinstance(value, list):
        return []
    return [({key: deepcopy(row[key]) for key in allowed if isinstance(row.get(key), (str, int, float, bool))}
             if isinstance(row, dict) else row) for row in value if isinstance(row, (dict, str))]


def _section_items(section):
    if not section:
        return []
    items = _list_items(section.get('data'), ('title', 'description', 'text', 'assumption', 'exclusion'))
    items = [row for row in items if row]
    if items:
        return items
    content = _meaningful(section.get('content'))
    return [content] if content else []


def source_snapshot(actor, deal, project, technical_id, *, lock=False):
    project = require_project(actor, deal, project.pk, lock=lock)
    try:
        technical_id = int(technical_id)
    except (ValueError, TypeError):
        raise ValidationError({'technical_proposal_id': 'Select an exact technical proposal revision.'}) from None
    query = TechnicalProposal.objects.filter(pk=technical_id, project=project, is_deleted=False)
    if lock:
        query = query.select_for_update(of=('self',))
    proposal = query.select_related('project', 'schedule_version__schedule', 'source_generation').first()
    if proposal is None:
        raise NotFound('This technical proposal revision is unavailable.')
    version, generation = proposal.schedule_version, proposal.source_generation
    if (version.is_deleted or version.schedule.is_deleted or version.schedule.project_id != project.pk
            or generation and (generation.is_deleted or generation.project_id != project.pk)
            or proposal.source_generation_id != version.source_generation_id):
        raise ValidationError({'technical_proposal_id': 'This technical revision has inconsistent planning sources.'})
    if lock:
        # Resource allocation commands lock resource before version. Matching
        # that order avoids deadlocks and freezes assigned resource evidence.
        list(ScheduleResource.objects.select_for_update().filter(project=project).order_by('pk'))
        version = ScheduleVersion.objects.select_for_update().select_related('schedule').get(pk=version.pk)
        if generation:
            generation = PlanningGeneration.objects.select_for_update().get(pk=generation.pk)
        proposal.schedule_version, proposal.source_generation = version, generation
        if (version.is_deleted or version.schedule.is_deleted or version.schedule.project_id != project.pk
                or generation and (generation.is_deleted or generation.project_id != project.pk)
                or proposal.source_generation_id != version.source_generation_id):
            raise ValidationError({'technical_proposal_id': 'This technical revision has inconsistent planning sources.'})
    snapshot = proposal.snapshot if isinstance(proposal.snapshot, dict) else {}
    frozen_version = snapshot.get('schedule') if isinstance(snapshot.get('schedule'), dict) else {}
    frozen_generation = snapshot.get('generation') if isinstance(snapshot.get('generation'), dict) else {}
    if ((frozen_version.get('version_id') is not None and str(frozen_version['version_id']) != str(version.pk))
            or (frozen_generation.get('id') is not None and str(frozen_generation['id']) != str(proposal.source_generation_id))):
        raise ValidationError({'technical_proposal_id': 'The frozen technical evidence does not match its source revision.'})
    sections = {row['key']: row for row in proposal.sections if isinstance(row, dict)
                and isinstance(row.get('key'), str) and row.get('included', True)}
    proposed, warnings = {}, []
    scope = _meaningful((sections.get('scope') or {}).get('content')) or _meaningful((sections.get('requirements') or {}).get('content'))
    if scope:
        proposed['scope'] = scope
    deliverables = _list_items((sections.get('deliverables') or {}).get('data', snapshot.get('deliverables')),
                              ('document_number', 'title', 'name', 'description', 'discipline', 'revision', 'status')) if 'deliverables' in sections else []
    if deliverables:
        proposed['deliverables'] = deliverables
    for key in ('assumptions', 'exclusions'):
        value = _section_items(sections.get(key))
        if value:
            proposed[key] = value
    disciplines = [row.get('name') for row in snapshot.get('disciplines', []) if isinstance(row, dict) and row.get('name')]
    if disciplines:
        proposed['disciplines'] = disciplines
    effort = _effort(snapshot.get('manhours'))
    if effort:
        proposed['estimated_hours'] = effort
    else:
        warnings.append('This technical snapshot has no known total effort. Existing commercial estimated hours are preserved.')
    resources = list(project.schedule_resources.filter(is_deleted=False,
        assignments__activity__version=version, assignments__activity__is_deleted=False,
        assignments__is_deleted=False).distinct().order_by('pk').values('id', 'code', 'name', 'role', 'resource_type', 'unit'))
    assignments = list(version.activities.filter(is_deleted=False, assignments__is_deleted=False,
        assignments__resource__is_deleted=False).order_by('pk', 'assignments__pk').values(
            'id', 'external_id', 'assignments__id', 'assignments__resource_id',
            'assignments__planned_units', 'assignments__budgeted_hours'))
    assignments = [{'id': row['assignments__id'], 'activity_id': row['id'], 'activity_code': row['external_id'],
                    'resource_id': row['assignments__resource_id'], 'planned_units': str(row['assignments__planned_units']),
                    'budgeted_hours': str(row['assignments__budgeted_hours'])} for row in assignments if row['assignments__id']]
    risks = [{key: value for key, value in row.items() if key in {
        'id', 'version_id', 'source_key', 'title', 'description', 'status', 'priority', 'response', 'resolution',
        'revision', 'probability_percent', 'schedule_impact_days', 'mitigation_due_date', 'mitigation_status', 'updated_at'}}
        for row in risk_snapshot(version)]
    if risks:
        proposed['risks'] = risks
    if proposal.status not in {'approved', 'issued'}:
        warnings.append('Technical content is a preparation draft; importing it does not establish technical or commercial approval.')
    if version.status == 'superseded':
        warnings.append('The selected schedule version is superseded. Its exact evidence remains historical.')
    if not project.enterprise_project_id:
        warnings.append('Final technical approval requires the existing enterprise-project authority. This pre-award link grants no approval authority.')
    warnings.append('Resource allocations and risk assessments are current records for the selected schedule version; they are separate from the frozen technical snapshot.')
    evidence = {'basis': ADAPTER_VERSION,
        'technical': {'snapshot_captured_at': snapshot.get('captured_at'),
            'sections': [{key: row.get(key) for key in ('key', 'title', 'content', 'source', 'readiness')}
                         for row in sections.values() if row['key'] in NARRATIVE_KEYS]},
        'schedule': {key: frozen_version.get(key) for key in ('id', 'version_id', 'version', 'status', 'planned_start', 'calculated_finish', 'activity_count', 'wbs_count', 'milestone_count')},
        'generation': {'id': generation.pk, 'version': generation.version} if generation else None,
        'deliverables': deliverables, 'effort': effort, 'resources': resources, 'assignments': assignments, 'risks': risks,
        'frozen_resources': _list_items(snapshot.get('resources'), ('code', 'name', 'role', 'type')),
        'provenance': {'technical_basis': 'Selected technical revision sections and frozen snapshot',
            'resource_basis': 'Current assignments for the exact selected schedule version',
            'risk_basis': 'Current nonfinancial risk register for the exact selected schedule version',
            'generation_basis': 'Exact linked generation only; no latest-generation fallback',
            'commercial_basis': 'No prices, rates, risk costs, currencies or financial approvals imported'}}
    source = _source_data(proposal)
    fingerprint = {'adapter_version': ADAPTER_VERSION, 'source': source,
        'project_updated_at': project.updated_at.isoformat(), 'technical_content': canonical_fingerprint({
            'snapshot': proposal.snapshot, 'sections': proposal.sections, 'client_name': proposal.client_name,
            'opportunity_reference': proposal.opportunity_reference}),
        'version_updated_at': version.updated_at.isoformat(),
        'generation_updated_at': generation.updated_at.isoformat() if generation else None,
        'evidence': canonical_fingerprint(evidence), 'proposed_fields': canonical_fingerprint(proposed)}
    return _json({'source': source, 'evidence': evidence, 'proposed_fields': proposed,
                  'warnings': warnings, 'fingerprint': fingerprint})


def bound_sales_origin(project, actor):
    """Only a currently authorized Sales reader may carry Sales data into Planning."""
    from apps.sales.models import BidPreparation
    binding = BidPreparation.objects.filter(planning_project=project).select_related('opportunity__client', 'opportunity__owner').first()
    if binding is None:
        return None
    deal = binding.opportunity
    require_project(actor, deal, project.pk)
    return {'opportunity_id': str(deal.pk), 'opportunity_reference': deal.deal_code,
            'client_id': str(deal.client_id), 'client_name': deal.client.company_name,
            'client_reference': deal.client_reference, 'tender_title': deal.deal_name,
            'submission_date': deal.submission_due_date.isoformat() if deal.submission_due_date else None}


def validate_bound_enterprise_project(project, target):
    from apps.sales.models import BidPreparation
    binding = BidPreparation.objects.filter(planning_project=project).select_related('opportunity').first()
    if binding and (not target or binding.opportunity.converted_project_id != target.pk):
        raise ValidationError('This bid workspace can only connect to its opportunity’s converted project.')
