from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone
from django.conf import settings


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UsageLog',
            fields=[
                ('id',               models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('user',             models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='usage_logs', to=settings.AUTH_USER_MODEL)),
                ('user_email',       models.EmailField(blank=True, db_index=True, max_length=254)),
                ('user_full_name',   models.CharField(blank=True, max_length=255)),
                ('discipline_key',   models.CharField(db_index=True, max_length=80)),
                ('discipline_label', models.CharField(max_length=120)),
                ('request_path',     models.CharField(max_length=500)),
                ('request_method',   models.CharField(default='GET', max_length=10)),
                ('response_status',  models.SmallIntegerField(default=200)),
                ('response_time_ms', models.IntegerField(blank=True, null=True)),
                ('success',          models.BooleanField(default=True)),
                ('timestamp',        models.DateTimeField(db_index=True, default=django.utils.timezone.now)),
            ],
            options={
                'db_table':  'usage_log',
                'ordering':  ['-timestamp'],
            },
        ),
        migrations.AddIndex(
            model_name='usagelog',
            index=models.Index(fields=['-timestamp', 'discipline_key'], name='usage_log_ts_disc_idx'),
        ),
        migrations.AddIndex(
            model_name='usagelog',
            index=models.Index(fields=['user_email', '-timestamp'], name='usage_log_email_ts_idx'),
        ),
    ]
