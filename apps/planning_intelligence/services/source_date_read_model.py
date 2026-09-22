"""Display source dates independently of an executable/calculated schedule.

Only already matched, traceable evidence supplies these read-only fields. A
printed date remains useful even when its calendar or predecessor network is
unverified. This module never fills planned dates, computes an endpoint, matches
titles, selects a document, or changes the persisted planning state.
"""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re


def _date(value):
    if not isinstance(value, str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}', value):
        return None
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def _review_status(status):
    if status in {'invalid', 'conflicting'}:
        return status
    if status in {'ambiguous', 'unsupported_date_format', 'unresolved'}:
        return 'ambiguous'
    return None


def _references(evidence):
    # A filename alone is not a traceable activity source.
    return [deepcopy(item) for item in evidence.get('source_references') or []
            if isinstance(item, dict) and item.get('file_id') is not None and item.get('locator')]


def _evidence_candidates(task):
    candidates = []
    seen = set()
    for key in ('source_evidence', 'duration_evidence'):
        evidence = task.get(key)
        if not isinstance(evidence, dict) or evidence.get('activity_specific') is False:
            continue
        references = _references(evidence)
        values = evidence.get('values')
        if not references or not isinstance(values, dict):
            continue
        identity = json.dumps(evidence, sort_keys=True)
        if identity not in seen:
            candidates.append((evidence, references))
            seen.add(identity)
    return candidates


def source_float_fields(task):
    """Keep printed total float visible without claiming a calculated network.

    Only traceable matched rows can supply this value. Zero and negative float
    are meaningful source facts; unsupported units and conflicting rows remain
    unresolved. The executable ``total_float_days`` field is never changed.
    """
    numbers, review_states, details, references = set(), set(), [], []
    for evidence, refs in _evidence_candidates(task):
        values = evidence['values']
        field_evidence = ((evidence.get('field_evidence') or {}).get('total_float_days')
                          or (evidence.get('field_evidence') or {}).get('total_float') or {})
        statuses = evidence.get('field_status') or {}
        field_status = statuses.get('total_float_days') or statuses.get('total_float') or field_evidence.get('status')
        raw = values.get('total_float_days')
        review = None
        if field_status and field_status.startswith('conflicting'):
            review = 'conflicting'
        elif field_status == 'invalid':
            review = 'invalid'
        elif field_status in {'ambiguous', 'unresolved', 'unsupported_unit', 'unit_not_specified'}:
            review = 'ambiguous'
        number = None
        if raw is not None and not isinstance(raw, bool):
            try:
                number = Decimal(str(raw))
                if not number.is_finite():
                    number = None
            except (InvalidOperation, ValueError, TypeError):
                pass
        if raw is not None and number is None:
            review = review or 'invalid'
        quantity = values.get('total_float') or {}
        if isinstance(quantity, dict) and quantity.get('unit') not in {None, 'days', 'working_days', 'calendar_days'}:
            review = review or 'ambiguous'
        if review:
            review_states.add(review)
        elif number is not None and field_status not in {'not_specified', 'explicit_none'}:
            numbers.add(number)
        if raw is not None or review or quantity:
            details.append({'value': raw, 'status': review or ('extracted' if number is not None else 'not_specified'),
                            'field_evidence': deepcopy(field_evidence),
                            'quantity': deepcopy(quantity), 'source_references': refs})
            references.extend(refs)
    if len(numbers) > 1 or 'conflicting' in review_states:
        status, value = 'conflicting', None
    elif review_states:
        status, value = ('invalid' if 'invalid' in review_states else 'ambiguous'), None
    elif numbers:
        number = next(iter(numbers))
        status, value = 'extracted', int(number) if number == number.to_integral_value() else float(number)
    else:
        status, value = 'not_specified', None
    return {'source_total_float_days': value, 'source_total_float_status': status,
            'source_total_float_evidence': details,
            'source_total_float_references': list({json.dumps(item, sort_keys=True): item for item in references}.values())}


def source_date_fields(task):
    """Return independent source-date facts and their per-endpoint status.

    ``duration_evidence`` is the historical matched-row contract; newer imports
    use ``source_evidence``. Do not read comparison evidence, parent dates,
    cached display fields, manually entered dates or prior calculated dates.
    Conflicting matched evidence stays explicit rather than choosing one copy.
    """
    candidates = _evidence_candidates(task)

    result = {}
    references, evidence_by_endpoint = [], {}
    for endpoint in ('start', 'finish'):
        field = f'planned_{endpoint}_date'
        dates, review_states, details = set(), set(), []
        explicit_blank = False
        for evidence, refs in candidates:
            values = evidence['values']
            field_evidence = (evidence.get('field_evidence') or {}).get(field) or {}
            field_status = (evidence.get('field_status') or {}).get(field) or field_evidence.get('status')
            column_status = values.get('date_columns_status')
            # A collapsed single-date PDF column cannot be assigned to Start
            # or Finish. Structured adapters can mark just one endpoint bad.
            review = _review_status(field_status)
            if field_status in {None, 'not_specified'} and column_status in {'ambiguous', 'invalid', 'unresolved'}:
                review = _review_status(column_status)
            raw = values.get(field)
            parsed = _date(raw)
            if field_status == 'explicit_none':
                explicit_blank = True
                if raw is not None:
                    review = 'conflicting'
            if raw is not None and parsed is None:
                review = review or 'invalid'
            if review:
                review_states.add(review)
            elif parsed and field_status not in {'not_specified', 'explicit_none'}:
                dates.add(parsed)
            if parsed or review or values.get('printed_single_date') or field_status == 'explicit_none':
                details.append({'value': raw, 'status': review or ('extracted' if parsed else
                                'explicit_none' if field_status == 'explicit_none' else 'not_specified'),
                                'field_evidence': deepcopy(field_evidence),
                                'printed_single_date': values.get('printed_single_date'),
                                'source_references': refs})
                references.extend(refs)
        if len(dates) > 1 or (dates and explicit_blank) or 'conflicting' in review_states:
            status, value = 'conflicting', None
        elif review_states:
            status, value = ('invalid' if 'invalid' in review_states else 'ambiguous'), None
        else:
            status, value = ('extracted', next(iter(dates))) if dates else ('explicit_none' if explicit_blank else 'not_specified', None)
        result[f'source_{endpoint}_date'] = value
        result[f'source_{endpoint}_status'] = status
        evidence_by_endpoint[endpoint] = details

    start, finish = result['source_start_date'], result['source_finish_date']
    if start and finish and finish < start:
        for endpoint in ('start', 'finish'):
            result[f'source_{endpoint}_date'] = None
            result[f'source_{endpoint}_status'] = 'invalid'
    statuses = {result['source_start_status'], result['source_finish_status']}
    result['source_date_status'] = next((status for status in ('conflicting', 'invalid', 'ambiguous') if status in statuses),
                                      'extracted' if statuses == {'extracted'} else
                                      'partial' if 'extracted' in statuses else 'not_specified')
    unique_references = {json.dumps(item, sort_keys=True): item for item in references}
    result['source_date_references'] = list(unique_references.values())
    result['source_date_evidence'] = evidence_by_endpoint
    return result


def enrich_source_dates(state):
    """Enrich an already copied API read model; never compute group rollups."""
    for task in state.get('tasks') or []:
        task.update(source_date_fields(task))
        task.update(source_float_fields(task))
    for parent in state.get('deliverables') or []:
        fields = {**source_date_fields(parent), **source_float_fields(parent)}
        parent.update(fields)
        if isinstance(parent.get('summary'), dict):
            parent['summary'].update(deepcopy(fields))
