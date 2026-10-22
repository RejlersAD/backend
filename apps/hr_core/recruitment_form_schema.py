"""Versioned field mapping for the two supplied RAD Word templates."""
import json
from datetime import date
from django.core.validators import validate_email
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework.exceptions import ValidationError

VERSION = 'rad-2026-10-v2'
DECLARATION = ('Subject to Article 15 of the Federal Law No. 33/2021, the employee should specify in writing the concerned family member/person who would receive the rights in case of death. '
               'I, {candidate_name}, hereby authorize {beneficiary_name}, relationship: {beneficiary_relationship}, to receive my rights in case of my death during my employment at Rejlers International Engineering Solutions - Abu Dhabi.')


def field(key, label, kind='text', required=False):
    return dict(key=key, label=label, type=kind, required=required)


def section(key, title, fields, repeatable=False):
    return dict(key=key, title=title, fields=fields, repeatable=repeatable)


CONTACT = [field('name', 'Full name', required=True), field('address', 'Address', 'textarea'), field('phone', 'Contact number', required=True), field('relationship', 'Relationship', required=True)]
SCHEMAS = {
    'cv': [
        section('candidate', 'Candidate details', [field('name', 'Full name', required=True), field('email', 'Personal email', 'email', True), field('phone', 'Personal contact number', required=True), field('current_position', 'Current position'), field('current_department', 'Current department'), field('birth_year', 'Year of birth (optional)', 'year')]),
        section('education', 'Education', [field('qualification', 'Qualification', required=True), field('institution', 'Institution'), field('period', 'Period')], True),
        section('languages', 'Languages', [field('language', 'Language', required=True), field('proficiency', 'Proficiency')], True),
        section('competence', 'Fields of competence', [field('projects', 'Projects', 'textarea'), field('technology', 'Technology', 'textarea'), field('techniques', 'Techniques', 'textarea')]),
        section('summary', 'Experience summary', [field('text', 'Professional summary', 'textarea')]),
        section('projects', 'Relevant project experience', [field('period', 'Period'), field('title', 'Title'), field('company', 'Company'), field('projects', 'Projects', 'textarea'), field('responsibilities', 'Key responsibilities', 'textarea')], True),
        section('employment', 'Employment history', [field('period', 'Period'), field('title', 'Title'), field('company', 'Company')], True),
        section('achievements', 'Key achievements and certifications', [field('achievements', 'Key achievements', 'textarea'), field('certifications', 'Certifications (if any)', 'textarea')]),
        section('acknowledgement', 'Accuracy acknowledgement', [field('name', 'Typed full name', required=True), field('accurate', 'I confirm that the information provided is accurate to the best of my knowledge.', 'checkbox', True)]),
    ],
    'pre_onboarding': [
        section('identity', 'Identity as per passport', [field('first_name', 'First name (as per passport)', required=True), field('family_name', 'Family name (as per passport)', required=True), field('birth_date', 'Date of birth', 'date', True), field('birth_place', 'Place of birth'), field('nationality', 'Nationality', required=True)]),
        section('personal', 'Personal and family details', [field('father_name', "Father's full name (optional)"), field('mother_name', "Mother's full name (optional)"), field('marital_status', 'Marital status (optional)'), field('religion', 'Religion (optional)')]),
        section('contact', 'Personal contact details', [field('phone', 'Personal contact number(s)', required=True), field('email', 'Personal email address', 'email', True), field('languages', 'Languages known')]),
        section('home', 'Home-country details', [field('address', 'Address (home country)', 'textarea'), field('telephone', 'Telephone (home country)'), field('airport', 'International airport (home country)')]),
        section('relatives_question', 'Relatives in UAE', [field('has_relatives', 'I have relatives in the UAE', 'checkbox')]),
        section('relatives', 'Relatives in UAE - details', CONTACT, True),
        section('emergency', 'Contact person in case of emergency', CONTACT),
        section('dependants', "Employee's dependant(s) details", [field('name', 'Name', required=True), field('relationship', 'Relationship', required=True), field('birthday', 'Birthday', 'date')], True),
        section('beneficiary', 'Beneficiary declaration', [field('candidate_name', 'Candidate full name', required=True), field('beneficiary_name', 'Nominated person', required=True), field('beneficiary_relationship', 'Relationship', required=True), field('typed_name', 'Typed name acknowledgement', required=True), field('date', 'Date', 'date', True), field('accepted', 'I have read and acknowledge the company-template beneficiary declaration.', 'checkbox', True)]),
    ],
}


