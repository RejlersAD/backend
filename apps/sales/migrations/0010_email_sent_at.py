from django.db import migrations, models


def refuse_loss_of_sent_evidence(apps, schema_editor):
    intake = apps.get_model('sales', 'SalesEmailIntake')
    if intake.objects.using(schema_editor.connection.alias).filter(sent_at__isnull=False).exists():
        raise RuntimeError(
            'Cannot reverse Sales email sent-time storage while sent evidence exists. '
            'Keep this schema or restore a verified pre-migration backup; '
            'do not discard captured source timestamps.'
        )


class Migration(migrations.Migration):
    dependencies = [('sales', '0009_automatic_mailbox_sync')]

    operations = [
        migrations.AddField(
            model_name='salesemailintake', name='sent_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        # Reverse executes this check before dropping the evidence column.
        migrations.RunPython(migrations.RunPython.noop, refuse_loss_of_sent_evidence),
    ]
