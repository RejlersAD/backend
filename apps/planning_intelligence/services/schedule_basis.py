"""Compile reviewed document evidence into a controlled schedule-input basis."""
from __future__ import annotations

import datetime
import hashlib
import re
from collections import defaultdict

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify

from ..config import DISCIPLINE_NAME_BY_CODE
from .identity_policy import IDENTITY_POLICY_VERSION, occurrence_key, stable_digest
from .register_rows import register_row_requires_review
from ..models import BasisDeliverable, DocumentAuthorityRule, ScheduleBasis


FACT_INFORMATION = {
    'effective_date': 'contract_dates', 'duration_months': 'contract_dates',
    'project_name': 'scope', 'client': 'scope', 'location': 'scope',
    'deliverable': 'deliverables', 'calendar': 'calendar',
}


def _as_date(value):
    if isinstance(value, datetime.date):
        return value
    raw = str(value or '').strip()
    parsed = parse_date(raw)
    if parsed:
        return parsed
    for pattern in ('%d-%b-%Y', '%d/%b/%Y', '%d-%B-%Y', '%d/%m/%Y'):
        try:
            return datetime.datetime.strptime(raw, pattern).date()
        except ValueError:
            continue
    return None


def _authority_lookup():
    return {
        (rule.information_type, rule.document_category): rule.priority
        for rule in DocumentAuthorityRule.objects.filter(is_deleted=False)
    }


def _scalar(run, fact_type, fallback, authority, *, issues=None):
    """Only a recorded acceptance selects a scalar; scores are not precedence."""
    facts = list(run.facts.filter(
        is_deleted=False, fact_type=fact_type,
    ).exclude(status__in=['rejected', 'superseded']).select_related('source_file'))
    if not facts:
        return fallback  # Existing project-record input, not an extracted value.
    accepted = [fact for fact in facts if fact.status == 'confirmed'
                and getattr(fact, 'reviewed_by_id', None) is not None
                and getattr(fact, 'reviewed_at', None) is not None]
    accepted_values = {stable_digest(fact.value) for fact in accepted}
    if len(accepted_values) == 1 and not any(fact.status == 'conflicted' for fact in facts):
        return accepted[0].value
    if issues is not None:
        values = {stable_digest(fact.value) for fact in facts}
        issues.append({
            'code': 'scalar_value_conflict' if len(values) > 1 else 'scalar_acceptance_required',
            'field': fact_type, 'fact_ids': [fact.pk for fact in facts],
            'source_references': [_source_reference(fact) for fact in facts],
            'message': f'Review and accept the {fact_type.replace("_", " ")} value. Source priority and confidence do not resolve it.',
            'blocks': ['calculation', 'approval'],
        })
    return None


def _name_key(value):
    return slugify(re.sub(r'\s+', ' ', str(value or '')).strip())[:300] or 'unnamed'


def _source_reference(fact):
    return {
        'fact_id': fact.id, 'project_id': fact.run.project_id,
        'file_id': fact.source_file_id,
        'extracted_text_sha256': (fact.source_locator or {}).get('extracted_text_sha256'),
        'filename': fact.source_file.original_filename if fact.source_file_id else 'Planner / AI review',
        'category': fact.source_file.category if fact.source_file_id else fact.extraction_method,
        'locator': fact.source_locator or {},
        'excerpt': fact.source_excerpt,
    }


def _preview_selection(preview):
    selected = {}
    for discipline, info in (preview.get('disciplines') or {}).items():
        excluded = set(info.get('excluded_deliverables') or [])
        selected[discipline] = [name for name in info.get('deliverables') or []
                                if info.get('in_scope') is not False and name not in excluded]
    if (preview.get('disciplines') or {}).get('hse', {}).get('in_scope') is not False:
        selected.setdefault('hse', []).extend(preview.get('hse_studies') or [])
    return selected


