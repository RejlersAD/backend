"""Config-driven folder/ZIP technical proposal assembly."""
from __future__ import annotations

import hashlib
import re
from io import BytesIO
from pathlib import PurePosixPath
from uuid import uuid4

COMPANY = {
    'name': 'Rejlers International Engineering Solutions AB',
    'short_name': 'Rejlers',
    'address': 'Millennium Tower, 13th Floor, Hamdan Street, P.O. Box 39317, Abu Dhabi, UAE',
    'signatory_default': 'Mr. Jarmo Suominen',
}
FORMAT_PRIORITY = ('.docx', '.xlsx', '.xls', '.pptx', '.pdf')
RULES = (
    ('cover', r'0_Cover'),
    ('qs', r'1_\s*QS'),
    ('experience', r'Previous Experience'),
    ('deliverables', r'List of Deliverables|LISTOF'),
    ('execution', r'Execution Plan|Execution Work Flow|EXECUT'),
    ('organization', r'Organization and Resources|Org\.? Chart'),
    ('personnel', r'Key Personnel List'),
    ('cvs', r"CV'?s"),
    ('project_control', r'Project Control'),
    ('hse', r'\bHSE\b'),
    ('subcontractors', r'Subcontractors'),
    ('bv', r'3_BV'),
    ('investment', r'4_Investment|5_Ta|6_Investment'),
    ('tq', r'\bTQ'),
    ('kom', r'\bKOM'),
)
SECTION_CONFIG = (
    ('cover', 'Cover Page & Document Control', 'cover', None, False),
    ('toc', 'Table of Contents', 'toc', None, False),
    ('letter', 'Covering Letter', 'letter', 'cover', False),
    ('qs', 'Qualification Statement', 'qualifications', 'qs', True),
    ('experience', 'Previous Experience', 'references', 'experience', True),
    ('execution', 'Project Execution Strategy', 'copy', 'execution', True),
    ('deliverables', 'List of Deliverables', 'copy', 'deliverables', False),
    ('organization', 'Organization and Resources', 'references', 'organization', True),
    ('personnel', 'Key Personnel', 'personnel', 'personnel', True),
    ('project_control', 'Project Control Requirements', 'references', 'project_control', False),
    ('hse', 'HSE Management', 'references', 'hse', True),
    ('subcontractors', 'Proposed Subcontractors', 'copy', 'subcontractors', False),
    ('bv', 'Business Venture', 'copy', 'bv', False),
    ('investment', 'Investment Plan', 'copy', 'investment', False),
    ('tq', 'Technical Query Log', 'queries', 'tq', False),
    ('annex', 'Annex Index', 'annex', None, False),
)


def read_office_source(stream, name):
    """Read passive DOCX/XLSX/XLS facts used by the folder pipeline."""
    suffix = PurePosixPath(name).suffix.casefold()
    blocks, spreadsheets, header_text = [], {}, ''
    if suffix == '.docx':
        from docx import Document
        from docx.table import Table
        from docx.text.paragraph import Paragraph

        stream.seek(0)
        document = Document(stream)
        for child in document.element.body.iterchildren():
            tag = child.tag.rsplit('}', 1)[-1]
            if tag == 'p':
                paragraph = Paragraph(child, document)
                text = paragraph.text.strip()
                if not text:
                    continue
                style = paragraph.style.name if paragraph.style else ''
                blocks.append({
                    'kind': 'heading' if style.startswith('Heading') else
                    'bullet' if 'List' in style or 'Bullet' in style else 'paragraph',
                    'text': text,
                    'level': int(''.join(value for value in style if value.isdigit()) or 1),
                })
            elif tag == 'tbl':
                table = Table(child, document)
                rows = []
                for row in table.rows:
                    values = [' '.join(cell.text.split()) for cell in row.cells]
                    deduped, previous = [], object()
                    for value in values:
                        deduped.append('' if value == previous and value else value)
                        previous = value
                    rows.append(deduped)
                blocks.append({'kind': 'table', 'rows': rows, 'text': ''})
        for section in document.sections:
            for part in (section.header, section.first_page_header):
                header_text += '\n' + '\n'.join(paragraph.text for paragraph in part.paragraphs)
                for table in part.tables:
                    for row in table.rows:
                        header_text += '\n' + ' | '.join(cell.text for cell in row.cells)
    elif suffix == '.xlsx':
        from openpyxl import load_workbook

        stream.seek(0)
        workbook = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
        try:
            for sheet in workbook.worksheets:
                spreadsheets[sheet.title] = [
                    ['' if value is None else str(value).strip() for value in row]
                    for row in sheet.iter_rows(values_only=True)
                ]
        finally:
            workbook.close()
    elif suffix == '.xls':
        import xlrd

        stream.seek(0)
        workbook = xlrd.open_workbook(file_contents=stream.read())
        for sheet in workbook.sheets():
            spreadsheets[sheet.name] = [
                [str(sheet.cell_value(row, column)).strip() for column in range(sheet.ncols)]
                for row in range(sheet.nrows)
            ]
    return {'blocks': blocks, 'spreadsheets': spreadsheets, 'header_text': header_text.strip()}


