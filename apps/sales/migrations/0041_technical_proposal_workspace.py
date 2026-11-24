from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0040_tender_content_classification'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OpportunityProposalSourceSelection',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('source_keys', models.JSONField(default=list)),
                ('expected_snapshot', models.CharField(blank=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='proposal_source_selections', to=settings.AUTH_USER_MODEL)),
                ('opportunity', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='proposal_source_selections', to='sales.deal')),
            ],
            options={
                'constraints': [
                    models.UniqueConstraint(fields=('opportunity', 'actor'), name='sales_proposal_source_actor_uq'),
                ],
            },
        ),
        migrations.CreateModel(
            name='QuoteTechnicalProposalVersion',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('version', models.PositiveIntegerField()),
                ('content', models.JSONField()),
                ('content_sha256', models.CharField(max_length=64)),
                ('reason', models.CharField(blank=True, max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('quote', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='technical_proposal_versions', to='sales.quote')),
                ('created_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='technical_proposal_versions_created', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-version'],
                'constraints': [
                    models.UniqueConstraint(fields=('quote', 'version'), name='sales_technical_proposal_version_uq'),
                ],
            },
        ),
    ]
