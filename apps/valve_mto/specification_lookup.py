"""User-owned, explicit project specification mappings shared by both engines."""
import re

LOOKUP_FIELDS = {
    'spec': 'pms_class', 'piping_class': 'pms_class',
    'pms_class': 'pms_class', 'material_class': 'pms_class',
    'rating': 'rating', 'facing': 'facing', 'area': 'area',
}

def size_nb_from_line_number(line_number):
    match = re.match(r'^(\d+(?:(?:[- ]\d+)?/\d+)?)["\u201d\u2033]\s*[-\u2013\u2014]\s*[A-Z0-9]+[-\u2013\u2014]',
                     str(line_number or '').strip(), re.IGNORECASE)
    if match:
        return match.group(1) + '"'
    # Service-first convention: P-U51316-8"-PA0D1 -> 8" (size at segment 3).
    service_first = re.match(
        r'^[A-Z]{1,4}-[A-Z]?\d{3,6}[A-Z]?-(\d{1,2}(?:-\d/\d)?)["\u201d\u2033]?\s*[-\u2013\u2014]',
        str(line_number or '').strip(), re.IGNORECASE)
    return service_first.group(1) + '"' if service_first else ''


def load_specification_lookups(user_id, pms_data=None):
    """Builds the code → {pms_class, rating, facing, area} lookup used by
    enrich_specifications(). Legend-sourced mappings (below) are the
    original source; `pms_data` — an optional list of
    {code, pms_class?, rating?, facing?} dicts parsed client-side from an
    uploaded PMS Excel/CSV (see ValveMTO.jsx's PMS Document stage) — is
    applied AFTER them and overwrites any legend value for the same code,
    so an uploaded PMS document always takes priority over legend data.
    """
    lookups = {key: {} for key in set(LOOKUP_FIELDS.values())}
    if user_id:
        from .models import ValveMTOLegend
        for legend in ValveMTOLegend.objects.filter(
            created_by_id=user_id, is_active=True,
            section__in=('piping', 'line_list', 'scope_symbols', 'limit_line'),
        ):
            for field in legend.definition.get('fields', []):
                target = LOOKUP_FIELDS.get(field.get('key'))
                if not target:
                    continue
                for code, value in (field.get('lookup') or {}).items():
                    code = str(code).strip().upper()
                    values = value if isinstance(value, dict) else {target: value}
                    for key in lookups:
                        text = str(values.get(key) or '').strip()
                        if not text:
                            continue
                        existing = lookups[key].get(code)
                        if existing and existing != text:
                            raise ValueError(f'Conflicting active legend {key} mappings for {code}.')
                        lookups[key][code] = text

    for row in (pms_data or []):
        if not isinstance(row, dict):
            continue
        code = str(row.get('code') or '').strip().upper()
        if not code:
            continue
        if row.get('pms_class'):
            lookups['pms_class'][code] = str(row['pms_class']).strip()
        if row.get('rating'):
            lookups['rating'][code] = str(row['rating']).strip()
        if row.get('facing'):
            lookups['facing'][code] = str(row['facing']).strip()

    return lookups


def enrich_specifications(row, lookups):
    """Use exact supplied codes only; never repair OCR letters/numbers by guessing."""
    tokens = str(row.get('line_number') or '').upper().split('-')
    known = set().union(*(set(lookups.get(key, {})) for key in ('pms_class', 'rating', 'facing')))
    matches = [code for code in tokens if code in known]
    if not row.get('piping_class') and len(set(matches)) == 1:
        row['piping_class'] = matches[0]
    code = str(row.get('piping_class') or '').upper()
    for key in ('pms_class', 'rating', 'facing'):
        value = lookups.get(key, {}).get(code)
        if not value:
            continue
        if row.get(key) and str(row[key]).upper() != value.upper():
            row['confidence'] = 'LOW'
            note = f'{key.upper()} CONFLICTS WITH ACTIVE PMS - VERIFY'
            if note not in row.get('remarks', ''):
                row['remarks'] = f'{row.get("remarks", "")} {note}'.strip()
        elif not row.get(key):
            row[key] = value
    if not row.get('tab'):
        mappings = lookups.get('area', {})
        areas = {mappings[key].upper() for key in (
            str(row.get('line_number') or '').upper(), str(row.get('pid_number') or '').upper(),
        ) if key in mappings}
        if len(areas) == 1 and next(iter(areas)) in ('ISLAND', 'FIELD'):
            row['tab'] = next(iter(areas)).lower()
    if not row.get('size_1') and row.get('line_number'):
        row['size_1'] = size_nb_from_line_number(row['line_number'])
    return row
