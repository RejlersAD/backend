"""
Replaces 'instrument_index' ("Instrument Tagging" tab) function-code
lookup with the full ISA instrument-function abbreviation table the user
supplied (project "ABBREVIATIONS – INSTRUMENTATION / NOTE 2" reference
sheet), transcribed in full: Flow, Level, Pressure, Temperature,
Analysis, Corrosion, Conductivity, Vibration, Density, Differential,
Humidity/Moisture, Panel/Control, Valve, Position, and Miscellaneous
sections.

Merged with (not simply replacing) the 13 existing entries the new sheet
doesn't cover — mostly ADNOC/project-specific BLOWDOWN/SHUTDOWN variants
added in earlier migrations (0013/0016) that aren't part of the generic
ISA sheet: TG, XV, FIT, SDY, BDHS, BPSV, BZSC, BZSO, SZSC, SZSO, XZSC,
XZSO, BDY, BPG. Where a code exists in both (e.g. FE, FT, PT, PSV, SDV),
the new sheet's wording wins since it's the explicit authoritative
reference just provided.

Deliberately NOT included: the reference sheet's seven "*.XX" entries
(*.HO, *.HS, *.HC, *.XO, *.XS, *.XC, *.XA, *.ZA, *.ZI, *.ZT) — these are
documented as PREFIX PATTERNS ("preceded by PP for pumps, FF for fans, MV
for MOV's, KK for compressor, SV for SDV's, GD for diesel generator"),
not literal standalone codes a flat lookup table can represent.

Only the exact seeded default row is touched (matched by name).

I/O List only — apps.pid_checker_v2 / apps.pid_verification are not
touched by, and do not read, this migration.
"""
from django.db import migrations

SECTION = 'instrument_index'
NAME = 'Instrument Tagging — [XX-[XX-]]AAAA-XXXXB[-X] (default)'

