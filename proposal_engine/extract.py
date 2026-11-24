"""Deterministic fact extraction. Every fact carries {source, location}; nothing is inferred."""
from __future__ import annotations

import re
from pathlib import Path

from .readers import docx_body_blocks, file_text, spreadsheet_rows


def _fact(value, row, location, method):
    return {'value': value, 'source': row['rel'], 'location': location, 'method': method}


def source_rows(selections, key, files):
    """Selected files of a component plus their format twins, editable sources first.
    Signed submissions are often image-only PDFs; their facts live in the DOCX twin."""
    selected = selections.get(key, ([], []))[0]
    rels = {row['rel'] for row in selected}
    twins = [row for row in files if not row.get('duplicate_of') and not row.get('variant') and (
        row.get('twin_of') in rels or any(sel.get('twin_of') == row['rel'] for sel in selected))]
    rows = {row['rel']: row for row in selected + twins}.values()
    return sorted(rows, key=lambda row: (row['ext'] != 'docx', row['rel']))


def extract_facts(config, selections, files, quote_ref=None):
    facts, cache = {}, {}

    def text_of(row):
        if row['rel'] not in cache:
            cache[row['rel']] = file_text(row['path'])
        return cache[row['rel']]

    for name, rule in (config.get('facts') or {}).items():
        if name == 'quote_ref' and quote_ref:
            facts[name] = {'value': quote_ref, 'source': 'build parameter', 'location': '--quote', 'method': 'given'}
            continue
        if rule.get('vote'):
            votes = {}
            for component in rule['components']:
                for row in source_rows(selections, component, files):
                    for location, text in [('file name', row['name'])] + text_of(row)['locations']:
                        if '(hidden)' in location:
                            continue
                        for match in re.finditer(rule['pattern'], text, re.I):
                            value = rule.get('format', '{0}').format(*match.groups())
                            votes.setdefault(value, []).append((row, location))
            if votes:
                ranked = sorted(votes.items(), key=lambda item: -len(item[1]))
                value, where = ranked[0]
                facts[name] = {**_fact(value, where[0][0], where[0][1], f'majority:{len(where)} occurrences'),
                               'alternatives': {other: len(hits) for other, hits in ranked[1:]}}
            continue
        for component in rule['components']:
            found = None
            for row in source_rows(selections, component, files):
                for location, text in text_of(row)['locations']:
                    if '(hidden)' in location:
                        continue
                    match = re.search(rule['pattern'], text, re.I)
                    if match:
                        groups = [' '.join(group.split()) for group in match.groups()]
                        value = rule.get('format', '{0}').format(*groups)
                        found = _fact(value, row, location, f'pattern:{name}')
                        break
                if found:
                    break
            if found:
                facts[name] = found
                break

    tables = {}
    for name, rule in (config.get('tables') or {}).items():
        tables[name] = _extract_table(rule, source_rows(selections, rule['component'], files))
    scopes = []
    scope_rule = config.get('scopes')
    if scope_rule:
        for row in source_rows(selections, scope_rule['component'], files):
            if row['ext'] != 'docx':
                continue
            for block in docx_body_blocks(row['path']):
                if block['kind'] == 'heading' and re.match(scope_rule['heading_pattern'], block['text'], re.I):
                    scopes.append(_fact(block['text'], row, block['style'], 'heading'))
    tqs = _extract_tqs(config.get('tq_logs'), files)
    return {'facts': facts, 'tables': tables, 'scopes': scopes, 'tqs': tqs}


def _extract_table(rule, rows):
    rows_out = []
    for row in rows[:1] if rows and rows[0]['ext'] == 'docx' else []:
        group = ''
        for block in docx_body_blocks(row['path']):
            if block['kind'] != 'table':
                group = block['text'].rstrip(':')[:80]
                continue
            header_index = next((i for i, values in enumerate(block['rows'])
                                 if all(any(word.casefold() in v.casefold() for v in values)
                                        for word in rule['header_contains'])), None)
            if header_index is None:
                continue
            header = block['rows'][header_index]
            for index, values in enumerate(block['rows'][header_index + 1:], header_index + 2):
                cells = [v for v in values]
                if not any(cells) or len([c for c in cells if c]) < rule.get('min_columns', 2):
                    continue
                columns = rule.get('columns') or [h or f'col{i}' for i, h in enumerate(header)]
                record = dict(zip(columns, cells))
                record['group'] = group
                rows_out.append({'value': record, 'source': row['rel'],
                                 'location': f'table "{group or header[0]}" row {index}', 'method': 'table'})
    return rows_out


def _extract_tqs(rule, files):
    if not rule:
        return []
    out = []
    for row in files:
        if not re.search(rule['match'], row['rel'], re.I) or row.get('duplicate_of'):
            continue
        try:
            sheets = spreadsheet_rows(row['path'])
        except Exception:
            continue
        for sheet, values in sheets.items():
            header = next((i for i, cells in enumerate(values)
                           if cells and re.match(rule['header_first_cell'], str(cells[0]).strip(), re.I)), None)
            if header is None:
                continue
            for index, cells in enumerate(values[header + 1:], header + 2):
                if cells and str(cells[0]).strip() and len(cells) >= 5 and any(c for c in cells[1:5]):
                    out.append({'value': {'no': cells[0], 'reference': cells[1], 'subject': cells[2],
                                          'query': cells[3], 'response': cells[4]},
                                'source': row['rel'], 'location': f'sheet {sheet} row {index}', 'method': 'table'})
    return out


def _raw_docx_text(path):
    import zipfile

    with zipfile.ZipFile(path) as archive:
        return '\n'.join(
            ' '.join(re.findall(r'<w:t(?:\s[^>]*)?>([^<]*)</w:t>', archive.read(name).decode('utf-8', 'ignore')))
            for name in archive.namelist() if re.match(r'word/[^/]+\.xml$', name)
        )


def reference_scan(rows, quote_ref):
    """Find Q-references other than the bid's own in each file: visible text, hidden runs,
    and text that exists only in the raw document XML (e.g. text-box fallbacks)."""
    target = re.sub(r'\D', '', quote_ref or '')
    pattern = r'\bQ-?(\d{6})\b'
    hits = []
    for row in rows:
        info = file_text(row['path'])
        seen = set()
        for location, text in info['locations']:
            for value in sorted(set(re.findall(pattern, text, re.I))):
                if target and value != target:
                    seen.add(value)
                    hits.append({'ref': f'Q-{value}', 'source': row['rel'], 'location': location,
                                 'hidden': '(hidden)' in location})
        if row['ext'] == 'docx':
            try:
                raw = _raw_docx_text(row['path'])
            except Exception:
                continue
            for value in sorted(set(re.findall(pattern, raw, re.I)) - seen):
                if target and value != target:
                    hits.append({'ref': f'Q-{value}', 'source': row['rel'],
                                 'location': 'document XML, not in visible text (hidden/text-box fallback)',
                                 'hidden': True})
    return hits


def unreadable(selected_rows):
    return [(row, file_text(row['path']).get('error')) for row in selected_rows
            if Path(row['path']).suffix.lower() in ('.docx', '.pptx', '.pdf') and file_text(row['path']).get('error')]