def _plain(value):
    return ' '.join(str(value or '').split())


def _text(source):
    return '\n'.join(str(chunk.get('text') or '') for chunk in source.get('chunks') or [])


def _classify(source):
    path = source.get('source_path') or source.get('source_name') or ''
    return next((key for key, pattern in RULES if re.search(pattern, path, re.I)), None)


def _normalized_stem(path):
    return re.sub(r'[^a-z0-9]', '', PurePosixPath(path).stem.casefold())


def scan_sources(sources):
    """Classify selected files and mark format twins while retaining archive paths."""
    rows = []
    for source in sources:
        item = dict(source)
        item['section_key'] = _classify(source)
        item['extension'] = PurePosixPath(
            source.get('source_name') or source.get('source_path') or '',
        ).suffix.casefold()
        item['duplicate_of'] = None
        rows.append(item)
    groups = {}
    for row in rows:
        path = row.get('source_path') or row.get('source_name') or ''
        groups.setdefault(
            (str(PurePosixPath(path).parent), _normalized_stem(path)), [],
        ).append(row)
    priority = {value: index for index, value in enumerate(FORMAT_PRIORITY)}
    for group in groups.values():
        group.sort(key=lambda row: priority.get(row.get('extension'), 99))
        for duplicate in group[1:]:
            duplicate['duplicate_of'] = group[0].get('source_key')
    return rows


def _grab(text, pattern, flags=re.I):
    match = re.search(pattern, text, flags)
    return match.group(1).strip() if match else ''


