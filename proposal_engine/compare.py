"""Structural equivalence between a generated binder and the baseline proposal PDF.

Both PDFs are analysed with the same functions:
  * hierarchy  – TOC entries (number + title) in order;
  * dividers   – sequence of divider titles;
  * sections   – each page labelled with the last divider before it, compared page by page;
  * content    – each baseline page matched to a generated page by text (word sets) or, for
                 image-only pages, by a thumbnail; counted only when matches stay in order.
"""
from __future__ import annotations

import bisect
import re
from difflib import SequenceMatcher

BOILERPLATE = re.compile(r'Millennium Tower|Tel: \+971|^Rejlers International Engineering Solutions AB\s*$|'
                         r'^Technical Proposal$|^Confidential$|^Rev \d+$|^Q-\d{6}$', re.I)


def _norm(text):
    return re.sub(r'[^A-Z0-9.]', '', text.upper().replace('’', "'"))


def analyse(path, labels):
    import pymupdf

    wanted = {_norm(label): label for label in labels}
    pages = []
    with pymupdf.open(str(path)) as document:
        for page in document:
            lines = [line.strip() for line in page.get_text().splitlines() if line.strip()]
            content = [line for line in lines if not BOILERPLATE.search(line)]
            words = {w for w in re.findall(r'[a-z0-9]{3,}', ' '.join(content).lower())}
            pix = page.get_pixmap(matrix=pymupdf.Matrix(0.06, 0.06), colorspace=pymupdf.csGRAY)
            divider = wanted.get(_norm(' '.join(content))) if 0 < len(content) <= 3 else None
            toc = [line for line in content] if any(re.fullmatch(r'TABLE OF CONTENTS', l, re.I) for l in content) else None
            pages.append({'words': words, 'thumb': bytes(pix.samples), 'size': (pix.width, pix.height),
                          'divider': divider, 'toc': toc})
    section = 'FRONT MATTER'
    for page in pages:
        section = page['divider'] or section
        page['section'] = section
    return pages


def _similar(a, b):
    if len(a['words']) >= 15 and len(b['words']) >= 15:
        return len(a['words'] & b['words']) / len(a['words'] | b['words'])
    if len(a['words']) >= 15 or len(b['words']) >= 15 or a['size'] != b['size']:
        return 0.0
    diff = sum(abs(x - y) for x, y in zip(a['thumb'], b['thumb'])) / (255 * max(1, len(a['thumb'])))
    return max(0.0, 1 - 4 * diff)


def _lis(values):
    tails = []
    for value in values:
        index = bisect.bisect_left(tails, value)
        tails[index:index + 1] = [value]
    return len(tails)


def compare(generated_pdf, reference_pdf, plan):
    labels = [entry['label'] for entry in plan if entry['divider'] or entry['subdivider']]
    gen, ref = analyse(generated_pdf, labels), analyse(reference_pdf, labels)

    def toc_entries(pages):
        page = next((p for p in pages if p['toc']), None)
        return [_norm(line) for line in (page['toc'] if page else []) if re.match(r'^\d', line)]

    ref_toc, gen_toc = toc_entries(ref), toc_entries(gen)
    hierarchy = SequenceMatcher(None, ref_toc, gen_toc).ratio() if ref_toc or gen_toc else 0.0
    ref_div = [p['divider'] for p in ref if p['divider']]
    gen_div = [p['divider'] for p in gen if p['divider']]
    dividers = SequenceMatcher(None, ref_div, gen_div).ratio() if ref_div or gen_div else 0.0
    sections = SequenceMatcher(None, [p['section'] for p in ref], [p['section'] for p in gen], autojunk=False).ratio()

    matched, unmatched = [], []
    for index, page in enumerate(ref):
        same = [j for j, other in enumerate(gen) if other['section'] == page['section']] or range(len(gen))
        best = max(same, key=lambda j: _similar(page, gen[j]), default=None)
        score = _similar(page, gen[best]) if best is not None else 0
        if score >= 0.5:
            matched.append(best)
        else:
            unmatched.append((index + 1, page['section']))
    content = _lis(matched) / len(ref) if ref else 0.0
    overall = (hierarchy + dividers + sections + content) / 4

    per_section = {}
    for page in ref:
        per_section.setdefault(page['section'], [0, 0])[0] += 1
    for page in gen:
        per_section.setdefault(page['section'], [0, 0])[1] += 1
    rows = '\n'.join(f'| {name} | {r} | {g} | {g - r:+d} |' for name, (r, g) in per_section.items())
    unmatched_ranges = _ranges(unmatched)
    markdown = '\n'.join([
        f'**Structural equivalence: {overall:.1%}** (target > 95%)',
        '',
        '| Measure | Score |', '|---|---|',
        f'| Section hierarchy & numbering (TOC entries) | {hierarchy:.1%} |',
        f'| Divider sequence | {dividers:.1%} |',
        f'| Page-by-page section composition | {sections:.1%} |',
        f'| Baseline pages matched in order by content | {content:.1%} ({_lis(matched)}/{len(ref)}) |',
        '',
        f'Pages: baseline {len(ref)}, generated {len(gen)}.',
        '',
        '| Section | Baseline pages | Generated pages | Δ |', '|---|---|---|---|', rows,
        '',
        'Baseline pages without a content match: ' + (unmatched_ranges or 'none'),
    ])
    return {'overall': overall, 'hierarchy': hierarchy, 'dividers': dividers, 'sections': sections,
            'content': content, 'reference_pages': len(ref), 'generated_pages': len(gen),
            'unmatched': unmatched, 'markdown': markdown}


def _ranges(items):
    if not items:
        return ''
    out, start, prev, section = [], items[0][0], items[0][0], items[0][1]
    for page, sec in items[1:] + [(None, None)]:
        if page is not None and page == prev + 1 and sec == section:
            prev = page
            continue
        out.append(f'{start}' + (f'–{prev}' if prev != start else '') + f' ({section})')
        if page is not None:
            start = prev = page
            section = sec
    return '; '.join(out)
