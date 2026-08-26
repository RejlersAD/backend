"""Compile reviewed document evidence into a controlled schedule-input basis."""
from __future__ import annotations

import datetime
import re
from collections import defaultdict
from difflib import SequenceMatcher

from django.db import transaction
from django.db.models import Max
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.text import slugify

from ..config import DELIVERABLE_ALIASES
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


def _rank_fact(fact, authority):
    info = FACT_INFORMATION.get(fact.fact_type, 'scope')
    category = fact.source_file.category if fact.source_file_id else 'manual'
    priority = 110 if fact.extraction_method == 'manual' else authority.get((info, category), 40)
    return (fact.status == 'confirmed', priority, fact.confidence, -fact.id)


def _scalar(run, fact_type, fallback, authority):
    facts = list(run.facts.filter(
        is_deleted=False, fact_type=fact_type,
    ).exclude(status__in=['rejected', 'superseded', 'conflicted']).select_related('source_file'))
    return max(facts, key=lambda fact: _rank_fact(fact, authority)).value if facts else fallback


def _name_key(value):
    return slugify(re.sub(r'\s+', ' ', str(value or '')).strip())[:300] or 'unnamed'


def _alias_map():
    result = {}
    for canonical, aliases in DELIVERABLE_ALIASES.items():
        result[_name_key(canonical)] = canonical
        for alias in aliases:
            result[_name_key(alias)] = canonical
    return result


def _canonical_name(name, aliases):
    return aliases.get(_name_key(name), re.sub(r'\s+', ' ', str(name or '')).strip())


def _same_deliverable(left, right):
    left_key, right_key = _name_key(left), _name_key(right)
    if left_key == right_key:
        return True
    left_tokens, right_tokens = set(left_key.split('-')), set(right_key.split('-'))
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens | right_tokens), 1)
    return overlap >= .88 or SequenceMatcher(None, left_key, right_key).ratio() >= .92


def _source_reference(fact):
    return {
        'fact_id': fact.id,
        'file_id': fact.source_file_id,
        'filename': fact.source_file.original_filename if fact.source_file_id else 'Planner / AI review',
        'category': fact.source_file.category if fact.source_file_id else fact.extraction_method,
        'locator': fact.source_locator or {},
        'excerpt': fact.source_excerpt,
    }


def _deliverable_rows(run):
    aliases = _alias_map()
    groups = []
    facts = run.facts.filter(
        is_deleted=False, fact_type='deliverable',
    ).exclude(status__in=['rejected', 'superseded', 'conflicted']).select_related('source_file').order_by('-confidence', 'id')
    for fact in facts:
        value = fact.value if isinstance(fact.value, dict) else {'name': str(fact.value)}
        original = value.get('original_title') or value.get('name') or ''
        canonical = _canonical_name(value.get('name') or original, aliases)
        discipline = value.get('discipline') or ''
        incoming_number = value.get('document_number') or ''
        group = next((item for item in groups if (
            item['discipline'] == discipline
            and (
                (incoming_number and item['document_number'] and incoming_number.casefold() == item['document_number'].casefold())
                or (not (incoming_number and item['document_number']) and _same_deliverable(item['canonical_name'], canonical))
            )
        )), None)
        if group is None:
            group = {
                'discipline': discipline, 'canonical_name': canonical, 'original_title': original,
                'document_number': incoming_number,
                'document_revision': value.get('document_revision') or '',
                'confidence': fact.confidence, 'fact_ids': [], 'references': [], 'aliases': [],
                'confirmed': False,
            }
            groups.append(group)
        group['fact_ids'].append(fact.id)
        group['references'].append(_source_reference(fact))
        group['confidence'] = max(group['confidence'], fact.confidence)
        group['confirmed'] = group['confirmed'] or fact.status == 'confirmed'
        if original and original not in group['aliases'] and original != group['original_title']:
            group['aliases'].append(original)
        if not group['document_number'] and value.get('document_number'):
            group['document_number'] = value['document_number']
        if not group['document_revision'] and value.get('document_revision'):
            group['document_revision'] = value['document_revision']

    # AI authoritative lists are useful evidence but remain explicitly reviewable.
    base = (run.summary or {}).get('base_intelligence') or {}
    authoritative = ((base.get('ai_scope') or {}).get('authoritative_deliverables_by_discipline') or {})
    for discipline, names in authoritative.items():
        for name in names:
            canonical = _canonical_name(name, aliases)
            if any(item['discipline'] == discipline and _same_deliverable(item['canonical_name'], canonical) for item in groups):
                continue
            groups.append({
                'discipline': discipline, 'canonical_name': canonical, 'original_title': name,
                'document_number': '', 'document_revision': '', 'confidence': .65,
                'fact_ids': [], 'references': [{
                    'fact_id': None, 'file_id': None, 'filename': 'AI document review',
                    'category': 'ai', 'locator': {}, 'excerpt': '',
                }], 'aliases': [], 'confirmed': False,
            })
    return groups


def refresh_basis_readiness(basis, *, save=True):
    deliverables = basis.deliverables.filter(is_deleted=False)
    blockers = []
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
    }
    if basis.status not in ('approved', 'superseded'):
        basis.status = 'ready' if not blockers else 'draft'
    if save:
        basis.save(update_fields=['readiness', 'status', 'updated_at'])
    return basis.readiness


@transaction.atomic
def build_schedule_basis(run):
    project = run.project
    project = type(project).objects.select_for_update().get(pk=project.pk)
    authority = _authority_lookup()
    next_version = (project.schedule_bases.aggregate(value=Max('version'))['value'] or 0) + 1
    basis = ScheduleBasis.objects.create(
        project=project, source_run=run, version=next_version,
        project_name=str(_scalar(run, 'project_name', project.name, authority) or '')[:255],
        client=str(_scalar(run, 'client', project.client, authority) or '')[:255],
        location=str(_scalar(run, 'location', project.location, authority) or '')[:255],
        effective_date=_as_date(_scalar(run, 'effective_date', project.effective_date, authority)),
        contractual_finish=project.planned_end_date,
        duration_months=_scalar(run, 'duration_months', project.duration_months, authority),
        calendar=dict(project.calendar_overrides or {}),
        authority_snapshot={
            info: [
                {'category': rule.document_category, 'priority': rule.priority, 'rationale': rule.rationale}
                for rule in DocumentAuthorityRule.objects.filter(is_deleted=False, information_type=info)
            ]
            for info, _label in DocumentAuthorityRule.INFORMATION_CHOICES
        },
    )
    BasisDeliverable.objects.bulk_create([
        BasisDeliverable(
            basis=basis, discipline=row['discipline'],
            canonical_key=(
                f"{_name_key(row['canonical_name'])}--{_name_key(row['document_number'])}"
                if row['document_number'] else _name_key(row['canonical_name'])
            )[:320], canonical_name=row['canonical_name'],
            original_title=row['original_title'], document_number=row['document_number'],
            document_revision=row['document_revision'],
            status='confirmed' if row['confirmed'] else 'needs_review', confidence=row['confidence'],
            source_fact_ids=row['fact_ids'], source_references=row['references'], aliases=row['aliases'],
        ) for row in _deliverable_rows(run)
    ])
    refresh_basis_readiness(basis)
    return basis


@transaction.atomic
def approve_schedule_basis(basis, user):
    basis = ScheduleBasis.objects.select_for_update().get(pk=basis.pk)
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
    return result
