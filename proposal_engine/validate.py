"""Validation findings (ERROR/WARN/INFO) and build_report.md."""
from __future__ import annotations

import re
from datetime import datetime, timezone

from .extract import reference_scan, source_rows

REQUIRED_FACTS = ('quote_ref', 'rft_number', 'rft_title', 'client', 'revision', 'submission_date', 'validity_days')
ORDER = {'ERROR': 0, 'WARN': 1, 'INFO': 2}


def validate(config, inventory, plan, selections, facts, quote_ref):
    findings = []
    files = inventory['files']
    for name in REQUIRED_FACTS:
        if name not in facts['facts']:
            findings.append(('ERROR', 'META_MISSING', f'Required fact `{name}` could not be extracted from the mapped sources'))
    extracted_quote = facts['facts'].get('quote_ref', {})
    for other, count in (extracted_quote.get('alternatives') or {}).items():
        findings.append(('WARN', 'FACT_CONFLICT', f'Quote reference {other} also occurs {count}× in submitted documents '
                                                  f"(majority value {extracted_quote['value']} used)"))
    for entry in plan:
        if entry['required'] and entry['components'] and not any(c['files'] for c in entry['components']):
            findings.append(('ERROR', 'SECTION_NO_SOURCE', f"Required section {entry['label']} has no source file"))
        for component in entry['components']:
            if not component['files']:
                severity = 'ERROR' if entry['required'] else 'WARN'
                findings.append((severity, 'COMPONENT_NO_SOURCE',
                                 f"{entry['label']}: component `{component['key']}` matched no usable file"))

    assembled = [row for entry in plan for component in entry['components'] for row in component['files']]
    assembled_rels = {row['rel'] for row in assembled}
    twins = []
    for entry in plan:
        for component in entry['components']:
            twins.extend(source_rows({component['key']: (component['files'], [])}, component['key'], files))
    scanned = {row['rel']: row for row in assembled + twins}.values()
    grouped = {}
    for hit in reference_scan(scanned, quote_ref):
        grouped.setdefault((hit['ref'], hit['source'], hit['hidden']), []).append(hit['location'])
    for (ref, source, hidden), locations in grouped.items():
        where = 'assembled' if source in assembled_rels else 'editable twin of an assembled file'
        kind = 'Hidden legacy reference' if hidden else 'Reference to another bid'
        shown = ', '.join(locations[:4]) + (f' … ({len(locations)} locations)' if len(locations) > 4 else '')
        findings.append(('ERROR', 'LEGACY_REFERENCE', f'{kind} {ref} in {source} ({shown}; {where})'))

    for row in files:
        rel = row['rel']
        if row['foreign_refs']:
            severity = 'ERROR' if rel in assembled_rels else 'WARN'
            findings.append((severity, 'FOREIGN_BID_ARTIFACT',
                             f"{rel} belongs to {', '.join(row['foreign_refs'])}"
                             + (' and is ASSEMBLED' if severity == 'ERROR' else ' (not assembled)')))
        if not row['supported']:
            twin = next((other['rel'] for other in files if other.get('twin_of') == rel or row.get('twin_of') == other['rel']), None)
            findings.append(('WARN', 'UNSUPPORTED_FORMAT', f".{row['ext']} cannot be rendered: {rel}"
                             + (f' (renderable twin: {twin})' if twin else ' (no renderable twin)')))
        if row.get('duplicate_of'):
            findings.append(('WARN', 'DUPLICATE_FILE', f"{rel} duplicates {row['duplicate_of']} ({row.get('duplicate_kind')})"))
        if row['variant']:
            findings.append(('WARN', 'VARIANT_FILE', f'{rel} looks like a non-final variant; excluded'))
        if not row['components'] and not row['reference_only']:
            findings.append(('WARN', 'UNCLASSIFIED', f'{rel} matched no folder→section rule'))
        if row['reference_only']:
            findings.append(('INFO', 'REFERENCE_ONLY', f"{rel}: {row['reference_only']}"))
        if row.get('twin_of'):
            findings.append(('INFO', 'FORMAT_TWIN', f"{rel} ⇄ {row['twin_of']}"))

    expected = config.get('expected_inputs') or []
    for folder in inventory['folders']:
        if not folder['empty']:
            continue
        rule = next((r for r in expected if re.search(r['match'], folder['rel'], re.I)), None)
        if rule:
            findings.append((rule.get('severity', 'WARN'), 'EXPECTED_INPUT_EMPTY',
                             f"{rule['label']} folder is empty: {folder['rel']}"))
        else:
            findings.append(('INFO', 'EMPTY_FOLDER', folder['rel']))

    unanswered = [tq for tq in facts['tqs'] if not str(tq['value']['response']).strip()]
    if facts['tqs']:
        findings.append(('INFO', 'TQ_LOG', f"{len(facts['tqs'])} technical queries extracted; "
                                           f'{len(unanswered)} have no client response recorded'))
    return findings


