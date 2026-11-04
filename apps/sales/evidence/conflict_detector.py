"""Identify competing evidence without hiding source values."""

IDENTITY_FIELDS = frozenset({'customer_name', 'organization_name'})


def detect_conflicts(records):
    groups = {}
    for row in records:
        groups.setdefault(row['field'], {}).setdefault(row['value'].strip().casefold(), []).append(row)
    conflicts = []
    for field, values in groups.items():
        if len(values) < 2:
            continue
        candidates = [row for same_value in values.values() for row in same_value]
        source_values = {}
        for value_rows in values.values():
            for row in value_rows:
                source_values.setdefault(row.get('source_id') or id(row), set()).add(row['value'].strip().casefold())
        if len(source_values) == 1:
            conflicts.append({'field': field, 'kind': 'same_source_conflict', 'selected_value': '',
                              'source_ids': [row.get('source_id', '') for row in candidates],
                              'reason': 'One source contains multiple different values; reviewer confirmation is required.'})
            continue
        # Distinct customer identities are never auto-resolved as revisions.
        if field not in IDENTITY_FIELDS:
            numbered = [row for row in candidates if int(row.get('revision') or 0) > 0]
            latest_revision = max((int(row.get('revision') or 0) for row in numbered), default=0)
            latest = [row for row in numbered if int(row.get('revision') or 0) == latest_revision]
            if not numbered:
                dated = [row for row in candidates if row.get('revision_date')]
                latest_date = max((str(row.get('revision_date')) for row in dated), default='')
                latest = [row for row in dated if str(row.get('revision_date')) == latest_date]
            if latest and len({row['value'].strip().casefold() for row in latest}) == 1:
                conflicts.append({'field': field, 'kind': 'revision_supersedes', 'selected_value': latest[0]['value'],
                                  'source_ids': [row.get('source_id', '') for row in candidates],
                                  'reason': 'A uniquely numbered later revision supplies this value.'})
                continue
        conflicts.append({'field': field, 'kind': 'competing_values', 'selected_value': '',
                          'source_ids': [row.get('source_id', '') for row in candidates],
                          'reason': 'Sources contain different values; reviewer confirmation is required.'})
    return conflicts
