"""
Document parsing service — best-effort text extraction (MODULE 2).

Modular parser interface: each file category / extension routes to a small
extractor function. Every extractor is wrapped so a parsing failure never
crashes the request — it degrades to an empty string + low confidence score,
and the caller (views.py) records parse_status='failed' with the error.
"""
from __future__ import annotations

import csv
import hashlib
import io
import logging
import os

from .extraction_coverage import finish_coverage, new_coverage, record_issue, record_unit

logger = logging.getLogger(__name__)

def _positive_setting(name, default):
    try:
        return max(1, int(os.environ.get(name, str(default))))
    except ValueError:
        return default


# Resource budgets are configurable and every omitted unit is reported.
MAX_EXTRACTED_CHARS = _positive_setting('PLANNING_MAX_EXTRACTED_CHARS', 4_000_000)
MAX_OCR_PAGES = _positive_setting('PLANNING_MAX_OCR_PAGES', 50)


def _pdf_page_needs_ocr(page, text):
    """A scanned body with an electronic footer is not a text-readable page."""
    if not text.strip():
        return True
    try:
        width, height = float(page.width), float(page.height)
        if width <= 0 or height <= 0:
            return False
        images = getattr(page, 'images', []) or []
        large_image = any(
            max(0, min(width, float(item.get('x1', 0))) - max(0, float(item.get('x0', 0))))
            * max(0, min(height, float(item.get('bottom', 0))) - max(0, float(item.get('top', 0))))
            >= width * height * 0.5 for item in images
        )
        if not large_image:
            return False
        if len(text.split()) < 80:
            return True
        # Repeated watermark/footer overlays may have many characters, while
        # none of the scanned agreement's body has a searchable text layer.
        chars = getattr(page, 'chars', []) or []
        body_chars = [item for item in chars if height * 0.18 < float(item.get('top', 0)) < height * 0.82]
        return bool(chars) and len(body_chars) < 40
    except (TypeError, ValueError, AttributeError):
        return False


def _truncate(text: str, coverage=None) -> str:
    # Defensive: strip NUL bytes from any extractor's output — Postgres TEXT
    # columns reject them and would otherwise crash the save() call.
    if '\x00' in text:
        record_issue(coverage, 'null_characters_removed', 'NUL characters were removed because text storage cannot retain them; review the original source.')
        text = text.replace('\x00', '')
    if len(text) > MAX_EXTRACTED_CHARS:
        if coverage is not None:
            coverage['text_truncated'] = True
            record_issue(coverage, 'text_truncated', f'Extraction retained the first {MAX_EXTRACTED_CHARS} characters; the remaining content has not been analyzed.')
        return text[:MAX_EXTRACTED_CHARS] + '\n...[truncated]'
    return text


def _extract_pdf(file_obj, coverage=None) -> str:
    if coverage is not None:
        coverage['unit_type'] = 'page'
    try:
        import pdfplumber
        text_parts = []
        with pdfplumber.open(file_obj) as pdf:
            ocr_count = 0
            for index, page in enumerate(pdf.pages):
                try:
                    text = page.extract_text() or ''
                except Exception:
                    text = ''
                method = 'pdf_text'
                status = 'processed'
                if _pdf_page_needs_ocr(page, text):
                    native_text = text
                    if ocr_count < MAX_OCR_PAGES:
                        ocr_count += 1
                        ocr_text = _ocr_pdf_page(file_obj, index)
                        method = 'tesseract_ocr'
                        if ocr_text:
                            text = f'--- OCR Page: {index + 1} ---\n{ocr_text}'
                            if native_text.strip() and native_text.strip() not in ocr_text:
                                text += f'\n--- Embedded text ---\n{native_text}'
                        else:
                            text = native_text
                            status = 'failed' if native_text.strip() else 'empty'
                            record_issue(coverage, 'page_without_text', 'The scanned page body could not be read; any embedded footer text is incomplete evidence.', page=index + 1)
                    else:
                        status = 'skipped'
                        record_issue(coverage, 'ocr_page_limit', 'This page requires OCR but the OCR page budget was exhausted.', page=index + 1)
                record_unit(coverage, {'page': index + 1}, text, status=status, method=method)
                text_parts.append(text)
        return '\f'.join(text_parts)
    except Exception as exc:  # noqa: BLE001
        logger.info('pdfplumber failed (%s); falling back to PyPDF2', exc)
        if coverage is not None:
            coverage.update(new_coverage('page'))

    try:
        file_obj.seek(0)
        from PyPDF2 import PdfReader
        reader = PdfReader(file_obj)
        text_parts = []
        for index, page in enumerate(reader.pages):
            text = page.extract_text() or ''
            record_unit(coverage, {'page': index + 1}, text, method='pypdf_text')
            text_parts.append(text)
        joined = '\f'.join(text_parts)
        if joined.strip():
            record_issue(coverage, 'pdf_fallback', 'PDF fallback text extraction was used; page structure requires review.')
            return joined
    except Exception as exc:  # noqa: BLE001
        logger.warning('PyPDF2 fallback also failed: %s', exc)
    # Discard empty fallback units before recording OCR coverage of those pages.
    if coverage is not None:
        coverage.update(new_coverage('page'))
    return _extract_pdf_ocr(file_obj, coverage)