def extract_facts(rows):
    cover_text = '\n'.join(
        _text(row) + '\n' + str(row.get('header_text') or '')
        for row in rows
        if row.get('section_key') in {'cover', 'qs'} and not row.get('duplicate_of')
    )
    cover_text += '\n' + '\n'.join(
        row.get('source_name') or '' for row in rows if row.get('section_key') in {'cover', 'qs'}
    )
    meta = {
        'rft_number': (
            _grab(cover_text, r'RFT\s*Number:?\**\s*\**\s*(\d{6,})')
            or _grab(cover_text, r'RFT Number:\s*\|?\s*(\d+)')
        ),
        'rft_title': _grab(cover_text, r'RFT\s*Title:?\**\s*\**\s*(.+)'),
        'our_ref': _grab(cover_text, r'\b(Q-\d{6})\b'),
        'submission_date': _grab(
            cover_text,
            r'(\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{4})',
        ),
        'revision': _grab(cover_text, r'Rev\.?\s*(\d+)'),
        'validity_days': _grab(cover_text, r'valid for .*?\((\d+)\)\s*days'),
        'framework': _grab(cover_text, r'Framework Agreement\s*(\d+)'),
        'client': 'ADNOC Onshore' if re.search(r'ADNOC Onshore', cover_text, re.I) else '',
        'client_contact': _grab(cover_text, r'To:\s*\|\s*([^|\n]+)').split('  ')[0],
        'bid_contact': _grab(cover_text, r'Bid Focal Contact:\s*\|\s*([^|]+)'),
    }
    if not meta['our_ref']:
        match = _grab(cover_text, r'\bQ(\d{6})\b')
        if match:
            meta['our_ref'] = f'Q-{match}'
    if not meta['rft_title']:
        meta['rft_title'] = _grab(cover_text, r'\*\*Subject:.*?\|\s*\*\*([^|*]+)')

    qualifications = []
    for row in rows:
        if row.get('section_key') != 'qs' or row.get('duplicate_of'):
            continue
        kind = 'Technical'
        for block in row.get('blocks') or []:
            text = str(block.get('text') or '')
            if block.get('kind') == 'heading' and 'Commercial' in text:
                kind = 'Commercial'
            elif block.get('kind') == 'heading':
                kind = 'Technical'
            for values in block.get('rows') or []:
                values = [str(value or '') for value in values]
                if len(values) >= 4 and values[1] and not re.match(r'(?i)^ref|rft', values[1]):
                    qualifications.append({
                        'type': kind, 'ref': values[1],
                        'text': values[2], 'justification': values[3],
                    })

    personnel = []
    for row in rows:
        if row.get('section_key') != 'personnel' or row.get('duplicate_of'):
            continue
        for block in row.get('blocks') or []:
            for values in block.get('rows') or []:
                values = [str(value or '') for value in values]
                if values and len(values) >= 5 and values[0].casefold() != 'name':
                    personnel.append(values[:6])

    queries = []
    for row in rows:
        if row.get('section_key') != 'tq' or row.get('duplicate_of'):
            continue
        for sheet, sheet_rows in (row.get('spreadsheets') or {}).items():
            header = next((
                index for index, values in enumerate(sheet_rows)
                if values and str(values[0]).casefold().startswith('sl')
            ), None)
            if header is None:
                continue
            for values in sheet_rows[header + 1:]:
                if values and str(values[0]).strip() and len(values) >= 5:
                    queries.append({
                        'no': str(values[0]), 'ref': str(values[1]),
                        'subject': str(values[2]), 'query': str(values[3]),
                        'response': str(values[4]), 'source': f"{row.get('source_name')}/{sheet}",
                    })

    scopes = []
    for row in rows:
        if row.get('section_key') != 'execution' or row.get('duplicate_of'):
            continue
        for block in row.get('blocks') or []:
            text = str(block.get('text') or '')
            if block.get('kind') == 'heading' and re.match(r'(?i)^scope\s*\d*\s*:', text):
                scopes.append(text)
    return {
        'meta': meta, 'qualifications': qualifications, 'personnel': personnel,
        'tqs': queries, 'scopes': scopes,
    }


def _source_rows(rows, key):
    return [
        row for row in rows
        if row.get('section_key') == key and not row.get('duplicate_of')
    ]


def _blocks(rows, key):
    result = []
    for row in _source_rows(rows, key):
        for block in row.get('blocks') or []:
            if block.get('kind') != 'heading' or block.get('text'):
                if block.get('source_path') is None:
                    block = {**block, 'source_path': row.get('source_path') or row.get('source_name')}
                result.append(block)
    return result


def validate_build(rows, facts, included):
    findings = []
    for row in rows:
        if row.get('extraction_error'):
            findings.append({
                'severity': 'WARN', 'code': 'SOURCE_DETAILS_UNREADABLE',
                'message': (
                    f"{row.get('source_path') or row.get('source_name')}: "
                    f"{row['extraction_error']}"
                ),
            })
    for field, label in (
        ('rft_number', 'RFT number'), ('our_ref', 'Our reference'),
        ('submission_date', 'Submission date'), ('validity_days', 'Offer validity'),
        ('client', 'Client'),
    ):
        if not facts['meta'].get(field):
            findings.append({'severity': 'ERROR', 'code': 'META_MISSING', 'message': f'{label} could not be extracted'})
    for key, title, _, source, required in SECTION_CONFIG:
        if required and source and not _source_rows(rows, source):
            findings.append({
                'severity': 'ERROR', 'code': 'SECTION_NO_SOURCE',
                'message': f"Required section '{title}' has no source files",
            })
        if key not in included:
            findings.append({
                'severity': 'ERROR', 'code': 'SECTION_MISSING',
                'message': f"Section '{title}' was not generated",
            })
    refs = set()
    for row in _source_rows(rows, 'cover') + _source_rows(rows, 'qs'):
        refs.update(re.findall(r'\bQ-?\d{5,6}\b', _text(row), re.I))
    if len({value.replace('Q', 'Q-').replace('--', '-') for value in refs}) > 1:
        findings.append({
            'severity': 'WARN', 'code': 'REF_INCONSISTENT',
            'message': f'Multiple quotation references found: {sorted(refs)}',
        })
    if not facts['qualifications']:
        findings.append({'severity': 'WARN', 'code': 'NO_QUALIFICATIONS', 'message': 'No qualifications extracted from QS'})
    if not facts['personnel']:
        findings.append({'severity': 'WARN', 'code': 'NO_PERSONNEL', 'message': 'No key personnel rows extracted'})
    unclassified = [row.get('source_path') or row.get('source_name') for row in rows
                    if not row.get('section_key') and not row.get('duplicate_of')]
    if unclassified:
        findings.append({
            'severity': 'WARN', 'code': 'UNCLASSIFIED',
            'message': f'{len(unclassified)} files matched no rule, e.g. {unclassified[:3]}',
        })
    return findings


