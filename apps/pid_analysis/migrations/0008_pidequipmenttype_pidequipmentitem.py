"""Migration: add PIDEquipmentType and PIDEquipmentItem tables."""
import uuid
import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pid_analysis', '0007_add_evidence_to_pid_issue'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PIDEquipmentType',
            fields=[
                ('code',        models.CharField(max_length=10, primary_key=True, serialize=False,
                                                 help_text='Designation code, e.g. PC, HE, VV')),
                ('name',        models.CharField(max_length=120, help_text='Human-readable type name')),
                ('category',    models.CharField(max_length=20, default='MISC',
                                                 choices=[
                                                     ('VESSEL',         'Vessel / Drum'),
                                                     ('HEAT_EXCHANGER', 'Heat Exchanger'),
                                                     ('HEATER_COOLER',  'Heater / Cooler'),
                                                     ('ROTATING',       'Rotating Equipment'),
                                                     ('REACTOR',        'Reactor'),
                                                     ('PACKAGE',        'Package Equipment'),
                                                     ('MISC',           'Miscellaneous'),
                                                 ])),
                ('is_rotating', models.BooleanField(default=False)),
                ('is_active',   models.BooleanField(default=True)),
                ('created_at',  models.DateTimeField(auto_now_add=True)),
                ('updated_at',  models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name':        'PID Equipment Type',
                'verbose_name_plural': 'PID Equipment Types',
                'db_table':            'pid_equipment_types',
                'ordering':            ['category', 'code'],
            },
        ),
        migrations.CreateModel(
            name='PIDEquipmentItem',
            fields=[
                ('id',              models.UUIDField(primary_key=True, default=uuid.uuid4,
                                                     editable=False, serialize=False)),
                ('upload_id',       models.CharField(max_length=40, db_index=True,
                                                     help_text='upload_id from the analysis session')),
                ('drawing_ref',     models.CharField(max_length=120, blank=True, db_index=True,
                                                     help_text='Drawing / DWG NO from title block')),
                ('tag',             models.CharField(max_length=60, db_index=True,
                                                     help_text='Equipment tag number e.g. V-803-TF')),
                ('equipment_type',  models.ForeignKey(
                                        to='pid_analysis.pidequipmenttype',
                                        on_delete=django.db.models.deletion.SET_NULL,
                                        null=True, blank=True,
                                        related_name='items',
                                        help_text='Classified equipment type',
                                    )),
                ('revision',        models.CharField(max_length=10, blank=True)),
                ('description',     models.TextField(blank=True)),
                ('extraction_mode', models.CharField(max_length=30, blank=True)),
                ('data',            models.JSONField(default=dict, blank=True,
                                                     help_text='Extracted process parameters')),
                ('uploaded_by',     models.ForeignKey(
                                        to=settings.AUTH_USER_MODEL,
                                        on_delete=django.db.models.deletion.SET_NULL,
                                        null=True, blank=True,
                                        related_name='pid_equipment_items',
                                    )),
                ('created_at',      models.DateTimeField(auto_now_add=True)),
                ('updated_at',      models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name':        'PID Equipment Item',
                'verbose_name_plural': 'PID Equipment Items',
                'db_table':            'pid_equipment_items',
                'ordering':            ['drawing_ref', 'tag'],
            },
        ),
        migrations.AddConstraint(
            model_name='pidequipmentitem',
            constraint=models.UniqueConstraint(
                fields=['upload_id', 'tag'],
                name='pid_equip_item_upload_tag_uniq',
            ),
        ),
        migrations.AddIndex(
            model_name='pidequipmentitem',
            index=models.Index(fields=['drawing_ref', 'tag'],
                               name='pid_equip_item_drw_tag_idx'),
        ),
        migrations.AddIndex(
            model_name='pidequipmentitem',
            index=models.Index(fields=['upload_id'],
                               name='pid_equip_item_upload_idx'),
        ),
    ]
