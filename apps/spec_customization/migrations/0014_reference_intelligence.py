"""Phase 2 — Reference Intelligence schema extensions.

Extends MatchingWorkbookSet with file hashes, schema validation, edition/units
metadata and freeze markers; adds ComponentMatchDecision outcome records and
the PaperSpecExtractionJob.reference_workbook_set pin for reference revision
pinning during matching.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):
    dependencies = [
        ('spec_customization', '0013_verify_project_reference_keys'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── MatchingWorkbookSet extensions ──────────────────────────────
        migrations.AddField(
            model_name='matchingworkbookset',
            name='match_file_hash',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='spec_file_hash',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='cat_file_hash',
            field=models.CharField(blank=True, db_index=True, max_length=64),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='schema_valid',
            field=models.BooleanField(default=False, db_index=True),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='schema_errors',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='standard_edition',
            field=models.CharField(blank=True, db_index=True, max_length=64, default=''),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='units',
            field=models.CharField(blank=True, db_index=True, max_length=16, default=''),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='effective_date',
            field=models.DateField(blank=True, null=True, db_index=True),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='review_status',
            field=models.CharField(
                blank=True, db_index=True, max_length=16,
                choices=[('draft', 'Draft'), ('review', 'Under Review'), ('approved', 'Approved')],
                default='draft',
            ),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='frozen_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='matchingworkbookset',
            name='frozen_by',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='frozen_matching_workbook_sets', to=settings.AUTH_USER_MODEL,
            ),
        ),
        # ── PaperSpecExtractionJob reference pin ────────────────────────
        migrations.AddField(
            model_name='paperspecextractionjob',
            name='reference_workbook_set',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='pinned_jobs', to='spec_customization.matchingworkbookset',
            ),
        ),
        # ── ComponentMatchDecision ──────────────────────────────────────
        migrations.CreateModel(
            name='ComponentMatchDecision',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('pdf_component_name', models.CharField(db_index=True, max_length=256)),
                ('nominal_size', models.CharField(blank=True, max_length=32)),
                ('pressure_rating', models.CharField(blank=True, max_length=32)),
                ('material_grade', models.CharField(blank=True, max_length=64)),
                ('sub_type', models.CharField(blank=True, max_length=128)),
                ('end_connection', models.CharField(blank=True, max_length=128)),
                ('state', models.CharField(
                    choices=[('unique_match', 'Unique Match'), ('ambiguous', 'Ambiguous'),
                             ('unmatched', 'Unmatched'), ('ineligible', 'Ineligible'),
                             ('invalid_reference', 'Invalid Reference')],
                    db_index=True, max_length=32,
                )),
                ('matched_commodity_code', models.CharField(blank=True, max_length=128)),
                ('matched_description', models.TextField(blank=True, default='')),
                ('match_score', models.FloatField(default=0.0)),
                ('candidates', models.JSONField(blank=True, default=list)),
                ('discriminators_matched', models.JSONField(blank=True, default=dict)),
                ('review_decision', models.CharField(
                    blank=True, db_index=True, max_length=16,
                    choices=[('approved', 'Approved'), ('rejected', 'Rejected'), ('remapped', 'Remapped')],
                    default=''),
                ),
                ('final_commodity_code', models.CharField(blank=True, db_index=True, max_length=128)),
                ('review_notes', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('workbook_set', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='match_decisions', to='spec_customization.matchingworkbookset',
                )),
                ('job', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='match_decisions', to='spec_customization.paperspecextractionjob',
                )),
                ('reviewed_by', models.ForeignKey(
                    blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='reviewed_match_decisions', to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                'verbose_name': 'Component Match Decision',
                'verbose_name_plural': 'Component Match Decisions',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['job', 'state'], name='s3d_decision_job_state_idx'),
                    models.Index(fields=['workbook_set', 'state'], name='s3d_decision_set_state_idx'),
                    models.Index(fields=['state', '-created_at'], name='s3d_decision_state_date_idx'),
                ],
                'constraints': [
                    models.UniqueConstraint(
                        fields=['job', 'workbook_set', 'pdf_component_name', 'nominal_size',
                                'pressure_rating', 'material_grade'],
                        name='unique_match_decision_per_criteria',
                    ),
                ],
            },
        ),
    ]