def _paragraph(text, citation='', requirement_id=None, evidence_ids=None, rows=None):
    return {
        'id': 'PAR-' + uuid4().hex[:12],
        'text': str(text or ''),
        'requirement_ids': [requirement_id] if requirement_id else [],
        'evidence_ids': list(evidence_ids or []),
        'citation': citation,
        'rows': rows or [],
    }


def _section(key, title, paragraphs, *, rows=None, data=None):
    return {
        'key': key, 'title': title,
        'paragraphs': paragraphs,
        'items': paragraphs,
        'data': data or [],
        'rows': rows or [],
        'requirement_ids': [],
        'status': 'draft', 'approved': False,
    }


def compose_folder_proposal(quote, matrix):
    sources = scan_sources(matrix.get('all_sources') or [])
    facts = extract_facts(sources)
    meta = facts['meta']
    sections = []

    cover = [
        ['Client', meta.get('client') or quote.client.company_name],
        ['RFT Number', meta.get('rft_number') or '[RFT no.]'],
        ['Our Reference', meta.get('our_ref') or '[ref]'],
        ['Submitted by', COMPANY['name']],
        ['Date / Revision', f"{meta.get('submission_date') or '[date]'} / Rev {meta.get('revision') or '0'}"],
        ['Offer validity', f"{meta.get('validity_days')} days" if meta.get('validity_days') else '[validity]'],
        ['Classification', 'Confidential'],
    ]
    sections.append(_section('cover', 'Cover Page & Document Control', [
        _paragraph(f"TECHNICAL PROPOSAL\n{meta.get('rft_title') or quote.deal.deal_name}"),
    ], rows=cover))
    sections.append(_section('toc', 'Table of Contents', [
        _paragraph('The exported Word document will populate the table of contents on open.'),
    ]))
    letter = (
        f"Thank you for the opportunity to submit our Technical Proposal for "
        f"{meta.get('rft_title') or quote.deal.deal_name}. "
        f"Our offer is valid for {meta.get('validity_days') or '[n]'} days from the technical bid submission date.\n\n"
        f"Yours sincerely,\n{COMPANY['signatory_default']}\n{COMPANY['name']}"
    )
    sections.append(_section('letter', 'Covering Letter', [
        _paragraph(letter, citation=(next(iter(_source_rows(sources, 'cover')), {}) or {}).get('source_path', '')),
    ]))
    qualification_rows = [['Type', 'Reference', 'Qualification', 'Justification']] + [
        [item['type'], item['ref'], item['text'], item['justification']]
        for item in facts['qualifications']
    ]
    sections.append(_section('qs', 'Qualification Statement', [
        _paragraph('No qualifications.' if len(qualification_rows) == 1 else
                   'Qualifications extracted from the submitted Qualification Statement.'),
    ], rows=qualification_rows))

    reference_groups = {
        'experience': 'experience', 'organization': 'organization',
        'project_control': 'project_control', 'hse': 'hse',
    }
    for key, title, mode, source, _ in SECTION_CONFIG:
        if key in {'cover', 'toc', 'letter', 'qs', 'annex'}:
            continue
        if key == 'execution':
            blocks = _blocks(sources, 'execution')
            paragraphs = [
                _paragraph(block.get('text') or '', citation=block.get('source_path') or '')
                for block in blocks
            ] or [_paragraph('[Content to be provided]')]
            sections.append(_section(key, title, paragraphs, data=blocks))
        elif key in {'deliverables', 'subcontractors', 'bv', 'investment'}:
            blocks = _blocks(sources, source)
            paragraphs = [
                _paragraph(block.get('text') or '', citation=block.get('source_path') or '')
                for block in blocks
            ]
            if not paragraphs and key in {'bv', 'investment'}:
                paragraphs = [_paragraph('Not applicable to this tender.')]
            elif not paragraphs:
                paragraphs = [_paragraph('[Content to be provided]')]
            sections.append(_section(key, title, paragraphs, data=blocks))
        elif key in reference_groups:
            files = _source_rows(sources, reference_groups[key])
            paragraphs = [_paragraph(
                f"Supporting document: {row.get('source_name')}",
                citation=row.get('source_path') or row.get('source_name') or '',
            ) for row in files] or [_paragraph('[Content to be provided]')]
            document_rows = [['Document', 'Format', 'Size (MB)', 'Location']] + [
                [row.get('source_name', ''), row.get('extension', '').lstrip('.').upper(),
                 f"{row.get('size_mb', 0):.1f}", row.get('source_path') or '']
                for row in files
            ]
            sections.append(_section(key, title, paragraphs, rows=document_rows))
        elif key == 'personnel':
            personnel_rows = [['Name', 'Age', 'Nationality', 'Qualification', 'Years Exp.', 'HSE Training']] + facts['personnel']
            sections.append(_section(key, title, [
                _paragraph(f"{len(facts['personnel'])} personnel rows extracted."
                           if facts['personnel'] else '[Key personnel list to be provided]'),
            ], rows=personnel_rows))
        elif key == 'tq':
            query_rows = [['No.', 'Reference', 'Subject', 'Client response']] + [
                [item['no'], item['ref'][:60], item['subject'], item['response'][:350]]
                for item in facts['tqs']
            ]
            sections.append(_section(key, title, [
                _paragraph(f"{len(facts['tqs'])} technical queries extracted."
                           if facts['tqs'] else 'No technical queries recorded.'),
            ], rows=query_rows))

    annex_rows = [['#', 'Document', 'Folder section', 'Format', 'Duplicate of']] + [
        [
            str(index), row.get('source_name', ''), row.get('section_key') or 'Unclassified',
            row.get('extension', '').lstrip('.').upper(), row.get('duplicate_of') or '',
        ]
        for index, row in enumerate(sources, 1)
    ]
    sections.append(_section('annex', 'Annex Index', [
        _paragraph(f'{len(sources)} selected files catalogued. PDFs and CVs are listed as annexes.'),
    ], rows=annex_rows))

    included = [section['key'] for section in sections]
    findings = validate_build(sources, facts, included)
    registry = matrix.get('document_registry') or []
    return {
        'title': f"Technical Proposal - {meta.get('rft_title') or quote.deal.deal_name}",
        'template_version': 'folder_pipeline_v1',
        'client_name': meta.get('client') or quote.client.company_name,
        'opportunity_title': quote.deal.deal_name,
        'opportunity_reference': meta.get('rft_number') or quote.deal.deal_code,
        'proposal_number': quote.quote_number,
        'revision': quote.version,
        'issue_date': str(quote.issue_date or ''),
        'analysis_status': 'ready',
        'source_snapshot': str(matrix.get('snapshot') or ''),
        'selected_source_keys': list(matrix.get('selected_source_keys') or []),
        'requirement_coverage': matrix.get('coverage') or {},
        'requirement_matrix': matrix.get('requirements') or [],
        'normalized_requirement_matrix': [],
        'requirement_normalization': {},
        'compliance_matrix': [],
        'traceability_matrix': [],
        'evidence_registry': {},
        'publication_validation': {
            'ready': False,
            'blockers': [f"{item['severity']}:{item['code']}" for item in findings
                         if item['severity'] == 'ERROR'] or ['human_review_required'],
        },
        'sections': sections,
        'document_registry': registry,
        'amendments': matrix.get('amendments') or [],
        'requirements_included': len(matrix.get('requirements') or []),
        'requirements_truncated': bool((matrix.get('coverage') or {}).get('records_truncated')),
        'build_meta': meta,
        'build_findings': findings,
        'build_inventory': [{
            'file': row.get('source_path') or row.get('source_name'),
            'section': row.get('section_key'),
            'size_mb': row.get('size_mb', 0),
            'duplicate_of': row.get('duplicate_of'),
        } for row in sources],
    }
