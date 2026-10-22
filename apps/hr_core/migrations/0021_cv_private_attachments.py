import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('hr_core', '0020_cv_direct_access_three_day_expiry')]

    operations = [
        migrations.CreateModel(
            name='RecruitmentFormAttachment',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('kind', models.CharField(choices=[('photo', 'Professional photo'), ('cv', 'CV')], max_length=10)),
                ('name', models.CharField(max_length=200)),
                ('content_type', models.CharField(max_length=100)),
                ('content', models.BinaryField()),
                ('size', models.PositiveIntegerField()),
                ('sha256', models.CharField(max_length=64)),
                ('extraction', models.JSONField(blank=True, default=dict)),
                ('invitation', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='attachments', to='hr_core.recruitmentforminvitation')),
            ],
            options={'abstract': False, 'ordering': ['-created_at']},
        ),
        migrations.AddField(model_name='recruitmentformsubmission', name='attachments',
            field=models.ManyToManyField(blank=True, to='hr_core.recruitmentformattachment')),
    ]
