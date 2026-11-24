"""Resolve the configured section tree and component mapping into an assembly plan."""
from __future__ import annotations

import re


def walk_sections(sections, depth=1, parent=None):
    for section in sections:
        yield section, depth, parent
        yield from walk_sections(section.get('children') or [], depth + 1, section)


def section_label(section):
    number = section.get('number')
    return f"{number} {section['title'].upper()}" if number else section['title'].upper()


def _numeric_prefix(row):
    match = re.match(r'^\s*(\d+)', row['stem'])
    return (int(match.group(1)) if match else 10 ** 6, row['name'].casefold())


def select_component(key, rule, files):
    """Pick the file(s) for one component; return (selected, rejected[(row, reason)])."""
    candidates = [row for row in files if key in row['components']]
    rejected, usable = [], []
    for row in candidates:
        if row.get('duplicate_of'):
            rejected.append((row, f"duplicate of {row['duplicate_of']} ({row.get('duplicate_kind')})"))
        elif row['variant']:
            rejected.append((row, 'non-final variant (markup/draft copy)'))
        elif rule.get('exclude_aggregate') and re.search(rule['exclude_aggregate'], row['name'], re.I):
            rejected.append((row, 'compiled aggregate; individual files used instead'))
        else:
            usable.append(row)
    prefer = rule.get('prefer') or []
    rank = {ext: index for index, ext in enumerate(prefer)}
    if rule.get('multi'):
        groups = {}
        for row in usable:
            groups.setdefault(re.sub(r'[^a-z0-9]', '', row['stem'].casefold()), []).append(row)
        selected = []
        for group in groups.values():
            group.sort(key=lambda r: rank.get(r['ext'], 99))
            selected.append(group[0])
            rejected.extend((row, f"format twin of {group[0]['rel']}") for row in group[1:])
        selected.sort(key=_numeric_prefix)
        return selected, rejected
    usable.sort(key=lambda r: (rank.get(r['ext'], 99), not r['supported'], r['name']))
    renderable = [row for row in usable if row['supported']]
    if not renderable:
        return [], rejected + [(row, 'format not renderable') for row in usable]
    chosen = renderable[0]
    rejected.extend((row, f"alternative format of {chosen['rel']}") for row in usable if row is not chosen)
    return [chosen], rejected


def build_plan(config, inventory):
    files = inventory['files']
    plan, selections = [], {}
    for key, rule in config['components'].items():
        selections[key] = select_component(key, rule, files)
    for section, depth, parent in walk_sections(config['sections']):
        entry = {
            'key': section['key'], 'title': section['title'], 'number': section.get('number'),
            'label': section_label(section), 'depth': depth,
            'parent': parent['key'] if parent else None,
            'divider': bool(section.get('divider')), 'subdivider': bool(section.get('subdivider')),
            'required': bool(section.get('required')), 'generated': section.get('generated'),
            'toc': section.get('toc', True), 'not_applicable': bool(section.get('not_applicable')),
            'components': [],
        }
        for component in section.get('components') or []:
            selected, _ = selections[component]
            rule = config['components'][component]
            entry['components'].append({
                'key': component, 'files': selected, 'pages': rule.get('pages'),
                'role': rule.get('role', 'authored'),
            })
        plan.append(entry)
    return plan, selections


def review_sections(config):
    """(key, label) of every section that carries components — the units a reviewer approves."""
    return [(section['key'], section_label(section)) for section, _, _ in walk_sections(config['sections'])
            if section.get('components')]


def plan_summary(plan):
    """JSON-safe view of a resolved plan (no absolute paths)."""
    return [{
        'key': entry['key'], 'label': entry['label'], 'number': entry['number'], 'depth': entry['depth'],
        'parent': entry['parent'], 'required': entry['required'], 'divider': entry['divider'] or entry['subdivider'],
        'components': [{
            'key': component['key'], 'pages': component['pages'],
            'files': [{'rel': row['rel'], 'name': row['name'], 'ext': row['ext'], 'sha256': row['sha256'],
                       'binder_pages': next(([a, b] for a, b, rel in component.get('binder_pages') or []
                                             if rel == row['rel']), None)}
                      for row in component['files']],
        } for component in entry['components']],
    } for entry in plan]
