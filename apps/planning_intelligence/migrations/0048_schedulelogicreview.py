from django.conf import settings
from django.core.validators import MinValueValidator
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('planning_intelligence', '0047_agreement_workspace'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [migrations.CreateModel(
        name='ScheduleLogicReview',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('fingerprint', models.CharField(max_length=64)),
            ('group_id', models.CharField(max_length=64)),
            ('rationale', models.TextField(max_length=5000)),
            ('capacity_basis', models.TextField(max_length=5000)),
            ('duration_basis', models.TextField(max_length=5000)),
            ('max_parallel_deliverables', models.PositiveIntegerField(validators=[MinValueValidator(1)])),
            ('created_at', models.DateTimeField(auto_now_add=True)),
            ('project', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='logic_reviews', to='planning_intelligence.planningproject')),
            ('reviewed_by', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='schedule_logic_reviews', to=settings.AUTH_USER_MODEL)),
            ('version', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='logic_reviews', to='planning_intelligence.scheduleversion')),
        ],
        options={'ordering': ['-created_at', '-pk'], 'indexes': [models.Index(fields=['project', 'version', 'fingerprint', 'group_id'], name='plan_logic_review_lookup')]},
    )]
