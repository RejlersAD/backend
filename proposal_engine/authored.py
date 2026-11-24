"""Render an authored technical proposal (DEC-TP-001) into one PDF deliverable and a Word master.

Framework-independent: input is the proposal content (sections with cited blocks) plus facts; the
company standard comes from `config/rejlers_technical_proposal.yaml` and the curated templates.
"""
from __future__ import annotations

import re
from copy import deepcopy
from pathlib import Path

import yaml
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Pt

from .docxkit import clear_body, fill_tokens, open_template, repeat_header_row, replace_text

DEFAULT_AUTHORED_CONFIG = Path(__file__).parent / 'config' / 'rejlers_technical_proposal.yaml'
TEMPLATE_KEYS = ('body', 'divider', 'cover', 'standard_content')


def load_authored_config(path=None):
    path = Path(path or DEFAULT_AUTHORED_CONFIG)
    with open(path, encoding='utf-8') as handle:
        config = yaml.safe_load(handle)
    for key in TEMPLATE_KEYS:
        config['templates'][key] = str((path.parent / config['templates'][key]).resolve())
    config['templates']['stamp']['image'] = str((path.parent / config['templates']['stamp']['image']).resolve())
    keys = [section['key'] for section in config['sections']]
    if len(keys) != len(set(keys)):
        raise ValueError(f'{path}: duplicate section keys')
    config['_path'] = str(path)
    return config


def section_label(section):
    number = section.get('number')
    title = section['title'].upper()
    return f'{number} {title}' if number else title


def review_units(config):
    """Sections a reviewer approves, in document order: everything except the TOC and pure dividers."""
    return [(section['key'], section_label(section)) for section in config['sections']
            if section['kind'] not in ('toc', 'divider')]


# --- section documents ----------------------------------------------------------------------

def _body_document(config, quote_ref):
    document = open_template(config['templates']['body'])
    clear_body(document)
    replace_text(document, config['templates']['quote_token'], quote_ref)
    return document


def _style(document, *names):
    available = {style.name for style in document.styles}
    return next((name for name in names if name in available), None)


def _plain_heading(document, text, size=12):
    paragraph = document.add_paragraph()
    run = paragraph.add_run(text)
    run.bold = True
    run.font.size = Pt(size)
    paragraph.paragraph_format.space_before = Pt(10)
    paragraph.paragraph_format.keep_with_next = True
    return paragraph


def _heading(document, text, level, numbered):
    style = _style(document, f'Heading {level}') if numbered else None
    if style:
        return document.add_paragraph(text, style=style)
    return _plain_heading(document, text, 13 if level == 1 else 11)


def _bullets(document, items):
    style = _style(document, 'List Bullet', 'List Paragraph')
    for item in items:
        if style == 'List Bullet':
            document.add_paragraph(item, style=style)
        else:
            paragraph = document.add_paragraph(f'•  {item}', style=style) if style else document.add_paragraph(f'•  {item}')
            paragraph.paragraph_format.left_indent = Pt(18)
            paragraph.paragraph_format.first_line_indent = Pt(-12)


def _table(document, columns, rows):
    width = max([len(columns)] + [len(row) for row in rows]) or 1
    table = document.add_table(rows=1, cols=width)
    style = _style(document, 'Table Grid')
    if style:
        table.style = style
    for cell, text in zip(table.rows[0].cells, columns + [''] * (width - len(columns))):
        cell.text = text
        for run in cell.paragraphs[0].runs:
            run.bold = True
    repeat_header_row(table)
    for row in rows:
        cells = table.add_row().cells
        for cell, text in zip(cells, list(row) + [''] * (width - len(row))):
            cell.text = str(text)
    document.add_paragraph()
    return table


def write_blocks(document, blocks, *, numbered):
    for block in blocks:
        kind = block.get('type')
        if kind == 'part':
            _heading(document, block['text'], 1, numbered)
        elif kind == 'heading':
            _heading(document, block['text'], int(block.get('level') or 2), numbered)
        elif kind == 'paragraph' and block.get('text'):
            document.add_paragraph(block['text'])
        elif kind == 'bullets' and block.get('items'):
            if block.get('text'):
                document.add_paragraph(block['text'])
            _bullets(document, block['items'])
        elif kind == 'table' and block.get('rows'):
            if block.get('text'):
                document.add_paragraph(block['text'])
            _table(document, list(block.get('columns') or []), block['rows'])
        elif kind == 'placeholder':
            paragraph = document.add_paragraph()
            run = paragraph.add_run(block['text'])
            run.italic = True


def section_document(config, section, content, quote_ref, path):
    # The section title is carried by its divider page (PDF) and section heading (Word master).
    document = _body_document(config, quote_ref)
    write_blocks(document, content.get('blocks') or [], numbered=bool(section.get('numbered_headings')))
    document.save(str(path))
    return path


def cover_document(config, facts, path):
    return fill_tokens(config['templates']['cover'], path, {
        'quote_ref': facts['quote_ref'], 'tender_title': facts['tender_title'].upper(),
        'rft_number': facts.get('rft_number') or 'TO BE CONFIRMED', 'revision_line': facts['revision_line'],
        'client': facts['client'].upper(),
    })


