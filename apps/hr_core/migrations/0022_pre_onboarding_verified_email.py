from django.db import migrations, models
from django.db.models import F


def retain_verified_sessions(apps, schema_editor):
    invitation = apps.get_model('hr_core', 'RecruitmentFormInvitation')
    # Previously, pre-onboarding sessions were issued only after successful OTP.
    invitation.objects.using(schema_editor.connection.alias).filter(kind='pre_onboarding').exclude(
        session_hash='').exclude(recipient='').update(verified_email=F('recipient'))


class Migration(migrations.Migration):
    dependencies = [('hr_core', '0021_cv_private_attachments')]
    operations = [
        migrations.AddField(model_name='recruitmentforminvitation', name='verified_email',
            field=models.EmailField(blank=True, default='', max_length=254)),
        migrations.RunPython(retain_verified_sessions, migrations.RunPython.noop),
    ]
