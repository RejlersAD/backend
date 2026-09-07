"""
Seeds one active legend for the new 'inline_equipment' section — single
field, identity name->name lookup, same pattern as 'main_equipment' (0032).

I/O List only — apps.pid_checker_v2 is not touched by, and does not read,
this migration.
"""
from django.db import migrations

LOOKUP = {
    'HOSE CONNECTION': 'HOSE CONNECTION',
    'BLIND FLANGE': 'BLIND FLANGE',
    'CAP (BUTT WELDED)': 'CAP (BUTT WELDED)',
    'SPECTACLE BLIND (OPEN)': 'SPECTACLE BLIND (OPEN)',
    'SPECTACLE BLIND (CLOSED)': 'SPECTACLE BLIND (CLOSED)',
    'FLOW STRAIGHTENING VANES': 'FLOW STRAIGHTENING VANES',
    'FILTER (BASIC SYMBOL)': 'FILTER (BASIC SYMBOL)',
    'STRAINER "Y" TYPE': 'STRAINER "Y" TYPE',
    'CONCENTRIC REDUCER (BUTT WELDED)': 'CONCENTRIC REDUCER (BUTT WELDED)',
    'ECCENTRIC REDUCER (BUTT WELDED) BOTTOM FLAT': 'ECCENTRIC REDUCER (BUTT WELDED) BOTTOM FLAT',
    'ECCENTRIC REDUCER (BUTT WELDED) TOP FLAT': 'ECCENTRIC REDUCER (BUTT WELDED) TOP FLAT',
    'BARRED TEE': 'BARRED TEE',
    'SPOOL PIECE FLANGED': 'SPOOL PIECE FLANGED',
    'SPADE': 'SPADE',
    'SPACER (RING SPACER)': 'SPACER (RING SPACER)',
    'IN LINE ANALYSER': 'IN LINE ANALYSER',
    'SIGHT GLASS': 'SIGHT GLASS',
    'MECHANICAL PIG SIGNALLER': 'MECHANICAL PIG SIGNALLER',
    'SPECIALITY ITEM': 'SPECIALITY ITEM',
    'BASKET TYPE STRAINER': 'BASKET TYPE STRAINER',
    'MONOBLOCK INSULATING JOINT': 'MONOBLOCK INSULATING JOINT',
    'INSULATING FLANGE': 'INSULATING FLANGE',
    'TUNDISH DRAIN OR OPEN DRAIN': 'TUNDISH DRAIN OR OPEN DRAIN',
    'VACUUM BREAKER OR BREATHER VALVE': 'VACUUM BREAKER OR BREATHER VALVE',
    'FLEXIBLE HOSE': 'FLEXIBLE HOSE',
    'FLAME ARRESTOR': 'FLAME ARRESTOR',
    'MONITOR': 'MONITOR',
    'FIRE HYDRANT': 'FIRE HYDRANT',
    'SAMPLE CONNECTION': 'SAMPLE CONNECTION',
    'FUSIBLE PLUG': 'FUSIBLE PLUG',
    'H2S DETECTOR': 'H2S DETECTOR',
    'HYDROCARBON DETECTOR': 'HYDROCARBON DETECTOR',
    'STRAINER BASIC SYMBOL': 'STRAINER BASIC SYMBOL',
    'TIE-IN TO EXISTING LINE OR EQUIPMENT': 'TIE-IN TO EXISTING LINE OR EQUIPMENT',
    'CHEMICAL INHIBITOR INJECTION POINT': 'CHEMICAL INHIBITOR INJECTION POINT',
    'CORROSION COUPON': 'CORROSION COUPON',
    'CORROSION PROBE': 'CORROSION PROBE',
    'CORROSION MONITOR': 'CORROSION MONITOR',
    'UV/IR FIRE DETECTOR': 'UV/IR FIRE DETECTOR',
    'VORTEX BREAKER': 'VORTEX BREAKER',
    'GAS PRODUCING WELL': 'GAS PRODUCING WELL',
    'GAS INJECTION WELL': 'GAS INJECTION WELL',
}

DEFINITION = {
    'separator': '-',
    'fields': [{
        'key': 'inline_equipment_type',
        'label': 'In Line Equipment Type',
        'notes': 'In line equipment type matched against Legend.',
        'regex': '[A-Z0-9 /()"-]+',
        'lookup': LOOKUP,
    }],
}

SECTION = 'inline_equipment'
NAME = 'In Line Equipment — In Line Equipment Type (default)'
DESCRIPTION = 'In line equipment type matched directly against Legend.'


def seed_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')

    if IOListLegendSheet.objects.filter(section=SECTION).exists():
        return  # already seeded (e.g. migration re-run) — don't duplicate

    template = IOListLegendSheet.objects.exclude(created_by__isnull=True).first()
    if not template:
        return

    IOListLegendSheet.objects.create(
        created_by_id=template.created_by_id,
        section=SECTION,
        name=NAME,
        description=DESCRIPTION,
        definition=DEFINITION,
        is_active=True,
    )
    print(f'[instrument_io_workflow] Seeded 1 legend for section={SECTION!r}.')


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0045_add_inline_equipment_section'),
    ]

    operations = [
        migrations.RunPython(seed_forward, migrations.RunPython.noop),
    ]
