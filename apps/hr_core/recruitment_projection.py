"""Project confirmed form facts for HR; preserve the full immutable submission."""
import re


def _text(value):
    return value.strip() if isinstance(value, str) else ''


def _row_text(row, fields):
    return ' · '.join(_text(row.get(field)) for field in fields if _text(row.get(field)))


def cv_projection(data):
    education = '\n'.join(filter(None, (_row_text(row, ('qualification', 'institution', 'period')) for row in data.get('education', []))))
    experience = _text(data.get('summary', {}).get('text'))
    if not experience:
        experience = '\n'.join(filter(None, (_row_text(row, ('period', 'title', 'company')) for row in data.get('employment', []) + data.get('projects', []))))
    skills = []
    for key in ('projects', 'technology', 'techniques'):
        for skill in re.split(r'[\n,;]+', _text(data.get('competence', {}).get(key))):
            skill = skill.strip()
            if skill and skill.casefold() not in {value.casefold() for value in skills}:
                skills.append(skill)
    # These are compact summaries; full values remain available in submitted forms.
    return {'education': education[:4000], 'experience': experience[:4000], 'skills': ', '.join(skills)[:1000]}


def submitted_source(candidate):
    from .recruitment_cv import attachment_summary
    chosen = {}
    for invitation in candidate.form_invitations.all():
        for submission in invitation.submissions.all():
            previous = chosen.get(invitation.kind)
            if previous is None or (submission.created_at, submission.pk) > (previous.created_at, previous.pk):
                chosen[invitation.kind] = submission
    forms, documents, cv_details, projection, photo = [], [], {}, {}, None
    for kind, submission in chosen.items():
        forms.append({'invitation': submission.invitation_id, 'kind': kind, 'version': submission.version, 'created_at': submission.created_at})
        for attachment in submission.attachments.all():
            metadata = {**attachment_summary(attachment), 'invitation': submission.invitation_id, 'version': submission.version}
            documents.append(metadata)
            if attachment.kind == 'photo':
                photo = metadata
        if kind == 'cv':
            projection = cv_projection(submission.data)
            identity = submission.data.get('candidate', {})
            cv_details = {key: identity.get(key, '') for key in ('current_position', 'current_department', 'birth_year')}
            cv_details['languages'] = submission.data.get('languages', [])
            cv_details.update(submission.data.get('achievements', {}))
    return {'submitted_forms': forms, 'documents': documents, 'photo': photo, 'cv_details': cv_details, 'projection': projection}
