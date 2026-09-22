"""Evidence-controlled translation of a Schedule Basis into generation rules."""
from __future__ import annotations

import re
from collections import defaultdict, deque
from decimal import Decimal

from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from ..models import (
    GenerationDecisionGate, GenerationDependency, GenerationPhase, GenerationPlan,
    PlanDeliverable, ScheduleBasis,
)


FAMILY_TEMPLATE_CODES = {
    'engineering_document': 'STANDARD_5_STAGE', 'inspection_report': 'INSPECTION_REPORT',
    'technical_study': 'TECHNICAL_STUDY', 'drawing': 'DRAWING',
    'plan_procedure': 'PLAN_PROCEDURE', 'recurring_report': 'RECURRING_REPORT',
    'tender_package': 'TENDER_PACKAGE', 'final_dossier': 'FINAL_DOSSIER',
    'cost_estimate': 'COST_ESTIMATE',
}


def _range_count(document_number):
    match = re.search(r'(\d+)\s*~\s*(\d+)', document_number or '')
    if not match:
        return None
    start, finish = int(match.group(1)), int(match.group(2))
    return finish - start + 1 if finish >= start else None


def classify_deliverable(deliverable):
    """Names identify documents; they do not establish workflow or execution order."""
    return {
        'workflow_family': 'not_specified', 'recurrence': 'none', 'recurrence_count': 1,
        'scenario_code': 'common', 'technical_sequence': 0,
        'classification_reason': 'Not Specified: no source-defined workflow, recurrence or execution sequence.',
    }


def refresh_generation_plan_readiness(plan, *, save=True):
    proposed = plan.dependencies.filter(is_deleted=False, status='proposed').count()
    branches = set(plan.deliverables.filter(is_deleted=False).exclude(scenario_code='common').values_list('scenario_code', flat=True))
    blockers = []
    if not plan.deliverables.filter(is_deleted=False).exists():
        blockers.append('No confirmed Schedule Basis deliverables are available.')
    if proposed:
        blockers.append(f'Review {proposed} proposed technical dependency link(s).')
    if branches and plan.selected_scenario not in branches:
        blockers.append('Select the contractual execution scenario for conditional scope.')
    confirmed_links = list(plan.dependencies.filter(is_deleted=False, status='confirmed').values_list('predecessor_id', 'successor_id'))
    nodes = set(plan.deliverables.filter(is_deleted=False).values_list('id', flat=True))
    indegree = {node: 0 for node in nodes}
    outgoing = defaultdict(list)
    for predecessor, successor in confirmed_links:
        if predecessor in nodes and successor in nodes:
            outgoing[predecessor].append(successor)
            indegree[successor] += 1
    queue = deque(node for node, count in indegree.items() if count == 0)
    visited = 0
    while queue:
        node = queue.popleft()
        visited += 1
        for successor in outgoing[node]:
            indegree[successor] -= 1
            if indegree[successor] == 0:
                queue.append(successor)
    has_cycle = bool(nodes and visited != len(nodes))
    if has_cycle:
        blockers.append('Confirmed technical dependencies contain a cycle.')
    plan.readiness = {
        'ready': not blockers, 'blockers': blockers,
        'deliverable_count': plan.deliverables.filter(is_deleted=False).count(),
        'phase_count': plan.phases.filter(is_deleted=False).count(),
        'proposed_dependencies': proposed,
        'confirmed_dependencies': plan.dependencies.filter(is_deleted=False, status='confirmed').count(),
        'rejected_dependencies': plan.dependencies.filter(is_deleted=False, status='rejected').count(),
        'available_scenarios': sorted(branches),
        'has_dependency_cycle': has_cycle,
    }
    if plan.status not in ('approved', 'superseded'):
        plan.status = 'ready' if not blockers else 'draft'
    if save:
        plan.save(update_fields=['readiness', 'status', 'updated_at'])
    return plan.readiness


def _references(*entries):
    seen, result = set(), []
    for entry in entries:
        for reference in entry.basis_deliverable.source_references or []:
            key = (reference.get('file_id'), str(reference.get('locator')))
            if key in seen:
                continue
            seen.add(key)
            result.append(reference)
    return result[:6]


def _document_phase_evidence(basis):
    result = {}
    files = basis.project.files.filter(
        id__in=basis.source_run.source_file_ids, is_deleted=False, parse_status='done',
    )
    for file_obj in files:
        for line_number, line in enumerate((file_obj.extracted_text or '').splitlines(), 1):
            clean = re.sub(r'\s+', ' ', line).strip()
            match = re.search(r'(?P<months>\d+(?:\.\d+)?)\s*months?\b', clean, re.I)
            if not match:
                continue
            lower = clean.casefold()
            code = None
            if any(term in lower for term in ('offshore', 'site activit', 'field activit', 'inspection activit')):
                code = 'fieldwork'
            elif any(term in lower for term in ('engineering', 'project management', 'design activit', 'study activit')):
                code = 'engineering'
            if not code:
                continue
            evidence = result.setdefault(code, {'duration_months': Decimal(match.group('months')), 'references': []})
            evidence['references'].append({
                'file_id': file_obj.id, 'filename': file_obj.original_filename,
                'category': file_obj.category, 'locator': {'line': line_number}, 'excerpt': clean[:1000],
            })
    return result


@transaction.atomic
def build_generation_plan(basis):
    basis = ScheduleBasis.objects.select_for_update().get(pk=basis.pk)
    if basis.status != 'approved':
        raise ValueError('Approve the Schedule Basis before building a Generation Plan.')
    next_version = (basis.project.generation_plans.aggregate(value=Max('version'))['value'] or 0) + 1
    plan = GenerationPlan.objects.create(project=basis.project, basis=basis, version=next_version)
    plan_entries = []
    for deliverable in basis.deliverables.filter(is_deleted=False, status='confirmed').order_by('id'):
        plan_entries.append(PlanDeliverable(plan=plan, basis_deliverable=deliverable, **classify_deliverable(deliverable)))
    PlanDeliverable.objects.bulk_create(plan_entries)
    # A deliverable title or discipline is not evidence of a phase, dependency,
    # scenario or approval gate. Explicit source activities/relationships are
    # recovered by document_plan, with per-field references and unresolved gaps.
    refresh_generation_plan_readiness(plan)
    return plan


@transaction.atomic
def approve_generation_plan(plan, user):
    plan = GenerationPlan.objects.select_for_update().get(pk=plan.pk)
    from ..access import require_planning_approval, current_generation_plan
    require_planning_approval(user, plan.project, current=current_generation_plan(plan))
    readiness = refresh_generation_plan_readiness(plan)
    if not readiness['ready']:
        raise ValueError('Generation Plan is not ready: ' + ' '.join(readiness['blockers']))
    GenerationPlan.objects.filter(project=plan.project, status='approved', is_deleted=False).exclude(pk=plan.pk).update(status='superseded')
    plan.status = 'approved'
    plan.approved_by = user
    plan.approved_at = timezone.now()
    plan.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
    return plan


def apply_approved_generation_plan(project, intelligence):
    plan = project.generation_plans.filter(
        is_deleted=False, status='approved', basis__status='approved',
    ).select_related('basis').first()
    if not plan:
        return intelligence
    result = dict(intelligence)
    result.update({
        'generation_plan_id': plan.id, 'generation_plan_version': plan.version,
        'generation_plan_status': plan.status, 'selected_scenario': plan.selected_scenario,
    })
    return result
