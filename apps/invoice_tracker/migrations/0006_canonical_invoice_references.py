import django.db.models.deletion
from django.db import migrations, models


def preserve_reviewed_references(apps, schema_editor):
    invoice = apps.get_model('invoice_tracker', 'CustomerInvoice')
    if invoice.objects.using(schema_editor.connection.alias).filter(
        models.Q(canonical_project_id__isnull=False) | models.Q(canonical_client_id__isnull=False)
        | ~models.Q(canonical_identity_basis={}),
    ).exists():
        raise RuntimeError('Reviewed invoice references exist. Roll back application code while retaining this schema and evidence.')


class Migration(migrations.Migration):
    dependencies = [
        ('invoice_tracker', '0005_invoiceduplicateresolution'),
        ('core', '0014_shared_record_identity'),
        ('sales', '0013_private_opportunity_attachments'),
    ]

    operations = [
        migrations.AddField(
            model_name='customerinvoice', name='canonical_project',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='customer_invoice_references', to='core.project'),
        ),
        migrations.AddField(
            model_name='customerinvoice', name='canonical_client',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT,
                                    related_name='customer_invoice_references', to='sales.client'),
        ),
        migrations.AddField(
            model_name='customerinvoice', name='canonical_identity_basis',
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.RunPython(migrations.RunPython.noop, preserve_reviewed_references),
    ]