def schema_for(kind):
    import copy
    sections = copy.deepcopy(SCHEMAS[kind])
    if kind == 'cv':
        candidate = sections[0]
        order = ['name', 'current_position', 'current_department', 'birth_year', 'email', 'phone']
        candidate['fields'].sort(key=lambda item: order.index(item['key']))
        for spec in candidate['fields']:
            if spec['key'] in ('email', 'phone'):
                spec['supplemental'] = True
    return dict(kind=kind, version=VERSION, sections=sections, declaration=DECLARATION if kind == 'pre_onboarding' else '', signature_note='Typed names record acknowledgement; they are not verified digital signatures.')


def display_sections(kind):
    sections = schema_for(kind)['sections']
    if kind != 'cv':
        return sections
    output, contact, acknowledgement = [], [], None
    for item in sections:
        if item['key'] == 'candidate':
            contact = [field for field in item['fields'] if field['key'] in ('email', 'phone')]
            output.append({**item, 'fields': [field for field in item['fields'] if field['key'] not in ('email', 'phone')]})
        elif item['key'] == 'achievements':
            for name, title in [('achievements', 'Key Achievements'), ('certifications', 'Certifications (If Any)')]:
                output.append({**item, 'title': title, 'fields': [field for field in item['fields'] if field['key'] == name]})
        elif item['key'] == 'acknowledgement':
            acknowledgement = item
        else:
            output.append(item)
    output.append({'key': 'candidate', 'title': 'Application contact details', 'repeatable': False, 'fields': contact})
    if acknowledgement:
        output.append(acknowledgement)
    return output


def validate_form(kind, data, final=False):
    if not isinstance(data, dict) or len(json.dumps(data, ensure_ascii=False).encode('utf-8')) > 200000:
        raise ValidationError({'data': 'A form object under 200 KB is required.'})
    schema = SCHEMAS[kind]
    if set(data) - {item['key'] for item in schema}:
        raise ValidationError({'data': 'Unknown form sections.'})
    result, errors = {}, {}
    for item in schema:
        key = item['key']
        value = data.get(key, [] if item['repeatable'] else {})
        relatives_question = data.get('relatives_question', {})
        if key == 'relatives' and not (isinstance(relatives_question, dict) and relatives_question.get('has_relatives', False)):
            value = []
        rows = value if item['repeatable'] else [value]
        if not isinstance(rows, list) or len(rows) > 40:
            errors[key] = 'Use at most 40 rows.'
            continue
        clean_rows = []
        for index, row in enumerate(rows):
            row_key = f'{key}.{index + 1}' if item['repeatable'] else key
            if not isinstance(row, dict) or set(row) - {spec['key'] for spec in item['fields']}:
                errors[row_key] = 'Invalid fields.'
                continue
            cleaned = {}
            for spec in item['fields']:
                name, category = spec['key'], spec['type']
                content = row.get(name, False if category == 'checkbox' else '')
                if category == 'checkbox':
                    if not isinstance(content, bool): errors[f'{row_key}.{name}'] = 'Use true or false.'
                elif not isinstance(content, str) or len(content) > (10000 if category == 'textarea' else 500):
                    errors[f'{row_key}.{name}'] = 'Text is too long or invalid.'
                else:
                    content = content.strip()
                    try:
                        if content and category == 'email': validate_email(content)
                        if content and category == 'date':
                            parsed = date.fromisoformat(content)
                            if name in ('birth_date', 'birthday') and parsed > date.today(): raise ValueError()
                        if content and category == 'year' and (not content.isdigit() or not 1900 <= int(content) <= date.today().year): raise ValueError()
                    except (DjangoValidationError, ValueError):
                        errors[f'{row_key}.{name}'] = f'Enter a valid {category}.'
                if final and spec['required'] and not content:
                    errors[f'{row_key}.{name}'] = 'This field is required.'
                cleaned[name] = content
            clean_rows.append(cleaned)
        result[key] = clean_rows if item['repeatable'] else (clean_rows[0] if clean_rows else {})
    if final and kind == 'pre_onboarding' and result.get('relatives_question', {}).get('has_relatives') and not result.get('relatives'):
        errors['relatives'] = 'Add details for at least one relative.'
    if errors:
        raise ValidationError({'fields': errors})
    return result