def _remove_ocr_table_rules(image):
    """Remove only long straight rules that make Tesseract omit contract cells."""
    try:
        import numpy as np
        from PIL import Image
        pixels = np.array(image.convert('L'))
        dark = pixels < 160
        mask = np.zeros_like(dark)
        for transpose in (False, True):
            rows = dark.T if transpose else dark
            target = mask.T if transpose else mask
            minimum = max(100, int(rows.shape[1] * 0.18))
            for index, row in enumerate(rows):
                transitions = np.diff(np.pad(row.astype(np.int8), (1, 1)))
                for start, end in zip(np.where(transitions == 1)[0], np.where(transitions == -1)[0]):
                    if end - start >= minimum:
                        target[index, max(0, start - 1):end + 1] = True
        pixels[mask] = 255
        return Image.fromarray(pixels)
    except ImportError:
        return image


def _ocr_pdf_page(file_obj, index):
    try:
        import fitz
        import pytesseract
        from PIL import Image
        file_obj.seek(0)
        with fitz.open(stream=file_obj.read(), filetype='pdf') as document:
            pixmap = document[index].get_pixmap(matrix=fitz.Matrix(300 / 72, 300 / 72), alpha=False)
            with Image.open(io.BytesIO(pixmap.tobytes('png'))) as image:
                prepared = _remove_ocr_table_rules(image)
                try:
                    return pytesseract.image_to_string(prepared, lang='eng', config='--oem 3 --psm 6', timeout=45).strip()
                finally:
                    if prepared is not image:
                        prepared.close()
    except Exception as exc:
        logger.warning('PDF page OCR failed: %s', exc)
        return ''


def _extract_pdf_ocr(file_obj, coverage=None) -> str:
    """Bounded optional OCR fallback for image-only/scanned PDF references."""
    try:
        import fitz
        file_obj.seek(0)
        pages = []
        with fitz.open(stream=file_obj.read(), filetype='pdf') as document:
            count = len(document)
        for index in range(count):
            if index >= MAX_OCR_PAGES:
                record_unit(coverage, {'page': index + 1}, status='skipped')
                record_issue(coverage, 'ocr_page_limit', 'This page was not processed because the OCR page budget was exhausted.', page=index + 1)
                pages.append('')
                continue
            text = _ocr_pdf_page(file_obj, index)
            record_unit(coverage, {'page': index + 1}, text, method='tesseract_ocr')
            pages.append(f'--- OCR Page: {index + 1} ---\n{text}' if text else '')
        return '\f'.join(pages)
    except Exception as exc:  # noqa: BLE001
        logger.warning('Scanned PDF OCR fallback unavailable or failed: %s', exc)
        record_issue(coverage, 'pdf_extraction_failed', 'PDF text and OCR extraction were unavailable or failed.')
        return ''


