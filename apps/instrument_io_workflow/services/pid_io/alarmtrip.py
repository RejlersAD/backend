"""Rev 2 — Alarm & trip register.

Builds the ISA alarm/trip tag list from P&ID alarm letters (scope='pid') or
from every IO-list alarm value (scope='all'), classifies each row
(Trip / Alarm / Alarm (common) / Discrepancy), resolves the set point through
the IO-list → loop-transmitter → P&ID → C&E chain, and matches each ISA tag
back against the baseline IO-list alarm columns.

Pure functions over the stored payloads (tags_payload, setpoints_payload,
vendor_payload['baselineRows'], cause_notes_payload) — unit-testable without
Django or a database.
"""
import re

LEVEL_TO_COLUMN = {
    'LL': 'Low_Low_Alarm_Value', 'L': 'Low_Alarm_Value',
    'H': 'High_Alarm_Value', 'HH': 'High_High_Alarm_Value',
}
COLUMN_TO_LEVEL = {v: k for k, v in LEVEL_TO_COLUMN.items()}

_ISA_MODIFIERS = set('DFQKM')
_TAG_RE = re.compile(r'^(\d{2})-([A-Z]{1,6})-(\d{3,5})([A-Z]?)$')
_LETTER_RE = re.compile(r'^(SHH|SLL|HH|LL|SH|SL|H|L)(\s*\((ESD)\))?$', re.IGNORECASE)


def parse_tag(tag):
    """'13-PT-7204B' → (unit, func, loop_digits, suffix). None if unparseable."""
    m = _TAG_RE.match((tag or '').replace(' ', '').upper())
    return m.groups() if m else None


def parse_letter(letter):
    """'SLL' → ('SLL', 'S', 'LL', False); 'HH (ESD)' → ('HH', 'S', 'HH', True).

    Returns (base_letter, sa_class, level, esd_flag). sa_class is 'S' when the
    letter starts with S or carries an explicit (ESD) marker, else 'A'."""
    m = _LETTER_RE.match((letter or '').strip())
    if not m:
        return None
    base = m.group(1).upper()
    esd = bool(m.group(3))
    sa = 'S' if base.startswith('S') or esd else 'A'
    level = base[1:] if base.startswith('S') else base
    return base, sa, level, esd


def isa_variable(func):
    """ISA variable part: first letter (+ second when it is an ISA modifier
    D, F, Q, K, M — e.g. PDA → PD)."""
    func = (func or '').upper()
    if not func:
        return ''
    if len(func) > 1 and func[1] in _ISA_MODIFIERS:
        return func[:2]
    return func[0]


def build_isa(unit, func, loop_digits, suffix, level, sa):
    """`<unit>-<variable><S|A><level>-<loop><suffix>`."""
    return f'{unit}-{isa_variable(func)}{sa}{level}-{loop_digits}{suffix}'


def isa_no_suffix(isa_tag):
    """Strip the loop suffix letter: 13-PSLL-7204B → 13-PSLL-7204."""
    p = parse_tag(isa_tag)
    if not p:
        return isa_tag
    unit, func, digits, _suffix = p
    return f'{unit}-{func}-{digits}'


def _norm(tag):
    return re.sub(r'\s+', '', str(tag or '')).upper()


def _is_transmitter(func):
    return len(func or '') >= 2 and func[1].upper() == 'T'


def _is_esd_system(system):
    s = str(system or '').upper()
    return 'ESD' in s or s == 'S'


def _baseline_combos(baseline_rows):
    """One combo per non-empty IO-list alarm column:
    {rowTag, column, level, sa, isa, levelKey, value, system}."""
    combos = []
    for row in baseline_rows or []:
        tag = row.get('_tag_norm') or _norm(row.get('TAG_Number'))
        parsed = parse_tag(tag)
        if not parsed:
            continue
        unit, func, digits, suffix = parsed
        system = row.get('System') or ''
        for column, level in COLUMN_TO_LEVEL.items():
            value = str(row.get(column) or '').strip()
            if not value:
                continue
            sa = 'S' if level in ('HH', 'LL') and _is_esd_system(system) else 'A'
            isa = build_isa(unit, func, digits, suffix, level, sa)
            combos.append({
                'rowTag': tag, 'column': column, 'level': level, 'sa': sa,
                'isa': isa, 'levelKey': (isa_variable(func), level, digits, suffix),
                'value': value, 'system': system,
                'unit': unit, 'func': func, 'digits': digits, 'suffix': suffix,
            })
    return combos


def _cause_note_tags(cause_notes):
    """All tag strings mentioned across the stored cause notes (normalised)."""
    out = set()
    for note in cause_notes or []:
        for t in note.get('tags', []):
            out.add(_norm(t))
    return out