def _table(headers, rows):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ')
    lines = ['| ' + ' | '.join(headers) + ' |', '|' + '---|' * len(headers)]
    lines += ['| ' + ' | '.join(cell(v) for v in row) + ' |' for row in rows]
    return '\n'.join(lines)


def write_report(path, *, config, inventory, plan, facts, findings, outputs, comparison=None, quote_ref=None):
    findings = sorted(set(findings), key=lambda f: (ORDER[f[0]], f[1], f[2]))
    counts = {level: sum(1 for f in findings if f[0] == level) for level in ORDER}
    lines = [
        f"# Build report — Technical Proposal {quote_ref or ''}".rstrip(),
        '',
        f"- Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        f"- Standard: `{config['standard']['id']}` (baseline {config['standard']['baseline']})",
        f"- Files scanned: {len(inventory['files'])} · folders: {len(inventory['folders'])}",
        f"- Findings: **{counts['ERROR']} ERROR**, {counts['WARN']} WARN, {counts['INFO']} INFO",
        f"- Publishable: **{'NO' if counts['ERROR'] else 'YES (after human review)'}**",
        '',
        '## Outputs',
        '',
        *[f'- {label}: `{value}`' for label, value in outputs.items()],
        '',
    ]
    if comparison:
        lines += ['## Structural equivalence with the baseline', '', comparison['markdown'], '']
    lines += ['## Extracted facts (with provenance)', '',
              _table(['Fact', 'Value', 'Source', 'Location', 'Method'],
                     [(name, fact['value'], fact['source'], fact['location'], fact['method'])
                      for name, fact in facts['facts'].items()]),
              '']
    lines += [f"Tables: {len(facts['tables'].get('key_personnel', []))} key-personnel rows, "
              f"{len(facts['tables'].get('qualifications', []))} qualifications, "
              f"{len(facts['tables'].get('subcontractors', []))} subcontractor rows, "
              f"{len(facts['scopes'])} scopes, {len(facts['tqs'])} TQs — full values with row-level provenance in `facts.json`.",
              '']
    lines += ['## Assembly plan', '',
              _table(['Section', 'Component', 'File', 'Binder pages'],
                     [(entry['label'], component['key'], row['rel'].rsplit('/', 1)[-1],
                       next((f'{a}–{b}' for a, b, rel in component.get('binder_pages') or [] if rel == row['rel']), '—'))
                      for entry in plan for component in entry['components'] for row in component['files']]
                     + [(entry['label'], component['key'], '**MISSING**', '—')
                        for entry in plan for component in entry['components'] if not component['files']]),
              '']
    for level in ORDER:
        rows = [(code, message) for severity, code, message in findings if severity == level]
        lines += [f'## {level} ({len(rows)})', '']
        lines += [_table(['Code', 'Finding'], rows) if rows else '_None._', '']
    with open(path, 'w', encoding='utf-8') as handle:
        handle.write('\n'.join(lines))
    return counts
