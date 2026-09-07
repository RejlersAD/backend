"""
Adds 13 real instrument-classification codes to 'instrument_index'
("Instrument Tagging" tab), found while cross-checking Legend Check
findings against a second real P&ID drawing document (document #33,
Vision-extracted) as part of a full I/O List module audit. Same
evidence-based approach as migration 0056: each code's meaning below is
read directly from that same document's own AI-reported 'symbol_type'
for a real tag using it, not guessed from a generic ISA table —

  1520-TXT-1586C  -> TEMPERATURE TRANSMITTER
  1520-TXI-1586B  -> TEMPERATURE INDICATOR
  1520-PDI-1610   -> PRESSURE DIFFERENTIAL INDICATOR
  1520-GI-1003A   -> RUNNING STATUS INDICATOR (service: RUNNING)
  1520-HPB-1003A  -> HAND PUSH BUTTON (service: STOP)
  1520-TWG-1564   -> TEMPERATURE WELL GAUGE
  1520-TXHH-1588  -> TEMPERATURE HIGH-HIGH SWITCH
  1520-VT-1003A   -> VIBRATION TRANSMITTER (service: VF)
  1520-VI-1003A   -> VIBRATION INDICATOR (service: HH)
  1520-XL-1003A   -> STATUS LIGHT (service: VFD STATUS) — same X=unclassified
                      + suffix convention as XY/XHSC/XHSO added in 0056
  1520-ESD-1004   -> EMERGENCY SHUTDOWN SWITCH
  1520-FC-1004    -> FLOW CONTROLLER
  1520-MHS-1002   -> MANUAL SWITCH (service: STARTUP BY PASS)

Only the exact seeded default row is touched (matched by name), same as
migrations 0054/0056. I/O List only — apps.pid_checker_v2 /
apps.pid_verification are not touched by, and do not read, this
migration.
"""
from django.db import migrations

SECTION = 'instrument_index'
NAME = 'Instrument Tagging — [XX-[XX-]]AAAA-XXXXB[-X] (default)'

NEW_CODES = {
    'TXT':  'TEMPERATURE TRANSMITTER',
    'TXI':  'TEMPERATURE INDICATOR',
    'PDI':  'PRESSURE DIFFERENTIAL INDICATOR',
    'GI':   'RUNNING STATUS INDICATOR',
    'HPB':  'HAND PUSH BUTTON',
    'TWG':  'TEMPERATURE WELL GAUGE',
    'TXHH': 'TEMPERATURE HIGH-HIGH SWITCH',
    'VT':   'VIBRATION TRANSMITTER',
    'VI':   'VIBRATION INDICATOR',
    'XL':   'STATUS LIGHT',
    'ESD':  'EMERGENCY SHUTDOWN SWITCH',
    'FC':   'FLOW CONTROLLER',
    'MHS':  'MANUAL SWITCH',
}


def apply_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'instrument_classification':
            field['lookup'] = {**field.get('lookup', {}), **NEW_CODES}
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Added {len(NEW_CODES)} instrument_classification '
          f'codes to {SECTION!r}: {sorted(NEW_CODES)}')


def apply_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'instrument_classification':
            for code in NEW_CODES:
                field.get('lookup', {}).pop(code, None)
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0059_widen_sequence_number_min_digits'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
