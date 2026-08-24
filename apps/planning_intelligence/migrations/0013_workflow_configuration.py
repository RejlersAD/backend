# Generated for Phase A workflow configuration.
import django.core.validators
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0012_technicalproposal_bid_focal_point_and_more'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='EngineeringDependencyTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('code', models.CharField(max_length=64)), ('name', models.CharField(max_length=160)),
                ('discipline', models.CharField(default='process', max_length=64)), ('description', models.TextField(blank=True)),
                ('version', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('retired', 'Retired')], db_index=True, default='draft', max_length=12)),
                ('is_system', models.BooleanField(default=False)), ('is_default', models.BooleanField(default=False)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planning_dependency_templates_created', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='engineering_dependency_templates', to='planning_intelligence.planningproject')),
                ('supersedes', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revisions', to='planning_intelligence.engineeringdependencytemplate')),
            ], options={'ordering': ['discipline', 'code', '-version']},
        ),
        migrations.CreateModel(
            name='EngineeringDependencyRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('sequence', models.PositiveIntegerField(default=0)), ('predecessor_code', models.CharField(max_length=80)),
                ('predecessor_name', models.CharField(max_length=255)), ('predecessor_stage_code', models.CharField(default='FINAL_ISSUE', max_length=40)),
                ('successor_code', models.CharField(max_length=80)), ('successor_name', models.CharField(max_length=255)),
                ('successor_stage_code', models.CharField(default='IFR', max_length=40)),
                ('relationship_type', models.CharField(choices=[('FS', 'Finish to Start'), ('SS', 'Start to Start'), ('FF', 'Finish to Finish'), ('SF', 'Start to Finish')], default='FS', max_length=2)),
                ('lag_days', models.DecimalField(decimal_places=2, default=0, max_digits=8, validators=[django.core.validators.MinValueValidator(-365), django.core.validators.MaxValueValidator(365)])),
                ('rationale', models.TextField(blank=True)), ('source_reference', models.CharField(blank=True, max_length=255)),
                ('requires_confirmation', models.BooleanField(default=True)),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rules', to='planning_intelligence.engineeringdependencytemplate')),
            ], options={'ordering': ['sequence', 'predecessor_code', 'successor_code']},
        ),
        migrations.CreateModel(
            name='WorkflowTemplate',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('code', models.CharField(max_length=64)), ('name', models.CharField(max_length=160)),
                ('description', models.TextField(blank=True)), ('version', models.PositiveIntegerField(default=1)),
                ('status', models.CharField(choices=[('draft', 'Draft'), ('active', 'Active'), ('retired', 'Retired')], db_index=True, default='draft', max_length=12)),
                ('is_system', models.BooleanField(default=False)), ('is_default', models.BooleanField(default=False)),
                ('created_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planning_workflow_templates_created', to=settings.AUTH_USER_MODEL)),
                ('project', models.ForeignKey(blank=True, help_text='Null for a protected corporate/system template.', null=True, on_delete=django.db.models.deletion.CASCADE, related_name='workflow_templates', to='planning_intelligence.planningproject')),
                ('supersedes', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='revisions', to='planning_intelligence.workflowtemplate')),
            ], options={'ordering': ['code', '-version']},
        ),
        migrations.CreateModel(
            name='WorkflowStage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('sequence', models.PositiveSmallIntegerField(validators=[django.core.validators.MinValueValidator(1)])),
                ('code', models.CharField(max_length=40)), ('name', models.CharField(max_length=120)),
                ('activity_name_template', models.CharField(default='{deliverable} - {stage}', help_text='Supports {deliverable}, {stage}, and {discipline}.', max_length=255)),
                ('duration_days', models.DecimalField(decimal_places=2, default=1, max_digits=8, validators=[django.core.validators.MinValueValidator(0)])),
                ('responsible_party', models.CharField(blank=True, max_length=120)),
                ('activity_type', models.CharField(choices=[('task', 'Task'), ('start_milestone', 'Start Milestone'), ('finish_milestone', 'Finish Milestone'), ('level_of_effort', 'Level of Effort')], default='task', max_length=20)),
                ('relationship_to_previous', models.CharField(blank=True, choices=[('', 'None'), ('FS', 'Finish to Start'), ('SS', 'Start to Start'), ('FF', 'Finish to Finish'), ('SF', 'Start to Finish')], default='FS', max_length=2)),
                ('lag_days', models.DecimalField(decimal_places=2, default=0, max_digits=8, validators=[django.core.validators.MinValueValidator(-365), django.core.validators.MaxValueValidator(365)])),
                ('progress_weight', models.DecimalField(decimal_places=4, default=0, max_digits=7, validators=[django.core.validators.MinValueValidator(0), django.core.validators.MaxValueValidator(100)])),
                ('is_release_gate', models.BooleanField(default=True)),
                ('template', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stages', to='planning_intelligence.workflowtemplate')),
            ], options={'ordering': ['sequence']},
        ),
        migrations.CreateModel(
            name='ProjectScheduleConfiguration',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('standard_task_count', models.PositiveSmallIntegerField(default=5, validators=[django.core.validators.MinValueValidator(1), django.core.validators.MaxValueValidator(50)])),
                ('configuration_version', models.PositiveIntegerField(default=1)), ('settings', models.JSONField(blank=True, default=dict)),
                ('dependency_template', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='project_configurations', to='planning_intelligence.engineeringdependencytemplate')),
                ('project', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='schedule_configuration', to='planning_intelligence.planningproject')),
                ('updated_by', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='planning_schedule_configurations_updated', to=settings.AUTH_USER_MODEL)),
                ('workflow_template', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='project_configurations', to='planning_intelligence.workflowtemplate')),
            ], options={'ordering': ['project_id']},
        ),
        migrations.CreateModel(
            name='WorkflowTemplateOverride',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)), ('updated_at', models.DateTimeField(auto_now=True)),
                ('is_deleted', models.BooleanField(default=False)), ('deleted_at', models.DateTimeField(blank=True, null=True)),
                ('scope_type', models.CharField(choices=[('discipline', 'Discipline'), ('deliverable', 'Deliverable')], max_length=16)),
                ('scope_key', models.CharField(max_length=255)), ('priority', models.PositiveSmallIntegerField(default=100)),
                ('is_active', models.BooleanField(default=True)),
                ('configuration', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='overrides', to='planning_intelligence.projectscheduleconfiguration')),
                ('workflow_template', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='configuration_overrides', to='planning_intelligence.workflowtemplate')),
            ], options={'ordering': ['priority', 'scope_type', 'scope_key']},
        ),
        migrations.AddConstraint(model_name='engineeringdependencytemplate', constraint=models.UniqueConstraint(fields=('project', 'code', 'version'), name='uniq_project_dependency_template_version')),
        migrations.AddConstraint(model_name='engineeringdependencytemplate', constraint=models.UniqueConstraint(condition=models.Q(('project__isnull', True)), fields=('code', 'version'), name='uniq_system_dependency_template_version')),
        migrations.AddConstraint(model_name='engineeringdependencytemplate', constraint=models.CheckConstraint(check=models.Q(('is_system', False), ('project__isnull', True), _connector='OR'), name='system_dependency_template_has_no_project')),
        migrations.AddConstraint(model_name='engineeringdependencyrule', constraint=models.UniqueConstraint(fields=('template', 'predecessor_code', 'predecessor_stage_code', 'successor_code', 'successor_stage_code', 'relationship_type'), name='uniq_engineering_dependency_gate')),
        migrations.AddConstraint(model_name='engineeringdependencyrule', constraint=models.CheckConstraint(check=models.Q(('predecessor_code', models.F('successor_code')), _negated=True), name='dependency_rule_distinct_deliverables')),
        migrations.AddConstraint(model_name='workflowtemplate', constraint=models.UniqueConstraint(fields=('project', 'code', 'version'), name='uniq_project_workflow_template_version')),
        migrations.AddConstraint(model_name='workflowtemplate', constraint=models.UniqueConstraint(condition=models.Q(('project__isnull', True)), fields=('code', 'version'), name='uniq_system_workflow_template_version')),
        migrations.AddConstraint(model_name='workflowtemplate', constraint=models.CheckConstraint(check=models.Q(('is_system', False), ('project__isnull', True), _connector='OR'), name='system_workflow_template_has_no_project')),
        migrations.AddConstraint(model_name='workflowstage', constraint=models.UniqueConstraint(fields=('template', 'sequence'), name='uniq_workflow_stage_sequence')),
        migrations.AddConstraint(model_name='workflowstage', constraint=models.UniqueConstraint(fields=('template', 'code'), name='uniq_workflow_stage_code')),
        migrations.AddConstraint(model_name='workflowtemplateoverride', constraint=models.UniqueConstraint(fields=('configuration', 'scope_type', 'scope_key'), name='uniq_project_workflow_override_scope')),
    ]
