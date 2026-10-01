"""Bounded passive text extraction and evidence-based document-type suggestions.

Technical extraction budgets do not restrict uploads or original downloads. No
macros, formulas, links, external entities, images or embedded objects are run.
"""
import re
from pathlib import PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZipFile
from xml.etree import ElementTree

SOURCE_BYTES = 32 * 1024 * 1024
XML_BYTES = 4 * 1024 * 1024
EXPANDED_BYTES = 16 * 1024 * 1024
TEXT_CHARS = 20_000
LABELS = {
    'incoming_mail': 'Incoming Mail', 'outgoing_mail': 'Outgoing Mail',
    'correspondence_register': 'Correspondence Register', 'eoi': 'EOI', 'rft': 'RFT',
    'tbs': 'TBs', 'tcs': 'TCs', 'tender_register': 'Tender Register',
    'budgetary_proposal': 'Budgetary Proposal', 'technical_proposal': 'Technical Proposal',
    'commercial_proposal': 'Commercial Proposal', 'bid_review': 'Bid Review',
    'discipline_input': 'Discipline Input', 'kom_presentation': 'KOM Presentation',
    'subcontractor_quote': 'Subcontractor Quote', 'costing_sheet': 'Costing Sheet', 'tq': 'TQ',
    'submitted_proposal': 'Submitted Proposal', 'submission_receipt': 'Submission Receipt',
    'loa': 'LOA', 'contract': 'Contract', 'pbg': 'PBG', 'insurance': 'Insurance',
    'sales_delivery_handover': 'Sales-to-Delivery Handover',
    'delivery_sales_handover': 'Delivery-to-Sales Handover',
    'completion_certificate': 'Completion Certificate', 'unclassified': 'Unclassified',
}
COLORS = {**dict.fromkeys(list(LABELS)[:3], 'blue'), **dict.fromkeys(list(LABELS)[3:8], 'amber'),
          **dict.fromkeys(list(LABELS)[8:12], 'violet'), **dict.fromkeys(list(LABELS)[12:17], 'slate'),
          **dict.fromkeys(list(LABELS)[17:19], 'cyan'), **dict.fromkeys(list(LABELS)[19:26], 'emerald'),
          'unclassified': 'slate'}
PATTERNS = {
    'incoming_mail': r'incoming\s+(?:mail|email|correspondence)',
    'outgoing_mail': r'outgoing\s+(?:mail|email|correspondence)',
    'correspondence_register': r'correspondence\s+register',
    'eoi': r'EOI|expression\s+of\s+interest', 'rft': r'RFT|request\s+for\s+tender',
    'tbs': r'TBs?|tender\s+bulletins?', 'tcs': r'TCs?|tender\s+clarifications?',
    'tender_register': r'tender\s+register',
    'budgetary_proposal': r'budgetary\s+(?:proposal|offer)',
    'technical_proposal': r'technical\s+proposal', 'commercial_proposal': r'commercial\s+proposal',
    'bid_review': r'bid\s+review', 'discipline_input': r'discipline\s+inputs?',
    'kom_presentation': r'(?:KOM|kick\s*off\s+meeting)\s+presentation',
    'subcontractor_quote': r'subcontractor\s+(?:quote|quotation)',
    'costing_sheet': r'costing\s+sheet', 'tq': r'TQs?|technical\s+quer(?:y|ies)',
    'submitted_proposal': r'submitted\s+proposal',
    'submission_receipt': r'submission\s+(?:receipt|confirmation|acknowledg(?:e)?ment)',
    'loa': r'LOA|letter\s+of\s+award', 'contract': r'(?:executed\s+|signed\s+)?contract(?:\s+agreement)?',
    'pbg': r'PBG|performance\s+bank\s+guarantee', 'insurance': r'insurance\s+(?:policy|certificate)|insurance',
    'sales_delivery_handover': r'sales\s+to\s+delivery\s+handover',
    'delivery_sales_handover': r'delivery\s+to\s+sales\s+handover',
    'completion_certificate': r'(?:project\s+)?completion\s+certificate',
}


def type_catalog():
    return [{'value': value, 'label': label, 'color': COLORS[value]} for value, label in LABELS.items()]


def rule_suggestion(name, text):
    """Filename or the leading content title can classify; conflicting titles cannot."""
    candidates = {}
    for source, value in (('filename', PurePosixPath(name).stem.replace('_', ' ').replace('-', ' ')),
                          ('content', '\n'.join(text.splitlines()[:8])[:1000])):
        if re.search(r'\b(?:technical\s+(?:and|&)\s+commercial|commercial\s+(?:and|&)\s+technical)\s+proposal\b', value, re.I):
            return 'unclassified', []
        for key, pattern in PATTERNS.items():
            match = re.search(r'\b(?:' + pattern + r')\b', value, re.I)
            if match:
                candidates.setdefault(key, []).append({'source': source, 'rule': key,
                                                       'matched_text': match.group(0)[:160]})
    if len(candidates) == 1:
        kind, evidence = next(iter(candidates.items()))
        return kind, evidence[:3]
    return 'unclassified', []