def _group_tag(tag, members):
    """The field transmitter the row groups under: same-suffix transmitter,
    else any loop transmitter, else the tag itself."""
    parsed = parse_tag(tag['tag'])
    suffix = parsed[3] if parsed else ''
    txs = [m for m in members if _is_transmitter(m.get('func', ''))]
    if suffix:
        for m in txs:
            mp = parse_tag(m['tag'])
            if mp and mp[3] == suffix:
                return m['tag']
    return txs[0]['tag'] if txs else tag['tag']


def _resolve_setpoint(tag, letter_info, members, baseline_by_tag, setpoints_by_tag):
    """Set-point resolution chain (contract §Alarm & trip):
    own IO-list alarm column → same-suffix loop transmitter → any loop
    transmitter ('IO list via <tag>') → P&ID level scale → C&E / datasheet."""
    base, _sa, level, _esd = letter_info
    column = LEVEL_TO_COLUMN.get(level)
    tag_norm = _norm(tag['tag'])

    def _column_value(tag_name):
        row = baseline_by_tag.get(_norm(tag_name))
        if row and column:
            value = str(row.get(column) or '').strip()
            return value or None
        return None

    value = _column_value(tag['tag'])
    if value:
        return value, 'IO list (this tag)'

    parsed = parse_tag(tag['tag'])
    suffix = parsed[3] if parsed else ''
    txs = [m for m in members if _is_transmitter(m.get('func', '')) and _norm(m['tag']) != tag_norm]
    same_suffix = [m for m in txs if suffix and (parse_tag(m['tag']) or ('', '', '', ''))[3] == suffix]
    for candidate in same_suffix + [m for m in txs if m not in same_suffix]:
        value = _column_value(candidate['tag'])
        if value:
            return value, f"IO list via {candidate['tag']}"

    for sp in setpoints_by_tag.get(tag_norm, []):
        if (sp.get('point') or '').upper() == base and sp.get('value'):
            unit = sp.get('unit') or ''
            return f"{sp['value']} {unit}".strip(), 'P&ID'
    return '', 'C&E / datasheet'


def _classify_pid_type(tag, letter_info, members):
    base, _sa, _level, esd = letter_info
    if base.startswith('S') or esd:
        return 'Trip'
    if (tag.get('func') or '').upper().endswith('DA'):
        return 'Discrepancy'
    parsed = parse_tag(tag['tag'])
    if parsed and not parsed[3]:
        voted = [m for m in members
                 if m['tag'] != tag['tag'] and (parse_tag(m['tag']) or ('', '', '', ''))[3]
                 and m.get('alarms')]
        if len(voted) >= 2:
            return 'Alarm (common)'
    return 'Alarm'


def _check_string(isa_tag, row_type, tag, members, note_tags):
    """Cause-note checks first; loop-relation strings as fallback."""
    parsed = parse_tag(tag['tag'])
    suffix = parsed[3] if parsed else ''
    no_suffix = isa_no_suffix(isa_tag)
    if _norm(isa_tag) in note_tags:
        return 'Same tag in cause note ✓'
    if suffix and _norm(no_suffix) in note_tags:
        return f'Voted {no_suffix} written in cause note ✓'
    if row_type == 'Discrepancy':
        return 'Labelled DISCREPANCY on the sheet'
    if row_type == 'Alarm (common)':
        block = ''
        for m in members:
            mp = parse_tag(m['tag'])
            if mp and mp[3] and m.get('alarms'):
                info = parse_letter(m['alarms'][0])
                if info:
                    block = isa_no_suffix(build_isa(mp[0], mp[1], mp[2], mp[3], info[2], info[1]))
                    break
        return f'2oo3 output of {block}' if block else '2oo3 output'
    if suffix:
        for m in members:
            mp = parse_tag(m['tag'])
            if m['tag'] != tag['tag'] and mp and mp[3] == suffix and m.get('alarms'):
                return f'Same loop and suffix {suffix}'
    return ''


