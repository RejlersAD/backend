"""Bounded passive extraction with explicit coverage and page/part citations."""
from email import policy
from email.parser import BytesParser
from pathlib import PurePosixPath
import re

from .document_classification_content import SOURCE_BYTES, _xml_text, extract_document_text
from .email_content import project_email_body

CHUNK_CHARS = 12_000
MAX_TEXT = 2_000_000


def _plain(value):
    # Reuse the inert HTML projection; no links, images or scripts are fetched.
    return project_email_body(value, 'html')['body_text']


def extract_analysis_chunks(stream, name, size):
    """Return ([(location,text)], diagnostic). A diagnostic denotes incomplete reading."""
    if size > SOURCE_BYTES:
        return [], 'source_too_large'
    suffix = PurePosixPath(name.lower()).suffix
    pieces, diagnostic = [], ''
    if suffix == '.pdf':
        import pymupdf
        doc = pymupdf.open(stream=stream.read(SOURCE_BYTES + 1), filetype='pdf')
        try:
            total = 0
            for index, page in enumerate(doc):
                text = page.get_text()
                if not text.strip():
                    diagnostic = 'some_pages_have_no_text'
                if total + len(text) > MAX_TEXT:
                    text = text[:MAX_TEXT - total]
                    diagnostic = 'text_limit'
                if text.strip():
                    pieces.append((f'Page {index + 1}', text))
                total += len(text)
                if total >= MAX_TEXT:
                    break
        finally:
            doc.close()
    elif suffix == '.eml':
        message = BytesParser(policy=policy.default).parsebytes(stream.read(SOURCE_BYTES + 1))
        part = message.get_body(preferencelist=('plain', 'html'))
        body = str(part.get_content()) if part else ''
        if part and part.get_content_type() == 'text/html':
            body = _plain(body)
        headers = '\n'.join(f'{key}: {message[key]}' for key in ('From', 'To', 'Date', 'Subject', 'Message-ID') if message[key])
        text = headers + '\n\n' + body
        pieces = [('Email body', text[:MAX_TEXT])]
        diagnostic = 'text_limit' if len(text) > MAX_TEXT else ''
        if any(part.get_content_disposition() == 'attachment' for part in message.walk()):
            diagnostic = 'embedded_attachments_not_read'
    elif suffix in {'.docx', '.xlsx', '.pptx'}:
        # XML container budgets remain shared with the existing safe extractor.
        text = _xml_text(stream, suffix, text_limit=MAX_TEXT, all_parts=True)
        pieces = [('Document text', text)]
        diagnostic = ''
        if suffix == '.xlsx':
            # Read saved numeric/date/cell values as well as shared strings.
            # No formulas, macros, external workbook links or code are run.
            from openpyxl import load_workbook
            from openpyxl.utils import get_column_letter
            stream.seek(0)
            workbook = load_workbook(stream, read_only=True, data_only=True, keep_links=False)
            pieces, total = [], 0
            diagnostic = 'spreadsheet_cached_values_only'
            try:
                for sheet in workbook.worksheets:
                    lines = []
                    if (sheet.max_row or 0) > 100000 or (sheet.max_column or 0) > 256:
                        diagnostic = 'spreadsheet_structure_budget'
                    for number, row in enumerate(sheet.iter_rows(max_row=min(sheet.max_row or 1, 100000),
                            max_col=min(sheet.max_column or 1, 256), values_only=True), 1):
                        values = [f'{get_column_letter(index)}={value}' for index, value in enumerate(row, 1) if value is not None]
                        if not values:
                            continue
                        line = f'Row {number}: ' + ' | '.join(values)
                        if total + len(line) > MAX_TEXT:
                            line = line[:MAX_TEXT - total]
                            diagnostic = 'text_limit'
                        lines.append(line)
                        total += len(line)
                        if total >= MAX_TEXT:
                            break
                    if lines:
                        pieces.append((f'Sheet {sheet.title}', '\n'.join(lines)))
                    if total >= MAX_TEXT:
                        break
            finally:
                workbook.close()
        # DOCX body only: headers/footers and embedded objects are not read.
        elif suffix == '.docx':
            diagnostic = 'document_body_only'
        if len(text) >= MAX_TEXT:
            diagnostic = 'text_limit'
    elif suffix in {'.txt', '.csv', '.md', '.log', '.html', '.htm'}:
        data = stream.read(SOURCE_BYTES + 1)
        if b'\x00' in data:
            return [], 'unsupported_text_encoding'
        text = data.decode('utf-8-sig', errors='strict')
        if suffix in {'.html', '.htm'}:
            text = _plain(text)
        pieces = [('Document text', text[:MAX_TEXT])]
        diagnostic = 'text_limit' if len(text) > MAX_TEXT else ''
    else:
        text, diagnostic = extract_document_text(stream, name, size)
        pieces = [('Message text', text)] if text else []
        if suffix == '.msg' and text:
            diagnostic = diagnostic or 'msg_body_budget'
    chunks = []
    for location, text in pieces:
        for offset in range(0, len(text), CHUNK_CHARS):
            value = text[offset:offset + CHUNK_CHARS].strip()
            if value:
                chunks.append((f'{location}, part {offset // CHUNK_CHARS + 1}', value))
    return chunks, diagnostic or ('' if chunks else 'no_readable_text')


def document_references(text):
    return sorted(set(re.findall(r'\b(?:Doc\d{5,}|MSG\d{5,}|(?:RFQ|RFP|EOI|ITT|RFT|PO|CONTRACT)[-_/][A-Z0-9][A-Z0-9._/-]{2,})\b', text, re.I)))[:100]