def _extract_xlsx(file_obj, coverage=None) -> str:
    try:
        import openpyxl
        wb = openpyxl.load_workbook(file_obj, data_only=True, read_only=True)
        if coverage is not None:
            coverage['unit_type'] = 'sheet'
        output = io.StringIO()
        writer = csv.writer(output, delimiter='|', lineterminator='\n')
        try:
            for ws in wb.worksheets:
                start = output.tell()
                row_count = 0
                writer.writerow([f'--- Sheet: {ws.title} ---'])
                for row in ws.iter_rows(values_only=True):
                    row_count += 1
                    if any(cell is not None for cell in row):
                        # Empty document-number/revision cells still occupy a
                        # column. CSV quoting also preserves literal pipes,
                        # quotes and newlines within a deliverable title.
                        writer.writerow(['' if cell is None else str(cell) for cell in row])
                record_unit(coverage, {'sheet': ws.title, 'rows_scanned': row_count, 'hidden': ws.sheet_state != 'visible'}, output.getvalue()[start:], method='openpyxl')
            record_issue(coverage, 'spreadsheet_noncell_content_unverified', 'Cell values from every worksheet were read. Formula expressions, uncached formula results, comments, drawings and embedded objects require separate review.')
        finally:
            wb.close()
        return output.getvalue().rstrip('\n')
    except Exception as exc:  # noqa: BLE001
        logger.warning('openpyxl extraction failed: %s', exc)
        record_issue(coverage, 'workbook_extraction_failed', 'The workbook could not be read; its contents have not been analyzed.')
        return ''


def _extract_csv(file_obj, coverage=None) -> str:
    try:
        raw = file_obj.read()
        text = _decode_text(raw, coverage)
        try:
            dialect = csv.Sniffer().sniff(text[:8192], delimiters=',;\t|')
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(io.StringIO(text), dialect)
        output = io.StringIO()
        writer = csv.writer(output, delimiter='|', lineterminator='\n')
        row_count = 0
        for row in reader:
            writer.writerow(row)
            row_count += 1
        result = output.getvalue().rstrip('\n')
        record_unit(coverage, {'rows_scanned': row_count}, result, method='csv')
        return result
    except Exception as exc:  # noqa: BLE001
        logger.warning('csv extraction failed: %s', exc)
        record_issue(coverage, 'csv_extraction_failed', 'Delimited text could not be parsed.')
        return ''


def _extract_docx(file_obj, coverage=None) -> str:
    try:
        import docx
        document = docx.Document(file_obj)
        from docx.oxml.text.paragraph import CT_P
        from docx.oxml.table import CT_Tbl
        from docx.text.paragraph import Paragraph
        from docx.table import Table
        lines = []
        table_index = 0
        paragraph_index = 0
        if coverage is not None:
            coverage['unit_type'] = 'block'
        for block in document.element.body.iterchildren():
            if isinstance(block, CT_P):
                paragraph_index += 1
                text = Paragraph(block, document).text
                if text.strip():
                    lines.append(text)
                    record_unit(coverage, {'paragraph': paragraph_index}, text, method='docx')
            elif isinstance(block, CT_Tbl):
                table_index += 1
                table = Table(block, document)
                output = io.StringIO()
                writer = csv.writer(output, delimiter='|', lineterminator='\n')
                writer.writerow([f'--- Table: {table_index} ---'])
                for row in table.rows:
                    writer.writerow([cell.text.strip() for cell in row.cells])
                text = output.getvalue().rstrip('\n')
                lines.append(text)
                record_unit(coverage, {'table': table_index}, text, method='docx')
        record_issue(coverage, 'docx_nonbody_content_unverified', 'Body paragraphs and tables were read in source order. Headers, footers, notes, tracked changes, drawings and embedded objects require separate review.')
        return '\n'.join(lines)
    except Exception as exc:  # noqa: BLE001
        logger.warning('docx extraction failed: %s', exc)
        record_issue(coverage, 'docx_extraction_failed', 'The Word document could not be read.')
        return ''


def _decode_text(raw, coverage=None):
    if not isinstance(raw, bytes):
        return str(raw)
    encoding = 'utf-16' if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else 'utf-8-sig'
    try:
        return raw.decode(encoding)
    except UnicodeDecodeError:
        record_issue(coverage, 'text_decode_replacement', 'Some characters could not be decoded and require source review.')
        return raw.decode(encoding, errors='replace')


def _extract_plain_text(file_obj, coverage=None) -> str:
    try:
        raw = file_obj.read()
        text = _decode_text(raw, coverage)
        record_unit(coverage, {'lines_scanned': len(text.splitlines())}, text, method='text')
        return text
    except Exception as exc:  # noqa: BLE001
        logger.warning('plain text extraction failed: %s', exc)
        return ''


