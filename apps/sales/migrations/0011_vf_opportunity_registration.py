from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def seed_vf_sequence(apps, schema_editor):
    sequence = apps.get_model('sales', 'OpportunityNumberSequence')
    sequence.objects.using(schema_editor.connection.alias).get_or_create(
        pk=1, defaults={'next_number': 102101},
    )


def refuse_loss_of_registration(apps, schema_editor):
    alias = schema_editor.connection.alias
    sequence = apps.get_model('sales', 'OpportunityNumberSequence')
    deal = apps.get_model('sales', 'Deal')
    registered = models.Q(created_by__isnull=False) | models.Q(open_date__isnull=False)
    registered |= ~models.Q(opportunity_type='')
    incomplete = (
        models.Q(estimated_value__isnull=True) | models.Q(weighted_value__isnull=True)
        | models.Q(expected_close_date__isnull=True) | models.Q(currency='')
    )
    if (
        sequence.objects.using(alias).filter(next_number__gt=102101).exists()
        or deal.objects.using(alias).filter(registered | incomplete).exists()
    ):
        raise RuntimeError(
            'Cannot reverse VF registration while issued numbers, registration '
            'evidence or incomplete commercial records exist. Keep this schema '
            'or restore a verified pre-migration backup; do not recycle VF numbers '
            'or fabricate missing commercial values.'
        )


class Migration(migrations.Migration):
    dependencies = [
        ('sales', '0010_email_sent_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='OpportunityNumberSequence',
            fields=[
                ('id', models.PositiveSmallIntegerField(default=1, editable=False, primary_key=True, serialize=False)),
                ('next_number', models.PositiveBigIntegerField(default=102101)),
            ],
            options={
                'db_table': 'sales_opportunity_number_sequence',
                'constraints': [
                    models.CheckConstraint(check=models.Q(id=1), name='sales_vf_singleton'),
                    models.CheckConstraint(check=models.Q(next_number__gte=102101), name='sales_vf_minimum'),
                ],
            },
        ),
        migrations.AddField(
            model_name='deal', name='opportunity_type',
            field=models.CharField(blank=True, choices=[
                ('tender', 'Tender'), ('rfq', 'RFQ'), ('eoi', 'EOI'),
                ('direct_enquiry', 'Direct enquiry'), ('other', 'Other'),
            ], max_length=20),
        ),
        migrations.AddField(
            model_name='deal', name='open_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='deal', name='created_by',
            field=models.ForeignKey(
                blank=True, editable=False, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='sales_opportunities_created', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='deal', name='estimated_value',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True),
        ),
        migrations.AlterField(
            model_name='deal', name='weighted_value',
            field=models.DecimalField(blank=True, decimal_places=2, max_digits=15, null=True),
        ),
        migrations.AlterField(
            model_name='deal', name='expected_close_date',
            field=models.DateField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='deal', name='currency',
            field=models.CharField(blank=True, default='', max_length=10),
        ),
        migrations.AlterField(
            model_name='deal', name='owner',
            field=models.ForeignKey(
                blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                related_name='deals_owned', to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name='deal', name='stage',
            field=models.CharField(choices=[
                ('lead', 'Open'), ('qualified', 'Qualified Lead'),
                ('proposal', 'Proposal & Estimate'), ('negotiation', 'Negotiation'),
                ('award_pending', 'Award Approval'), ('awarded', 'Awarded'),
                ('converted', 'Converted to Project'), ('no_bid', 'No Bid'),
                ('lost', 'Lost'), ('cancelled', 'Cancelled'),
            ], default='lead', max_length=20),
        ),
        migrations.RunPython(seed_vf_sequence, migrations.RunPython.noop),
        # Run the reverse guard before removing columns or restoring NOT NULL.
        migrations.RunPython(migrations.RunPython.noop, refuse_loss_of_registration),
    ]