def _fact_deliverable_row(fact):
    value = fact.value if isinstance(fact.value, dict) else {'name': str(fact.value)}
    title = value.get('original_title') or value.get('name') or ''
    if not title or (not fact.source_file_id and fact.extraction_method != 'manual'):
        return None
    reference = _source_reference(fact)
    identity = occurrence_key(reference, identifier=value.get('document_number'), fact_id=fact.pk)
    requires_source_review = register_row_requires_review(value)
    return {
        'discipline': 'hse' if fact.fact_type == 'hse_study' else value.get('discipline') or 'not_specified',
        'canonical_name': title, 'original_title': title,
        'document_number': value.get('document_number') or '',
        'document_revision': value.get('document_revision') or '',
        'confidence': fact.confidence, 'fact_ids': [fact.pk],
        'references': [reference], 'aliases': [], 'confirmed': fact.status == 'confirmed' and not requires_source_review,
        'requires_source_review': requires_source_review,
        'source_identity': identity, 'identity_policy': IDENTITY_POLICY_VERSION,
        'identity_status': 'distinct_source_record',
    }


def _apply_preview_selection(groups, run, preview):
    if preview is None:
        return groups
    selected = _preview_selection(preview)
    for row in groups:
        # Legacy previews select a complete list of source titles. This updates
        # inclusion only; two selected records never become one identity.
        included = row['original_title'] in selected.get(row['discipline'], [])
        row['confirmed'] = included and not row.get('requires_source_review')
        row['excluded'] = not included
    for discipline, names in selected.items():
        for index, name in enumerate(names):
            if any(row['discipline'] == discipline and row['original_title'] == name for row in groups):
                continue
            reference = {
                'fact_id': None, 'file_id': None, 'project_id': run.project_id,
                'filename': 'Confirmed Document Intelligence Preview', 'category': 'planner',
                'locator': {'intelligence_run_id': run.id, 'selection_index': index}, 'excerpt': '',
            }
            groups.append({
                'discipline': discipline, 'canonical_name': name, 'original_title': name,
                'document_number': '', 'document_revision': '', 'confidence': 1.0,
                'fact_ids': [], 'references': [reference], 'aliases': [], 'confirmed': True,
                'excluded': False, 'identity_policy': IDENTITY_POLICY_VERSION,
                'source_identity': occurrence_key(reference, fact_id=f'preview:{run.id}:{discipline}:{index}'),
                'identity_status': 'approved_preview_input',
            })
    return groups


def _register_deliverable_rows(run, preview=None):
    """Every source-register occurrence remains distinct, including duplicates."""
    facts = run.facts.filter(
        is_deleted=False, fact_type='deliverable', value__source_register=True,
    ).exclude(status__in=['rejected', 'superseded', 'conflicted']).select_related('source_file').order_by('id')
    groups = [row for fact in facts if (row := _fact_deliverable_row(fact)) is not None]
    return _apply_preview_selection(groups, run, preview)


def _document_deliverable_rows(run, preview=None):
    """No aliases, title similarity, exact-number merging or cross-file merging."""
    facts = run.facts.filter(
        is_deleted=False, fact_type__in=['deliverable', 'hse_study'] if preview is not None else ['deliverable'],
    ).exclude(status__in=['rejected', 'superseded', 'conflicted']).select_related('source_file').order_by('id')
    groups = [row for fact in facts if (row := _fact_deliverable_row(fact)) is not None]
    return _apply_preview_selection(groups, run, preview)


def _deliverable_rows(run, preview=None):
    base = (run.summary or {}).get('base_intelligence') or {}
    if base.get('deliverable_source') != 'register':
        # Apply the same boundary to historical/legacy runs. A compatibility
        # route must not silently restore approximate identity or AI scope.
        return _document_deliverable_rows(run, preview)
    groups = _register_deliverable_rows(run)
    included_fact_ids = {fact_id for row in groups for fact_id in row['fact_ids']}
    if base.get('document_driven'):
        confirmed_ai = set(run.facts.filter(
            is_deleted=False, fact_type='deliverable', extraction_method='ai',
            status='confirmed', source_file__isnull=False,
        ).values_list('id', flat=True))
        for row in _document_deliverable_rows(run):
            # Distinct assertions are retained even when titles/IDs coincide.
            if confirmed_ai.intersection(row['fact_ids']) and not included_fact_ids.intersection(row['fact_ids']):
                groups.append(row)
                included_fact_ids.update(row['fact_ids'])
    return _apply_preview_selection(groups, run, preview)