FULL_ISA_LOOKUP = {
    # ── Flow ──────────────────────────────────────────────────────────
    'FAH': 'FLOW ALARM HIGH',
    'FAL': 'FLOW ALARM LOW',
    'FCV': 'FLOW CONTROL VALVE',
    'FE': 'FLOW ELEMENT',
    'FG': 'FLOW SIGHT GLASS',
    'FHS': 'FLOW HAND SWITCH',
    'FI': 'FLOW INDICATOR',
    'FIC': 'FLOW INDICATOR CONTROLLER',
    'FQI': 'FLOW TOTALIZER INDICATOR',
    'FSAH': 'FLOW SHUTDOWN ALARM HIGH',
    'FSAL': 'FLOW SHUTDOWN ALARM LOW',
    'FSDH': 'FLOW SHUTDOWN HIGH',
    'FSDL': 'FLOW SHUTDOWN LOW',
    'FSH': 'FLOW SWITCH HIGH',
    'FSHH': 'FLOW SWITCH HIGH HIGH',
    'FSL': 'FLOW SWITCH LOW',
    'FSLL': 'FLOW SWITCH LOW LOW',
    'FSOV': 'FCV SOLENOID VALVE',
    'FT': 'FLOW TRANSMITTER',
    'FV': 'FLOW ON/OFF CONTROL VALVE',
    'FX': 'FLOW COMPUTER',
    'FY': 'FLOW I/P RELAY / CONVERTER',
    'FZT': 'FCV POSITION TRANSMITTER',
    'FZSH': 'FCV LIMIT SWITCH - OPEN',
    'FZSL': 'FCV LIMIT SWITCH - CLOSED',
    'RO': 'RESTRICTION ORIFICE',
    # ── Level ─────────────────────────────────────────────────────────
    'LAH': 'LEVEL ALARM HIGH',
    'LAL': 'LEVEL ALARM LOW',
    'LALL': 'LEVEL ALARM LOW LOW',
    'LCV': 'LEVEL CONTROL VALVE',
    'LG': 'LEVEL GAUGE',
    'LHS': 'LEVEL HAND SWITCH',
    'LI': 'LEVEL INDICATOR',
    'LIC': 'LEVEL INDICATOR CONTROLLER',
    'LSAH': 'LEVEL SHUTDOWN ALARM HIGH',
    'LSAL': 'LEVEL SHUTDOWN ALARM LOW',
    'LSDH': 'LEVEL SHUTDOWN HIGH',
    'LSDL': 'LEVEL SHUTDOWN LOW',
    'LSH': 'LEVEL SWITCH HIGH',
    'LSHH': 'LEVEL SWITCH HIGH HIGH',
    'LSL': 'LEVEL SWITCH LOW',
    'LSLL': 'LEVEL SWITCH LOW LOW',
    'LSOV': 'LCV SOLENOID VALVE',
    'LT': 'LEVEL TRANSMITTER',
    'LV': 'LEVEL ON/OFF CONTROL VALVE',
    'LY': 'LEVEL I/P RELAY / CONVERTER',
    'LZSH': 'LCV LIMIT SWITCH - OPEN',
    'LZSL': 'LCV LIMIT SWITCH - CLOSED',
    # ── Pressure ──────────────────────────────────────────────────────
    'PAH': 'PRESSURE ALARM HIGH',
    'PAL': 'PRESSURE ALARM LOW',
    'PCV': 'PRESSURE CONTROL VALVE',
    'PG': 'PRESSURE GAUGE INDICATOR',
    'PI': 'PRESSURE INDICATOR',
    'PIC': 'PRESSURE INDICATOR CONTROLLER',
    'PRV': 'PRESSURE REDUCING / REGUL. VALVE',
    'PSAH': 'PRESSURE SHUTDOWN ALARM HIGH',
    'PSAL': 'PRESSURE SHUTDOWN ALARM LOW',
    'PSDH': 'PRESSURE SHUTDOWN HIGH',
    'PSDL': 'PRESSURE SHUTDOWN LOW',
    'PSH': 'PRESSURE SWITCH HIGH',
    'PSHH': 'PRESSURE SWITCH HIGH HIGH',
    'PSL': 'PRESSURE SWITCH LOW',
    'PSLL': 'PRESSURE SWITCH LOW LOW',
    'PSE': 'BURSTING DISC',
    'PSV': 'PRESSURE SAFETY VALVE',
    'PSOV': 'PCV SOLENOID VALVE',
    'PT': 'PRESSURE TRANSMITTER',
    'PV': 'PRESSURE ON/OFF CONTROL VALVE',
    'PVSV': 'PRESSURE / VACUUM SAFETY VALVE',
    'PY': 'PRESSURE I/P RELAY / CONVERTER',
    'PZT': 'PCV POSITION TRANSMITTER',
    'PZSH': 'PCV LIMIT SWITCH - OPEN',
    'PZSL': 'PCV LIMIT SWITCH - CLOSED',
    'VSV': 'VACUUM SAFETY VALVE',
    # ── Temperature ───────────────────────────────────────────────────
    'TAH': 'TEMPERATURE ALARM HIGH',
    'TAL': 'TEMPERATURE ALARM LOW',
    'TALL': 'TEMPERATURE ALARM LOW LOW',
    'TCV': 'TEMPERATURE CONTROL VALVE',
    'TE': 'TEMPERATURE ELEMENT',
    'THS': 'TEMPERATURE HAND SWITCH',
    'TI': 'TEMPERATURE INDICATOR',
    'TIC': 'TEMPERATURE IND. CONTROLLER',
    'TSAH': 'TEMPERATURE SHUTDOWN ALARM HIGH',
    'TSAL': 'TEMPERATURE SHUTDOWN ALARM LOW',
    'TSDH': 'TEMPERATURE SHUTDOWN HIGH',
    'TSDL': 'TEMPERATURE SHUTDOWN LOW',
    'TSH': 'TEMPERATURE SWITCH HIGH',
    'TSHH': 'TEMPERATURE SWITCH HIGH HIGH',
    'TSL': 'TEMPERATURE SWITCH LOW',
    'TSLL': 'TEMPERATURE SWITCH LOW LOW',
    'TSV': 'THERMAL SAFETY RELIEF VALVE',
    'TSOV': 'TCV SOLENOID VALVE',
    'TT': 'TEMPERATURE TRANSMITTER',
    'TV': 'TEMP. ON/OFF CONTROL VALVE',
    'TW': 'THERMOWELL',
    'TY': 'TEMP. I/P RELAY / CONVERTER',
    'TZT': 'TCV POSITION TRANSMITTER',
    'TZSH': 'TCV LIMIT SWITCH - OPEN',
    'TZSL': 'TCV LIMIT SWITCH - CLOSED',
    # ── Analysis ──────────────────────────────────────────────────────
    'ACV': 'ANALYZER CONTROL VALVE',
    'AE': 'ANALYZER PROBE',
    'AI': 'ANALYZER INDICATOR',
    'AIC': 'ANALYZER IND. CONTROLLER',
    'AIT': 'ANALYZER IND. TRANSMITTER',
    'AP': 'ANALYZER SAMPLING POINT',
    'AT': 'ANALYZER TRANSMITTER',
    'ASDH': 'ANALYZER SHUTDOWN HIGH',
    'ASAH': 'ANALYZER SHUTDOWN ALARM HIGH',
    'AXXX': 'ANALYZER - GENERAL',
    # ── Corrosion ─────────────────────────────────────────────────────
    'CC': 'CORROSION COUPON',
    'CM': 'CORROSION MONITOR',
    'CMA': 'CORROSION MONITOR ALARM',
    'CP': 'CORROSION PROBE',
    # ── Conductivity ──────────────────────────────────────────────────
    'CAH': 'CONDUCTIVITY ALARM HIGH',
    'CAL': 'CONDUCTIVITY ALARM LOW',
    'CE': 'CONDUCTIVITY ELEMENT',
    'CSH': 'CONDUCTIVITY SWITCH HIGH',
    'CSL': 'CONDUCTIVITY SWITCH LOW',
    # ── Vibration ─────────────────────────────────────────────────────
    'XSH': 'VIBRATION SWITCH HIGH (SELECTOR SWITCH)',
    'XVA': 'VIBRATION ALARM',
    'XVE': 'VIBRATION PROBE',
    'XVM': 'VIBRATION MONITOR',
    'XVS': 'VIBRATION SWITCH',
    'XVSA': 'VIBRATION SHUTDOWN ALARM',
    'XVT': 'VIBRATION TRANSMITTER',
    'XVSD': 'VIBRATION SHUTDOWN',
    'XVXX': 'VIBRATION - GENERAL',
    # ── Density ───────────────────────────────────────────────────────
    'DE': 'DENSITY ELEMENT',
    'DI': 'DENSITY INDICATOR',
    'ST': 'DENSITY TRANSMITTER',
    # ── Differential ──────────────────────────────────────────────────
    'DPI': 'DIFFERENTIAL PRESSURE INDICATOR',
    'DPIC': 'DIFFERENTIAL PRESS. IND. CONTROLLER',
    'DPSH': 'DIFFERENTIAL PRESS. SWITCH HIGH',
    'DPSL': 'DIFFERENTIAL PRESS. SWITCH LOW',
    'DPZY': 'DIFFERENTIAL PRESS. PERMISSIVE',
    'DPT': 'DIFFERENTIAL PRESS. TRANSMITTER',
    'DTI': 'DIFFERENTIAL TEMP. INDICATOR',
    # ── Humidity / Moisture ───────────────────────────────────────────
    'MAH': 'HUMIDITY ALARM HIGH',
    'MAL': 'HUMIDITY ALARM LOW',
    'MX': 'MOISTURE ANALYZER',
    'ASH': 'HUMIDITY/MOISTURE SWITCH HIGH',
    'AAH': 'HUMIDITY/MOISTURE ALARM HIGH',
    # ── Panel / Control ───────────────────────────────────────────────
    'CCR': 'CENTRAL CONTROL ROOM',
    'CCP': 'CENTRAL CONTROL PANEL',
    'DCS': 'DISTRIBUTED CONTROL SYSTEM',
    'DCC': 'DCS - CONTROL CABINET',
    'FCP': 'FIRE & GAS CONTROL PANEL',
    'ICP': 'INTERMEDIATE CONTROL PANEL',
    'LCP': 'LOCAL CONTROL PANEL',
    'MP': 'MIMIC PANEL',
    'PLC': 'PROGRAMMABLE LOGIC CONTROLLER',
    'SDP': 'SHUTDOWN PANEL',
    'XCP': 'SCADA RTU / SCADA CONSOLE / MISC PANELS',
    'XVCP': 'VIBRATION CONTROL PANEL',
    'XMR': 'MARSHALLING CABINET',
    'XPC': 'COMPUTER',
    'DWS': 'DCS WORKSTATION',
    'XR': 'MISC. RECORDING / PRINTER',
    'XWS': 'MISCELLANEOUS WORKSTATION',
    # ── Valve ─────────────────────────────────────────────────────────
    'BDV': 'BLOWDOWN VALVE',
    'CVA': 'CHOKE VALVE',
    'EIV': 'EMERGENCY MANUAL ISOLATION VALVE',
    'HCV': 'HAND CONTROL VALVE',
    'HV': 'HAND ON/OFF CONTROL VALVE',
    'MOV': 'MOTOR OPERATED VALVE',
    'ROV': 'REMOTE OPERATED VALVE',
    'RSOV': 'ROV SOLENOID VALVE',
    'RZSH': 'ROV LIMIT SWITCH - OPEN',
    'RZSL': 'ROV LIMIT SWITCH - CLOSED',
    'SDV': 'SHUTDOWN VALVE',
    'SOV': 'SOLENOID VALVE',
    'XSOV': 'MISCELLANEOUS SOLENOID VALVE',
    'SSV': 'SURFACE SAFETY VALVE',
    'SSSV': 'SUB-SURFACE SAFETY VALVE',
    'VV': 'MAIN ISOLATION VALVE (HAND OPER.)',
    'XOV': 'LOCALLY OPERATED VALVE',
    # ── Position (for SOV's & MOV's) ──────────────────────────────────
    'ZAM': 'POSITION ALARM (MIDDLE OR IN BETWEEN "OPEN" & "CLOSED")',
    'ZIH': 'POS. INDICATOR "HIGH" OR "OPEN"',
    'ZIL': 'POS. INDICATOR "LOW" OR "CLOSED"',
    'ZSH': 'POSITION SWITCH "HIGH" OR "OPEN"',
    'ZSL': 'POSITION SWITCH "LOW" OR "CLOSED"',
    'ZSM': 'POSITION SWITCH (MIDDLE OR IN BETWEEN "OPEN" & "CLOSED")',
    'ZIC': 'POSITION INDICATOR "CLOSING"',
    'ZIO': 'POSITION INDICATOR "OPENING"',
    'ZXA': 'VALVE DISCREPANCY ALARM (GENERAL)',
    'SVHI': 'SHUTDOWN VALVE STROKE INDICATOR (0-100%)',
    'SVHCX': 'SHUTDOWN VALVE PARTIAL STROKE TEST',
    'ZY': 'POSITIONER',
    'XI': 'STATUS INDICATION (GENERAL)',
    'XA': 'FAULT ALARM (GENERAL)',
    'ZT': 'POSITION TRANSMITTER',
    # ── Miscellaneous ─────────────────────────────────────────────────
    'ACP': 'ALARM COLLECTION POINT',
    'ANN': 'ANNUNCIATOR',
    'BE': 'BURNER ELEMENT (FLAME SCANNER)',
    'BOH': "TANK'S BLOWOUT HATCH",
    'HIC': 'MANUAL LOADER',
    'HY': 'I/P RELAY CONVERTER FOR HCV',
    'HS': 'HAND OPERATED SWITCH (PUSH)',
    'HSD': 'HAND OPERATED SHUTDOWN SWITCH',
    'HSDA': 'HAND OPERATED SHUTDOWN ALARM',
    'JB': 'JUNCTION BOX',
    'JPB': 'JUNCTION BOX (PNEUMATIC)',
    'MOS': 'MAINTENANCE OVERRIDE SWITCH',
    'SC': 'SCALE COUPON',
    'II': 'CURRENT INDICATOR',
    'JI': 'TEMPERATURE SCANNER',
    'SI': 'SPEED INDICATOR',
    'XHS': 'SELECTOR SWITCH - HAND OPERATED',
    'SSDH': 'SPEED SHUTDOWN HIGH',
    'TCB': 'TELEPHONE CONNECTION BOX',
    'XPD': 'PIG DETECTOR',
}