def letter_document(config, facts, path):
    document = _body_document(config, facts['quote_ref'])
    company = config['company']
    rows = [('From:', f"{company['signatory']}, {company['name']}"), ('Email:', company['contact_email']),
            ('Telephone:', company['telephone']), ('To:', facts['client']),
            ('Your Ref.:', facts.get('rft_number') or 'To be confirmed'),
            ('Our Ref.:', f"{facts['quote_ref']} Rev. {facts['revision']} Technical Proposal"),
            ('Date:', facts['date']), ('Subject:', f"{facts['tender_title'].upper()} – TECHNICAL PROPOSAL")]
    table = document.add_table(rows=0, cols=2)
    for label, value in rows:
        cells = table.add_row().cells
        cells[0].text, cells[1].text = label, value
        cells[0].paragraphs[0].runs[0].bold = True
    document.add_paragraph()
    validity = facts.get('validity_days')
    validity_sentence = (f'Our offer is valid for {validity} days from the Technical Bid Submission date.' if validity
                         else 'Our offer is valid for the period required by the tender.')
    for text in config['letter']['paragraphs']:
        document.add_paragraph(text.format(tender_title=facts['tender_title'], validity_sentence=validity_sentence))
    document.add_paragraph()
    for line in [company['signatory']] + list(company['signatory_title']):
        document.add_paragraph(line)
    document.save(str(path))
    return path


def compliance_content(requirements, traceability, labels):
    rows = []
    for index, requirement in enumerate(requirements, 1):
        links = traceability.get(requirement['id']) or []
        where = ', '.join(sorted({labels.get(key, key) for key in links})) or '—'
        source = requirement.get('source_file', '')
        if requirement.get('page'):
            source += f", p. {requirement['page']}"
        rows.append([str(index), requirement['requirement_text'][:400], source,
                     where, 'Addressed' if links else 'To be confirmed'])
    return {'blocks': [
        {'type': 'paragraph', 'text': 'The table lists each requirement identified in the tender documents and the '
                                      'proposal section that responds to it.'},
        {'type': 'table', 'columns': ['No.', 'Tender requirement', 'Tender source', 'Response in', 'Status'], 'rows': rows},
    ]}


# --- assembly -------------------------------------------------------------------------------

def _row(path, key):
    path = Path(path)
    return {'path': str(path), 'rel': f'generated/{key}/{path.name}', 'name': path.name, 'ext': 'docx',
            'sha256': '', 'duplicate_of': None, 'variant': False, 'twin_of': None, 'supported': True,
            'components': [key], 'reference_only': None, 'foreign_refs': []}


def build_plan(config, files):
    """Binder plan (see proposal_engine.binder) over generated section documents."""
    plan = []
    for section in config['sections']:
        kind = section['kind']
        parent = section.get('parent')
        numbered = bool(section.get('number'))
        component = [] if section['key'] not in files else [{
            'key': section['key'], 'files': [_row(files[section['key']], section['key'])], 'pages': None,
            'role': 'submitted' if kind in ('cover', 'letter') else 'authored'}]
        plan.append({
            'key': section['key'], 'title': section['title'], 'number': section.get('number'),
            'label': section_label(section), 'depth': 2 if parent else 1, 'parent': parent,
            'divider': numbered and kind not in ('cover', 'letter', 'toc'), 'subdivider': False,
            'required': False, 'generated': 'toc' if kind == 'toc' else None, 'toc': numbered,
            'not_applicable': kind == 'not_applicable', 'components': component,
        })
    return plan


def render(config, facts, sections, requirements, traceability, converter, workdir):
    """Write every section document, compile the PDF binder and the Word master.

    `sections` maps section key → {'blocks': [...]} for authored/evidence/not-applicable sections.
    Returns {'pdf', 'docx', 'section_pages', 'findings'}.
    """
    from .binder import build_binder
    from .master import build_master

    workdir = Path(workdir)
    (workdir / 'sections').mkdir(parents=True, exist_ok=True)
    findings, files = [], {}
    labels = dict(review_units(config))
    for section in config['sections']:
        key, kind = section['key'], section['kind']
        path = workdir / 'sections' / f'{len(files) + 1:02d}-{key}.docx'
        if kind == 'cover':
            files[key] = cover_document(config, facts, path)
        elif kind == 'letter':
            files[key] = letter_document(config, facts, path)
        elif kind == 'compliance':
            files[key] = section_document(config, section, compliance_content(requirements, traceability, labels),
                                          facts['quote_ref'], path)
        elif kind in ('authored', 'evidence', 'not_applicable'):
            files[key] = section_document(config, section, sections.get(key) or {}, facts['quote_ref'], path)
    config = deepcopy(config)
    config['_title'] = f"{facts['quote_ref']} Technical Proposal Rev {facts['revision']}"
    plan = build_plan(config, files)
    pdf = workdir / 'proposal.pdf'
    page_map = build_binder(plan, config, converter, workdir, pdf, findings)
    docx = workdir / 'proposal.docx'
    build_master(plan, config, {'facts': {'quote_ref': {'value': facts['quote_ref']}}}, {}, [], docx, findings,
                 annex_index=False)
    section_pages = {}
    for item in page_map:
        section_pages.setdefault(item['section'], [item['page'], item['page']])
        section_pages[item['section']][1] = item['page']
    findings.extend(_reference_leaks(files.values(), facts['quote_ref']))
    return {'pdf': pdf, 'docx': docx, 'section_pages': section_pages, 'findings': findings}


def _reference_leaks(paths, quote_ref):
    """Other bids' quote references must never appear in a generated proposal."""
    from .readers import docx_parts

    own = re.sub(r'\D', '', quote_ref)
    leaks = set()
    for path in paths:
        for part in docx_parts(path).values():
            for value in re.findall(r'\bQ-?(\d{6})\b', part['text'] + part['hidden']):
                if value != own:
                    leaks.add(f'Q-{value}')
    return [('ERROR', 'LEGACY_REFERENCE', f'Generated text refers to another bid {ref}') for ref in sorted(leaks)]
