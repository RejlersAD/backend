# Generated data migration for default sales letter templates
from django.db import migrations


EOI_SUBJECT = "Expression of Interest - {{ deal_name }} ({{ deal_code }})"
EOI_BODY = """Dear {{ client_contact_name }},

We wish to confirm our interest in participating in the {{ deal_name }} opportunity (reference: {{ client_reference }}).

Based on our review of the opportunity details, our {{ service_line }} capabilities are well-aligned with the project requirements. We are confident in our ability to deliver value through our experienced team and proven track record in {{ industry_type }}.

We look forward to receiving the complete tender/proposal documentation and are available for any clarification meetings.

Yours sincerely,
{{ owner_name }}
{{ owner_title }}
{{ company_name }}"""


REGRET_EXPERTISE_SUBJECT = "Regret - {{ deal_name }} ({{ deal_code }})"
REGRET_EXPERTISE_BODY = """Dear {{ client_contact_name }},

Thank you for inviting us to participate in the {{ deal_name }} opportunity.

After careful review, we regret to inform you that this opportunity falls outside our core area of expertise and specialization. We focus our resources on {{ core_services|default:"our core service lines" }} where we can deliver maximum value to our clients.

We appreciate the opportunity to be considered and wish you success with this project.

Yours sincerely,
{{ owner_name }}"""


REGRET_MANPOWER_SUBJECT = "Regret - {{ deal_name }} ({{ deal_code }})"
REGRET_MANPOWER_BODY = """Dear {{ client_contact_name }},

Thank you for inviting us to participate in the {{ deal_name }} opportunity.

While this opportunity aligns well with our expertise in {{ service_line }}, we regret that we are unable to commit the required resources at this time due to current workload commitments. We would be unable to dedicate the necessary team to meet your project timeline and quality standards.

We value our relationship with {{ client_name }} and hope to collaborate on future opportunities where we can fully commit our capabilities.

Yours sincerely,
{{ owner_name }}"""


def seed_templates(apps, schema_editor):
    SalesLetterTemplate = apps.get_model('sales', 'SalesLetterTemplate')
    
    templates = [
        {
            'letter_type': 'eoi',
            'subject_template': EOI_SUBJECT,
            'body_template': EOI_BODY,
            'is_active': True,
            'version': 1,
        },
        {
            'letter_type': 'regret_expertise',
            'subject_template': REGRET_EXPERTISE_SUBJECT,
            'body_template': REGRET_EXPERTISE_BODY,
            'is_active': True,
            'version': 1,
        },
        {
            'letter_type': 'regret_manpower',
            'subject_template': REGRET_MANPOWER_SUBJECT,
            'body_template': REGRET_MANPOWER_BODY,
            'is_active': True,
            'version': 1,
        },
    ]
    
    for template_data in templates:
        SalesLetterTemplate.objects.get_or_create(
            letter_type=template_data['letter_type'],
            defaults=template_data,
        )


def unseed_templates(apps, schema_editor):
    SalesLetterTemplate = apps.get_model('sales', 'SalesLetterTemplate')
    SalesLetterTemplate.objects.filter(
        letter_type__in=['eoi', 'regret_expertise', 'regret_manpower']
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0024_sales_letter_models'),
    ]

    operations = [
        migrations.RunPython(seed_templates, unseed_templates),
    ]