# Existing entries the new sheet doesn't cover — ADNOC/project-specific
# BLOWDOWN/SHUTDOWN variants from migrations 0013/0016, preserved rather
# than dropped.
PRESERVED_LEGACY_LOOKUP = {
    'TG': 'TEMPERATURE GAUGE',
    'XV': 'ON/OFF ISOLATION VALVE',
    'FIT': 'FLOW INDICATING TRANSMITTER',
    'SDY': 'SHUTDOWN SOLENOID / RELAY',
    'BDHS': 'BLOWDOWN HAND SWITCH (LOCAL PUSH BUTTON)',
    'BPSV': 'BLOWDOWN PRESSURE SAFETY VALVE',
    'BZSC': 'BLOWDOWN POSITION SWITCH — CLOSED',
    'BZSO': 'BLOWDOWN POSITION SWITCH — OPEN',
    'SZSC': 'SHUTDOWN POSITION SWITCH — CLOSED',
    'SZSO': 'SHUTDOWN POSITION SWITCH — OPEN',
    'XZSC': 'POSITION SWITCH — CLOSED',
    'XZSO': 'POSITION SWITCH — OPEN',
    'BDY': 'BLOWDOWN SOLENOID / RELAY',
    'BPG': 'BLOWDOWN PRESSURE GAUGE',
}

