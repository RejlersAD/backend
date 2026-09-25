"""Pure source catalogue for an explicitly requested Planning Package proposal.

Source occurrences remain distinct. This adapter adds quoted AI deliverables to
register scope; it never supplies workflow, timing, calendar or approval values.
"""
from collections import defaultdict
from copy import deepcopy
import hashlib
from uuid import NAMESPACE_URL, uuid5

from .identity_policy import stable_digest
from .planning_fact_extraction import validate_fact_value
from .register_rows import extract_register_rows, normalize_register_discipline, register_row_requires_review


USABLE = {'detected', 'confirmed'}
_MISSING_DISCIPLINES = {'', 'not_specified', 'not specified', 'unknown'}


def _title(value):
    return ' '.join(str(value or '').split()).casefold()


def _identifier(source, locator, title):
    identity = [source['id'], source['_text_hash'], locator, title]
    return str(uuid5(NAMESPACE_URL, 'radai:planning-package-source:' + stable_digest(identity)))


def _reference(source, locator, quote, fact_id=None):
    result = {'file_id': source['id'], 'project_id': source.get('project_id'),
              'filename': source.get('filename', ''), 'category': source.get('category', ''),
              'extracted_text_sha256': source['_text_hash'],
              'locator': deepcopy(locator), 'excerpt': quote}
    if fact_id is not None:
        result['fact_id'] = fact_id
    return result


def _fact_location(fact, source):
    """Recheck persisted citations against the supplied unchanged text snapshot."""
    locator = deepcopy(fact.get('source_locator') or {})
    if locator.get('extracted_text_sha256') not in (None, '', source['_text_hash']):
        return None
    text = source.get('text') or ''
    start, end, quote = locator.get('character_start'), locator.get('character_end'), locator.get('quote')
    if type(start) is int and type(end) is int and 0 <= start < end <= len(text):
        actual = text[start:end]
        if quote is not None and quote != actual:
            return None
        quote = actual
    else:
        quote = quote or fact.get('source_excerpt')
        if not isinstance(quote, str) or not quote.strip():
            return None
        start = text.find(quote)
        if start < 0 or text.find(quote, start + 1) >= 0:
            return None
        end = start + len(quote)
    locator.update(character_start=start, character_end=end)
    return start, end, quote, locator


def _overlaps(left, right):
    return (all(type(value) is int for value in (left[0], left[1], right[0], right[1]))
            and left[0] < right[1] and right[0] < left[1])


def _register_overlaps(row, location):
    if _overlaps((row['start'], row['end']), location):
        return True
    # A PDF cell may wrap into non-contiguous lines in the saved plain text.
    # Keep exact located cell spans so AI wording cannot evade a scope qualifier.
    return any(
        type(span.get('character_start')) is int and type(span.get('character_end')) is int
        and _overlaps((span['character_start'], span['character_end']), location)
        for span in (row.get('source_locator') or {}).get('text_ranges', [])
    )


def _discipline(value):
    label = str(value or '').strip()
    if _title(label) in _MISSING_DISCIPLINES:
        return 'not_specified'
    return normalize_register_discipline(label.replace('_', ' '))


def _merge_evidence(target, addition):
    for field in ('source_references', 'source_fact_ids'):
        for value in addition[field]:
            if value not in target[field]:
                target[field].append(deepcopy(value))
    if target['discipline'] == 'not_specified' and addition['discipline'] != 'not_specified':
        target['discipline'] = addition['discipline']
        target['discipline_basis'] = addition['discipline_basis']


def _compatible(left, right):
    return (left['discipline'] == right['discipline']
            or 'not_specified' in {left['discipline'], right['discipline']}) and (
        not left.get('source_group') or not right.get('source_group')
        or left['source_group'] == right['source_group']) and (
        not left.get('document_number') or not right.get('document_number')
        or left['document_number'] == right['document_number'])


