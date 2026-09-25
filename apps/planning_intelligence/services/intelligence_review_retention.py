"""Retain actual review decisions when unchanged evidence is analysed again."""
from collections import defaultdict
from copy import deepcopy

from django.utils import timezone

from .audit import record_event
from .operational_jobs import canonical_fingerprint
from .preview_confirmation import review_fingerprint


def _identity(fact):
    return canonical_fingerprint({
        'source_file_id': fact.source_file_id, 'type': fact.fact_type, 'key': fact.key,
        'value': fact.value, 'method': fact.extraction_method,
        'excerpt': fact.source_excerpt, 'locator': fact.source_locator,
    })


def retain_unchanged_reviews(run, *, actor=None):
    """Called inside extraction's transaction; never accept changed assertions.

    The latest prior run is the only candidate. Exact source/engine identities
    are required, and new conflicts prevent reuse of individual decisions.
    Original actors/timestamps survive; the new audit records the reuse itself.
    """
    previous = run.project.intelligence_runs.select_for_update().filter(
        is_deleted=False, status='succeeded', created_at__lt=run.created_at,
    ).order_by('-created_at', '-pk').first()
    if previous is None or previous.engine_version != run.engine_version:
        return
    before, after = previous.summary or {}, run.summary or {}
    if (not before.get('source_fingerprint')
            or before['source_fingerprint'] != after.get('source_fingerprint')
            or not before.get('extraction_source_manifest')
            or before['extraction_source_manifest'] != after.get('extraction_source_manifest')):
        return

    # Resolve-conflict commands lock the conflict before updating its facts.
    # Keep that order here, after the run lock also used by preview confirmation.
    old_conflicts = list(previous.conflicts.select_for_update().filter(is_deleted=False).order_by('pk'))
    old_facts = list(previous.facts.select_for_update().filter(is_deleted=False).order_by('pk'))
    new_facts = list(run.facts.filter(is_deleted=False))
    old_by_identity, new_by_identity = defaultdict(list), defaultdict(list)
    for fact in old_facts:
        old_by_identity[_identity(fact)].append(fact)
    for fact in new_facts:
        new_by_identity[_identity(fact)].append(fact)
    # Ambiguous duplicates cannot inherit a decision from an arbitrary row.
    matches = {
        old[0].pk: new_by_identity[key][0]
        for key, old in old_by_identity.items()
        if len(old) == 1 and len(new_by_identity.get(key, [])) == 1
    }
    new_conflicts = list(run.conflicts.filter(is_deleted=False))
    retained_conflicts, resolved_fact_ids = [], set()
    now = timezone.now()
    for conflict in new_conflicts:
        candidates = [old for old in old_conflicts
                      if old.key == conflict.key and old.conflict_type == conflict.conflict_type
                      and all(pk in matches for pk in old.fact_ids)
                      and {matches[pk].pk for pk in old.fact_ids} == set(conflict.fact_ids)]
        if len(candidates) != 1:
            continue
        old = candidates[0]
        if old.status not in {'resolved', 'ignored'} or old.resolved_at is None:
            continue
        resolution = deepcopy(old.resolution)
        if old.status == 'resolved':
            selected = matches.get(resolution.get('selected_fact_id'))
            if selected is None:
                continue
            resolution['selected_fact_id'] = selected.pk
            resolved_fact_ids.update(conflict.fact_ids)
        conflict.status, conflict.resolution = old.status, resolution
        conflict.resolved_by_id, conflict.resolved_at = old.resolved_by_id, old.resolved_at
        conflict.updated_at = now
        conflict.save(update_fields=['status', 'resolution', 'resolved_by', 'resolved_at', 'updated_at'])
        retained_conflicts.append({'from': old.pk, 'to': conflict.pk})

    # A fact can participate in more than one conflict; any unresolved conflict
    # must still block acceptance even if another conflict was resolved earlier.
    blocked = {pk for conflict in new_conflicts if conflict.status in {'open', 'ignored'}
               for pk in conflict.fact_ids}
    retained_facts = []
    for old in old_facts:
        fact = matches.get(old.pk)
        if (fact is None or old.status not in {'confirmed', 'rejected'} or old.reviewed_at is None
                or fact.pk in blocked or (fact.status == 'conflicted' and fact.pk not in resolved_fact_ids)):
            continue
        fact.status, fact.reviewed_by_id, fact.reviewed_at = old.status, old.reviewed_by_id, old.reviewed_at
        fact.updated_at = now
        fact.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        retained_facts.append({'from': old.pk, 'to': fact.pk})

    confirmation = before.get('preview_confirmation') or {}
    preview_retained = bool(
        confirmation.get('confirmed_at') and confirmation.get('preview')
        and confirmation.get('source_fingerprint') == after['source_fingerprint']
        and confirmation.get('review_fingerprint') == review_fingerprint(previous)
        and len(matches) == len(old_facts) == len(new_facts)
        and all(matches[old.pk].status == old.status
                and matches[old.pk].reviewed_at == old.reviewed_at
                and matches[old.pk].reviewed_by_id == old.reviewed_by_id
                and matches[old.pk].confidence == old.confidence for old in old_facts)
        and len(retained_conflicts) == len(old_conflicts) == len(new_conflicts)
        and not blocked
        and before.get('base_intelligence') == after.get('base_intelligence')
        and before.get('processing_coverage') == after.get('processing_coverage')
    )
    retained_summary = {}
    if preview_retained:
        retained_summary['preview_confirmation'] = {
            **deepcopy(confirmation), 'review_fingerprint': review_fingerprint(run),
            'retained_from_run_id': previous.pk,
        }
    if retained_facts or retained_conflicts or preview_retained:
        retention = {'previous_run_id': previous.pk, 'facts': retained_facts,
                     'conflicts': retained_conflicts, 'preview_retained': preview_retained}
        record_event(project=run.project, actor=actor, action='intelligence.reviews_retained',
                     entity=run, after=retention)
        run.summary = {**run.summary, **retained_summary, 'review_retention': retention}