NEW_LOOKUP = {**PRESERVED_LEGACY_LOOKUP, **FULL_ISA_LOOKUP}


def apply_forward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'instrument_classification':
            field['lookup'] = NEW_LOOKUP
    row.definition = definition
    row.save(update_fields=['definition'])
    print(f'[instrument_io_workflow] Set {SECTION!r} lookup to {len(NEW_LOOKUP)} entries '
          f'({len(FULL_ISA_LOOKUP)} from the ISA sheet + {len(PRESERVED_LEGACY_LOOKUP)} preserved).')


def apply_backward(apps, schema_editor):
    IOListLegendSheet = apps.get_model('instrument_io_workflow', 'IOListLegendSheet')
    row = IOListLegendSheet.objects.filter(section=SECTION, name=NAME).first()
    if not row:
        return
    definition = row.definition
    for field in definition.get('fields', []):
        if field.get('key') == 'instrument_classification':
            # Restore the pre-migration 0016/0050 35-entry set.
            field['lookup'] = {
                'FE': 'FLOW ELEMENT', 'FT': 'FLOW TRANSMITTER', 'FV': 'FLOW CONTROL VALVE',
                'LG': 'LEVEL GAUGE', 'LI': 'LEVEL INDICATOR', 'LT': 'LEVEL TRANSMITTER',
                'LV': 'LEVEL CONTROL VALVE', 'LY': 'LEVEL CONVERTER / POSITIONER',
                'PG': 'PRESSURE GAUGE', 'PI': 'PRESSURE INDICATOR', 'PT': 'PRESSURE TRANSMITTER',
                'PY': 'PRESSURE CONVERTER / POSITIONER', 'TG': 'TEMPERATURE GAUGE',
                'TI': 'TEMPERATURE INDICATOR', 'TT': 'TEMPERATURE TRANSMITTER', 'TW': 'THERMOWELL',
                'XV': 'ON/OFF ISOLATION VALVE', 'BDV': 'BLOWDOWN VALVE',
                'BDY': 'BLOWDOWN SOLENOID / RELAY', 'BPG': 'BLOWDOWN PRESSURE GAUGE',
                'FCV': 'FLOW CONTROL VALVE', 'FIT': 'FLOW INDICATING TRANSMITTER',
                'LCV': 'LEVEL CONTROL VALVE', 'PCV': 'PRESSURE CONTROL VALVE',
                'PSV': 'PRESSURE SAFETY VALVE', 'SDV': 'SHUTDOWN VALVE',
                'SDY': 'SHUTDOWN SOLENOID / RELAY',
                'BDHS': 'BLOWDOWN HAND SWITCH (LOCAL PUSH BUTTON)',
                'BPSV': 'BLOWDOWN PRESSURE SAFETY VALVE',
                'BZSC': 'BLOWDOWN POSITION SWITCH — CLOSED',
                'BZSO': 'BLOWDOWN POSITION SWITCH — OPEN',
                'SZSC': 'SHUTDOWN POSITION SWITCH — CLOSED',
                'SZSO': 'SHUTDOWN POSITION SWITCH — OPEN',
                'XZSC': 'POSITION SWITCH — CLOSED', 'XZSO': 'POSITION SWITCH — OPEN',
            }
    row.definition = definition
    row.save(update_fields=['definition'])


class Migration(migrations.Migration):

    dependencies = [
        ('instrument_io_workflow', '0053_add_document_page_progress_fields'),
    ]

    operations = [
        migrations.RunPython(apply_forward, apply_backward),
    ]
