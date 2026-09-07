from django.db import migrations, models


def activate_pending_vendors(apps, schema_editor):
    Vendor = apps.get_model('procurement', 'Vendor')
    Vendor.objects.filter(status='pending').update(status='active')


class Migration(migrations.Migration):
    dependencies = [
        ('procurement', '0038_vendor_business_profile_fields'),
    ]

    operations = [
        migrations.RunPython(activate_pending_vendors, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='vendor',
            name='status',
            field=models.CharField(
                choices=[
                    ('active', 'Active'),
                    ('inactive', 'Inactive'),
                    ('blacklisted', 'Blacklisted'),
                ],
                default='active',
                max_length=20,
            ),
        ),
    ]
