from django.db import migrations


def rename_purchase_recommendations_module(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    Module.objects.filter(code='procurement_requisitions').update(
        name='Purchase Recommendations',
        description='Purchase recommendation workflow and approvals',
    )


def restore_purchase_requisitions_module(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    Module.objects.filter(code='procurement_requisitions').update(
        name='Purchase Requisitions',
        description='Purchase recommendations and requisitions',
    )


class Migration(migrations.Migration):
    dependencies = [
        ('rbac', '0046_ensure_default_organization'),
    ]

    operations = [
        migrations.RunPython(
            rename_purchase_recommendations_module,
            restore_purchase_requisitions_module,
        ),
    ]
