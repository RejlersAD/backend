"""
Create PIDVInstrumentSymbol model — queryable per-symbol registry for
all instrument / valve / equipment symbols extracted from legend sheets.

Six standard instrumentation categories:
  control_valve, manual_valve, instrument,
  instrument_tagging, equipment_numbering, inline_equipment
"""
import uuid

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('pid_verification', '0005_pidv_legend_sheet'),
    ]

    operations = [
        migrations.CreateModel(
            name='PIDVInstrumentSymbol',
            fields=[
                ('id',               models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('symbol_id',        models.UUIDField(default=uuid.uuid4, unique=True, editable=False)),
                ('symbol_code',      models.CharField(db_index=True, max_length=100,
                                      help_text='e.g. HV, FIC, E-100, ZV')),
                ('description',      models.TextField(help_text='Human-readable description from legend sheet')),
                ('category',         models.CharField(
                    choices=[
                        ('control_valve',       'Control Valves'),
                        ('manual_valve',        'Manual Valves'),
                        ('instrument',          'Instruments'),
                        ('instrument_tagging',  'Instrument Tagging'),
                        ('equipment_numbering', 'Equipment Numbering'),
                        ('inline_equipment',    'In-Line Equipment'),
                    ],
                    db_index=True,
                    max_length=30,
                )),
                ('symbol_type',      models.CharField(blank=True, max_length=100,
                                      help_text='e.g. ball_valve, diff_pressure_transmitter')),
                ('drawing_standard', models.CharField(blank=True, default='ISA 5.1', max_length=100,
                                      help_text='e.g. ISA 5.1, IEC 62424')),
                ('attributes',       models.JSONField(blank=True, default=dict,
                                      help_text='Flexible per-category attributes')),
                ('source',           models.CharField(
                    choices=[
                        ('ai_extraction', 'AI Extraction'),
                        ('text_parse',    'Text Parse'),
                        ('manual',        'Manual Entry'),
                    ],
                    default='ai_extraction',
                    max_length=20,
                )),
                ('created_at',       models.DateTimeField(auto_now_add=True)),
                ('updated_at',       models.DateTimeField(auto_now=True)),
                ('project', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='instrument_symbols',
                    to='pid_verification.pidvproject',
                )),
                ('legend_sheet', models.ForeignKey(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='instrument_symbols',
                    to='pid_verification.pidvlegendsheet',
                )),
            ],
            options={
                'db_table': 'pidv_instrument_symbols',
                'ordering': ['category', 'symbol_code'],
                'indexes': [
                    models.Index(fields=['project', 'category'], name='pidv_is_proj_cat_idx'),
                    models.Index(fields=['symbol_id'],            name='pidv_is_symbol_id_idx'),
                    models.Index(fields=['symbol_code'],          name='pidv_is_code_idx'),
                ],
                'unique_together': {('project', 'symbol_code', 'category')},
            },
        ),
    ]