def refresh_basis_readiness(basis, *, save=True):
    deliverables = basis.deliverables.filter(is_deleted=False)
    blockers = []
    scalar_issues = (basis.authority_snapshot or {}).get('scalar_review_issues') or []
    blockers.extend(issue['message'] for issue in scalar_issues)
    open_conflicts = basis.source_run.conflicts.filter(
        is_deleted=False, status__in=['open', 'ignored'],
    ).count()
    needs_review = deliverables.filter(status='needs_review').count()
    confirmed = deliverables.filter(status='confirmed').count()
    if open_conflicts:
        blockers.append(f'Resolve {open_conflicts} source-evidence conflict(s).')
    if needs_review:
        blockers.append(f'Review {needs_review} deliverable(s).')
    if not confirmed:
        blockers.append('Confirm at least one deliverable.')
    if not basis.effective_date:
        blockers.append('Confirm the project effective date.')
    if not basis.contractual_finish:
        blockers.append('Confirm the contractual finish date.')
    basis.readiness = {
        'ready': not blockers, 'blockers': blockers, 'open_conflicts': open_conflicts,
        'deliverable_count': deliverables.count(), 'confirmed_deliverables': confirmed,
        'excluded_deliverables': deliverables.filter(status='excluded').count(),
        'deliverables_needing_review': needs_review,
        'scalar_review_issues': scalar_issues,
    }
    if basis.status not in ('approved', 'superseded'):
        basis.status = 'ready' if not blockers else 'draft'
    if save:
        basis.save(update_fields=['readiness', 'status', 'updated_at'])
    return basis.readiness


@transaction.atomic
def build_schedule_basis(run):
    from .preview_confirmation import current_confirmed_preview

    project = run.project
    project = type(project).objects.select_for_update().get(pk=project.pk)
    run.project = project
    preview = current_confirmed_preview(run)
    confirmation = (run.summary or {}).get('preview_confirmation') or {}
    if confirmation and preview is None:
        raise ValueError('Confirm and save the current Document Intelligence Preview before building the schedule basis.')
    authority = _authority_lookup()
    authority_snapshot = {
        info: [
            {'category': rule.document_category, 'priority': rule.priority, 'rationale': rule.rationale}
            for rule in DocumentAuthorityRule.objects.filter(is_deleted=False, information_type=info)
        ]
        for info, _label in DocumentAuthorityRule.INFORMATION_CHOICES
    }
    if preview is not None:
        authority_snapshot['preview_confirmation'] = {
            'confirmed_at': confirmation.get('confirmed_at'),
            'confirmed_by': confirmation.get('confirmed_by'), 'selections': preview,
        }
    scalar_issues = []
    authority_snapshot['scalar_review_issues'] = scalar_issues
    def selected_scalar(key, fact_type, fallback):
        return preview[key] if preview is not None and key in preview else _scalar(run, fact_type, fallback, authority, issues=scalar_issues)

    next_version = (project.schedule_bases.aggregate(value=Max('version'))['value'] or 0) + 1
    basis = ScheduleBasis.objects.create(
        project=project, source_run=run, version=next_version,
        project_name=str(selected_scalar('detected_project_name', 'project_name', project.name) or '')[:255],
        client=str(_scalar(run, 'client', project.client, authority, issues=scalar_issues) or '')[:255],
        location=str(_scalar(run, 'location', project.location, authority, issues=scalar_issues) or '')[:255],
        effective_date=_as_date(selected_scalar('detected_effective_date_text', 'effective_date', project.effective_date)),
        contractual_finish=project.planned_end_date,
        duration_months=selected_scalar('detected_duration_months', 'duration_months', project.duration_months),
        calendar=dict(project.calendar_overrides or {}),
        authority_snapshot=authority_snapshot,
    )
    BasisDeliverable.objects.bulk_create([
        BasisDeliverable(
            basis=basis, discipline=row['discipline'],
            canonical_key=(
                'register-' + hashlib.sha256(row['source_identity'].encode()).hexdigest()
                if row.get('source_identity') else
                f"{_name_key(row['canonical_name'])}--{_name_key(row['document_number'])}"
                if row['document_number'] else _name_key(row['canonical_name'])
            )[:320], canonical_name=row['canonical_name'],
            original_title=row['original_title'], document_number=row['document_number'],
            document_revision=row['document_revision'],
            status='excluded' if row.get('excluded') else 'confirmed' if row['confirmed'] else 'needs_review',
            confidence=row['confidence'],
            source_fact_ids=row['fact_ids'], source_references=row['references'], aliases=row['aliases'],
        ) for row in _deliverable_rows(run, preview)
    ])
    refresh_basis_readiness(basis)
    return basis


