# Generated migration for sales letter models
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0023_opportunity_registration_types'),
    ]

    operations = [
        migrations.CreateModel(
            name='SalesLetterTemplate',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('letter_type', models.CharField(choices=[('eoi', 'Expression of Interest'), ('regret_expertise', 'Regret - Area of Expertise'), ('regret_manpower', 'Regret - Manpower Availability')], max_length=20, unique=True)),
                ('subject_template', models.TextField()),
                ('body_template', models.TextField()),
                ('is_active', models.BooleanField(default=True)),
                ('version', models.PositiveIntegerField(default=1)),
            ],
            options={
                'db_table': 'sales_letter_templates',
                'ordering': ['letter_type'],
            },
        ),
        migrations.CreateModel(
            name='SalesLetter',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('letter_type', models.CharField(choices=[('eoi', 'Expression of Interest'), ('regret_expertise', 'Regret - Area of Expertise'), ('regret_manpower', 'Regret - Manpower Availability')], max_length=20)),
                ('subject', models.CharField(max_length=300)),
                ('body', models.TextField()),
                ('generated_at', models.DateTimeField(auto_now_add=True)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('sent', 'Sent'), ('archived', 'Archived')], default='draft', max_length=20)),
                ('sent_to', models.EmailField(blank=True)),
                ('sent_at', models.DateTimeField(blank=True, null=True)),
                ('custom_data', models.JSONField(blank=True, default=dict)),
                ('generated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sales_letters_generated', to=settings.AUTH_USER_MODEL)),
                ('opportunity', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='letters', to='sales.deal')),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='letters', to='sales.saleslettertemplate')),
            ],
            options={
                'db_table': 'sales_letters',
                'ordering': ['-generated_at'],
            },
        ),
        migrations.AddIndex(
            model_name='salesletter',
            index=models.Index(fields=['opportunity', 'letter_type'], name='sales_letter_opport_1a2b3c_idx'),
        ),
        migrations.AddIndex(
            model_name='salesletter',
            index=models.Index(fields=['status'], name='sales_letter_status_4d5e6f_idx'),
        ),
    ]