"""SLD comparison engine (spec §5.2–§5.4).

Inputs:
  sld_tags:  [SldTag]  — tags read from the drawing (sld_extract.extract_tags)
  eq_rows:   [EqRow]   — equipment list rows (sld_excel.read_equipment_list)
  load_rows: [LoadRow] — load list rows (sld_excel.read_load_list)

Outputs per spec status tables. Loads are NEVER compared in the equipment
tab; boards/transformers/generators are never counted as Load List extras
on boards the Load List doesn't cover.
"""
from __future__ import annotations

# Equipment comparison only covers these categories (spec §5.2)
EQ_CATEGORIES = {'board', 'transformer', 'generator'}
# list-only types that never appear on an SLD (spec §5.2 reasons)
LIST_ONLY_REASONS = [
    ('IPCMS', 'Control system — not drawn on an SLD'),
    ('-JB-', 'Junction boxes — not drawn on this SLD'),
    ('-BD-', 'Bus duct — shown by symbol only (NOTE-4, 285-U-503A feeders 5 and 10)'),
    ('-AB-', 'Adapter box — part of the bus duct run, not tagged on SLD'),
    ('-TSG-', 'Temporary switchgear — not on the permanent SLD'),
]

KW_TOL_ABS = 0.06
KW_TOL_PCT = 0.02


def _kw_agrees(a, b):
    return abs(a - b) <= max(KW_TOL_ABS, KW_TOL_PCT * max(a, b))


def _not_on_sld_reason(row):
    tag = row.get('tag', '')
    desc = (row.get('description') or '').lower()
    for key, reason in LIST_ONLY_REASONS:
        if key in tag:
            return reason
    if 'fire pump' in desc and row.get('listStatus') == 'demolition':
        return 'Demolished — fire pump loads now on 285-U-505C'
    return ''


def compare_equipment(sld_tags, eq_rows):
    """SLD vs Equipment List (spec §5.2)."""
    sld_eq = [t for t in sld_tags if t['category'] in EQ_CATEGORIES]
    sld_by_tag = {t['tag']: t for t in sld_eq}
    cable_norms = {t['tag'] for t in sld_tags if t['category'] == 'cable'}

    rows = []
    seen_sld = set()

    for row in eq_rows:
        if row.get('listStatus') == 'ignore':
            continue
        norms = row.get('norm') or []
        if not norms:
            rows.append({'tag': row.get('tag', ''), 'description': row.get('description', ''),
                         'category': '', 'listSheet': row.get('sheet', ''), 'onSld': '',
                         'status': 'no-tag-in-list', 'reason': ''})
            continue
        for n in norms:
            hit = sld_by_tag.get(n)
            from_tag = row.get('fromTag', '')
            base = {'tag': n, 'description': row.get('description', ''),
                    'category': '', 'listSheet': row.get('sheet', ''),
                    'onSld': f"Sheet {hit['sheet']:03d}" if hit else '',
                    'subline': f'from {from_tag}' if from_tag else ''}
            if hit:
                seen_sld.add(n)
                if row.get('listStatus') == 'demolition':
                    status, reason = 'sld-demolition', ''
                else:
                    status, reason = 'matched', ''
                # name swap: same tag, different names on New vs Demolition
                rows.append({**base, 'status': status, 'reason': reason})
            else:
                if n in cable_norms:
                    status, reason = 'cable-only', ''
                elif row.get('listStatus') == 'demolition':
                    status = 'not-on-sld'
                    reason = _not_on_sld_reason(row) or ('Demolished — not on the current SLD')
                else:
                    status = 'not-on-sld'
                    reason = _not_on_sld_reason(row)
                rows.append({**base, 'status': status, 'reason': reason})

    # different-tag check for demolition rows: e.g. 285-U-425A/B/C on list vs
    # 285-UM-425A/B/C on SLD (U→UM motor rule)
    for r in rows:
        if r['status'] == 'not-on-sld' and r['listSheet'] == 'Demolition':
            alt = r['tag'].replace('-U-', '-UM-')
            hit = sld_by_tag.get(alt)
            if hit:
                r['status'] = 'different-tag'
                r['reason'] = f'On SLD as {alt}'
                r['onSld'] = f"Sheet {hit['sheet']:03d}"
                seen_sld.add(alt)

    # name-swap detection (New vs Demolition describe the same tag differently)
    names = {}
    for row in eq_rows:
        for n in (row.get('norm') or []):
            names.setdefault(n, {})[row.get('sheet', '')] = row.get('description', '')
    for r in rows:
        n = r['tag']
        per_sheet = names.get(n, {})
        new_name, demo_name = per_sheet.get('New Equipment'), per_sheet.get('Demolition')
        if new_name and demo_name and new_name != demo_name and r['listSheet'] == 'Demolition':
            extra = f'New sheet calls {n} {new_name}'
            r['reason'] = f"{r['reason']} {extra}".strip()

    # SLD-only boards/TF/GEN (area 285, absent from both list sheets)
    for t in sld_eq:
        if t['tag'] in seen_sld or not t['tag'].startswith(('285-', 'XXXX', 'YYYY')):
            continue
        rows.append({'tag': t['tag'], 'description': t.get('description', ''),
                     'category': t['category'], 'listSheet': '',
                     'onSld': f"Sheet {t['sheet']:03d}", 'status': 'sld-only',
                     'reason': '', 'rating': t.get('rating', '')})

    return rows


