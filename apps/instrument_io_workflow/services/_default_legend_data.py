"""
Static, repo-committed seed data for every I/O List legend section —
extracted verbatim from the fully-migrated, hand-verified reference
database this data was originally built up in across migrations 0010,
0012, 0014, 0016, 0018, 0020, 0022, 0027, 0032, 0035, 0038, 0044, 0046,
0050, 0052, 0054, 0056-0061 (each one further correcting/relabeling the
one before it) plus the now-removed pid_checker_v2 copy (0006/0008,
reworked to no-ops — see their own docstrings). Kept as ONE static
snapshot here instead of relying on that migration chain, for two
reasons: (1) that chain depended on an existing legend row to bootstrap
its shared 'created_by' from, which no longer exists on a genuinely
fresh database now that 0006/0008 do nothing; (2) a single seed function
callable any time (seed_io_default_legends, below) is far easier to
verify, re-run, and reason about than 60 one-shot data migrations.

Each entry here becomes ONE shared, repo-provisioned IOListLegendSheet
row (created_by=None, is_default=True, is_active=True) via
seed_io_default_legends() — visible to every user as that section's
default until/unless they create their own (see views.py's
IOListLegendSheetListCreateView.get_queryset and orchestrator.py's
_active_legends_for_user for the priority order).

DO NOT hand-edit the data below unless you're also updating the source
migrations' comments to match — treat this as generated-and-frozen.
"""
from __future__ import annotations