def _extract_image_ocr(file_obj, coverage=None) -> str:
    try:
        import pytesseract
        from PIL import Image, ImageSequence
        file_obj.seek(0)
        if coverage is not None:
            coverage['unit_type'] = 'page'
        pages = []
        with Image.open(file_obj) as document:
            for index, frame in enumerate(ImageSequence.Iterator(document)):
                if index >= MAX_OCR_PAGES:
                    record_unit(coverage, {'page': index + 1}, status='skipped')
                    record_issue(coverage, 'ocr_page_limit', 'This image page was not processed because the OCR page budget was exhausted.', page=index + 1)
                    pages.append('')
                    continue
                text = pytesseract.image_to_string(frame, lang='eng', config='--oem 3 --psm 6').strip()
                record_unit(coverage, {'page': index + 1}, text, method='tesseract_ocr')
                pages.append(f'--- OCR Page: {index + 1} ---\n{text}' if text else '')
        record_issue(coverage, 'image_semantics_unverified', 'OCR reads visible text; graphical relationships and image details require separate review.')
        return '\f'.join(pages)
    except Exception as exc:  # noqa: BLE001
        logger.warning('Image OCR extraction unavailable or failed: %s', exc)
        record_issue(coverage, 'image_ocr_failed', 'OCR failed or was unavailable. Image text and diagrams have not been completely analyzed.')
        return ''


_EXTRACTORS = {
    'pdf': _extract_pdf,
    'xlsx': _extract_xlsx,
    'xlsm': _extract_xlsx,
    'xls': _extract_xlsx,
    'csv': _extract_csv,
    'tsv': _extract_csv,
    'docx': _extract_docx,
    'txt': _extract_plain_text,
    'md': _extract_plain_text,
    'xer': _extract_plain_text,  # Primavera native export is plain text (tab-delimited)
    'png': _extract_image_ocr,
    'jpg': _extract_image_ocr,
    'jpeg': _extract_image_ocr,
    'tif': _extract_image_ocr,
    'tiff': _extract_image_ocr,
}


def extract_text_with_coverage(file_field, original_filename: str):
    """
    Returns (extracted_text, confidence_score). confidence_score is a coarse
    heuristic (0.0-1.0) based on extracted content length — NOT a real ML
    confidence — kept intentionally simple per MVP scope.
    """
    ext = (original_filename.rsplit('.', 1)[-1] if '.' in original_filename else '').lower()
    coverage = new_coverage()
    if ext not in _EXTRACTORS or ext == 'xls':
        coverage['status'] = 'unsupported'
        record_unit(coverage, {'extension': ext}, status='unsupported')
        record_issue(coverage, 'unsupported_format', 'This file format has no supported parser. Convert it to a supported format; no content has been interpreted.')
        return '', 0.0, finish_coverage(coverage, '', '')
    extractor = _EXTRACTORS[ext]

    try:
        file_field.seek(0)
    except Exception:  # noqa: BLE001
        pass

    raw_text = extractor(file_field, coverage) or ''
    # Keep PDF page separators, including empty boundary pages, for provenance.
    text = _truncate(raw_text.strip(' \t\r\n'), coverage)
    finish_coverage(coverage, raw_text, text)
    if ext == 'pdf':
        # A separate bounded adapter retains PDF column placement. The ordinary
        # text remains intact for classification, search and other parsers.
        try:
            from .reference_schedule_geometry import parse_reference_schedule_pdf
            geometry = parse_reference_schedule_pdf(file_field)
            if geometry.get('rows') and geometry.get('status') in {'parsed', 'partial'}:
                geometry['text_sha256'] = hashlib.sha256(text.encode('utf-8')).hexdigest()
                geometry['source_storage_name'] = getattr(file_field, 'name', None)
                coverage['structured_evidence'] = {'reference_schedule_geometry': geometry}
        except Exception as exc:
            logger.info('Optional PDF schedule geometry extraction failed: %s', exc)

    if not text.strip():
        coverage['status'] = 'failed'
        return '', 0.0, coverage
    confidence = min(1.0, 0.3 + len(text) / 20000)
    return text, round(confidence, 2), coverage


def extract_text(file_field, original_filename: str) -> tuple[str, float]:
    """Compatibility API; workers use extract_text_with_coverage to persist gaps."""
    text, confidence, _coverage = extract_text_with_coverage(file_field, original_filename)
    return text, confidence
