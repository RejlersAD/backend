"""
Corrects the 'line_list' legend's field breakdown to match this project's
real line-tag format, per the project legend sheet:

    XX - AA - XXXX - XXXXXX - X - X
    size - service_code - serial_number - specification - dep/deviation(opt) - insulation(opt)

The previous default had 5 fields in a different order (size, service,
spec, serial, insulation — spec before serial) and no dep/deviation field.
This migration only replaces the `definition` JSON on the existing active
'line_list' row (name/description updated to match); no schema change.

Only the exact seeded default row is touched (matched by name), same
caution as the instrument_io_workflow field-order fix — never overwrites a
legend a user has since customized/renamed themselves.

The service-code lookup table (37 entries) is carried over unchanged from
the previous definition, just under the renamed field key 'service_code'.
"""
from django.db import migrations

SECTION = 'line_list'

SERVICE_LOOKUP = {
    'AM': 'AMIN LIQUID',
    'BD': 'BLOWDOWN',
    'CD': 'CLOSED DRAIN',
    'CH': 'SRP/DEAERATION/CIP CHEMICAL',
    'CL': 'OTHER CHEMICAL',
    'CR': 'CORROSION INHIBITOR',
    'DC': 'CLOSED DRAIN',
    'DF': 'DIESEL OIL',
    'DL': 'DRAIN LIQUID',
    'DO': 'SEA WATER SERVICE OPEN DRAIN',
    'DR': 'SOUR WATER',
    'DW': 'FRESH WATER',
    'FG': 'FUEL GAS',
    'FL': 'FLARE GAS',
    'FW': 'FIRE WATER',
    'G': 'GAS HYDROCARBON VAPOR',
    'GL': 'GLYCOL',
    'HC': 'HYDROCARBON LIQUID',
    'HL': 'SODIUM HYPO-CHLORITE',
    'IA': 'INSTRUMENT AIR',
    'IW': 'INJECTION WATER',
    'ME': 'METHANOL',
    'N2': 'NITROGEN GAS',
    'NG': 'NATURAL GAS',
    'OW': 'OILY WATER',
    'P': 'CRUDE OIL',
    'PA': 'PLANT AIR',
    'PL': 'PIPELINE',
    'PW': 'PRODUCED WATER',
    'RW': 'REJECT WATER',
    'SG': 'SOUR GAS',
    'SW': 'SEAWATER',
    'TW': 'TREATED WATER',
    'UA': 'PLANT AIR',
    'UW': 'UTILITY WATER',
    'VG': 'VENT GAS',
    'VT': 'VENT',
    'XN': 'XYLENE',
}

INSULATION_LOOKUP = {
    'C': 'COLD CONSERVATION',
    'H': 'HEAT CONSERVATION',
    'P': 'PERSONAL PROTECTION',
    'T': 'TRACING',
}

NEW_DEFINITION = {
    'separator': '-',
    'fields': [
        {
            'key': 'size',
            'label': 'Size (inches)',
            'regex': r'\d{1,3}(?:/\d)?',
            'suffix': '"',
            'notes': 'Pipe size in inches (integer or fraction, e.g. 6, 3/4)',
        },
        {
            'key': 'service_code',
            'label': 'Service Code',
            'regex': '[A-Z]{2,3}',
            'notes': 'Service identifier',
            'lookup': SERVICE_LOOKUP,
        },
        {
            'key': 'serial_number',
            'label': 'Serial Number',
            'regex': '[A-Z0-9]{3,6}',
            'notes': 'Line serial number',
        },
        {
            'key': 'specification',
            'label': 'Specification',
            'regex': '[A-Z0-9]{4,8}',
            'notes': 'As per the Piping Material Specification',
        },
        {
            'key': 'dep_deviation',
            'label': 'Dep/Deviation',
            'regex': '[A-Z0-9]{1,2}',
            'optional': True,
            'notes': 'Optional departure/deviation code',
        },
        {
            'key': 'insulation',
            'label': 'Insulation',
            'regex': '[A-Z]',
            'optional': True,
            'notes': 'H=Hot, C=Cold, P=Personnel',
            'lookup': INSULATION_LOOKUP,
        },
    ],
}

NEW_NAME = 'Line List — XX-AA-XXXX-XXXXXX-X-X (default)'
NEW_DESCRIPTION = (
    'Composite pipeline line tag per the project legend sheet: pipe size, '
    'service code, serial number, specification, and optional '
    'dep/deviation and insulation codes.'
)

OLD_DEFINITION = {
    'separator': '-',
    'fields': [
        {
            'key': 'size',
            'label': 'Pipe Size',
            'regex': r'\d{1,2}(?:[-/]\d{1,2}(?:/\d)?)?',
            'suffix': '"',
            'notes': 'Pipe Size in inches (integer or fraction, e.g. 6, 3/4, 1-1/2)',
        },
        {
            'key': 'service',
            'label': 'Service Identifier',
            'regex': r'[A-Z]{1,4}',
            'lookup': SERVICE_LOOKUP,
        },
        {
            'key': 'spec',
            'label': 'Line Classification',
            'regex': r'[A-Z0-9]{2,6}',
            'notes': 'As per the Piping Material Specification',
        },
        {
            'key': 'serial',
            'label': 'Line Number',
            'regex': r'\d{3,5}',
            'notes': 'Line sequence number',
        },
        {
            'key': 'insulation',
            'label': 'Insulation Class',
            'regex': r'[A-Z]',
            'optional': True,
            'lookup': INSULATION_LOOKUP,
        },
    ],
}
OLD_NAME = 'Line List — XX-XX-XXXX-XXXX-X (default)'
OLD_DESCRIPTION = (
    'Composite pipeline line tag: pipe size, service identifier, '
    'line classification, line number and (optional) insulation class.'
)


def fix_forward(apps, schema_editor):
    PidCheckerV2LegendSheet = apps.get_model('pid_checker_v2', 'PidCheckerV2LegendSheet')
    updated = PidCheckerV2LegendSheet.objects.filter(section=SECTION, name=OLD_NAME).update(
        name=NEW_NAME,
        description=NEW_DESCRIPTION,
        definition=NEW_DEFINITION,
    )
    print(f'[pid_checker_v2] Fixed field order on {updated} {SECTION!r} legend row(s).')


def fix_backward(apps, schema_editor):
    PidCheckerV2LegendSheet = apps.get_model('pid_checker_v2', 'PidCheckerV2LegendSheet')
    PidCheckerV2LegendSheet.objects.filter(section=SECTION, name=NEW_NAME).update(
        name=OLD_NAME,
        description=OLD_DESCRIPTION,
        definition=OLD_DEFINITION,
    )


class Migration(migrations.Migration):

    dependencies = [
        ('pid_checker_v2', '0017_remove_pidcheckerv2equipmentlistupload_uniq_pidv2_active_equipment_list_per_user_and_more'),
    ]

    operations = [
        migrations.RunPython(fix_forward, fix_backward),
    ]
