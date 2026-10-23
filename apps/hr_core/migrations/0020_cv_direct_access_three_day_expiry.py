from datetime import timedelta

import django.db.models.deletion
from django.db import migrations, models


def cap_existing_expiry(apps, schema_editor):
    for name in ('RecruitmentApplicationLink', 'RecruitmentFormInvitation'):
        model = apps.get_model('hr_core', name)
        for record in model.objects.using(schema_editor.connection.alias).all().iterator():
            deadline = record.created_at + timedelta(days=3)
            if name == 'RecruitmentFormInvitation' and record.application_link_id:
                deadline = min(deadline, record.application_link.expires_at)
            if record.expires_at > deadline:
                model.objects.using(schema_editor.connection.alias).filter(pk=record.pk).update(expires_at=deadline)


class Migration(migrations.Migration):
    dependencies = [('hr_core', '0019_careers_sync_public_applications')]

    operations = [
        migrations.AlterField(
            model_name='recruitmentforminvitation', name='opening',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='hr_core.recruitmentopening'),
        ),
        migrations.RunPython(cap_existing_expiry, migrations.RunPython.noop),
    ]
