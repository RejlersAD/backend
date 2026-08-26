from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


AUTHORITY_RULES = [
    ('contract_dates', 'schedule_requirements', 100, 'Contract schedule requirements govern contractual dates.'),
    ('contract_dates', 'timeline', 90, 'Approved milestone and timeline files govern dates when no schedule requirement exists.'),
    ('contract_dates', 'reference_schedule', 80, 'Reference schedules provide secondary date evidence.'),
    ('contract_dates', 'sow', 70, 'The scope may state duration or phase dates.'),
    ('scope', 'sow', 100, 'The Scope of Work governs obligations and exclusions.'),
    ('scope', 'mdr', 90, 'The MDR is supporting evidence for documented scope.'),
    ('scope', 'eddr', 90, 'The EDDR is supporting evidence for documented scope.'),
    ('deliverables', 'mdr', 100, 'The MDR governs deliverable identity, number and revision.'),
    ('deliverables', 'eddr', 100, 'The EDDR governs deliverable identity, number and revision.'),
    ('deliverables', 'sow', 80, 'The SOW governs required outputs missing from registers.'),
    ('technical_logic', 'reference_schedule', 100, 'An approved reference schedule is primary logic evidence.'),
    ('technical_logic', 'wbs', 90, 'The WBS provides execution hierarchy and sequencing evidence.'),
    ('technical_logic', 'sow', 80, 'The SOW provides methodology and technical order.'),
    ('review_cycle', 'project_control_procedure', 100, 'The project-control procedure governs review cycles.'),
    ('review_cycle', 'schedule_requirements', 95, 'Schedule requirements provide contractual review rules.'),
    ('review_cycle', 'mdr', 70, 'The MDR may provide document-specific issue stages.'),
    ('calendar', 'schedule_requirements', 100, 'Schedule requirements govern the planning calendar.'),
    ('calendar', 'project_control_procedure', 90, 'The project-control procedure provides secondary calendar rules.'),
    ('calendar', 'reference_schedule', 80, 'Reference schedules provide fallback calendar evidence.'),
]


def seed_authority_rules(apps, schema_editor):
    Rule = apps.get_model('planning_intelligence', 'DocumentAuthorityRule')
    Rule.objects.bulk_create([
        Rule(information_type=info, document_category=category, priority=priority, rationale=rationale)
        for info, category, priority, rationale in AUTHORITY_RULES
    ], ignore_conflicts=True)


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('planning_intelligence', '0019_reconcile_schedule_schema'),
    ]

    operations = [
        migrations.CreateModel(
            name='DocumentAuthorityRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('information_type', models.CharField(choices=[('contract_dates', 'Contract Dates'), ('scope', 'Scope'), ('deliverables', 'Deliverables'), ('technical_logic', 'Technical Logic'), ('review_cycle', 'Review Cycle'), ('calendar', 'Calendar')], max_length=32)),
                ('document_category', models.CharField(max_length=40)), ('priority', models.PositiveSmallIntegerField(default=50)),
                ('rationale', models.CharField(blank=True, max_length=500)), ('is_system', models.BooleanField(default=True)),
            ],
            options={'ordering': ['information_type', '-priority', 'document_category']},
        ),
        migrations.AddConstraint(model_name='documentauthorityrule', constraint=models.UniqueConstraint(fields=('information_type', 'document_category'), name='uniq_document_authority_information_category')),
        migrations.CreateModel(
            name='ScheduleBasis',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('version', models.PositiveIntegerField(default=1)), ('status', models.CharField(choices=[('draft', 'Draft'), ('ready', 'Ready for Approval'), ('approved', 'Approved'), ('superseded', 'Superseded')], db_index=True, default='draft', max_length=16)),
                ('project_name', models.CharField(blank=True, max_length=255)), ('client', models.CharField(blank=True, max_length=255)), ('location', models.CharField(blank=True, max_length=255)),
                ('effective_date', models.DateField(blank=True, null=True)), ('contractual_finish', models.DateField(blank=True, null=True)),
                ('duration_months', models.DecimalField(blank=True, decimal_places=4, max_digits=8, null=True)),
                ('calendar', models.JSONField(blank=True, default=dict)), ('authority_snapshot', models.JSONField(blank=True, default=dict)), ('readiness', models.JSONField(blank=True, default=dict)),
                ('approved_at', models.DateTimeField(blank=True, null=True)),
                ('approved_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='schedule_bases_approved', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='schedule_bases', to='planning_intelligence.planningproject')),
                ('source_run', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='schedule_bases', to='planning_intelligence.documentintelligencerun')),
            ],
            options={'ordering': ['-version']},
        ),
        migrations.AddConstraint(model_name='schedulebasis', constraint=models.UniqueConstraint(fields=('project', 'version'), name='uniq_project_schedule_basis_version')),
        migrations.CreateModel(
            name='BasisDeliverable',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('discipline', models.CharField(blank=True, max_length=64)), ('canonical_key', models.CharField(max_length=320)),
                ('canonical_name', models.CharField(max_length=500)), ('original_title', models.CharField(max_length=500)),
                ('document_number', models.CharField(blank=True, max_length=160)), ('document_revision', models.CharField(blank=True, max_length=80)),
                ('status', models.CharField(choices=[('needs_review', 'Needs Review'), ('confirmed', 'Confirmed'), ('excluded', 'Excluded')], db_index=True, default='needs_review', max_length=16)),
                ('confidence', models.FloatField(default=0)), ('source_fact_ids', models.JSONField(blank=True, default=list)),
                ('source_references', models.JSONField(blank=True, default=list)), ('aliases', models.JSONField(blank=True, default=list)),
                ('reviewed_at', models.DateTimeField(blank=True, null=True)),
                ('basis', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='deliverables', to='planning_intelligence.schedulebasis')),
                ('reviewed_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='basis_deliverables_reviewed', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['discipline', 'canonical_name']},
        ),
        migrations.AddConstraint(model_name='basisdeliverable', constraint=models.UniqueConstraint(fields=('basis', 'discipline', 'canonical_key'), name='uniq_basis_canonical_deliverable')),
        migrations.RunPython(seed_authority_rules, reverse_code=migrations.RunPython.noop),
    ]
