"""Conservative PCMC catalog suggestions; source identifiers are never rewritten."""
import hashlib
import json
import re
from difflib import SequenceMatcher


def key(value):
    return re.sub(r'[^a-z0-9]', '', str(value).casefold())


def pcmc_code_format_issue(code):
    return 'Approved code must contain 18–20 characters with no spaces. Confirm a compliant approved catalog identifier; do not pad or rewrite this code.' if not 18 <= len(code) <= 20 or any(character.isspace() for character in code) else ''


def description_attributes(text):
    """Recognize explicit engineering tokens, never decode a commodity code."""
    text = re.sub(r'\(REF\s+DOC:.*?\)', '', str(text), flags=re.I).upper()
    result = {}
    for kind, pattern in [('flange', r'\bFLANGES?\b'), ('valve', r'\bVALVES?\b'),
                          ('pipe', r'\b(?:PIPE|PIPING|TUBE)\b'),
                          ('fitting', r'\b(?:ELBOW|TEE|REDUCER|CAP|COUPLING|RETURN)\b')]:
        if re.search(pattern, text):
            result['component_type'] = kind
            break
    for name, pattern in [('rating', r'\b(\d{2,4})\s*#'),
                          ('material', r'\bASTM\s+(A\s*\d+(?:\s+GR\.?\s*[A-Z0-9]+)?)'),
                          ('facing', r'\b(RF|RTJ|FF)\b'), ('end_connection', r'\b(BW|SW|BE|THD)\b'),
                          ('schedule', r'\bSCH\.?\s*(\d+|STD|XS|XXS)\b')]:
        match = re.search(pattern, text)
        if match:
            result[name] = match.group(1)
    angle = re.search(r'\b(45|90|180)\s*(?:DEG(?:REE)?S?|°)', text)
    if angle:
        result['angle'] = angle.group(1)
    radius = re.search(r'\b(LR|SR|LONG RADIUS|SHORT RADIUS)\b', text)
    if radius:
        result['radius'] = {'LONG RADIUS': 'LR', 'SHORT RADIUS': 'SR'}.get(radius.group(1), radius.group(1))
    for name, pattern in [('slip on', r'\b(?:SLIP[ -]?ON|SO)\b'), ('blind', r'\bBLIND\b'), ('weld neck', r'\b(?:WELD[ -]?NECK|WN)\b')]:
        if result.get('component_type') == 'flange' and re.search(pattern, text):
            result['flange_type'] = name
    return result


def size_number(value):
    value = str(value or '').strip().replace('½', ' 1/2').replace('¼', ' 1/4').replace('¾', ' 3/4')
    value = re.sub(r'(?i)NPS|DN|inches|inch|in|mm|["”″]', '', value).strip()
    try:
        parts = value.replace('-', ' ').split()
        return sum(float(p.split('/')[0]) / float(p.split('/')[1]) if '/' in p else float(p) for p in parts) if parts else None
    except (ValueError, ZeroDivisionError):
        return None


