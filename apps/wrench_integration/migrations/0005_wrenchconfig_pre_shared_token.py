from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wrench_integration', '0004_wrench_s3_sync_job'),
    ]

    operations = [
        migrations.AddField(
            model_name='wrenchconfig',
            name='pre_shared_token',
            field=models.TextField(
                blank=True,
                default='',
                help_text=(
                    'Pre-shared Wrench session token (obtained externally from the Wrench team). '
                    'When non-empty, this token is used directly — bypassing the username/password '
                    'login flow. The rolling refresh from each API response keeps it current.'
                ),
            ),
        ),
    ]