def build(tags_payload, setpoints_payload, baseline_rows, cause_notes, scope='pid'):
    """Assemble the alarm & trip payload for one scope.

    scope='pid': combinations from P&ID alarm letters.
    scope='all': P&ID-letter rows first, then one row per IO-list alarm value
    not covered by a P&ID letter ('io-list-only' rows)."""
    scope = scope if scope in ('pid', 'all') else 'pid'
    tags_payload = tags_payload or []
    baseline_rows = baseline_rows or []

    members_by_loop = {}
    for t in tags_payload:
        members_by_loop.setdefault(t.get('loopKey') or t.get('tag'), []).append(t)

    baseline_by_tag = {}
    for row in baseline_rows:
        key = row.get('_tag_norm') or _norm(row.get('TAG_Number'))
        if key:
            baseline_by_tag.setdefault(key, row)

    setpoints_by_tag = {}
    for sp in setpoints_payload or []:
        setpoints_by_tag.setdefault(_norm(sp.get('tag')), []).append(sp)

    combos = _baseline_combos(baseline_rows)
    note_tags = _cause_note_tags(cause_notes)

    rows = []
    pid_level_keys = set()
    for tag in tags_payload:
        if tag.get('derived'):
            continue
        parsed = parse_tag(tag.get('tag'))
        if not parsed:
            continue
        unit, func, digits, suffix = parsed
        members = members_by_loop.get(tag.get('loopKey') or tag['tag'], [tag])
        for letter in tag.get('alarms') or []:
            info = parse_letter(letter)
            if not info:
                continue
            base, sa, level, _esd = info
            isa = build_isa(unit, func, digits, suffix, level, sa)
            level_key = (isa_variable(func), level, digits, suffix)
            pid_level_keys.add(level_key)

            row_type = _classify_pid_type(tag, info, members)
            setpoint, value_from = _resolve_setpoint(
                tag, info, members, baseline_by_tag, setpoints_by_tag)

            candidates = [c for c in combos if c['levelKey'] == level_key]
            same = next((c for c in candidates if c['isa'] == isa), None)
            if same:
                in_io, detail = 'same', f"{same['rowTag']} · {same['column']}"
            elif candidates:
                c = candidates[0]
                in_io, detail = 'type-differs', f"{c['rowTag']} · {c['column']}"
            else:
                in_io, detail = 'not-in-io-list', ''

            sheet = str((tag.get('page') or 0) + 1)
            rows.append({
                'tag': tag['tag'], 'isaTag': isa, 'letter': base,
                'tagAlarm': f"{tag['tag']} {base}",
                'type': row_type, 'system': tag.get('system') or '',
                'sheet': sheet, 'zone': '',
                'setpoint': setpoint, 'valueFrom': value_from,
                'inIoList': in_io, 'inIoListDetail': detail,
                'check': _check_string(isa, row_type, tag, members, note_tags),
                'groupTag': _group_tag(tag, members),
                'groupMeta': sheet,
                '_trip': row_type == 'Trip',
            })

    rows.sort(key=lambda r: (r['sheet'], r['groupTag'], not r['_trip'], r['tag']))

    io_only = []
    if scope == 'all':
        for c in combos:
            if c['levelKey'] in pid_level_keys:
                continue
            row_tag = c['rowTag']
            esd_trip = c['level'] in ('HH', 'LL') and _is_esd_system(c['system'])
            row_type = 'Trip' if esd_trip else 'Alarm'
            io_only.append({
                'tag': row_tag, 'isaTag': c['isa'], 'letter': c['level'],
                'tagAlarm': f"{row_tag} {c['level']}",
                'type': row_type, 'system': str(c['system'] or ''),
                'sheet': '', 'zone': '',
                'setpoint': c['value'], 'valueFrom': 'IO list (this tag)',
                'inIoList': 'io-list-only',
                'inIoListDetail': f"{row_tag} · {c['column']}",
                'check': ('Same tag in cause note ✓' if _norm(c['isa']) in note_tags else ''),
                'groupTag': row_tag, 'groupMeta': 'IO list only',
                '_trip': row_type == 'Trip',
            })
        io_only.sort(key=lambda r: (r['groupTag'], not r['_trip'], r['tag']))

    out_rows = rows + io_only
    for r in out_rows:
        r.pop('_trip', None)

    counts = {
        'pidLetters': len(rows),
        'trips': sum(1 for r in out_rows if r['type'] == 'Trip'),
        'alarms': sum(1 for r in out_rows if r['type'] != 'Trip'),
        'causeNoteMatches': sum(1 for r in out_rows if r['check'].endswith('cause note ✓')),
        'withSetpoint': sum(1 for r in out_rows if r['setpoint']),
        'sameIsa': sum(1 for r in rows if r['inIoList'] == 'same'),
        'typeDiffers': sum(1 for r in rows if r['inIoList'] == 'type-differs'),
        'notInIoList': sum(1 for r in rows if r['inIoList'] == 'not-in-io-list'),
        'ioListOnly': len(io_only),
        'wholeIoList': len(combos),
        'wholeTags': len({c['rowTag'] for c in combos}),
    }
    return {'counts': counts, 'rows': out_rows}


def for_extraction(extraction, scope='pid'):
    """Stored alarmtrip payload when present, else computed lazily from the
    extraction's stored payloads (older extractions; cause notes may be
    absent — checks then fall back to loop relations)."""
    stored = getattr(extraction, 'alarmtrip_payload', None) or {}
    payload = stored.get(scope)
    if payload:
        return payload
    vendor = extraction.vendor_payload or {}
    return build(extraction.tags_payload, extraction.setpoints_payload,
                 vendor.get('baselineRows', []),
                 getattr(extraction, 'cause_notes_payload', None) or [],
                 scope=scope)
