"""BUHASA-only normalization; source facts and frozen reference bytes stay intact."""
from functools import lru_cache
import hashlib
import json
from pathlib import Path

CATEGORIES = ('EndPreparation', 'FlangeFacing', 'PressureRating', 'ScheduleThickness', 'MaterialsGrade', 'BoltType')
DATA = Path(__file__).resolve().parent.parent / 'data'


@lru_cache(maxsize=1)
def dictionary():
    raw = (DATA / 'buhasa_codelists.json').read_bytes()
    aliases = (DATA / 'buhasa_codelist_aliases.json').read_bytes()
    return json.loads(raw), json.loads(aliases), hashlib.sha256(raw + aliases).hexdigest()


@lru_cache(maxsize=1)
def lookup_indexes():
    labels, codes = {}, {}
    for category, entries in dictionary()[0].items():
        labels[category], codes[category] = {}, {}
        for entry in entries:
            labels[category].setdefault(entry['shortDesc'].casefold(), []).append(entry)
            codes[category].setdefault(str(entry['code']), []).append(entry)
    return labels, codes


def resolve(category, raw, selected=None, catalog=False):
    lists, aliases, _ = dictionary()
    raw = str(raw or '').strip()
    if selected is not None:
        if not isinstance(selected, dict) or type(selected.get('code')) is not int or selected not in lists[category]:
            raise ValueError(f'Invalid {category} selection; choose an actual BUHASA entry.')
        candidates = [selected]
    elif not raw:
        return {'raw': '', 'status': 'NOT_SUPPLIED', 'candidates': [], 'confidence': 0.0}
    elif catalog and raw.replace('.0', '', 1).isdigit():
        # Numeric CAT cells are codelist IDs, not pressure/schedule magnitudes.
        candidates = lookup_indexes()[1][category].get(raw.removesuffix('.0'), [])
    else:
        candidates = aliases.get(category, {}).get(raw.upper(), [])
        if not candidates:
            candidates = lookup_indexes()[0][category].get(raw.casefold(), [])
    # Preserve hierarchy-distinct values; never resolve by first row or code alone.
    status = 'RESOLVED' if len(candidates) == 1 else 'AMBIGUOUS' if candidates else 'UNMAPPED_CODELIST'
    result = {'raw': raw, 'status': status, 'candidates': candidates,
              'confidence': 1.0 if status == 'RESOLVED' else 0.5 if candidates else 0.0}
    if status == 'RESOLVED':
        result.update(shortDesc=candidates[0]['shortDesc'], code=candidates[0]['code'])
    return result


def normalize(attributes, selections=None):
    selections = selections or {}
    if set(selections) - set(CATEGORIES):
        raise ValueError('Unsupported codelist category.')
    return {'profile': 'BUHASA', 'dictionary_hash': dictionary()[2], 'selections': selections,
            'attributes': {category: resolve(category, attributes.get(category, ''), selections.get(category))
                           for category in CATEGORIES}}


def raw_attributes(component):
    pc = component.piping_class
    return {'EndPreparation': component.end_connection, 'FlangeFacing': pc.flange_facing,
            'PressureRating': pc.pressure_rating, 'ScheduleThickness': component.schedule_or_rating,
            'MaterialsGrade': component.material_standard or pc.material_grade,
            'BoltType': component.sub_type if component.component_type == 'bolt' else ''}


CAT_COLUMNS = {
    'PressureRating': ['PressureRating[1]', 'PressureRating[2]', 'PressureRating'],
    'EndPreparation': ['EndPreparation[1]', 'EndPreparation[2]', 'EndPreparation'],
    'FlangeFacing': ['FlangeFacing[1]', 'FlangeFacing[2]', 'FlangeFacing'],
    'MaterialsGrade': ['MaterialGrade', 'MaterialsGrade'],
    'ScheduleThickness': ['ScheduleThickness[1]', 'ScheduleThickness[2]', 'ScheduleThickness', 'Schedule'],
    'BoltType': ['BoltType'],
}