DEFAULT_LEGEND_SECTIONS = [{'section': 'equipment_register',
  'name': 'Equipment Numbering — [XXXX-]AAAA-NNNNN[X] (default)',
  'description': 'Equipment numbering: area code, plant area code, equipment code, system code, '
                 'unit number and sequence number.',
  'definition': {'fields': [{'key': 'unit_number',
                             'label': 'Unit Number',
                             'regex': '\\d{2,4}',
                             'optional': True},
                            {'key': 'equipment_code',
                             'label': 'Equipment Code',
                             'regex': '[A-Z]{1,4}'},
                            {'key': 'sequence_number',
                             'label': 'Sequence Number',
                             'regex': '\\d{1,5}[A-Z]{0,2}'}],
                 'separator': '-'}},
 {'section': 'instrument_index',
  'name': 'Instrument Tagging — [XX-[XX-]]AAAA-XXXXB[-X] (default)',
  'description': 'Flexible instrument tag numbering, 2 to 5 parts: optional area code, optional '
                 'plant area code, required instrument classification code, required sequence '
                 'number (+ optional letter), optional trailing suffix (e.g. XHSC-9502, '
                 '113-XHSC-9502, 11-13-XHSC-9502, 11-13-XHSC-9502-A).',
  'definition': {'fields': [{'key': 'area_code',
                             'label': 'Area Code',
                             'notes': 'Area code e.g. 11, 113',
                             'regex': '[A-Z0-9]{1,4}',
                             'optional': True},
                            {'key': 'plant_area_code',
                             'label': 'Plant Area Code',
                             'notes': 'Plant area code e.g. 13',
                             'regex': '[A-Z0-9]{1,4}',
                             'optional': True},
                            {'key': 'instrument_classification',
                             'label': 'Instrument Classification Code',
                             'notes': 'e.g. XHSC, PZT, PT, TT, X',
                             'regex': '[A-Z]{1,6}',
                             'lookup': {'AE': 'ANALYZER PROBE',
                                        'AI': 'ANALYZER INDICATOR',
                                        'AP': 'ANALYZER SAMPLING POINT',
                                        'AT': 'ANALYZER TRANSMITTER',
                                        'BE': 'BURNER ELEMENT (FLAME SCANNER)',
                                        'CC': 'CORROSION COUPON',
                                        'CE': 'CONDUCTIVITY ELEMENT',
                                        'CM': 'CORROSION MONITOR',
                                        'CP': 'CORROSION PROBE',
                                        'DE': 'DENSITY ELEMENT',
                                        'DI': 'DENSITY INDICATOR',
                                        'FC': 'FLOW CONTROLLER',
                                        'FE': 'FLOW ELEMENT',
                                        'FG': 'FLOW SIGHT GLASS',
                                        'FI': 'FLOW INDICATOR',
                                        'FT': 'FLOW TRANSMITTER',
                                        'FV': 'FLOW ON/OFF CONTROL VALVE',
                                        'FX': 'FLOW COMPUTER',
                                        'FY': 'FLOW I/P RELAY / CONVERTER',
                                        'GI': 'RUNNING STATUS INDICATOR',
                                        'HS': 'HAND OPERATED SWITCH (PUSH)',
                                        'HV': 'HAND ON/OFF CONTROL VALVE',
                                        'HY': 'I/P RELAY CONVERTER FOR HCV',
                                        'II': 'CURRENT INDICATOR',
                                        'JB': 'JUNCTION BOX',
                                        'JI': 'TEMPERATURE SCANNER',
                                        'LG': 'LEVEL GAUGE',
                                        'LI': 'LEVEL INDICATOR',
                                        'LT': 'LEVEL TRANSMITTER',
                                        'LV': 'LEVEL ON/OFF CONTROL VALVE',
                                        'LY': 'LEVEL I/P RELAY / CONVERTER',
                                        'MP': 'MIMIC PANEL',
                                        'MX': 'MOISTURE ANALYZER',
                                        'PG': 'PRESSURE GAUGE INDICATOR',
                                        'PI': 'PRESSURE INDICATOR',
                                        'PT': 'PRESSURE TRANSMITTER',
                                        'PV': 'PRESSURE ON/OFF CONTROL VALVE',
                                        'PY': 'PRESSURE I/P RELAY / CONVERTER',
                                        'RO': 'RESTRICTION ORIFICE',
                                        'SC': 'SCALE COUPON',
                                        'SI': 'SPEED INDICATOR',
                                        'ST': 'DENSITY TRANSMITTER',
                                        'TE': 'TEMPERATURE ELEMENT',
                                        'TG': 'TEMPERATURE GAUGE',
                                        'TI': 'TEMPERATURE INDICATOR',
                                        'TT': 'TEMPERATURE TRANSMITTER',
                                        'TV': 'TEMP. ON/OFF CONTROL VALVE',
                                        'TW': 'THERMOWELL',
                                        'TY': 'TEMP. I/P RELAY / CONVERTER',
                                        'VI': 'VIBRATION INDICATOR',
                                        'VT': 'VIBRATION TRANSMITTER',
                                        'VV': 'MAIN ISOLATION VALVE (HAND OPER.)',
                                        'XA': 'FAULT ALARM (GENERAL)',
                                        'XI': 'STATUS INDICATION (GENERAL)',
                                        'XL': 'STATUS LIGHT',
                                        'XR': 'MISC. RECORDING / PRINTER',
                                        'XV': 'ON/OFF ISOLATION VALVE',
                                        'XY': 'MISCELLANEOUS RELAY / SOLENOID',
                                        'ZT': 'POSITION TRANSMITTER',
                                        'ZY': 'POSITIONER',
                                        'AAH': 'HUMIDITY/MOISTURE ALARM HIGH',
                                        'ACP': 'ALARM COLLECTION POINT',
                                        'ACV': 'ANALYZER CONTROL VALVE',
                                        'AIC': 'ANALYZER IND. CONTROLLER',
                                        'AIT': 'ANALYZER IND. TRANSMITTER',
                                        'ANN': 'ANNUNCIATOR',
                                        'ASH': 'HUMIDITY/MOISTURE SWITCH HIGH',
                                        'BDV': 'BLOWDOWN VALVE',
                                        'BDY': 'BLOWDOWN SOLENOID / RELAY',
                                        'BOH': "TANK'S BLOWOUT HATCH",
                                        'BPG': 'BLOWDOWN PRESSURE GAUGE',
                                        'CAH': 'CONDUCTIVITY ALARM HIGH',
                                        'CAL': 'CONDUCTIVITY ALARM LOW',
                                        'CCP': 'CENTRAL CONTROL PANEL',
                                        'CCR': 'CENTRAL CONTROL ROOM',
                                        'CMA': 'CORROSION MONITOR ALARM',
                                        'CSH': 'CONDUCTIVITY SWITCH HIGH',
                                        'CSL': 'CONDUCTIVITY SWITCH LOW',
                                        'CVA': 'CHOKE VALVE',
                                        'DCC': 'DCS - CONTROL CABINET',
                                        'DCS': 'DISTRIBUTED CONTROL SYSTEM',
                                        'DPI': 'DIFFERENTIAL PRESSURE INDICATOR',
                                        'DPT': 'DIFFERENTIAL PRESS. TRANSMITTER',
                                        'DTI': 'DIFFERENTIAL TEMP. INDICATOR',
                                        'DWS': 'DCS WORKSTATION',
                                        'EIV': 'EMERGENCY MANUAL ISOLATION VALVE',
                                        'ESD': 'EMERGENCY SHUTDOWN SWITCH',
                                        'FAH': 'FLOW ALARM HIGH',
                                        'FAL': 'FLOW ALARM LOW',
                                        'FCP': 'FIRE & GAS CONTROL PANEL',
                                        'FCV': 'FLOW CONTROL VALVE',
                                        'FHS': 'FLOW HAND SWITCH',
                                        'FIC': 'FLOW INDICATOR CONTROLLER',
                                        'FIT': 'FLOW INDICATING TRANSMITTER',
                                        'FQI': 'FLOW TOTALIZER INDICATOR',
                                        'FSH': 'FLOW SWITCH HIGH',
                                        'FSL': 'FLOW SWITCH LOW',
                                        'FZT': 'FCV POSITION TRANSMITTER',
                                        'HCV': 'HAND CONTROL VALVE',
                                        'HIC': 'MANUAL LOADER',
                                        'HPB': 'HAND PUSH BUTTON',
                                        'HSD': 'HAND OPERATED SHUTDOWN SWITCH',
                                        'ICP': 'INTERMEDIATE CONTROL PANEL',
                                        'JPB': 'JUNCTION BOX (PNEUMATIC)',
                                        'LAH': 'LEVEL ALARM HIGH',
                                        'LAL': 'LEVEL ALARM LOW',
                                        'LCP': 'LOCAL CONTROL PANEL',
                                        'LCV': 'LEVEL CONTROL VALVE',
                                        'LHS': 'LEVEL HAND SWITCH',
                                        'LIC': 'LEVEL INDICATOR CONTROLLER',
                                        'LSH': 'LEVEL SWITCH HIGH',
                                        'LSL': 'LEVEL SWITCH LOW',
                                        'MAH': 'HUMIDITY ALARM HIGH',
                                        'MAL': 'HUMIDITY ALARM LOW',
                                        'MHS': 'MANUAL SWITCH',
                                        'MOS': 'MAINTENANCE OVERRIDE SWITCH',
                                        'MOV': 'MOTOR OPERATED VALVE',
                                        'PAH': 'PRESSURE ALARM HIGH',
                                        'PAL': 'PRESSURE ALARM LOW',
                                        'PCV': 'PRESSURE CONTROL VALVE',
                                        'PDI': 'PRESSURE DIFFERENTIAL INDICATOR',
                                        'PDT': 'PRESSURE DIFFERENTIAL TRANSMITTER',
                                        'PIC': 'PRESSURE INDICATOR CONTROLLER',
                                        'PLC': 'PROGRAMMABLE LOGIC CONTROLLER',
                                        'PRV': 'PRESSURE REDUCING / REGUL. VALVE',
                                        'PSE': 'BURSTING DISC',
                                        'PSH': 'PRESSURE SWITCH HIGH',
                                        'PSL': 'PRESSURE SWITCH LOW',
                                        'PSV': 'PRESSURE SAFETY VALVE',
                                        'PZT': 'PCV POSITION TRANSMITTER',
                                        'ROV': 'REMOTE OPERATED VALVE',
                                        'SDP': 'SHUTDOWN PANEL',
                                        'SDV': 'SHUTDOWN VALVE',
                                        'SDY': 'SHUTDOWN SOLENOID / RELAY',
                                        'SOV': 'SOLENOID VALVE',
                                        'SSV': 'SURFACE SAFETY VALVE',
                                        'TAH': 'TEMPERATURE ALARM HIGH',
                                        'TAL': 'TEMPERATURE ALARM LOW',
                                        'TCB': 'TELEPHONE CONNECTION BOX',
                                        'TCV': 'TEMPERATURE CONTROL VALVE',
                                        'THS': 'TEMPERATURE HAND SWITCH',
                                        'TIC': 'TEMPERATURE IND. CONTROLLER',
                                        'TSH': 'TEMPERATURE SWITCH HIGH',
                                        'TSL': 'TEMPERATURE SWITCH LOW',
                                        'TSV': 'THERMAL SAFETY RELIEF VALVE',
                                        'TWG': 'TEMPERATURE WELL GAUGE',
                                        'TXI': 'TEMPERATURE INDICATOR',
                                        'TXT': 'TEMPERATURE TRANSMITTER',
                                        'TZT': 'TCV POSITION TRANSMITTER',
                                        'VSV': 'VACUUM SAFETY VALVE',
                                        'XCP': 'SCADA RTU / SCADA CONSOLE / MISC PANELS',
                                        'XHS': 'SELECTOR SWITCH - HAND OPERATED',
                                        'XMR': 'MARSHALLING CABINET',
                                        'XOV': 'LOCALLY OPERATED VALVE',
                                        'XPC': 'COMPUTER',
                                        'XPD': 'PIG DETECTOR',
                                        'XSH': 'VIBRATION SWITCH HIGH (SELECTOR SWITCH)',
                                        'XVA': 'VIBRATION ALARM',
                                        'XVE': 'VIBRATION PROBE',
                                        'XVM': 'VIBRATION MONITOR',
                                        'XVS': 'VIBRATION SWITCH',
                                        'XVT': 'VIBRATION TRANSMITTER',
                                        'XWS': 'MISCELLANEOUS WORKSTATION',
                                        'ZAM': 'POSITION ALARM (MIDDLE OR IN BETWEEN "OPEN" & '
                                               '"CLOSED")',
                                        'ZIC': 'POSITION INDICATOR "CLOSING"',
                                        'ZIH': 'POS. INDICATOR "HIGH" OR "OPEN"',
                                        'ZIL': 'POS. INDICATOR "LOW" OR "CLOSED"',
                                        'ZIO': 'POSITION INDICATOR "OPENING"',
                                        'ZSH': 'POSITION SWITCH "HIGH" OR "OPEN"',
                                        'ZSL': 'POSITION SWITCH "LOW" OR "CLOSED"',
                                        'ZSM': 'POSITION SWITCH (MIDDLE OR IN BETWEEN "OPEN" & '
                                               '"CLOSED")',
                                        'ZXA': 'VALVE DISCREPANCY ALARM (GENERAL)',
                                        'ASAH': 'ANALYZER SHUTDOWN ALARM HIGH',
                                        'ASDH': 'ANALYZER SHUTDOWN HIGH',
                                        'AXXX': 'ANALYZER - GENERAL',
                                        'BDHS': 'BLOWDOWN HAND SWITCH (LOCAL PUSH BUTTON)',
                                        'BPSV': 'BLOWDOWN PRESSURE SAFETY VALVE',
                                        'BZSC': 'BLOWDOWN POSITION SWITCH — CLOSED',
                                        'BZSO': 'BLOWDOWN POSITION SWITCH — OPEN',
                                        'DPIC': 'DIFFERENTIAL PRESS. IND. CONTROLLER',
                                        'DPSH': 'DIFFERENTIAL PRESS. SWITCH HIGH',
                                        'DPSL': 'DIFFERENTIAL PRESS. SWITCH LOW',
                                        'DPZY': 'DIFFERENTIAL PRESS. PERMISSIVE',
                                        'FSAH': 'FLOW SHUTDOWN ALARM HIGH',
                                        'FSAL': 'FLOW SHUTDOWN ALARM LOW',
                                        'FSDH': 'FLOW SHUTDOWN HIGH',
                                        'FSDL': 'FLOW SHUTDOWN LOW',
                                        'FSHH': 'FLOW SWITCH HIGH HIGH',
                                        'FSLL': 'FLOW SWITCH LOW LOW',
                                        'FSOV': 'FCV SOLENOID VALVE',
                                        'FZSH': 'FCV LIMIT SWITCH - OPEN',
                                        'FZSL': 'FCV LIMIT SWITCH - CLOSED',
                                        'HSDA': 'HAND OPERATED SHUTDOWN ALARM',
                                        'LALL': 'LEVEL ALARM LOW LOW',
                                        'LSAH': 'LEVEL SHUTDOWN ALARM HIGH',
                                        'LSAL': 'LEVEL SHUTDOWN ALARM LOW',
                                        'LSDH': 'LEVEL SHUTDOWN HIGH',
                                        'LSDL': 'LEVEL SHUTDOWN LOW',
                                        'LSHH': 'LEVEL SWITCH HIGH HIGH',
                                        'LSLL': 'LEVEL SWITCH LOW LOW',
                                        'LSOV': 'LCV SOLENOID VALVE',
                                        'LZSH': 'LCV LIMIT SWITCH - OPEN',
                                        'LZSL': 'LCV LIMIT SWITCH - CLOSED',
                                        'PSAH': 'PRESSURE SHUTDOWN ALARM HIGH',
                                        'PSAL': 'PRESSURE SHUTDOWN ALARM LOW',
                                        'PSDH': 'PRESSURE SHUTDOWN HIGH',
                                        'PSDL': 'PRESSURE SHUTDOWN LOW',
                                        'PSHH': 'PRESSURE SWITCH HIGH HIGH',
                                        'PSLL': 'PRESSURE SWITCH LOW LOW',
                                        'PSOV': 'PCV SOLENOID VALVE',
                                        'PVSV': 'PRESSURE / VACUUM SAFETY VALVE',
                                        'PZSH': 'PCV LIMIT SWITCH - OPEN',
                                        'PZSL': 'PCV LIMIT SWITCH - CLOSED',
                                        'RSOV': 'ROV SOLENOID VALVE',
                                        'RZSH': 'ROV LIMIT SWITCH - OPEN',
                                        'RZSL': 'ROV LIMIT SWITCH - CLOSED',
                                        'SSDH': 'SPEED SHUTDOWN HIGH',
                                        'SSSV': 'SUB-SURFACE SAFETY VALVE',
                                        'SVHI': 'SHUTDOWN VALVE STROKE INDICATOR (0-100%)',
                                        'SZSC': 'SHUTDOWN POSITION SWITCH — CLOSED',
                                        'SZSO': 'SHUTDOWN POSITION SWITCH — OPEN',
                                        'TALL': 'TEMPERATURE ALARM LOW LOW',
                                        'TSAH': 'TEMPERATURE SHUTDOWN ALARM HIGH',
                                        'TSAL': 'TEMPERATURE SHUTDOWN ALARM LOW',
                                        'TSDH': 'TEMPERATURE SHUTDOWN HIGH',
                                        'TSDL': 'TEMPERATURE SHUTDOWN LOW',
                                        'TSHH': 'TEMPERATURE SWITCH HIGH HIGH',
                                        'TSLL': 'TEMPERATURE SWITCH LOW LOW',
                                        'TSOV': 'TCV SOLENOID VALVE',
                                        'TXHH': 'TEMPERATURE HIGH-HIGH SWITCH',
                                        'TZSH': 'TCV LIMIT SWITCH - OPEN',
                                        'TZSL': 'TCV LIMIT SWITCH - CLOSED',
                                        'XHSC': 'HAND SWITCH CLOSE',
                                        'XHSO': 'HAND SWITCH OPEN',
                                        'XSOV': 'MISCELLANEOUS SOLENOID VALVE',
                                        'XVCP': 'VIBRATION CONTROL PANEL',
                                        'XVSA': 'VIBRATION SHUTDOWN ALARM',
                                        'XVSD': 'VIBRATION SHUTDOWN',
                                        'XVXX': 'VIBRATION - GENERAL',
                                        'XZSC': 'POSITION SWITCH — CLOSED',
                                        'XZSO': 'POSITION SWITCH — OPEN',
                                        'SVHCX': 'SHUTDOWN VALVE PARTIAL STROKE TEST'}},
                            {'key': 'sequence_number',
                             'label': 'Sequence Number',
                             'notes': 'e.g. 9502, 9501B, 3191',
                             'regex': '\\d{2,5}[A-Z]?'},
                            {'key': 'suffix',
                             'label': 'Suffix',
                             'notes': 'Optional suffix e.g. A, B',
                             'regex': '[A-Z]{1,2}',
                             'optional': True}],
                 'separator': '-'}},
 {'section': 'valve_types',
  'name': 'Manual Valves — Valve Type (default)',
  'description': 'Valve type, matched directly against Legend.xlsx (no Symbol column / short code '
                 'in the source sheet, so the full valve name is used as both the tag and the '
                 'lookup value).',
  'definition': {'fields': [{'key': 'valve_type',
                             'label': 'Valve Type',
                             'notes': 'Valve type — see lookup table.',
                             'regex': '[A-Z0-9 ()&]+',
                             'lookup': {'PLUG VALVE': 'PLUG VALVE',
                                        'MIXING VALVE': 'MIXING VALVE',
                                        'ROTARY CHOKE': 'ROTARY CHOKE',
                                        'BREATHER VALVE': 'BREATHER VALVE',
                                        'FOUR WAY VALVE': 'FOUR WAY VALVE',
                                        'WELD END VALVE': 'WELD END VALVE',
                                        'BUTTERFLY VALVE': 'BUTTERFLY VALVE',
                                        'THREE WAY VALVE': 'THREE WAY VALVE',
                                        'ADJUSTABLE CHOKE': 'ADJUSTABLE CHOKE',
                                        'FLANGED END VALVE': 'FLANGED END VALVE',
                                        'SCREWED END VALVE': 'SCREWED END VALVE',
                                        'VALVE (BASIC SYMBOL)': 'VALVE (BASIC SYMBOL)',
                                        'PRESSURE RELIEF VALVE': 'PRESSURE RELIEF VALVE',
                                        'BALL VALVE (NORMAL OPEN)': 'BALL VALVE (NORMAL OPEN)',
                                        'CHECK VALVE (SWING TYPE)': 'CHECK VALVE (SWING TYPE)',
                                        'CHECK VALVE (WAFER TYPE)': 'CHECK VALVE (WAFER TYPE)',
                                        'GATE VALVE (NORMAL OPEN)': 'GATE VALVE (NORMAL OPEN)',
                                        'BALL VALVE (NORMAL CLOSE)': 'BALL VALVE (NORMAL CLOSE)',
                                        'GATE VALVE (NORMAL CLOSE)': 'GATE VALVE (NORMAL CLOSE)',
                                        'GLOBE VALVE (NORMAL OPEN)': 'GLOBE VALVE (NORMAL OPEN)',
                                        'GLOBE VALVE (NORMAL CLOSE)': 'GLOBE VALVE (NORMAL CLOSE)',
                                        'NEEDLE VALVE (NORMAL OPEN)': 'NEEDLE VALVE (NORMAL OPEN)',
                                        'NEEDLE VALVE (NORMAL CLOSE)': 'NEEDLE VALVE (NORMAL '
                                                                       'CLOSE)',
                                        'PRESSURE AND VACUUM RELIEF VALVE': 'PRESSURE AND VACUUM '
                                                                            'RELIEF VALVE',
                                        'INTEGRAL DOUBLE BLOCK & BLEED VALVE': 'INTEGRAL DOUBLE '
                                                                               'BLOCK & BLEED '
                                                                               'VALVE',
                                        'MULTIPLE ORIFICE VALVE (CHOKE VALVE)': 'MULTIPLE ORIFICE '
                                                                                'VALVE (CHOKE '
                                                                                'VALVE)'}}],
                 'separator': '-'}},
 {'section': 'actuator_types',
  'name': 'Actuator Types — Actuator Type (default)',
  'description': 'Actuator type, matched directly against Legend (no Symbol column / short code in '
                 'the source sheet, so the full actuator type name is used as both the tag and the '
                 'lookup value).',
  'definition': {'fields': [{'key': 'actuator_type',
                             'label': 'Actuator Type',
                             'notes': 'Actuator type matched directly against Legend',
                             'regex': '[A-Z0-9 /()&-]+',
                             'lookup': {'SOLENOID': 'SOLENOID',
                                        'ROTARY MOTOR': 'ROTARY MOTOR',
                                        'DIAPHRAGM PRESSURE-BALANCED': 'DIAPHRAGM '
                                                                       'PRESSURE-BALANCED',
                                        'CYLINDER WITHOUT POSITIONER OR OTHER PILOT': 'CYLINDER '
                                                                                      'WITHOUT '
                                                                                      'POSITIONER '
                                                                                      'OR OTHER '
                                                                                      'PILOT',
                                        'DIAPHRAGM SPRING-OPPOSED OR UNSPECIFIED ACTUATOR': 'DIAPHRAGM '
                                                                                            'SPRING-OPPOSED '
                                                                                            'OR '
                                                                                            'UNSPECIFIED '
                                                                                            'ACTUATOR'}}],
                 'separator': '-'}},
 {'section': 'flow_instruments',
  'name': 'Flow Instruments — Flow Detector Type (default)',
  'description': 'Flow detector type, matched directly against Legend (no Symbol column / short '
                 'code in the source sheet, so the full type name is used as both the tag and the '
                 'lookup value).',
  'definition': {'fields': [{'key': 'flow_detector_type',
                             'label': 'Flow Detector Type',
                             'notes': 'Flow detector type matched directly against Legend (no '
                                      'Symbol column / short code in the source sheet, so the full '
                                      'type name is used as both the tag and the lookup value)',
                             'regex': '[A-Z0-9 /()&-]+',
                             'lookup': {'SIGHT GLASS': 'SIGHT GLASS',
                                        'V-CONE FLOW METER': 'V-CONE FLOW METER',
                                        'CORIOLIS FLOW METER': 'CORIOLIS FLOW METER',
                                        'MAGNETIC FLOW METER': 'MAGNETIC FLOW METER',
                                        'ULTRASONIC FLOW METER': 'ULTRASONIC FLOW METER',
                                        'VOLTEX TYPE FLOW METER': 'VOLTEX TYPE FLOW METER',
                                        'TURBINE TYPE FLOW METER': 'TURBINE TYPE FLOW METER',
                                        'VARIABLE FLOW INDICATOR': 'VARIABLE FLOW INDICATOR',
                                        'VENTURI TUBE OR FLOW NOZZLE': 'VENTURI TUBE OR FLOW '
                                                                       'NOZZLE',
                                        'ORIFICE PLATE OR RESTRICTION ORIFICE': 'ORIFICE PLATE OR '
                                                                                'RESTRICTION '
                                                                                'ORIFICE',
                                        'ORIFICE PLATE IN QUICK-CHANGE FITTING': 'ORIFICE PLATE IN '
                                                                                 'QUICK-CHANGE '
                                                                                 'FITTING',
                                        'POSITIVE DISPLACEMENT TYPE FLOW METER': 'POSITIVE '
                                                                                 'DISPLACEMENT '
                                                                                 'TYPE FLOW '
                                                                                 'METER'}}],
                 'separator': '-'}},
 {'section': 'control_valves',
  'name': 'Control Valves — Control Valve Type (default)',
  'description': 'Control valve type, matched directly against Legend (no Symbol column / short '
                 'code in the source sheet, so the full type name is used as both the tag and the '
                 'lookup value).',
  'definition': {'fields': [{'key': 'control_valve_type',
                             'label': 'Control Valve Type',
                             'notes': 'Control valve type matched directly against Legend (no '
                                      'Symbol column / short code in the source sheet, so the full '
                                      'type name is used as both the tag and the lookup value)',
                             'regex': '[A-Z0-9 /()&-]+',
                             'lookup': {'CONTROL VALVE': 'CONTROL VALVE',
                                        'SHUTDOWN VALVE': 'SHUTDOWN VALVE',
                                        'MOTOR OPERATED VALVE': 'MOTOR OPERATED VALVE',
                                        'SURFACE SAFETY VALVE': 'SURFACE SAFETY VALVE',
                                        'PISTON OPERATED VALVE': 'PISTON OPERATED VALVE',
                                        'ADJUSTABLE CHOKE VALVE': 'ADJUSTABLE CHOKE VALVE',
                                        'SOLENOID OPERATED VALVE': 'SOLENOID OPERATED VALVE',
                                        'DIAPHRAGM OPERATED VALVE': 'DIAPHRAGM OPERATED VALVE',
                                        'CONTROL VALVE (ANGLE TYPE)': 'CONTROL VALVE (ANGLE TYPE)',
                                        'CONTROL VALVE WITH HAND WHEEL': 'CONTROL VALVE WITH HAND '
                                                                         'WHEEL',
                                        'VACUUM SAFETY OR RELIEF VALVE': 'VACUUM SAFETY OR RELIEF '
                                                                         'VALVE',
                                        'CONTROL VALVE OPEN ON AIR FAIL': 'CONTROL VALVE OPEN ON '
                                                                          'AIR FAIL',
                                        'CONTROL VALVE CLOSED ON AIR FAIL': 'CONTROL VALVE CLOSED '
                                                                            'ON AIR FAIL',
                                        'LEVEL REGULATOR WITH MECHANICAL LINKAGE': 'LEVEL '
                                                                                   'REGULATOR WITH '
                                                                                   'MECHANICAL '
                                                                                   'LINKAGE',
                                        'PRESSURE / VACUUM SAFETY OR RELIEF VALVE': 'PRESSURE / '
                                                                                    'VACUUM SAFETY '
                                                                                    'OR RELIEF '
                                                                                    'VALVE',
                                        'TEMPERATURE REGULATOR (FILLED SYSTEM TYPE)': 'TEMPERATURE '
                                                                                      'REGULATOR '
                                                                                      '(FILLED '
                                                                                      'SYSTEM '
                                                                                      'TYPE)',
                                        'SURFACE CONTROLLED SUB-SURFACE SAFETY VALVE': 'SURFACE '
                                                                                       'CONTROLLED '
                                                                                       'SUB-SURFACE '
                                                                                       'SAFETY '
                                                                                       'VALVE',
                                        'PRESSURE REDUCING REGULATOR (SELF-CONTAINED)': 'PRESSURE '
                                                                                        'REDUCING '
                                                                                        'REGULATOR '
                                                                                        '(SELF-CONTAINED)',
                                        'CONTROL VALVE WITH ELECTRO-PNEUMATIC CONVERTER': 'CONTROL '
                                                                                          'VALVE '
                                                                                          'WITH '
                                                                                          'ELECTRO-PNEUMATIC '
                                                                                          'CONVERTER',
                                        'DIFFERENTIAL PRESSURE REDUCING / REGULATING VALVE': 'DIFFERENTIAL '
                                                                                             'PRESSURE '
                                                                                             'REDUCING '
                                                                                             '/ '
                                                                                             'REGULATING '
                                                                                             'VALVE',
                                        'PRESSURE REDUCING / REGULATING VALVE - SELF CONTAINED': 'PRESSURE '
                                                                                                 'REDUCING '
                                                                                                 '/ '
                                                                                                 'REGULATING '
                                                                                                 'VALVE '
                                                                                                 '- '
                                                                                                 'SELF '
                                                                                                 'CONTAINED',
                                        'PRESSURE REDUCING REGULATOR WITH EXTERNAL PRESSURE TAP': 'PRESSURE '
                                                                                                  'REDUCING '
                                                                                                  'REGULATOR '
                                                                                                  'WITH '
                                                                                                  'EXTERNAL '
                                                                                                  'PRESSURE '
                                                                                                  'TAP',
                                        'PRESSURE SAFETY OR RELIEF VALVE OR THERMAL SAFETY VALVE': 'PRESSURE '
                                                                                                   'SAFETY '
                                                                                                   'OR '
                                                                                                   'RELIEF '
                                                                                                   'VALVE '
                                                                                                   'OR '
                                                                                                   'THERMAL '
                                                                                                   'SAFETY '
                                                                                                   'VALVE',
                                        'BACK PRESSURE REDUCING / REGULATING VALVE - SELF CONTAINED': 'BACK '
                                                                                                      'PRESSURE '
                                                                                                      'REDUCING '
                                                                                                      '/ '
                                                                                                      'REGULATING '
                                                                                                      'VALVE '
                                                                                                      '- '
                                                                                                      'SELF '
                                                                                                      'CONTAINED'}}],
                 'separator': '-'}},
 {'section': 'signal_line_types',
  'name': 'Line Representation — Signal Type (default)',
  'description': 'Instrument signal type matched directly against Legend. This sheet has '
                 'descriptions only (no picture/symbol column), so the full name is used as both '
                 'the tag and the lookup value).',
  'definition': {'fields': [{'key': 'signal_type',
                             'label': 'Signal Type',
                             'notes': 'Instrument signal type matched directly against Legend. '
                                      'This sheet has descriptions only (no picture/symbol '
                                      'column), full name used as both key and value.',
                             'regex': '[A-Z0-9 /()&-]+',
                             'lookup': {'FENCE': 'FENCE',
                                        'HYDRAULIC SIGNAL': 'HYDRAULIC SIGNAL',
                                        'PNEUMATIC SIGNAL': 'PNEUMATIC SIGNAL',
                                        'ELECTRICAL SIGNAL': 'ELECTRICAL SIGNAL',
                                        'MAIN PROCESS LINE': 'MAIN PROCESS LINE',
                                        'CONNECTION TO PROCESS': 'CONNECTION TO PROCESS',
                                        'INSTRUMENT AIR SUPPLY': 'INSTRUMENT AIR SUPPLY',
                                        'SKID OR PACKAGE LIMIT': 'SKID OR PACKAGE LIMIT',
                                        'SOFTWARE OR DATA LINK': 'SOFTWARE OR DATA LINK',
                                        'LINE TO SLOPE DOWN (UNDEFINED)': 'LINE TO SLOPE DOWN '
                                                                          '(UNDEFINED)',
                                        'CAPILLARY TUBING (FIELD SYSTEM)': 'CAPILLARY TUBING '
                                                                           '(FIELD SYSTEM)',
                                        'SECONDARY PROCESS & UTILITY LINE': 'SECONDARY PROCESS & '
                                                                            'UTILITY LINE',
                                        'REFERENCE TO CONTINUATION DRAWING': 'REFERENCE TO '
                                                                             'CONTINUATION DRAWING',
                                        'LINE TO SLOPE DOWN (WITH SLOPE ANGLE)': 'LINE TO SLOPE '
                                                                                 'DOWN (WITH SLOPE '
                                                                                 'ANGLE)',
                                        'ELECTROMAGNETIC OR SONIC SIGNAL (GUIDED)': 'ELECTROMAGNETIC '
                                                                                    'OR SONIC '
                                                                                    'SIGNAL '
                                                                                    '(GUIDED)',
                                        'EXISTING PIPE TO BE ABANDONED OR REMOVED': 'EXISTING PIPE '
                                                                                    'TO BE '
                                                                                    'ABANDONED OR '
                                                                                    'REMOVED',
                                        'AIR SUPPLY TUBING / PNEUMATIC SIGNAL TUBING': 'AIR SUPPLY '
                                                                                       'TUBING / '
                                                                                       'PNEUMATIC '
                                                                                       'SIGNAL '
                                                                                       'TUBING',
                                        'ELECTROMAGNETIC OR SONIC SIGNAL (NOT GUIDED)': 'ELECTROMAGNETIC '
                                                                                        'OR SONIC '
                                                                                        'SIGNAL '
                                                                                        '(NOT '
                                                                                        'GUIDED)',
                                        'INTERNAL SYSTEM LINK (SOFTWARE OR DATA LINK)': 'INTERNAL '
                                                                                        'SYSTEM '
                                                                                        'LINK '
                                                                                        '(SOFTWARE '
                                                                                        'OR DATA '
                                                                                        'LINK)',
                                        'ELECTROMAGNETIC OR SONIC SIGNAL (WITHOUT WIRING OR TUBING)': 'ELECTROMAGNETIC '
                                                                                                      'OR '
                                                                                                      'SONIC '
                                                                                                      'SIGNAL '
                                                                                                      '(WITHOUT '
                                                                                                      'WIRING '
                                                                                                      'OR '
                                                                                                      'TUBING)'}}],
                 'separator': '-'}},
 {'section': 'equipment_symbols',
  'name': 'Equipment Symbols — Equipment Type (default)',
  'description': 'Equipment type matched directly against Legend (no short code in source sheet, '
                 'so the full name is used as both the tag and the lookup value).',
  'definition': {'fields': [{'key': 'equipment_type',
                             'label': 'Equipment Type',
                             'notes': 'Equipment type matched directly against Legend (no short '
                                      'code in source sheet, full name used as both key and value)',
                             'regex': '[A-Z0-9 /()&-]+',
                             'lookup': {'PIT': 'PIT',
                                        'MIXER': 'MIXER',
                                        'BLOWER': 'BLOWER',
                                        'COLUMN': 'COLUMN',
                                        'COOLER': 'COOLER',
                                        'HEATER': 'HEATER',
                                        'MEMBRANE': 'MEMBRANE',
                                        'SCRUBBER': 'SCRUBBER',
                                        'GEAR PUMP': 'GEAR PUMP',
                                        'AIR COOLER': 'AIR COOLER',
                                        'COMPRESSOR': 'COMPRESSOR',
                                        'MANWAY (MW)': 'MANWAY (MW)',
                                        'PIG LAUNCHER': 'PIG LAUNCHER',
                                        'PIG RECEIVER': 'PIG RECEIVER',
                                        'HANDHOLE (HH)': 'HANDHOLE (HH)',
                                        'VERTICAL PUMP': 'VERTICAL PUMP',
                                        'DEGASSING BOOT': 'DEGASSING BOOT',
                                        'ELECTRIC MOTOR': 'ELECTRIC MOTOR',
                                        'FLAT ROOF TANK': 'FLAT ROOF TANK',
                                        'MULTIHEAD PUMP': 'MULTIHEAD PUMP',
                                        'ELECTRIC HEATER': 'ELECTRIC HEATER',
                                        'MIST ELIMINATOR': 'MIST ELIMINATOR',
                                        'CARTRIDGE FILTER': 'CARTRIDGE FILTER',
                                        'CENTRIFUGAL PUMP': 'CENTRIFUGAL PUMP',
                                        'PERISTALTIC PUMP': 'PERISTALTIC PUMP',
                                        'ELECTROLYTIC CELL': 'ELECTROLYTIC CELL',
                                        'HORIZONTAL VESSEL': 'HORIZONTAL VESSEL',
                                        'DISENGAGEMENT TANK': 'DISENGAGEMENT TANK',
                                        'GEAR PUMP (DOUBLE)': 'GEAR PUMP (DOUBLE)',
                                        'DOUBLE DIAPHRAM PUMP': 'DOUBLE DIAPHRAM PUMP',
                                        'CONED FIXED ROOF TANK': 'CONED FIXED ROOF TANK',
                                        'ACTIVATED CARBON FILTER': 'ACTIVATED CARBON FILTER',
                                        'SHELL AND TUBE EXCHANGER': 'SHELL AND TUBE EXCHANGER',
                                        'WILDEN PUMP (AIR DRIVEN)': 'WILDEN PUMP (AIR DRIVEN)',
                                        'HORIZONTAL CARTIDGE FILTER': 'HORIZONTAL CARTIDGE FILTER',
                                        'POSITIVE DISPLACEMENT PUMP': 'POSITIVE DISPLACEMENT PUMP',
                                        'HORIZONTAL VESSEL WITH BOOT': 'HORIZONTAL VESSEL WITH '
                                                                       'BOOT',
                                        'FILTER BASIC SYMBOL (PFD ONLY)': 'FILTER BASIC SYMBOL '
                                                                          '(PFD ONLY)',
                                        'VERTICAL VESSEL (TOP BOLTED FLAT COVER TYPE)': 'VERTICAL '
                                                                                        'VESSEL '
                                                                                        '(TOP '
                                                                                        'BOLTED '
                                                                                        'FLAT '
                                                                                        'COVER '
                                                                                        'TYPE)'}}],
                 'separator': '-'}},
 {'section': 'instrument_functions',
  'name': 'Instrument Functions — Instrument Function (default)',
  'description': 'Instrument function type matched directly against Legend (picture and '
                 'description columns, full name used as both the tag and the lookup value).',
  'definition': {'fields': [{'key': 'instrument_function_type',
                             'label': 'Instrument Function',
                             'notes': 'Instrument function type matched directly against Legend '
                                      '(picture and description columns, full name used as both '
                                      'key and value)',
                             'regex': '[A-Z0-9 /()&-]+',
                             'lookup': {'SWITCH': 'SWITCH',
                                        'AMMETER': 'AMMETER',
                                        'SUMMING': 'SUMMING',
                                        'PUSH BUTTON': 'PUSH BUTTON',
                                        'PCS INTERLOCK': 'PCS INTERLOCK',
                                        'INTERLOCK LOGIC': 'INTERLOCK LOGIC',
                                        'LOCAL STATUS LAMP': 'LOCAL STATUS LAMP',
                                        'CURRENT-PNEUMATIC CONVERTER': 'CURRENT-PNEUMATIC '
                                                                       'CONVERTER'}}],
                 'separator': '-'}},
 {'section': 'instrument_typical_letter',
  'name': 'Instrument Typical Letter — Instrument Code (default)',
  'description': 'ISA-5.1 instrument letter combinations. First letter indicates measured '
                 'variable, subsequent letters indicate function.',
  'definition': {'fields': [{'key': 'instrument_code',
                             'label': 'Instrument Code',
                             'notes': 'ISA-5.1 instrument letter combinations. First letter '
                                      'indicates measured variable, subsequent letters indicate '
                                      'function.',
                             'regex': '[A-Z]{1,4}',
                             'lookup': {'A': 'ANALYSIS',
                                        'F': 'FLOW RATE',
                                        'H': 'HAND',
                                        'L': 'LEVEL',
                                        'P': 'PRESSURE',
                                        'S': 'SPEED',
                                        'T': 'TEMPERATURE',
                                        'V': 'VIBRATION',
                                        'W': 'WEIGHT',
                                        'Y': 'EVENT',
                                        'Z': 'POSITION',
                                        'AC': 'ANALYSIS CONTROLLER',
                                        'AE': 'ANALYSIS ELEMENT',
                                        'AI': 'ANALYSIS INDICATOR',
                                        'AR': 'ANALYSIS RECORDER',
                                        'AT': 'ANALYSIS TRANSMITTER',
                                        'AY': 'ANALYSIS RELAY',
                                        'FC': 'FLOW CONTROLLER',
                                        'FE': 'FLOW ELEMENT',
                                        'FI': 'FLOW INDICATOR',
                                        'FR': 'FLOW RECORDER',
                                        'FT': 'FLOW TRANSMITTER',
                                        'FY': 'FLOW RELAY',
                                        'HC': 'HAND CONTROLLER',
                                        'HS': 'HAND SWITCH',
                                        'LC': 'LEVEL CONTROLLER',
                                        'LE': 'LEVEL ELEMENT',
                                        'LI': 'LEVEL INDICATOR',
                                        'LR': 'LEVEL RECORDER',
                                        'LT': 'LEVEL TRANSMITTER',
                                        'LY': 'LEVEL RELAY',
                                        'PC': 'PRESSURE CONTROLLER',
                                        'PE': 'PRESSURE ELEMENT',
                                        'PI': 'PRESSURE INDICATOR',
                                        'PR': 'PRESSURE RECORDER',
                                        'PT': 'PRESSURE TRANSMITTER',
                                        'PY': 'PRESSURE RELAY',
                                        'SI': 'SPEED INDICATOR',
                                        'ST': 'SPEED TRANSMITTER',
                                        'TC': 'TEMPERATURE CONTROLLER',
                                        'TE': 'TEMPERATURE ELEMENT',
                                        'TI': 'TEMPERATURE INDICATOR',
                                        'TR': 'TEMPERATURE RECORDER',
                                        'TT': 'TEMPERATURE TRANSMITTER',
                                        'TY': 'TEMPERATURE RELAY',
                                        'VT': 'VIBRATION TRANSMITTER',
                                        'WI': 'WEIGHT INDICATOR',
                                        'WT': 'WEIGHT TRANSMITTER',
                                        'YI': 'EVENT INDICATOR',
                                        'ZC': 'POSITION CONTROLLER',
                                        'ZI': 'POSITION INDICATOR',
                                        'ZT': 'POSITION TRANSMITTER',
                                        'AIC': 'ANALYSIS INDICATING CONTROLLER',
                                        'ARC': 'ANALYSIS RECORDING CONTROLLER',
                                        'ASH': 'ANALYSIS SWITCH HIGH',
                                        'ASL': 'ANALYSIS SWITCH LOW',
                                        'FCV': 'FLOW CONTROL VALVE',
                                        'FIC': 'FLOW INDICATING CONTROLLER',
                                        'FRC': 'FLOW RECORDING CONTROLLER',
                                        'FSH': 'FLOW SWITCH HIGH',
                                        'FSL': 'FLOW SWITCH LOW',
                                        'HIC': 'HAND INDICATING CONTROLLER',
                                        'LCV': 'LEVEL CONTROL VALVE',
                                        'LIC': 'LEVEL INDICATING CONTROLLER',
                                        'LRC': 'LEVEL RECORDING CONTROLLER',
                                        'LSH': 'LEVEL SWITCH HIGH',
                                        'LSL': 'LEVEL SWITCH LOW',
                                        'PCV': 'PRESSURE CONTROL VALVE',
                                        'PIC': 'PRESSURE INDICATING CONTROLLER',
                                        'PRC': 'PRESSURE RECORDING CONTROLLER',
                                        'PSH': 'PRESSURE SWITCH HIGH',
                                        'PSL': 'PRESSURE SWITCH LOW',
                                        'PSV': 'PRESSURE SAFETY VALVE',
                                        'SIC': 'SPEED INDICATING CONTROLLER',
                                        'SSH': 'SPEED SWITCH HIGH',
                                        'SSL': 'SPEED SWITCH LOW',
                                        'TCV': 'TEMPERATURE CONTROL VALVE',
                                        'TIC': 'TEMPERATURE INDICATING CONTROLLER',
                                        'TRC': 'TEMPERATURE RECORDING CONTROLLER',
                                        'TSH': 'TEMPERATURE SWITCH HIGH',
                                        'TSL': 'TEMPERATURE SWITCH LOW',
                                        'VSH': 'VIBRATION SWITCH HIGH',
                                        'VSL': 'VIBRATION SWITCH LOW',
                                        'WIC': 'WEIGHT INDICATING CONTROLLER',
                                        'WSH': 'WEIGHT SWITCH HIGH',
                                        'WSL': 'WEIGHT SWITCH LOW',
                                        'YIC': 'EVENT INDICATING CONTROLLER',
                                        'YSH': 'EVENT SWITCH HIGH',
                                        'YSL': 'EVENT SWITCH LOW',
                                        'ZCV': 'POSITION CONTROL VALVE',
                                        'ZIC': 'POSITION INDICATING CONTROLLER',
                                        'ZSH': 'POSITION SWITCH HIGH',
                                        'ZSL': 'POSITION SWITCH LOW',
                                        'FAHH': 'FLOW ALARM HIGH HIGH',
                                        'FALL': 'FLOW ALARM LOW LOW',
                                        'LAHH': 'LEVEL ALARM HIGH HIGH',
                                        'LALL': 'LEVEL ALARM LOW LOW',
                                        'PAHH': 'PRESSURE ALARM HIGH HIGH',
                                        'PALL': 'PRESSURE ALARM LOW LOW',
                                        'TAHH': 'TEMPERATURE ALARM HIGH HIGH',
                                        'TALL': 'TEMPERATURE ALARM LOW LOW'}}],
                 'separator': '-'}},
 {'section': 'instrument_bubbles',
  'name': 'Instrument Bubbles — Instrument Bubble Type (default)',
  'description': 'General instrument bubble type matched directly against Legend (picture and '
                 'description columns, full name used as both the tag and the lookup value).',
  'definition': {'fields': [{'key': 'instrument_bubble_type',
                             'label': 'Instrument Bubble Type',
                             'notes': 'General instrument bubble type matched against Legend. 12 '
                                      'standard symbols across 4 types and 3 locations, plus 3 '
                                      'behind-panel variants.',
                             'regex': '[A-Z0-9 /()&,-]+',
                             'lookup': {'COMPUTER FUNCTION (BEHIND PANEL)': 'COMPUTER FUNCTION '
                                                                            '(BEHIND PANEL)',
                                        'COMPUTER FUNCTION (FIELD MOUNTED)': 'COMPUTER FUNCTION '
                                                                             '(FIELD MOUNTED)',
                                        'DISCRETE INSTRUMENTS (BEHIND PANEL)': 'DISCRETE '
                                                                               'INSTRUMENTS '
                                                                               '(BEHIND PANEL)',
                                        'COMPUTER FUNCTION (PRIMARY LOCATION)': 'COMPUTER FUNCTION '
                                                                                '(PRIMARY '
                                                                                'LOCATION)',
                                        'DISCRETE INSTRUMENTS (FIELD MOUNTED)': 'DISCRETE '
                                                                                'INSTRUMENTS '
                                                                                '(FIELD MOUNTED)',
                                        'COMPUTER FUNCTION (AUXILIARY LOCATION)': 'COMPUTER '
                                                                                  'FUNCTION '
                                                                                  '(AUXILIARY '
                                                                                  'LOCATION)',
                                        'DISCRETE INSTRUMENTS (PRIMARY LOCATION)': 'DISCRETE '
                                                                                   'INSTRUMENTS '
                                                                                   '(PRIMARY '
                                                                                   'LOCATION)',
                                        'DISCRETE INSTRUMENTS (AUXILIARY LOCATION)': 'DISCRETE '
                                                                                     'INSTRUMENTS '
                                                                                     '(AUXILIARY '
                                                                                     'LOCATION)',
                                        'PROGRAMMABLE LOGIC CONTROL (BEHIND PANEL)': 'PROGRAMMABLE '
                                                                                     'LOGIC '
                                                                                     'CONTROL '
                                                                                     '(BEHIND '
                                                                                     'PANEL)',
                                        'PROGRAMMABLE LOGIC CONTROL (FIELD MOUNTED)': 'PROGRAMMABLE '
                                                                                      'LOGIC '
                                                                                      'CONTROL '
                                                                                      '(FIELD '
                                                                                      'MOUNTED)',
                                        'PROGRAMMABLE LOGIC CONTROL (PRIMARY LOCATION)': 'PROGRAMMABLE '
                                                                                         'LOGIC '
                                                                                         'CONTROL '
                                                                                         '(PRIMARY '
                                                                                         'LOCATION)',
                                        'SHARED DISPLAY SHARED CONTROL (FIELD MOUNTED)': 'SHARED '
                                                                                         'DISPLAY '
                                                                                         'SHARED '
                                                                                         'CONTROL '
                                                                                         '(FIELD '
                                                                                         'MOUNTED)',
                                        'PROGRAMMABLE LOGIC CONTROL (AUXILIARY LOCATION)': 'PROGRAMMABLE '
                                                                                           'LOGIC '
                                                                                           'CONTROL '
                                                                                           '(AUXILIARY '
                                                                                           'LOCATION)',
                                        'SHARED DISPLAY SHARED CONTROL (PRIMARY LOCATION)': 'SHARED '
                                                                                            'DISPLAY '
                                                                                            'SHARED '
                                                                                            'CONTROL '
                                                                                            '(PRIMARY '
                                                                                            'LOCATION)',
                                        'SHARED DISPLAY SHARED CONTROL (AUXILIARY LOCATION)': 'SHARED '
                                                                                              'DISPLAY '
                                                                                              'SHARED '
                                                                                              'CONTROL '
                                                                                              '(AUXILIARY '
                                                                                              'LOCATION)'}}],
                 'separator': '-'}},
 {'section': 'instrument_symbols',
  'name': 'Instruments — Symbol Type (default)',
  'description': 'Instrument symbol matched directly against Legend',
  'definition': {'fields': [{'key': 'instrument_symbol',
                             'label': 'Instrument Symbol',
                             'notes': 'Instrument symbol matched directly against Legend',
                             'regex': '[A-Z0-9 /()&-]+',
                             'lookup': {'LEVEL GAUGE': 'LEVEL GAUGE',
                                        'PILOT LIGHT': 'PILOT LIGHT',
                                        'ORIFICE PLATE': 'ORIFICE PLATE',
                                        'PIG SIGNALLER': 'PIG SIGNALLER',
                                        'ACTUATOR RESET': 'ACTUATOR RESET',
                                        'MASS FLOWMETER': 'MASS FLOWMETER',
                                        'LOCALLY MOUNTED': 'LOCALLY MOUNTED',
                                        'TURBINE FLOWMETER': 'TURBINE FLOWMETER',
                                        'VORTEX FLOW METER': 'VORTEX FLOW METER',
                                        'EMERGENCY SHUTDOWN': 'EMERGENCY SHUTDOWN',
                                        'SHUTDOWN SIGNAL IN': 'SHUTDOWN SIGNAL IN',
                                        'SHUTDOWN SIGNAL OUT': 'SHUTDOWN SIGNAL OUT',
                                        'SIGNAL TO TELEMETRY': 'SIGNAL TO TELEMETRY',
                                        'ULTRASONIC FLOWMETER': 'ULTRASONIC FLOWMETER',
                                        'SELECTOR SWITCH - DCS': 'SELECTOR SWITCH - DCS',
                                        'SIGNAL FROM TELEMETRY': 'SIGNAL FROM TELEMETRY',
                                        'MOUNTED ON LOCAL PANEL': 'MOUNTED ON LOCAL PANEL',
                                        'ANALYSER ELEMENT(PROBE)': 'ANALYSER ELEMENT(PROBE)',
                                        'SELECTOR SWITCH - LOCAL': 'SELECTOR SWITCH - LOCAL',
                                        'ROTAMETER TYPE FLOW INDICATOR': 'ROTAMETER TYPE FLOW '
                                                                         'INDICATOR',
                                        'MOUNTED ON BACK OF LOCAL PANEL': 'MOUNTED ON BACK OF '
                                                                          'LOCAL PANEL',
                                        'POSITIVE DISPLACEMENT FLOWMETER': 'POSITIVE DISPLACEMENT '
                                                                           'FLOWMETER',
                                        'ORIFICE PLATE-QUICK CHANGE FITTIN': 'ORIFICE PLATE-QUICK '
                                                                             'CHANGE FITTIN',
                                        'MOUNTED ON MAIN CONTROL ROOM PANEL': 'MOUNTED ON MAIN '
                                                                              'CONTROL ROOM PANEL',
                                        'DISPLACEMENT TYPE LEVEL TRANSMITTER': 'DISPLACEMENT TYPE '
                                                                               'LEVEL TRANSMITTER',
                                        'FIRE AND GAS PANEL LOCAL PANEL MOUNTED': 'FIRE AND GAS '
                                                                                  'PANEL LOCAL '
                                                                                  'PANEL MOUNTED',
                                        'FIRE AND GAS PANEL CONTROL ROOM MOUNTED': 'FIRE AND GAS '
                                                                                   'PANEL CONTROL '
                                                                                   'ROOM MOUNTED',
                                        'DIFFERENTIAL PRESSURE TYPE LEVEL TRANSMITTER': 'DIFFERENTIAL '
                                                                                        'PRESSURE '
                                                                                        'TYPE '
                                                                                        'LEVEL '
                                                                                        'TRANSMITTER',
                                        'GENERALIZED FOR UNDEFINED OR COMPLEX INTERLOCK LOGIC': 'GENERALIZED '
                                                                                                'FOR '
                                                                                                'UNDEFINED '
                                                                                                'OR '
                                                                                                'COMPLEX '
                                                                                                'INTERLOCK '
                                                                                                'LOGIC',
                                        'DISTRIBUTED CONTROL SYSTEM TEMS NORMALLY NOT ACCESSIBLE TO THE OPERATOR': 'DISTRIBUTED '
                                                                                                                   'CONTROL '
                                                                                                                   'SYSTEM '
                                                                                                                   'TEMS '
                                                                                                                   'NORMALLY '
                                                                                                                   'NOT '
                                                                                                                   'ACCESSIBLE '
                                                                                                                   'TO '
                                                                                                                   'THE '
                                                                                                                   'OPERATOR',
                                        'PROGRAMMABLE LOGIC CONTROL SYSTEM ITEMS NORMALLY ACCESSIBLE TO THE OPERATOR': 'PROGRAMMABLE '
                                                                                                                       'LOGIC '
                                                                                                                       'CONTROL '
                                                                                                                       'SYSTEM '
                                                                                                                       'ITEMS '
                                                                                                                       'NORMALLY '
                                                                                                                       'ACCESSIBLE '
                                                                                                                       'TO '
                                                                                                                       'THE '
                                                                                                                       'OPERATOR',
                                        'PROGRAMMABLE LOGIC CONTROL SYSTEM ITEMS NORMALLY NOT ACCESSIBLE TO THE OPERATOR': 'PROGRAMMABLE '
                                                                                                                           'LOGIC '
                                                                                                                           'CONTROL '
                                                                                                                           'SYSTEM '
                                                                                                                           'ITEMS '
                                                                                                                           'NORMALLY '
                                                                                                                           'NOT '
                                                                                                                           'ACCESSIBLE '
                                                                                                                           'TO '
                                                                                                                           'THE '
                                                                                                                           'OPERATOR'}}],
                 'separator': '-'}},
 {'section': 'signal_types',
  'name': 'Signal Types — Signal Type (default)',
  'description': 'I/O signal type matched directly against Legend.',
  'definition': {'fields': [{'key': 'signal_type',
                             'label': 'Signal Type',
                             'notes': 'I/O signal type',
                             'regex': '[A-Z0-9][A-Z0-9 /]{1,20}',
                             'lookup': {'AI': 'ANALOG INPUT',
                                        'AO': 'ANALOG OUTPUT',
                                        'DI': 'DIGITAL INPUT',
                                        'DO': 'DIGITAL OUTPUT',
                                        'PI': 'PULSE INPUT',
                                        'PO': 'PULSE OUTPUT',
                                        'SI': 'SERIAL INPUT',
                                        'SO': 'SERIAL OUTPUT',
                                        'TC': 'THERMOCOUPLE',
                                        'PWM': 'PULSE WIDTH MODULATION',
                                        'RTD': 'RESISTANCE TEMPERATURE DETECTOR',
                                        'FREQ': 'FREQUENCY INPUT',
                                        '24VDC': 'DIGITAL INPUT',
                                        '110VAC': 'DIGITAL INPUT',
                                        '220VAC': 'DIGITAL INPUT',
                                        '24V DC': 'DIGITAL INPUT',
                                        '4-20MA': 'ANALOG INPUT',
                                        '0-20 MA': 'ANALOG INPUT',
                                        '4-20 MA': 'ANALOG INPUT',
                                        'POTENTIAL FREE': 'DIGITAL INPUT'}}],
                 'separator': '-'}},
 {'section': 'cabinet_locations',
  'name': 'Cabinet/Panel Locations — Location (default)',
  'description': 'Physical cabinet/panel location matched directly against Legend.',
  'definition': {'fields': [{'key': 'location',
                             'label': 'Cabinet/Panel Location',
                             'notes': 'Physical location of instrument',
                             'regex': '[A-Z0-9 /-]+',
                             'lookup': {'JB': 'JUNCTION BOX',
                                        'CCR': 'CENTRAL CONTROL ROOM',
                                        'DCS': 'DISTRIBUTED CONTROL SYSTEM',
                                        'ESD': 'EMERGENCY SHUTDOWN SYSTEM',
                                        'FGS': 'FIRE AND GAS SYSTEM',
                                        'LCP': 'LOCAL CONTROL PANEL',
                                        'LJB': 'LOCAL JUNCTION BOX',
                                        'MCC': 'MOTOR CONTROL CENTRE',
                                        'PCCR': 'PROCESS CONTROL COMPUTER ROOM',
                                        'HIPPS': 'HIGH INTEGRITY PRESSURE PROTECTION SYSTEM'}}],
                 'separator': '-'}},
 {'section': 'well_instrument_tagging',
  'name': 'Well Instrument Tagging — XX-XX-AAAA-XXX-X-XXXX-X (default)',
  'description': 'Well instrument tag numbering: area code, plant area code, instrument '
                 'classification code, well number, well completion code, sequence number, and '
                 'optional suffix.',
  'definition': {'fields': [{'key': 'area_code', 'label': 'Area Code', 'regex': '[A-Z0-9]{2}'},
                            {'key': 'plant_area_code',
                             'label': 'Plant Area Code',
                             'regex': '[A-Z0-9]{2}'},
                            {'key': 'instrument_classification',
                             'label': 'Instrument Classification Code',
                             'regex': '[A-Z]{1,6}'},
                            {'key': 'well_number',
                             'label': 'Well Number',
                             'regex': '[A-Z0-9]{2,4}'},
                            {'key': 'well_completion_code',
                             'label': 'Well Completion Code',
                             'regex': '[A-Z0-9]{1,2}'},
                            {'key': 'sequence_number',
                             'label': 'Sequence Number',
                             'regex': '\\d{2,5}'},
                            {'key': 'suffix',
                             'label': 'Suffix',
                             'regex': '[A-Z]{1,2}',
                             'optional': True}],
                 'separator': '-'}},
 {'section': 'well_equipment_numbering',
  'name': 'Well Equipment Numbering — XX-XX-AAAA-XXX-X-XX (default)',
  'description': 'Well equipment numbering: area code, plant area code, equipment code, well '
                 'number, system completion code, and sequence number.',
  'definition': {'fields': [{'key': 'area_code', 'label': 'Area Code', 'regex': '[A-Z0-9]{2}'},
                            {'key': 'plant_area_code',
                             'label': 'Plant Area Code',
                             'regex': '[A-Z0-9]{2}'},
                            {'key': 'equipment_code',
                             'label': 'Equipment Code',
                             'regex': '[A-Z]{2,6}'},
                            {'key': 'well_number',
                             'label': 'Well Number',
                             'regex': '[A-Z0-9]{2,4}'},
                            {'key': 'system_completion_code',
                             'label': 'System Completion Code',
                             'regex': '[A-Z0-9]{1,2}'},
                            {'key': 'sequence_number',
                             'label': 'Sequence Number',
                             'regex': '\\d{2,4}'}],
                 'separator': '-'}},
 {'section': 'line_numbering',
  'name': 'Line Numbering — XX-AA-XXXX-XXXXXX-X-X (default)',
  'description': 'Composite pipeline line tag: size, service code, serial number, specification, '
                 'and optional dep/deviation and insulation codes.',
  'definition': {'fields': [{'key': 'size',
                             'label': 'Size (Inches)',
                             'regex': '\\d{1,3}(?:/\\d)?',
                             'suffix': '"'},
                            {'key': 'service_code', 'label': 'Service Code', 'regex': '[A-Z]{2,3}'},
                            {'key': 'serial_number',
                             'label': 'Serial No.',
                             'regex': '[A-Z0-9]{3,6}'},
                            {'key': 'specification',
                             'label': 'Specification',
                             'regex': '[A-Z0-9]{4,8}'},
                            {'key': 'dep_deviation',
                             'label': 'Dep. Deviation',
                             'regex': '[A-Z0-9]{1,2}',
                             'optional': True},
                            {'key': 'insulation',
                             'label': 'Insulation',
                             'regex': '[A-Z]',
                             'lookup': {'C': 'THERMAL COLD',
                                        'H': 'THERMAL HOT',
                                        'P': 'PERSONNEL PROTECTION'},
                             'optional': True}],
                 'separator': '-'}},
 {'section': 'main_equipment',
  'name': 'Main Equipment — Equipment Type (default)',
  'description': 'Main equipment type matched directly against Legend. Symbol images to be '
                 'uploaded manually.',
  'definition': {'fields': [{'key': 'equipment_type',
                             'label': 'Equipment Type',
                             'notes': 'Main equipment type matched against Legend. Symbol images '
                                      'to be uploaded manually.',
                             'regex': '[A-Z0-9 /()-]+',
                             'lookup': {'WELLHEAD': 'WELLHEAD',
                                        'COMPRESSOR': 'COMPRESSOR',
                                        'FLARE STACK': 'FLARE STACK',
                                        'METERING PUMP': 'METERING PUMP',
                                        'BURN PIT NOZZLE': 'BURN PIT NOZZLE',
                                        'VESSEL - BASIC SYMBOL': 'VESSEL - BASIC SYMBOL',
                                        'PIG LAUNCHER / RECEIVER': 'PIG LAUNCHER / RECEIVER',
                                        'TEST / PRODUCTION SEPARATOR': 'TEST / PRODUCTION '
                                                                       'SEPARATOR',
                                        'CENTRIFUGAL PUMP (MOTOR DRIVEN)': 'CENTRIFUGAL PUMP '
                                                                           '(MOTOR DRIVEN)',
                                        'RECIPROCATING PUMP (MOTOR DRIVEN)': 'RECIPROCATING PUMP '
                                                                             '(MOTOR DRIVEN)',
                                        'CENTRIFUGAL PUMP (VERTICAL TYPE) ELECTRIC MOTOR DRIVEN': 'CENTRIFUGAL '
                                                                                                  'PUMP '
                                                                                                  '(VERTICAL '
                                                                                                  'TYPE) '
                                                                                                  'ELECTRIC '
                                                                                                  'MOTOR '
                                                                                                  'DRIVEN',
                                        'CENTRIFUGAL PUMP (SUBMERGED SUCTION) ELECTRIC MOTOR DRIVEN': 'CENTRIFUGAL '
                                                                                                      'PUMP '
                                                                                                      '(SUBMERGED '
                                                                                                      'SUCTION) '
                                                                                                      'ELECTRIC '
                                                                                                      'MOTOR '
                                                                                                      'DRIVEN'}}],
                 'separator': '-'}},
 {'section': 'inline_equipment',
  'name': 'In Line Equipment — In Line Equipment Type (default)',
  'description': 'In line equipment type matched directly against Legend.',
  'definition': {'fields': [{'key': 'inline_equipment_type',
                             'label': 'In Line Equipment Type',
                             'notes': 'In line equipment type matched against Legend.',
                             'regex': '[A-Z0-9 /()"-]+',
                             'lookup': {'SPADE': 'SPADE',
                                        'MONITOR': 'MONITOR',
                                        'BARRED TEE': 'BARRED TEE',
                                        'SIGHT GLASS': 'SIGHT GLASS',
                                        'BLIND FLANGE': 'BLIND FLANGE',
                                        'FIRE HYDRANT': 'FIRE HYDRANT',
                                        'FUSIBLE PLUG': 'FUSIBLE PLUG',
                                        'H2S DETECTOR': 'H2S DETECTOR',
                                        'FLEXIBLE HOSE': 'FLEXIBLE HOSE',
                                        'FLAME ARRESTOR': 'FLAME ARRESTOR',
                                        'VORTEX BREAKER': 'VORTEX BREAKER',
                                        'CORROSION PROBE': 'CORROSION PROBE',
                                        'HOSE CONNECTION': 'HOSE CONNECTION',
                                        'SPECIALITY ITEM': 'SPECIALITY ITEM',
                                        'CORROSION COUPON': 'CORROSION COUPON',
                                        'IN LINE ANALYSER': 'IN LINE ANALYSER',
                                        'CAP (BUTT WELDED)': 'CAP (BUTT WELDED)',
                                        'CORROSION MONITOR': 'CORROSION MONITOR',
                                        'INSULATING FLANGE': 'INSULATING FLANGE',
                                        'SAMPLE CONNECTION': 'SAMPLE CONNECTION',
                                        'STRAINER "Y" TYPE': 'STRAINER "Y" TYPE',
                                        'GAS INJECTION WELL': 'GAS INJECTION WELL',
                                        'GAS PRODUCING WELL': 'GAS PRODUCING WELL',
                                        'SPOOL PIECE FLANGED': 'SPOOL PIECE FLANGED',
                                        'UV/IR FIRE DETECTOR': 'UV/IR FIRE DETECTOR',
                                        'BASKET TYPE STRAINER': 'BASKET TYPE STRAINER',
                                        'HYDROCARBON DETECTOR': 'HYDROCARBON DETECTOR',
                                        'SPACER (RING SPACER)': 'SPACER (RING SPACER)',
                                        'FILTER (BASIC SYMBOL)': 'FILTER (BASIC SYMBOL)',
                                        'STRAINER BASIC SYMBOL': 'STRAINER BASIC SYMBOL',
                                        'SPECTACLE BLIND (OPEN)': 'SPECTACLE BLIND (OPEN)',
                                        'FLOW STRAIGHTENING VANES': 'FLOW STRAIGHTENING VANES',
                                        'MECHANICAL PIG SIGNALLER': 'MECHANICAL PIG SIGNALLER',
                                        'SPECTACLE BLIND (CLOSED)': 'SPECTACLE BLIND (CLOSED)',
                                        'MONOBLOCK INSULATING JOINT': 'MONOBLOCK INSULATING JOINT',
                                        'TUNDISH DRAIN OR OPEN DRAIN': 'TUNDISH DRAIN OR OPEN '
                                                                       'DRAIN',
                                        'CONCENTRIC REDUCER (BUTT WELDED)': 'CONCENTRIC REDUCER '
                                                                            '(BUTT WELDED)',
                                        'VACUUM BREAKER OR BREATHER VALVE': 'VACUUM BREAKER OR '
                                                                            'BREATHER VALVE',
                                        'CHEMICAL INHIBITOR INJECTION POINT': 'CHEMICAL INHIBITOR '
                                                                              'INJECTION POINT',
                                        'TIE-IN TO EXISTING LINE OR EQUIPMENT': 'TIE-IN TO '
                                                                                'EXISTING LINE OR '
                                                                                'EQUIPMENT',
                                        'ECCENTRIC REDUCER (BUTT WELDED) TOP FLAT': 'ECCENTRIC '
                                                                                    'REDUCER (BUTT '
                                                                                    'WELDED) TOP '
                                                                                    'FLAT',
                                        'ECCENTRIC REDUCER (BUTT WELDED) BOTTOM FLAT': 'ECCENTRIC '
                                                                                       'REDUCER '
                                                                                       '(BUTT '
                                                                                       'WELDED) '
                                                                                       'BOTTOM '
                                                                                       'FLAT'}}],
                 'separator': '-'}}]
