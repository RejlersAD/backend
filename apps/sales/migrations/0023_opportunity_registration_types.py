from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0022_document_intelligence_confidence_range'),
    ]

    operations = [
        migrations.AlterField(
            model_name='deal',
            name='opportunity_type',
            field=models.CharField(blank=True, choices=[
                ('eio', 'EIO'),
                ('budgetary', 'Budgetary'),
                ('technical', 'Technical'),
                ('commercial', 'Commercial'),
                ('techno_commercial', 'Techno Commerical'),
                ('other', 'Others'),
                ('tender', 'Tender'),
                ('rfq', 'RFQ'),
                ('eoi', 'EOI'),
                ('direct_enquiry', 'Direct enquiry'),
            ], max_length=20),
        ),
    ]