def evaluate_catalog(cat, evidence, nominal_size, normalize_size, clean):
    """Ordered elimination: size, pressure, ends/facing, material, schedule/bolt."""
    viable = []
    stages = []
    rows = list(cat.iterrows())
    for _, row in rows:
        size_values = [clean(row.get(c, '')) for c in ('Npd[1]', 'Npd[2]', 'Npd') if c in cat.columns]
        if nominal_size and not any(normalize_size(v) == normalize_size(nominal_size) for v in size_values):
            continue
        normalized = {}
        for category, columns in CAT_COLUMNS.items():
            values = [clean(row.get(c, '')) for c in columns if c in cat.columns and clean(row.get(c, ''))]
            normalized[category] = {'values': values, 'codes': sorted({entry['code'] for value in values
                for entry in resolve(category, value, catalog=True)['candidates']})}
        viable.append({'row': row, 'codelists': normalized, 'size_values': size_values})
    stages.append({'attribute': 'Size', 'survivors': len(viable)})
    for category in CAT_COLUMNS:
        wanted = evidence['attributes'][category]
        codes = {entry['code'] for entry in wanted['candidates']}
        if wanted['status'] != 'NOT_SUPPLIED':
            # Unmapped source values keep size-eligible evidence for review, but
            # can never become an approvable match or pass release guards.
            if codes:
                viable = [candidate for candidate in viable if codes.intersection(candidate['codelists'][category]['codes'])]
        stages.append({'attribute': category, 'survivors': len(viable)})
    evidence['stages'] = stages
    differences = []
    for category in CAT_COLUMNS:
        values = sorted({' / '.join(describe(category, value) for value in candidate['codelists'][category]['values']) or 'missing' for candidate in viable})
        if len(values) > 1:
            differences.append(f"Differs by: {category} [{ ' vs '.join(values) }]")
    evidence['differences'] = differences
    return viable


def describe(category, value):
    entries = resolve(category, value, catalog=True)['candidates']
    if len(entries) == 1:
        entry = entries[0]
        long = f" — {entry['longDesc']}" if entry['longDesc'] and entry['longDesc'] != entry['shortDesc'] else ''
        return f"{entry['shortDesc']}{long} ({entry['code']})"
    return value


def blockers(evidence):
    if evidence.get('profile') != 'BUHASA':
        return []
    result = [f"{name}: {value['status']}" for name, value in evidence['attributes'].items()
              if value['status'] in {'UNMAPPED_CODELIST', 'AMBIGUOUS'}]
    if evidence.get('dictionary_hash') != dictionary()[2]:
        result.append('BUHASA dictionary changed; run matching again.')
    return result


def match_catalog(result, cat, evidence, size, subtype, component_name, normalize_size, clean):
    if 'IndustryCommodityCode' not in cat.columns:
        result.update(state='invalid_reference', status='invalid_reference', error='CAT sheet lacks commodity codes.')
        return result
    if subtype:
        cat = cat[cat.apply(lambda row: re_key(subtype) in re_key(clean(row.get('CommodityType', component_name))), axis=1)]
    viable = evaluate_catalog(cat, evidence, size, normalize_size, clean)
    candidates = []
    for value in viable:
        row = value['row']
        code = clean(row.get('IndustryCommodityCode', ''))
        if not code:
            result.update(state='invalid_reference', status='invalid_reference', error='Matching CAT row lacks a commodity code.')
            return result
        identity = hashlib.sha256(json.dumps({str(k): clean(v) for k, v in row.items()}, sort_keys=True).encode()).hexdigest()
        confidence = sum(attribute['confidence'] for attribute in evidence['attributes'].values()) / len(CATEGORIES)
        candidates.append({'candidate_id': identity, 'commodity_code': code,
                           'description': clean(row.get('Description', component_name)),
                           'ranking_score': confidence, 'codelists': value['codelists'],
                           'discriminators': {'size': {'requested': size, 'values': value['size_values'], 'matched': True}}})
    result.update(candidates=candidates, candidate_count=len(candidates),
                  match_score=max((c['ranking_score'] for c in candidates), default=0.0))
    unresolved = blockers(evidence)
    if not candidates:
        state = 'unmatched'
    elif len(candidates) > 1:
        state = 'ambiguous'
    elif any('UNMAPPED_CODELIST' in item for item in unresolved):
        state = 'unmatched'
    else:
        state = 'ambiguous' if unresolved else 'unique_match'
    result.update(state=state, status=state, matched=state == 'unique_match',
                  error='; '.join(unresolved) if unresolved else 'Select an explicit catalog candidate.' if state == 'ambiguous' else '' if candidates else 'No CAT candidates satisfy the ordered criteria.')
    if state == 'unique_match':
        result.update(commodity_code=candidates[0]['commodity_code'], description=candidates[0]['description'])
    return result


def re_key(value):
    import re
    return re.sub('[^a-z0-9]', '', value.casefold())
