from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0004_salesmailboxconnection'),
    ]

    operations = [
        migrations.AddField(
            model_name='salesmailboxconnection',
            name='auth_mode',
            field=models.CharField(
                choices=[
                    ('delegated', 'Connect my Outlook account'),
                    ('application', 'Application / shared mailbox'),
                ],
                db_index=True,
                default='application',
                max_length=20,
            ),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name='salesmailboxconnection',
            name='connected_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='salesmailboxconnection',
            name='delegated_account_id',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='salesmailboxconnection',
            name='delegated_account_name',
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AddField(
            model_name='salesmailboxconnection',
            name='delegated_scopes',
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name='salesmailboxconnection',
            name='encrypted_refresh_token',
            field=models.TextField(blank=True, default=''),
        ),
        migrations.AlterField(
            model_name='salesmailboxconnection',
            name='auth_mode',
            field=models.CharField(
                choices=[
                    ('delegated', 'Connect my Outlook account'),
                    ('application', 'Application / shared mailbox'),
                ],
                db_index=True,
                default='delegated',
                max_length=20,
            ),
        ),
    ]