class ContentUnavailable(Exception):
    def __init__(self, code):
        self.code = code


def _xml_text(source, suffix):
    total, count, pieces = 0, 0, []
    with ZipFile(source) as archive:
        entries = archive.infolist()
        if len(entries) > 4096 or len({row.filename for row in entries}) != len(entries):
            raise ContentUnavailable('archive_limit')
        for row in entries:
            if row.flag_bits & 1 or row.file_size > EXPANDED_BYTES:
                raise ContentUnavailable('archive_limit')
            total += row.file_size
            if total > EXPANDED_BYTES:
                raise ContentUnavailable('archive_limit')
        if suffix == '.docx':
            names = ['word/document.xml']
        elif suffix == '.pptx':
            names = sorted(row.filename for row in entries if re.fullmatch(r'ppt/slides/slide\d+\.xml', row.filename))[:50]
        else:
            names = ['xl/sharedStrings.xml'] + sorted(row.filename for row in entries
                     if re.fullmatch(r'xl/worksheets/sheet\d+\.xml', row.filename))[:10]
        for name in names:
            if name not in archive.namelist():
                continue
            if archive.getinfo(name).file_size > XML_BYTES:
                raise ContentUnavailable('archive_limit')
            with archive.open(name) as member:
                data = member.read(XML_BYTES + 1)
            if len(data) > XML_BYTES or b'<!DOCTYPE' in data.upper() or b'<!ENTITY' in data.upper() or b'\x00' in data:
                raise ContentUnavailable('unsafe_xml')
            tree = ElementTree.fromstring(data)
            for element in tree.iter():
                count += 1
                if count > 100_000:
                    raise ContentUnavailable('structure_limit')
                # Text nodes only; no formulas/URLs/XML attributes.
                if element.tag.rsplit('}', 1)[-1] in {'t', 'p'} and element.text and element.text.strip():
                    pieces.append(element.text.strip())
                    if sum(map(len, pieces)) >= TEXT_CHARS:
                        return '\n'.join(pieces)[:TEXT_CHARS]
    return '\n'.join(pieces)[:TEXT_CHARS]


def extract_document_text(source, name, size):
    """Return (bounded text, diagnostic); caller owns verified seekable source."""
    if size > SOURCE_BYTES:
        return '', 'source_too_large'
    position = source.tell()
    try:
        source.seek(0)
        suffix = PurePosixPath(name.lower()).suffix
        if suffix in {'.txt', '.csv', '.log', '.md', '.eml'}:
            data = source.read(TEXT_CHARS * 4 + 4)
            if b'\x00' in data:
                return '', 'unsupported_text_encoding'
            return data.decode('utf-8-sig', errors='strict')[:TEXT_CHARS], ''
        if suffix in {'.docx', '.xlsx', '.pptx'}:
            text = _xml_text(source, suffix)
            return text, '' if text else 'no_readable_text'
        if suffix == '.pdf':
            import fitz
            # PyMuPDF reads from a filesystem path without a complete in-memory copy.
            with TemporaryDirectory(prefix='radai-classify-') as directory:
                from pathlib import Path
                path = Path(directory) / 'source.pdf'
                with path.open('wb') as temporary:
                    while True:
                        chunk = source.read(64 * 1024)
                        if not chunk:
                            break
                        temporary.write(chunk)
                with path.open('rb') as signature:
                    if signature.read(5) != b'%PDF-':
                        return '', 'invalid_document'
                with fitz.open(path) as document:
                    if document.needs_pass or document.is_repaired or document.page_count > 500:
                        return '', 'unsupported_pdf'
                    pieces = []
                    for page in document.pages(0, min(document.page_count, 20)):
                        pieces.append(page.get_text()[:TEXT_CHARS])
                        if sum(map(len, pieces)) >= TEXT_CHARS:
                            break
                    text = '\n'.join(pieces)[:TEXT_CHARS]
                    return text, '' if text.strip() else 'no_readable_text'
        if suffix == '.msg':
            from .document_classification_msg import extract_msg_text
            return extract_msg_text(source, size)
        return '', 'unsupported_format'
    except ContentUnavailable as error:
        return '', error.code
    except (BadZipFile, ElementTree.ParseError, UnicodeError, ValueError, RuntimeError, OSError, KeyError):
        return '', 'invalid_document'
    finally:
        source.seek(position)