def compare_loads(sld_tags, load_rows, eq_rows):
    """SLD vs Load List (spec §5.3)."""
    covered_boards = {r['board'] for r in load_rows if r.get('board')}
    sld_by_tag = {t['tag']: t for t in sld_tags}
    demolition_boards = {n for row in eq_rows if row.get('listStatus') == 'demolition'
                         for n in (row.get('norm') or [])}

    rows = []
    for lr in load_rows:
        tag = lr['tag']
        hit = sld_by_tag.get(tag)
        if not hit:
            rows.append({'tag': tag, 'description': lr.get('description', ''),
                         'boardList': lr.get('board', ''), 'boardSld': '',
                         'kwList': lr.get('ratedKw', 0), 'kwSld': None,
                         'status': 'not-on-sld', 'issue': ''})
            continue
        issue = []
        status = 'agrees'
        board_sld = hit.get('fedFrom') or ''
        if board_sld and lr.get('board') and board_sld != lr['board']:
            status = 'board-mismatch'
            issue.append(f"SLD shows fed from {board_sld}")
            if board_sld in demolition_boards:
                pass  # the demolished side is the LIST board; routing remark below
        kw_sld = hit.get('ratedKw')
        if kw_sld is not None and lr.get('ratedKw') and not _kw_agrees(kw_sld, lr['ratedKw']):
            status = 'rating-mismatch'
            issue.append(f"SLD {kw_sld} kW vs list {lr['ratedKw']} kW")
        # tag conflict: same tag, different equipment (description keywords)
        if hit.get('description') and lr.get('description'):
            if _keywords(hit['description']) != _keywords(lr['description']) and _keywords(lr['description']):
                status = 'tag-conflict'
                issue.append(f"SLD: {hit['description']}")
        # routing remark: description names a panel marked Demolition
        for b in demolition_boards:
            if b and b in (lr.get('description', '') + ' ' + board_sld):
                issue.append(f'Routed via demolished panel {b}')
                break
        rows.append({'tag': tag, 'description': lr.get('description', ''),
                     'boardList': lr.get('board', ''), 'boardSld': board_sld,
                     'kwList': lr.get('ratedKw', 0), 'kwSld': kw_sld,
                     'status': status, 'issue': '; '.join(issue),
                     'sheet': hit.get('sheet')})

    # loads on the SLD for covered boards but not in the Load List
    listed = {r['tag'] for r in load_rows}
    not_in_list = [t for t in sld_tags
                   if t['category'] in ('motor', 'load')
                   and (t.get('fedFrom') or '') in covered_boards
                   and t['tag'] not in listed]
    # other boards: never counted as extra (spec §5.3)
    not_covered = [t for t in sld_tags
                   if t['category'] in ('motor', 'load')
                   and (t.get('fedFrom') or '') not in covered_boards
                   and t['tag'] not in listed]
    return rows, not_in_list, not_covered