def match_pcmc(cat, source, codelists=None):
    """Compare structured evidence first, rank descriptions only afterwards.

    Unknown values are not matches or conflicts. Ranges require proven full
    coverage before the existing single-record confirmation can be used.
    """
    from .buhasa_codelists import resolve
    columns = {key(c): c for c in cat.columns}
    def cell(row, *names):
        return next((str(row[columns[key(n)]]).strip() for n in names if key(n) in columns
                     and str(row[columns[key(n)]]).strip() not in ('', 'nan', 'None')), '')
    code_column = columns.get('contractorcommoditycode')
    if not code_column:
        return None
    described_source = description_attributes(f"{source.get('component_label', '')} {source.get('description', '')}")
    source = {**described_source, **{k: v for k, v in source.items() if v}}
    if str(source.get('material', '')).upper() in ('CS', 'SS', 'CARBON STEEL', 'STAINLESS STEEL'):
        source['material_family'] = source['material']
        source['material'] = described_source.get('material', '')
    requested = source.get('pms_commodity_code', '')
    requested = requested.strip()
    cached = cat.attrs.get('_pcmc_match_index')
    if cached is None:
        records, by_code = [], {}
        for row in cat.to_dict('records'):
            code = str(row.get(code_column) or '')
            if not code.strip():
                continue
            description = cell(row, 'LongMaterialDescription', 'ShortMaterialDescription', 'Description')
            record = {'row': row, 'code': code, 'description': description,
                      'described': description_attributes(description),
                      'identity': hashlib.sha256(json.dumps({str(k): str(v) for k, v in row.items()}, sort_keys=True).encode()).hexdigest()}
            records.append(record)
            by_code.setdefault(code, []).append(record)
        cached = cat.attrs['_pcmc_match_index'] = (records, by_code)
    exact = cached[1].get(requested, []) if requested else []
    pool = exact or cached[0]
    candidates, rejected = [], []
    lower, upper = size_number(source.get('size_from')), size_number(source.get('size_to') or source.get('size_from'))
    is_range = lower is None or upper is None or lower != upper
    categories = {'material': 'MaterialsGrade', 'schedule': 'ScheduleThickness', 'rating': 'PressureRating',
                  'end_connection': 'EndPreparation', 'facing': 'FlangeFacing'}
    aliases = {'material': ('MaterialGrade', 'MaterialsGrade'), 'schedule': ('ScheduleThickness', 'Schedule'),
               'rating': ('PressureRating', 'Rating'), 'end_connection': ('EndPreparation', 'EndConnection'),
               'facing': ('FlangeFacing',), 'component_type': ('ComponentType', 'CommodityType'),
               'angle': ('BendAngle',), 'radius': ('RadiusType',), 'flange_type': ('FlangeType',)}
    def comparable(name, value, catalog=False):
        if name in categories:
            selected = (codelists or {}).get('attributes', {}).get(categories[name], {})
            if not catalog and selected.get('status') == 'RESOLVED' and selected.get('raw') == value:
                return ('codelist', selected['code'])
            resolved = resolve(categories[name], value, catalog=catalog)
            if resolved.get('status') == 'RESOLVED':
                return ('codelist', resolved['code'])
        return ('literal', key(re.sub(r'(?i)\bASTM\b', '', value)) if name == 'material' else key(value))
    for record in pool:
        row, code, description, described = record['row'], record['code'], record['description'], record['described']
        matched, unknown, differences, missing_source = {}, [], [], []
        for name, names in aliases.items():
            wanted, actual = str(source.get(name) or ''), cell(row, *names) or described.get(name, '')
            if wanted and actual:
                if comparable(name, wanted) == comparable(name, actual, catalog=bool(cell(row, *names))):
                    matched[name] = {'source': wanted, 'catalog': actual}
                else:
                    differences.append({'attribute': name, 'source': wanted, 'catalog': actual})
            else:
                unknown.append(name)
                if actual and not wanted and name in ('material', 'rating', 'end_connection', 'facing'):
                    missing_source.append(name)
        if differences and not exact:
            # Known conflicts cannot be suggested. Avoid expensive ranking and
            # row hashing for unrelated rows in large approved catalogs.
            continue
        start = size_number(cell(row, 'FirstSizeFrom', 'Npd[1]', 'Npd'))
        end = size_number(cell(row, 'FirstSizeTo'))
        end = start if end is None else end
        coverage = {'source': [lower, upper], 'catalog': [start, end], 'complete': False, 'gaps': []}
        source_size_text = str(source.get('size_from') or '')
        source_unit = 'mm' if re.search(r'(?i)\bDN|mm\b', source_size_text) else 'in' if re.search(r'(?i)\bNPS|\bin\b|["”″]', source_size_text) else source.get('size_units', '')
        catalog_unit = cell(row, 'FirstSizeUnits', 'NpdUnitType')
        known_units = source_unit in ('mm', 'in') and catalog_unit in ('mm', 'in')
        if known_units and source_unit != catalog_unit:
            differences.append({'attribute': 'size units', 'source': source_unit, 'catalog': catalog_unit})
        elif not known_units:
            unknown.append('size units')
        if None not in (lower, upper, start, end):
            contains = start <= lower <= upper <= end
            coverage['complete'] = contains and (not is_range or known_units and source_unit == catalog_unit)
            if not contains:
                coverage['gaps'] = [[lower, min(upper, start)]] if start > lower else []
                if end < upper:
                    coverage['gaps'].append([max(lower, end), upper])
                if upper < start or lower > end:
                    differences.append({'attribute': 'size', 'source': [lower, upper], 'catalog': [start, end]})
            elif coverage['complete']:
                matched['size'] = coverage
        else:
            unknown.append('size coverage')
        candidate = {'candidate_id': record['identity'], 'commodity_code': code, 'description': description,
                     'industry_commodity_code': cell(row, 'IndustryCommodityCode'),
                     'catalog_row': int(row['__source_row__']) if '__source_row__' in row else None,
                     'matched_attributes': matched, 'unknown_attributes': unknown,
                     'differences': differences, 'coverage': coverage,
                     'missing_required_source': missing_source,
                     'confirmable': not differences and not missing_source and (not is_range or coverage['complete']),
                     'ranking_score': SequenceMatcher(None, str(source.get('description', '')).casefold(), description.casefold()).ratio()}
        if pcmc_code_format_issue(code):
            candidate['code_format_issue'] = pcmc_code_format_issue(code)
            candidate['confirmable'] = False
        (rejected if differences else candidates).append(candidate)
    candidates.sort(key=lambda value: (-len(value['matched_attributes']), -value['ranking_score'], value['commodity_code'], value['candidate_id']))
    shown = candidates if candidates else rejected
    covered = sorted([c['coverage']['catalog'] for c in candidates if None not in c['coverage']['catalog']])
    gaps, cursor = [], lower
    if lower is not None and upper is not None:
        for start, end in covered:
            if start > cursor:
                gaps.append([cursor, min(start, upper)])
            cursor = max(cursor, end)
            if cursor >= upper:
                break
        if cursor < upper:
            gaps.append([cursor, upper])
    reason = ('Exact PMS code conflicts with engineering attributes.' if exact and not candidates else
              'No suitable catalog candidate; catalog coverage or attributes conflict.' if not shown else
              'Range coverage is incomplete or unknown.' if is_range and not any(c['confirmable'] for c in candidates) else
              'Select and confirm a catalog record explicitly.')
    return {'matched': False, 'commodity_code': '', 'description': '', 'match_score': 0,
            'state': 'ambiguous' if shown else 'unmatched', 'status': 'ambiguous' if shown else 'unmatched',
            'candidates': shown[:30], 'candidate_count': len(shown), 'error': reason,
            'discriminators_matched': {'pcmc': True, 'source': source,
                'size_coverage': {'covered_sizes': [a if a == b else f'{a}..{b}' for a, b in covered],
                                  'gaps': gaps, 'unknown': any('size coverage' in c['unknown_attributes'] for c in candidates)},
                'pms_code_lookup': 'exact' if exact else 'not_found' if requested else 'blank',
                'candidate_count': len(shown), 'truncated': len(shown) > 30, 'reason': reason}}
