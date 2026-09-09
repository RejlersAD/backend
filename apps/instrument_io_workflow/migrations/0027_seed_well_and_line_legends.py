"""
Seeds one active legend each for the 3 new sections
('well_instrument_tagging', 'well_equipment_numbering', 'line_numbering'),
following the same pattern as 0018's signal_types/cabinet_locations seed.

No lookup tables were given for well_instrument_tagging's/
well_equipment_numbering's coded fields (instrument_classification,
equipment_code, etc.) — none is invented here, matching how earlier
under-specified fields in this app were left lookup-less rather than
guessed. line_numbering's insulation field's lookup (H/C/P) was given
explicitly and is included as specified.

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

WELL_INSTRUMENT_TAGGING_DEFINITION = {
    'separator': '-',
    'fields': [
        {'key': 'area_code', 'label': 'Area Code', 'regex': '[A-Z0-9]{2}'},
        {'key': 'plant_area_code', 'label': 'Plant Area Code', 'regex': '[A-Z0-9]{2}'},
        {'key': 'instrument_classification', 'label': 'Instrument Classification Code', 'regex': '[A-Z]{1,6}'},
        {'key': 'well_number', 'label': 'Well Number', 'regex': '[A-Z0-9]{2,4}'},
        {'key': 'well_completion_code', 'label': 'Well Completion Code', 'regex': '[A-Z0-9]{1,2}'},
        {'key': 'sequence_number', 'label': 'Sequence Number', 'regex': r'\d{3,5}'},
        {'key': 'suffix', 'label': 'Suffix', 'regex': '[A-Z]{1,2}', 'optional': True},
    ],
}

WELL_EQUIPMENT_NUMBERING_DEFINITION = {
    'separator': '-',
    'fields': [
        {'key': 'area_code', 'label': 'Area Code', 'regex': '[A-Z0-9]{2}'},
        {'key': 'plant_area_code', 'label': 'Plant Area Code', 'regex': '[A-Z0-9]{2}'},
        {'key': 'equipment_code', 'label': 'Equipment Code', 'regex': '[A-Z]{2,6}'},
        {'key': 'well_number', 'label': 'Well Number', 'regex': '[A-Z0-9]{2,4}'},
        {'key': 'system_completion_code', 'label': 'System Completion Code', 'regex': '[A-Z0-9]{1,2}'},
        {'key': 'sequence_number', 'label': 'Sequence Number', 'regex': r'\d{2,4}'},
    ],
}

LINE_NUMBERING_DEFINITION = {
    'separator': '-',
    'fields': [
        {
            'key': 'size', 'label': 'Size (Inches)',
            'regex': r'\d{1,3}(?:/\d)?', 'suffix': '"',
        },
        {'key': 'service_code', 'label': 'Service Code', 'regex': '[A-Z]{2,3}'},
        {'key': 'serial_number', 'label': 'Serial No.', 'regex': '[A-Z0-9]{3,6}'},
        {'key': 'specification', 'label': 'Specification', 'regex': '[A-Z0-9]{4,8}'},
        {
            'key': 'dep_deviation', 'label': 'Dep. Deviation',
            'regex': '[A-Z0-9]{1,2}', 'optional': True,
        },
        {
            'key': 'insulation', 'label': 'Insulation',
            'regex': '[A-Z]', 'optional': True,
            'lookup': {
                'H': 'THERMAL HOT',
                'C': 'THERMAL COLD',
                'P': 'PERSONNEL PROTECTION',
            },
        },
    ],
}

SEEDS = [
    ('well_instrument_tagging', 'Well Instrument Tagging — XX-XX-AAAA-XXX-X-XXXX-X (default)',
     'Well instrument tag numbering: area code, plant area code, instrument '
     'classification code, well number, well completion code, sequence '
     'number, and optional suffix.', WELL_INSTRUMENT_TAGGING_DEFINITION),
    ('well_equipment_numbering', 'Well Equipment Numbering — XX-XX-AAAA-XXX-X-XX (default)',
     'Well equipment numbering: area code, plant area code, equipment code, '
     'well number, system completion code, and sequence number.', WELL_EQUIPMENT_NUMBERING_DEFINITION),
    ('line_numbering', 'Line Numbering — XX-AA-XXXX-XXXXXX-X-X (default)',
     'Composite pipeline line tag: size, service code, serial number, '
     'specification, and optional dep/deviation and insulation codes.', LINE_NUMBERING_DEFINITION),
]


def seed_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    template = IOListLegendSheet.objects.exclude(created_by__isnull=True).first()
    if not template:
        return

    for section, name, description, definition in SEEDS:
        if IOListLegendSheet.objects.filter(section=section).exists():
            continue
        IOListLegendSheet.objects.create(
            created_by_id=template.created_by_id,
            section=section,
            name=name,
            description=description,
            definition=definition,
            is_active=True,
        )
        print(f'[instrument_io_workflow] Seeded 1 legend for section={section!r}.')


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0026_relabel_tag_register'),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
