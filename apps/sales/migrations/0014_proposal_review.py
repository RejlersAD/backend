import uuid

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def preserve_review_evidence(apps, schema_editor):
    if apps.get_model('sales', 'ProposalReviewDocument').objects.using(schema_editor.connection.alias).exists():
        raise RuntimeError('Proposal review evidence exists. Retain its document and feedback identity with a forward fix.')


class Migration(migrations.Migration):
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    ('sales', '0013_private_opportunity_attachments')]
    operations = [
        migrations.CreateModel(
            name='ProposalReviewDocument',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('revision', models.PositiveIntegerField()),
                ('name', models.CharField(max_length=255)),
                ('sha256', models.CharField(max_length=64)),
                ('size', models.PositiveBigIntegerField()),
                ('page_count', models.PositiveIntegerField()),
                ('feedback_version', models.PositiveIntegerField(default=1)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('attachment', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='proposal_reviews', to='sales.opportunityworkspaceupload')),
                ('created_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('quote', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='review_documents', to='sales.quote')),
            ], options={'ordering': ['-revision']},
        ),
        migrations.CreateModel(
            name='ProposalReviewComment',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('body', models.TextField()),
                ('kind', models.CharField(default='comment', max_length=20)),
                ('page_number', models.PositiveIntegerField(null=True)),
                ('anchor', models.JSONField(null=True)),
                ('context', models.CharField(blank=True, max_length=200)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('is_resolved', models.BooleanField(default=False)),
                ('resolved_at', models.DateTimeField(null=True)),
                ('author', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='comments', to='sales.proposalreviewdocument')),
                ('parent', models.ForeignKey(null=True, on_delete=django.db.models.deletion.PROTECT, related_name='replies', to='sales.proposalreviewcomment')),
                ('resolved_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='ProposalReviewCommand',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('request_id', models.UUIDField()),
                ('action', models.CharField(max_length=20)),
                ('payload_hash', models.CharField(max_length=64)),
                ('result', models.JSONField(default=dict)),
                ('outcome', models.CharField(blank=True, max_length=20)),
                ('note', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, to=settings.AUTH_USER_MODEL)),
                ('document', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='commands', to='sales.proposalreviewdocument')),
                ('quote', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='review_commands', to='sales.quote')),
            ],
        ),
        migrations.AddConstraint(model_name='proposalreviewdocument', constraint=models.UniqueConstraint(
            fields=('quote', 'revision'), name='sales_review_document_revision')),
        migrations.AddConstraint(model_name='proposalreviewcommand', constraint=models.UniqueConstraint(
            fields=('quote', 'request_id'), name='sales_review_request_identity')),
        migrations.RunPython(migrations.RunPython.noop, preserve_review_evidence),
    ]