@transaction.atomic
def approve_schedule_basis(basis, user):
    basis = ScheduleBasis.objects.select_for_update().get(pk=basis.pk)
    from ..access import require_planning_approval, current_basis
    require_planning_approval(user, basis.project, current=current_basis(basis))
    readiness = refresh_basis_readiness(basis)
    if not readiness['ready']:
        raise ValueError('Schedule Basis is not ready: ' + ' '.join(readiness['blockers']))
    ScheduleBasis.objects.filter(
        project=basis.project, status='approved', is_deleted=False,
    ).exclude(pk=basis.pk).update(status='superseded')
    basis.status = 'approved'
    basis.approved_by = user
    basis.approved_at = timezone.now()
    basis.save(update_fields=['status', 'approved_by', 'approved_at', 'updated_at'])
    return basis


def apply_approved_basis(project, intelligence):
    basis = project.schedule_bases.filter(is_deleted=False, status='approved').prefetch_related('deliverables').first()
    if not basis:
        return intelligence
    run = project.intelligence_runs.filter(is_deleted=False, status='succeeded').first()
    confirmation = ((run.summary or {}).get('preview_confirmation') or {}) if run else {}
    basis_confirmation = (basis.authority_snapshot or {}).get('preview_confirmation') or {}
    if confirmation or basis_confirmation:
        from .preview_confirmation import current_confirmed_preview

        if not run or current_confirmed_preview(run) is None:
            raise ValueError('Confirm and save the current Document Intelligence Preview before generating a schedule.')
        if basis.source_run_id != run.id or basis_confirmation.get('confirmed_at') != confirmation.get('confirmed_at'):
            raise ValueError('Build the schedule basis from the confirmed Document Intelligence Preview before generating a schedule.')
    result = dict(intelligence)
    disciplines = {}
    for item in basis.deliverables.filter(is_deleted=False, status='confirmed'):
        info = disciplines.setdefault(item.discipline or 'general', {
            'in_scope': True, 'deliverables': [], 'mentioned_in_source': [], 'ai_discovered': [],
        })
        info['deliverables'].append(item.canonical_name)
        info['mentioned_in_source'].append(item.canonical_name)
    result.update({
        'detected_project_name': basis.project_name,
        'detected_effective_date_text': basis.effective_date.isoformat() if basis.effective_date else None,
        'detected_duration_months': float(basis.duration_months) if basis.duration_months is not None else None,
        'disciplines': disciplines,
        'schedule_basis_id': basis.id,
        'schedule_basis_version': basis.version,
        'schedule_basis_status': basis.status,
    })
    if (basis.authority_snapshot or {}).get('preview_confirmation'):
        # WBS construction treats an absent discipline as in scope. Preserve the
        # confirmed exclusions explicitly rather than allowing empty branches.
        for code in DISCIPLINE_NAME_BY_CODE:
            disciplines.setdefault(code, {'in_scope': False, 'deliverables': [], 'mentioned_in_source': []})
        result['hse_studies'] = list(disciplines.get('hse', {}).get('deliverables') or [])
    return result