def build_planning_package_sources(files, facts):
    """Return a reviewable union from plain, project-scoped file/fact snapshots.

    Include all exact-run fact statuses: a rejected register assertion must not
    reappear merely because the raw register can be parsed again. No ORM/provider
    calls or input mutations occur here.
    """
    sources = {str(source['id']): {**deepcopy(source), '_text_hash': hashlib.sha256(
        (source.get('text') or '').encode('utf-8')).hexdigest()}
        for source in files if source.get('id') is not None and source.get('parse_status') == 'done'
        and not source.get('is_deleted') and source.get('category') != 'output_schedule_sample'}
    facts = [deepcopy(fact) for fact in facts if not fact.get('is_deleted')]
    located = []
    for fact in facts:
        source = sources.get(str(fact.get('source_file_id')))
        location = _fact_location(fact, source) if source else None
        if location:
            located.append((fact, source, location))
    deliverables, excluded, warnings = [], [], []
    register_index = defaultdict(list)
    for source in sources.values():
        for row in extract_register_rows(source.get('text') or '', structured_evidence=source.get('structured_evidence')):
            locator = deepcopy(row.get('source_locator') or {})
            if type(row.get('start')) is int and type(row.get('end')) is int:
                locator.update(character_start=row['start'], character_end=row['end'])
            matching = [fact for fact, owner, location in located
                        if owner['id'] == source['id'] and fact.get('fact_type') == 'deliverable'
                        and isinstance(fact.get('value'), dict) and fact['value'].get('source_register')
                        and _register_overlaps(row, location)]
            rejected = any(fact.get('status') not in USABLE for fact in matching)
            item = {'id': _identifier(source, locator, row['original_title']), 'title': row['original_title'],
                    'discipline': _discipline(row.get('discipline')), 'discipline_basis': 'source_register',
                    'document_number': row.get('document_number') or '', 'source_group': row.get('source_group') or '',
                    'explicit_dimensions': deepcopy(row.get('explicit_dimensions') or {}),
                    'source_references': [_reference(source, locator, row.get('source_excerpt') or '')],
                    'source_fact_ids': [fact['id'] for fact in matching if fact.get('id') is not None],
                    'source_basis': 'source_register', 'review_status': 'requires_review'}
            restricted = register_row_requires_review(row) or rejected
            register_index[str(source['id'])].append({'row': row, 'item': item, 'restricted': restricted})
            if restricted:
                excluded.append({**item, 'reason': 'source_review_excluded' if rejected else 'register_requires_review',
                                 'source_inventory': deepcopy(row)})
            else:
                deliverables.append(item)

    for fact, source, location in located:
        if fact.get('fact_type') != 'deliverable' or fact.get('status') not in USABLE:
            continue
        value = fact.get('value')
        value = value if isinstance(value, dict) else {'name': value}
        if value.get('source_register') or fact.get('extraction_method') != 'ai':
            continue
        title = value.get('original_title') or value.get('name')
        if not isinstance(title, str) or not title.strip() or not validate_fact_value('deliverable', title, location[2]):
            continue
        candidate = {'id': _identifier(source, location[3], title), 'title': title,
                     'discipline': _discipline(value.get('discipline')), 'discipline_basis': 'quoted_ai_fact',
                     'document_number': value.get('document_number') or '', 'source_group': value.get('source_group') or '',
                     'explicit_dimensions': {}, 'source_references': [_reference(source, location[3], location[2], fact.get('id'))],
                     'source_fact_ids': [fact['id']] if fact.get('id') is not None else [],
                     'source_basis': 'quoted_ai_fact', 'review_status': 'requires_review'}
        rows = register_index[str(source['id'])]
        # A broad quotation or a shorter AI title cannot evade matrix exclusions.
        restricted = [entry for entry in rows if entry['restricted'] and (
            _register_overlaps(entry['row'], location)
            or _title(title) == _title(entry['item']['title']))]
        if register_row_requires_review(value) or restricted:
            excluded.append({**candidate, 'reason': 'ai_claim_requires_register_review'})
            continue
        matching_rows = [entry['item'] for entry in rows if not entry['restricted']
                         and _title(entry['item']['title']) == _title(title)
                         and _compatible(entry['item'], candidate)]
        overlapping = [item for item in matching_rows if any(_overlaps(location,
                       (ref['locator']['character_start'], ref['locator']['character_end']))
                       for ref in item['source_references'])]
        matches = overlapping or matching_rows
        if len(matches) == 1:
            _merge_evidence(matches[0], candidate)
            continue
        if len(matches) > 1:
            excluded.append({**candidate, 'reason': 'ambiguous_register_identity'})
            continue
        duplicate = next((item for item in deliverables if item['source_basis'] == 'quoted_ai_fact'
                          and _title(item['title']) == _title(title) and _compatible(item, candidate)
                          and any(str(ref['file_id']) == str(source['id']) and _overlaps(location,
                              (ref['locator']['character_start'], ref['locator']['character_end']))
                              for ref in item['source_references'])), None)
        if duplicate:
            _merge_evidence(duplicate, candidate)
        else:
            deliverables.append(candidate)

    by_title, by_number = defaultdict(list), defaultdict(list)
    for item in deliverables:
        by_title[_title(item['title'])].append(item['id'])
        if item['document_number']:
            by_number[item['document_number']].append(item['id'])
    dependencies, unresolved = [], []
    for fact, source, location in located:
        if fact.get('fact_type') != 'dependency' or fact.get('status') not in USABLE:
            continue
        value = fact.get('value')
        reference = _reference(source, location[3], location[2], fact.get('id'))
        reason = None
        if not isinstance(value, dict) or not validate_fact_value('dependency', value, location[2]):
            reason = 'dependency_source_not_verified'
        else:
            predecessor = set(by_number.get(value['predecessor'], []) + by_title.get(_title(value['predecessor']), []))
            successor = set(by_number.get(value['successor'], []) + by_title.get(_title(value['successor']), []))
            if len(predecessor) != 1 or len(successor) != 1:
                reason = 'dependency_endpoints_not_unique'
            elif predecessor == successor:
                reason = 'dependency_self_link'
            elif value.get('relationship_type') not in {'FS', 'SS', 'FF', 'SF'} or value.get('lag_unit') not in {'working_days', 'working days'} or type(value.get('lag')) not in (int, float):
                reason = 'dependency_type_or_working_day_lag_not_specified'
            else:
                link = {'predecessor_id': next(iter(predecessor)), 'successor_id': next(iter(successor)),
                        'type': value['relationship_type'], 'lag_days': value['lag'], 'lag_unit': 'working_days',
                        'source_references': [reference], 'source_fact_ids': [fact['id']] if fact.get('id') is not None else [],
                        'evidence_type': 'source_dependency', 'review_status': 'requires_review'}
                duplicate = next((item for item in dependencies if all(item[key] == link[key] for key in
                    ('predecessor_id', 'successor_id', 'type', 'lag_days', 'lag_unit'))), None)
                if duplicate:
                    for key in ('source_references', 'source_fact_ids'):
                        duplicate[key].extend(item for item in link[key] if item not in duplicate[key])
                else:
                    dependencies.append(link)
        if reason:
            unresolved.append({'value': deepcopy(value), 'reason': reason, 'source_references': [reference],
                               'source_fact_ids': [fact['id']] if fact.get('id') is not None else []})
    repeated = sum(1 for identities in by_title.values() if len(identities) > 1)
    if repeated:
        warnings.append({'code': 'repeated_source_titles', 'count': repeated,
                         'message': 'Distinct source occurrences share titles. Their identities remain separate; review scope before sequencing.'})
    return {'deliverables': deliverables, 'dependencies': dependencies,
            'unresolved_dependencies': unresolved, 'excluded_inventory': excluded, 'warnings': warnings}
