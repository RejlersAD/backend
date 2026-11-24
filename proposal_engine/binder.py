"""Client PDF binder: generated TOC/dividers + rendered components in standard order."""
from __future__ import annotations

from pathlib import Path

from .docxkit import build_frontmatter


def _slice(count, spec):
    if not spec:
        return list(range(count))
    start, _, stop = str(spec).partition(':')
    return list(range(count))[slice(int(start) if start else None, int(stop) if stop else None)]


def build_binder(plan, config, converter, work_dir, out_pdf, findings):
    import pymupdf

    work_dir = Path(work_dir)
    toc_entries = [(entry['number'] or '', entry['title'].upper()) for entry in plan
                   if entry['toc'] and entry['depth'] <= 2 and not entry['subdivider'] and entry['number']]
    divider_entries = [entry for entry in plan if entry['divider'] or entry['subdivider']]
    labels = [entry['label'] for entry in divider_entries]
    front_docx = build_frontmatter(config['templates']['divider'], toc_entries, labels, work_dir / 'frontmatter.docx')
    front_pdf = pymupdf.open(str(converter.to_pdf(front_docx, update_fields=True)))
    if front_pdf.page_count != 1 + len(labels):
        findings.append(('ERROR', 'FRONTMATTER_LAYOUT',
                         f'Generated TOC/dividers rendered {front_pdf.page_count} pages; expected {1 + len(labels)} '
                         '(each divider must be exactly one page)'))
    divider_page = {entry['key']: index + 1 for index, entry in enumerate(divider_entries)}

    binder = pymupdf.open()
    page_map, outline = [], []

    def add(document, pages, section, component, source, source_pages=None):
        start = binder.page_count
        for page in pages:
            binder.insert_pdf(document, from_page=page, to_page=page)
        for offset, page in enumerate(pages):
            page_map.append({'page': start + offset + 1, 'section': section, 'component': component,
                             'source': source, 'source_page': (source_pages or pages)[offset] + 1})
        return start + 1

    for entry in plan:
        level = min(entry['depth'], 3)
        first_page = None
        if entry['generated'] == 'toc':
            first_page = add(front_pdf, [0], entry['key'], 'generated:toc', 'generated')
        if entry['key'] in divider_page:
            page = divider_page[entry['key']]
            if page < front_pdf.page_count:
                first_page = add(front_pdf, [page], entry['key'], 'generated:divider', 'generated')
        for component in entry['components']:
            for row in component['files']:
                try:
                    rendered = converter.to_pdf(row['path'])
                    document = pymupdf.open(str(rendered))
                except Exception as error:
                    findings.append(('ERROR', 'RENDER_FAILED', f"{row['rel']}: {error}"))
                    continue
                pages = _slice(document.page_count, component['pages'])
                if row['ext'] != 'pdf' and not component['pages']:
                    dropped = _trailing_blank_pages(document)
                    if dropped:
                        pages = pages[:len(pages) - dropped]
                        findings.append(('INFO', 'BLANK_PAGE_DROPPED',
                                         f"{row['rel']}: {dropped} trailing page(s) with letterhead only removed"))
                if not pages:
                    findings.append(('WARN', 'EMPTY_SLICE', f"{row['rel']}: page range {component['pages']} selects no pages"))
                    continue
                start = add(document, pages, entry['key'], component['key'], row['rel'])
                first_page = first_page or start
                component.setdefault('binder_pages', []).append((start, start + len(pages) - 1, row['rel']))
        if first_page:
            outline.append([level, entry['label'], first_page])
    # Bookmark levels must not skip (e.g. a level-3 entry directly after level 1).
    previous = 0
    for item in outline:
        item[0] = min(item[0], previous + 1)
        previous = item[0]
    stamped = apply_stamp(binder, page_map, plan, config.get('templates', {}).get('stamp'))
    if stamped is not None:
        findings.append(('INFO', 'COMPANY_STAMP', f'Company stamp applied to {stamped} pages'))
    binder.set_toc(outline)
    binder.set_metadata({'title': config.get('_title', 'Technical Proposal'), 'creator': 'proposal_engine',
                         'producer': 'proposal_engine'})
    # garbage=4 merges identical streams: LibreOffice repeats slide images per page (327 MB → 20 MB, lossless).
    binder.save(str(out_pdf), garbage=4, deflate=True)
    return page_map


def _trailing_blank_pages(document):
    """Count trailing pages that hold only letterhead (boilerplate text, page number, and no
    more images than the document's least-illustrated page) — Word reflow artefacts."""
    import re

    from .compare import BOILERPLATE

    if document.page_count < 2:
        return 0
    from collections import Counter

    texts = [[l.strip() for l in page.get_text().splitlines() if l.strip()] for page in document]
    # Running headers/footers: lines present on at least 80% of the document's pages.
    seen = Counter(line for lines in texts for line in set(lines))
    running = {line for line, hits in seen.items() if hits >= 0.8 * document.page_count}
    baseline_images = min(len(page.get_images()) for page in document)
    count = 0
    for index in range(document.page_count - 1, 0, -1):
        page = document[index]
        body = [l for l in texts[index] if l not in running and not BOILERPLATE.search(l)
                and not re.fullmatch(r'(page\s*)?\d+\s*(\(\d+\)|of\s*\d+)?', l, re.I)]
        if body or len(page.get_images()) > baseline_images or len(page.get_drawings()) > 20:
            break
        count += 1
    return count


def apply_stamp(binder, page_map, plan, stamp):
    """Overlay the company stamp on every page except excluded sections and pages that already
    carry the same stamp image."""
    import pymupdf

    if not stamp or not Path(stamp['image']).exists():
        return None
    data = Path(stamp['image']).read_bytes()
    pixmap = pymupdf.Pixmap(data)
    digest = pixmap.digest
    ratio = pixmap.height / pixmap.width
    excluded = set(stamp.get('exclude_sections') or [])
    parents = {entry['key']: entry['parent'] for entry in plan}
    xref, count = 0, 0
    for item in page_map:
        key = item['section']
        chain = {key}
        while parents.get(key):
            key = parents[key]
            chain.add(key)
        if chain & excluded and not item['component'].startswith('generated:'):
            continue
        page = binder[item['page'] - 1]
        if any(info.get('digest') == digest for info in page.get_image_info(hashes=True)):
            continue
        if item['component'].startswith('generated:'):
            spot = stamp['generated']
            rect = pymupdf.Rect(spot['x'], spot['y'], spot['x'] + spot['width'], spot['y'] + spot['width'] * ratio)
        else:
            spot = stamp['components']
            width, margin = spot['width'], spot.get('margin', 0)
            box = page.rect
            rect = pymupdf.Rect(box.x1 - margin - width, box.y1 - margin - width * ratio, box.x1 - margin, box.y1 - margin)
            # Map through page rotation so the stamp lands bottom-right as displayed.
            rect = rect * page.derotation_matrix if page.rotation else rect
        xref = page.insert_image(rect, stream=data if not xref else None, xref=xref, overlay=True)
        count += 1
    return count