def compare_equipment_vs_load(eq_rows, load_rows):
    """Equipment List ↔ Load List — the third comparison axis (no SLD
    needed): every load must trace to an Equipment-List board, and every
    board's covered loads are listed.
    Statuses: matched (tag appears in both), not-in-equipment-list,
    board-covered / board-not-covered (panel-level)."""
    eq_by_tag = {}
    boards = []
    for row in eq_rows:
        for n in (row.get('norm') or []):
            eq_by_tag[n] = row
        tag = (row.get('tag') or '')
        if any(k in tag for k in ('-U-', '-SB-', '-DB-')) or tag in ('XXXX', 'YYYY'):
            boards.append(row)

    rows = []
    for lr in load_rows:
        tag = lr['tag']
        hit = eq_by_tag.get(tag)
        rows.append({
            'tag': tag,
            'description': lr.get('description', ''),
            'board': lr.get('board', ''),
            'ratedKw': lr.get('ratedKw', 0),
            'status': 'matched' if hit else 'not-in-equipment-list',
            'equipmentDescription': (hit or {}).get('description', ''),
            'equipmentSheet': (hit or {}).get('sheet', ''),
        })

    covered_boards = {r['board'] for r in load_rows if r.get('board')}
    for b in boards:
        tag = (b.get('norm') or [b.get('tag', '')])[0]
        rows.append({
            'tag': tag,
            'description': b.get('description', ''),
            'board': tag,
            'ratedKw': None,
            'status': 'board-covered' if tag in covered_boards else 'board-not-covered',
            'equipmentDescription': b.get('description', ''),
            'equipmentSheet': b.get('sheet', ''),
        })
    return rows


def _keywords(desc):
    """Significant description words for tag-conflict detection."""
    import re
    words = re.findall(r'[A-Z]{3,}|\d+', (desc or '').upper())
    skip = {'THE', 'FOR', 'AND', 'WITH', 'MOTOR', 'PUMP', 'NO'}
    return {w for w in words if w not in skip}


def drafting_tag_checks(sld_tags, area='285'):
    """Spec §5.4 tag checks."""
    findings = []
    by_tag = {}
    for t in sld_tags:
        by_tag.setdefault(t['tag'], []).append(t)

    # wrong area code: area != 285 but the 285 version exists
    norm_tags = set(by_tag)
    for t in sld_tags:
        tag = t['tag']
        if len(tag) > 3 and tag[:3].isdigit() and not tag.startswith(f'{area}-'):
            alt = f'{area}-{tag.split("-", 1)[1]}' if '-' in tag else ''
            if alt in norm_tags:
                findings.append({'check': 'Wrong area code', 'severity': 'high',
                                 'sheet': t['sheet'], 'location': tag,
                                 'result': f'{tag} — {alt} exists on this SLD',
                                 'tag': tag})
    # duplicate tag: same tag, different description or rating
    for tag, items in by_tag.items():
        if len(items) < 2:
            continue
        descs = {(i.get('description') or '') for i in items}
        ratings = {(i.get('rating') or '') for i in items}
        if len(descs) > 1 or len(ratings) > 1:
            findings.append({'check': 'Duplicate tag', 'severity': 'high',
                             'sheet': items[0]['sheet'], 'location': tag,
                             'result': f'{tag} appears {len(items)}× with different description/rating',
                             'tag': tag, 'crops': [i.get('cropUrl', '') for i in items]})
    # repeated numbering, e.g. "No.3" on A/B/C
    import re
    num_groups = {}
    for t in sld_tags:
        m = re.search(r'No\.?\s*(\d+)', t.get('description') or '', re.I)
        if m:
            num_groups.setdefault((m.group(1), re.sub(r'[A-Z]$', '', t['tag'])), []).append(t['tag'])
    for (num, base), tags in num_groups.items():
        if len(set(tags)) > 1:
            findings.append({'check': 'Repeated numbering', 'severity': 'medium',
                             'sheet': '', 'location': ', '.join(sorted(set(tags))),
                             'result': f'"No.{num}" used on {" / ".join(sorted(set(tags)))}',
                             'tag': base})
    # placeholder tags in an issued revision
    for ph in ('XXXX', 'YYYY'):
        if any(t['tag'].endswith(ph) for t in sld_tags):
            findings.append({'check': 'Placeholder tags', 'severity': 'medium',
                             'sheet': '', 'location': f'…-{ph}',
                             'result': f'Placeholder {ph} in an issued revision',
                             'tag': ph})
    return findings
