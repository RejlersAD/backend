"""The only component allowed to select final values from evidence records."""

from .conflict_detector import detect_conflicts
from .revision_detector import compare_revision


def _key(value):
    return ' '.join(str(value or '').casefold().split())


def _revision_winner(candidates):
    numbered = [row for row in candidates if int(row.get('revision') or 0) > 0]
    if not numbered:
        dated = [row for row in candidates if row.get('revision_date')]
        if not dated:
            return None
        latest_date = max(str(row.get('revision_date')) for row in dated)
        latest = [row for row in dated if str(row.get('revision_date')) == latest_date]
        if len({_key(row['value']) for row in latest}) != 1:
            return None
        return max(latest, key=lambda row: row['confidence'])
    revision = max(int(row.get('revision') or 0) for row in numbered)
    latest = [row for row in numbered if int(row.get('revision') or 0) == revision]
    values = {_key(row['value']) for row in latest}
    if len(values) != 1:
        return None
    return max(latest, key=lambda row: row['confidence'])


def resolve_evidence(records):
    conflicts = detect_conflicts(records)
    conflict_fields = {item['field']: item for item in conflicts
                       if item['kind'] in {'competing_values', 'same_source_conflict'}}
    revision_fields = {item['field']: item for item in conflicts if item['kind'] == 'revision_supersedes'}
    grouped = {}
    for row in records:
        grouped.setdefault(row['field'], []).append(row)
    selected = {}
    for field, candidates in grouped.items():
        conflict = conflict_fields.get(field)
        if conflict:
            selected[field] = {
                'field': field, 'value': '', 'confidence': 0.0, 'status': 'manual_review_required',
                'reason': conflict['reason'], 'sources': candidates,
            }
            continue
        # Repeated matches across pages/chunks from one source are one
        # independent source. Preserve all locations, but resolve only the
        # distinct value candidates.
        value_groups = {}
        for row in candidates:
            value_groups.setdefault(_key(row['value']), []).append(row)
        candidates = [dict(max(group, key=lambda row: (row['confidence'], bool(row.get('source_location')))),
                           locations=list(dict.fromkeys(item.get('source_location', '') for item in group)),
                           sources=group)
                      for group in value_groups.values()]
        revision = revision_fields.get(field)
        if revision:
            revision_candidates = []
            for group in value_groups.values():
                best = max(group, key=lambda row: (row['confidence'], bool(row.get('source_location'))))
                revision_candidates.append({
                    **best, 'sources': group,
                    'locations': list(dict.fromkeys(item.get('source_location', '') for item in group)),
                })
            winner = _revision_winner(revision_candidates)
            if not winner:
                selected[field] = {
                    'field': field, 'value': '', 'confidence': 0.0,
                    'status': 'manual_review_required',
                    'reason': 'Revision evidence does not identify one unambiguous latest value.',
                    'sources': [row for group in value_groups.values() for row in group],
                }
                continue
            source_rows = winner.get('sources', [winner])
            selected[field] = {
                'field': field, 'value': winner['value'], 'confidence': winner['confidence'],
                'status': 'verified', 'reason': revision['reason'], 'sources': source_rows,
                'selected_source_id': winner.get('source_id', ''), 'locations': winner.get('locations', []),
            }
            continue
        distinct = {_key(row['value']) for row in candidates}
        if len(distinct) > 1:
            selected[field] = {
                'field': field, 'value': '', 'confidence': 0.0, 'status': 'manual_review_required',
                'reason': 'Conflicting values could not be safely ordered.', 'sources': candidates,
            }
            continue
        winner = max(candidates, key=lambda row: (row['confidence'], bool(row.get('source_id'))))
        same_sources = len({row.get('source_id') or id(row) for row in candidates}) > 1
        selected[field] = {
            'field': field, 'value': winner['value'], 'confidence': winner['confidence'],
            'status': 'verified' if same_sources and winner['confidence'] >= 85 else 'likely',
            'reason': ('Independent sources agree.' if same_sources else winner['reason']),
            'sources': [row for group in value_groups.values() for row in group],
            'selected_source_id': winner.get('source_id', ''), 'locations': winner.get('locations', []),
        }
    return {'selected': selected, 'conflicts': conflicts}
