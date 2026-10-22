"""Local, bounded CV text/OCR extraction; proposals never overwrite confirmed data."""
import base64
import io
import re
import time
from datetime import date

from .recruitment_form_schema import validate_form


HEADINGS = {
    'education': 'education', 'qualifications': 'education',
    'languages': 'languages', 'fields of competence': 'competence', 'skills': 'competence',
    'technical skills': 'competence', 'experience summary': 'summary',
    'professional summary': 'summary', 'profile': 'summary', 'summary': 'summary',
    'relevant project experience': 'projects', 'project experience': 'projects',
    'employment history': 'employment', 'work experience': 'employment',
    'professional experience': 'employment', 'key achievements': 'achievements',
    'achievements': 'achievements', 'certifications': 'certifications',
    'certifications (if any)': 'certifications',
}
LABELS = {'name': 'name', 'full name': 'name', 'current position': 'current_position',
          'department': 'current_department', 'current department': 'current_department',
          'year of birth': 'birth_year', 'email': 'email', 'personal email': 'email',
          'phone': 'phone', 'mobile': 'phone', 'contact number': 'phone'}


def clean(value):
    value = value.strip(' \t|:\u2022-')
    if not value or re.search(r'\b(?:x{2,}\w*|19xx|20xx|senior xyz)\b', value, re.I):
        return ''
    if re.fullmatch(r'(?:\d+[).]\s*)+', value):
        return ''
    return value


class CVTemplateError(ValueError):
    pass


def parse_cv_text(text):
    """Map explicit labels and section text, without inventing dates or identities."""
    if re.search(r'19\s*XX', text, re.I) and re.search(r'SENIOR\s+XYZ', text, re.I):
        raise CVTemplateError('Unfilled template')
    data = {'candidate': {}, 'education': [], 'languages': [], 'competence': {},
            'summary': {}, 'projects': [], 'employment': [], 'achievements': {}}
    active, field, row, pending = '', '', None, None
    # Table cells and headings on their own line both occur in the RAD template.
    lines = text.replace('\r', '\n').split('\n')
    for raw in lines:
        line = raw.strip().strip('|').strip()
        if not line:
            continue
        key, sep, value = line.partition(':')
        heading = key.strip().lower()
        if heading in HEADINGS:
            active, field, row, pending = HEADINGS[heading], '', None, None
            if not sep or not value.strip():
                continue
            line = value.strip()
            key, sep, value = line.partition(':')
            heading = key.strip().lower()
        if heading in LABELS and not active:
            field = LABELS[heading]
            if value.strip():
                candidate_value = clean(value)
                if field != 'birth_year' or (candidate_value.isdigit() and 1900 <= int(candidate_value) <= date.today().year):
                    data['candidate'][field] = candidate_value[:500]
                pending = None
            else:
                pending = field
            continue
        if pending:
            candidate_value = clean(line)
            if pending != 'birth_year' or (candidate_value.isdigit() and 1900 <= int(candidate_value) <= date.today().year):
                data['candidate'][pending] = candidate_value[:500]
            pending = None
            continue
        if active == 'education':
            parts = [clean(part) for part in line.split('|')]
            if parts[0]:
                data['education'].append(dict(zip(('qualification', 'institution', 'period'), parts[:3])))
        elif active == 'languages':
            parts = [clean(part) for part in re.split(r'\s*\|\s*|\s*:\s*|\s+[\u2013\u2014-]\s+', line, maxsplit=1)]
            if parts[0]:
                data['languages'].append({'language': parts[0], 'proficiency': parts[1] if len(parts) > 1 else ''})
        elif active == 'competence':
            if heading in ('projects', 'technology', 'techniques'):
                field = heading
                line = value
            field = field or 'technology'
            if clean(line):
                data['competence'][field] = (data['competence'].get(field, '') + '\n' + clean(line)).strip()
        elif active in ('projects', 'employment'):
            mapping = {'title': 'title', 'company': 'company', 'company name': 'company',
                       'period': 'period', 'projects': 'projects', 'key responsibilities': 'responsibilities'}
            period = re.match(r'^(?:\w+\s+)?(?:19|20)\d{2}\s*[-\u2013\u2014].*$', line)
            next_field = mapping.get(heading) if sep else None
            if period or row is None or (next_field in ('title', 'period') and row.get(next_field)):
                row = {}
                data[active].append(row)
                field = ''
            if period:
                row['period'], field = clean(line), ''
            elif next_field and (active == 'projects' or next_field in ('title', 'company', 'period')):
                field = next_field
                row[field] = clean(value)
            elif clean(line):
                # Preserve ambiguous source text rather than assigning an invented employer.
                target = field or ('responsibilities' if active == 'projects' else 'title')
                row[target] = (row.get(target, '') + '\n' + clean(line)).strip()
        elif active in ('summary', 'achievements', 'certifications') and clean(line):
            section = 'summary' if active == 'summary' else 'achievements'
            target = 'text' if active == 'summary' else active
            data[section][target] = (data[section].get(target, '') + '\n' + clean(line)).strip()
    # Contact details may be in a header before the first section.
    header = re.split(r'(?im)^\s*(?:education|qualifications)\s*:?\s*$', text)[0]
    email = re.search(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', header)
    if email:
        data['candidate']['email'] = email.group()[:500]
    phone = re.search(r'(?<!\w)(?:\+\d[\d ()-]{7,}\d)(?!\w)', header)
    if phone:
        data['candidate'].setdefault('phone', phone.group()[:500])
    for section in ('education', 'languages', 'projects', 'employment'):
        data[section] = [{key: value[:500 if key not in ('projects', 'responsibilities') else 10000]
                          for key, value in item.items()} for item in data[section] if any(item.values())][:40]
    for section in ('competence', 'summary', 'achievements'):
        data[section] = {key: value[:10000] for key, value in data[section].items()}
    return validate_form('cv', data)


def extract_local(attachment):
    from .recruitment_cv import cv_source
    text, images, _ = cv_source(bytes(attachment.content), attachment.name, include_images=True)
    if images:
        from PIL import Image
        import pytesseract
        started = time.monotonic()
        scanned = []
        for encoded in images:
            remaining = 70 - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError('OCR time limit reached')
            with Image.open(io.BytesIO(base64.b64decode(encoded))) as page:
                scanned.append(pytesseract.image_to_string(page, lang='eng', timeout=min(12, remaining)))
        # Reinsert scanned page text in page order, including mixed text/scanned PDFs.
        import pymupdf
        with pymupdf.open(stream=bytes(attachment.content), filetype='pdf') as document:
            scanned = iter(scanned)
            text = '\n'.join(next(scanned) if len(page.get_text().strip()) < 30 else page.get_text() for page in document)
    if not text.strip() or len(text) > 60000:
        raise ValueError('No readable CV text within limits')
    proposal = parse_cv_text(text)
    if not any(any(row.values()) if isinstance(row, dict) else bool(row) for key, row in proposal.items() if key != 'acknowledgement'):
        raise ValueError('No recognizable CV fields')
    return proposal
