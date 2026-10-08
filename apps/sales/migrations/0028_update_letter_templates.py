# Data migration aligning the seeded letter templates with the standard
# Rejlers correspondence wording (per the company template screenshots).
from django.db import migrations


EOI_SUBJECT = "{{ deal_name }}"
EOI_BODY = """Dear Sir,

Thank you for your interest in Rejlers International Engineering Solutions AB and for giving us this opportunity to provide our services for the subject project. We hereby confirm express our interest to participate in this strategic opportunity.

Rejlers is one of the largest and most rapidly expanding engineering consultancies originating from the Nordics. With our experience of 83+ years in the field of consulting, designing, engineering, operating and maintaining the world's most competitive hydrocarbon technologies, processes, and plants. Our uniqueness comes from our inside-the-fence experience and hands on involvement of innovating, designing, engineering, operating and maintaining the world's most competitive hydrocarbon technologies, processes, and plants.

Our comprehensive experience in executing Concept Engineering Studies, Pre-FEED, FEED, and Detailed Engineering, Project Management Consultancy projects globally ensures we are well-equipped to meet and exceed expectations of the subject study project. Our commitment to quality, safety, and efficiency will contribute significantly to achieving project objectives and delivering successful outcomes.

We look forward to receiving the RFT in due course accordingly. Please do not hesitate to contact the undersigned for any questions or clarifications.

Sincerely yours,"""


REGRET_EXPERTISE_SUBJECT = "{{ deal_name }}"
REGRET_EXPERTISE_BODY = """Dear Sir / Madam,

Thank you for your enquiry and entrusting Rejlers International Engineering Solutions AB to provide the services requested.

However, we regret to inform you that we will not be able to participate in the subject opportunity as the requested services are not currently aligned with our area of expertise.

We kindly request you to keep us on list for other projects and hope that we can offer you our services next time.

We want to express our sincere gratitude for your interest in our services, and we apologize for any inconvenience this may have caused.

Sincerely yours,"""


REGRET_MANPOWER_SUBJECT = "{{ deal_name }}"
REGRET_MANPOWER_BODY = """Dear Sir / Madam,

Thank you for your enquiry and entrusting Rejlers International Engineering Solutions AB to provide the services requested.

However, we regret to inform you that we will not be able to participate in the subject opportunity due to our current manpower availability constraints, despite the requested services being aligned with our area of expertise.

We kindly request you to keep us on list for other projects and hope that we can offer you our services next time.

We want to express our sincere gratitude for your interest in our services, and we apologize for any inconvenience this may have caused.

Sincerely yours,"""


TEMPLATES = {
    'eoi': {'subject_template': EOI_SUBJECT, 'body_template': EOI_BODY},
    'regret_expertise': {'subject_template': REGRET_EXPERTISE_SUBJECT, 'body_template': REGRET_EXPERTISE_BODY},
    'regret_manpower': {'subject_template': REGRET_MANPOWER_SUBJECT, 'body_template': REGRET_MANPOWER_BODY},
}


def update_templates(apps, schema_editor):
    SalesLetterTemplate = apps.get_model('sales', 'SalesLetterTemplate')
    for letter_type, content in TEMPLATES.items():
        template = SalesLetterTemplate.objects.filter(letter_type=letter_type).first()
        if template is None:
            SalesLetterTemplate.objects.create(
                letter_type=letter_type,
                subject_template=content['subject_template'],
                body_template=content['body_template'],
                is_active=True,
                version=1,
            )
            continue
        template.subject_template = content['subject_template']
        template.body_template = content['body_template']
        template.is_active = True
        template.version = (template.version or 1) + 1
        template.save(update_fields=['subject_template', 'body_template', 'is_active', 'version', 'updated_at'])


def noop(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0027_sales_letter_docx'),
    ]

    operations = [
        migrations.RunPython(update_templates, noop),
    ]
