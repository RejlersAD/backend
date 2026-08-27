"""Soft-coded default Legend-Sheet templates for P&ID Checker V2.

Users can load a section's default template as a starting point and edit it,
or upload/paste their own. The templates encode the rules in the same JSON
shape that `PidCheckerV2LegendSheet.definition` stores.

Add new sections by adding a new key to DEFAULT_TEMPLATES and registering
its section id in SECTIONS.
"""
from __future__ import annotations

# Registered sections (extend here as new document types are added)
SECTION_LINE_LIST = 'line_list'
SECTION_EQUIPMENT_LIST = 'equipment_list'
SECTION_INSTRUMENT_INDEX = 'instrument_index'
SECTION_VALVE = 'valve'
SECTION_PIPING = 'piping'
SECTION_ACTUATOR_SYMBOLS = 'actuator_symbols'
SECTION_FLOW_DETECTOR = 'flow_detector'
SECTION_CONTROL_VALVE_REGULATOR = 'control_valve_regulator'
SECTION_INSTRUMENT_SIGNAL = 'instrument_signal'
SECTION_EQUIPMENT_SYMBOLS = 'equipment_symbols'
SECTION_INSTRUMENT_FUNCTION = 'instrument_function'
SECTION_PIPE_CONNECTION = 'pipe_connection'
SECTION_PIPE_END = 'pipe_end'
SECTION_SPECIAL_PIPING = 'special_piping'
SECTION_SCOPE_SYMBOLS = 'scope_symbols'
SECTION_MISCELLANEOUS = 'miscellaneous'
SECTION_LIMIT_LINE = 'limit_line'
SECTION_OTHER_SPECIALTIES = 'other_specialties'
SECTION_INSTRUMENT_TYPICAL_LETTER = 'instrument_typical_letter'
SECTION_DRAWING_CONT = 'drawing_cont'
SECTION_GENERAL_INSTRUMENT = 'general_instrument'

SECTIONS = (
    SECTION_LINE_LIST,
    SECTION_EQUIPMENT_LIST,
    SECTION_INSTRUMENT_INDEX,
    SECTION_VALVE,
    SECTION_PIPING,
    SECTION_ACTUATOR_SYMBOLS,
    SECTION_FLOW_DETECTOR,
    SECTION_CONTROL_VALVE_REGULATOR,
    SECTION_INSTRUMENT_SIGNAL,
    SECTION_EQUIPMENT_SYMBOLS,
    SECTION_INSTRUMENT_FUNCTION,
    SECTION_PIPE_CONNECTION,
    SECTION_PIPE_END,
    SECTION_SPECIAL_PIPING,
    SECTION_SCOPE_SYMBOLS,
    SECTION_MISCELLANEOUS,
    SECTION_LIMIT_LINE,
    SECTION_OTHER_SPECIALTIES,
    SECTION_INSTRUMENT_TYPICAL_LETTER,
    SECTION_DRAWING_CONT,
    SECTION_GENERAL_INSTRUMENT,
)

SECTION_LABELS = {
    SECTION_LINE_LIST: 'Line List',
    SECTION_EQUIPMENT_LIST: 'Equipment List',
    SECTION_INSTRUMENT_INDEX: 'Instrument Index',
    SECTION_VALVE: 'Valve',
    SECTION_PIPING: 'Piping',
    SECTION_ACTUATOR_SYMBOLS: 'Actuator Symbols',
    SECTION_FLOW_DETECTOR: 'Flow Detector',
    SECTION_CONTROL_VALVE_REGULATOR: 'Control Valve & Regulator',
    SECTION_INSTRUMENT_SIGNAL: 'Instrument Signal',
    SECTION_EQUIPMENT_SYMBOLS: 'Equipment Symbols',
    SECTION_INSTRUMENT_FUNCTION: 'Instrument Function',
    SECTION_PIPE_CONNECTION: 'Pipe Connection',
    SECTION_PIPE_END: 'Pipe End',
    SECTION_SPECIAL_PIPING: 'Special Piping',
    SECTION_SCOPE_SYMBOLS: 'Scope Symbols',
    SECTION_MISCELLANEOUS: 'Miscellaneous',
    SECTION_LIMIT_LINE: 'Limit Line',
    SECTION_OTHER_SPECIALTIES: 'Other Specialties',
    SECTION_INSTRUMENT_TYPICAL_LETTER: 'Instrument Typical Letter',
    SECTION_DRAWING_CONT: 'Drawing Continuations',
    SECTION_GENERAL_INSTRUMENT: 'General Instrument or Function Symbols',
}


DEFAULT_TEMPLATES: dict[str, dict] = {
    SECTION_LINE_LIST: {
        'name': 'Line List — XX-XX-XXXX-XXXX-X (default)',
        'description': (
            'Composite pipeline line tag: pipe size, service identifier, '
            'line classification, line number and (optional) insulation class.'
        ),
        'definition': {
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
                    'lookup': {
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
                        'G':  'GAS HYDROCARBON VAPOR',
                        'GL': 'GLYCOL',
                        'HC': 'HYDROCARBON LIQUID',
                        'HL': 'SODIUM HYPO-CHLORITE',
                        'IA': 'INSTRUMENT AIR',
                        'IW': 'INJECTION WATER',
                        'ME': 'METHANOL',
                        'N2': 'NITROGEN GAS',
                        'NG': 'NATURAL GAS',
                        'OW': 'OILY WATER',
                        'P':  'CRUDE OIL',
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
                    },
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
                    'lookup': {
                        'C': 'COLD CONSERVATION',
                        'H': 'HEAT CONSERVATION',
                        'P': 'PERSONAL PROTECTION',
                        'T': 'TRACING',
                    },
                },
            ],
        },
    },
    SECTION_EQUIPMENT_LIST: {
        'name': 'Equipment List — XX-XXX-XX (default)',
        'description': (
            'Equipment numbering system: item symbol, equipment sequence '
            'number and site/platform symbol (e.g. V-803-TF, P-101A-CF).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'item_symbol',
                    'label': 'Item Symbol',
                    'regex': r'[A-Z]{1,3}',
                    'notes': 'Equipment type prefix (1–3 letters)',
                    'lookup': {
                        'B':  'BLOWER',
                        'C':  'COMPRESSOR/BLOWER',
                        'CP': 'CALIBRATION POT',
                        'D':  'DRYER',
                        'E':  'EXCHANGER/COOLER/REBOILER/RECLAIMER',
                        'F':  'FILTER',
                        'FL': 'FILTER VESSEL',
                        'H':  'HEATER',
                        'HD': 'HEADER',
                        'M':  'MOTOR',
                        'MG': 'GENERATOR',
                        'MX': 'MIXER',
                        'N':  'NITROGEN GENERATOR',
                        'P':  'PUMP',
                        'PX': 'PACKAGE',
                        'S':  'COMPRESSOR STAGE',
                        'SG': 'SIGHT GLASS',
                        'T':  'TANK, PIT',
                        'TR': 'TRANSFORMER',
                        'V':  'VESSEL, DRUM, COLUMN',
                        'W':  'WELL',
                        'WM': 'WATER MAKER',
                    },
                },
                {
                    'key': 'sequence',
                    'label': 'Equipment Sequence Number',
                    'regex': r'\d{2,4}[A-Z]?',
                    'notes': (
                        'Sequence number (2–4 digits) with optional trailing '
                        'letter suffix for parallel/duplicate items (e.g. 803, 101A).'
                    ),
                },
                {
                    'key': 'site_symbol',
                    'label': 'Site Symbol',
                    'regex': r'[A-Z]{2}',
                    'optional': True,
                    'notes': 'Site / platform / jacket identifier (2 letters)',
                    'lookup': {
                        'CF': 'CFP',
                        'HF': 'HAIL FIELD / HAIL SITE TERMINAL',
                        'TF': 'MUBARRAZ ISLAND',
                        'AA': 'PRODUCTION PLATFORM / JACKET (AA)',
                        'BA': 'PRODUCTION PLATFORM / JACKET (BA)',
                        'BB': 'PRODUCTION PLATFORM / JACKET (BB)',
                        'BC': 'PRODUCTION PLATFORM / JACKET (BC)',
                        'BD': 'PRODUCTION PLATFORM / JACKET (BD)',
                        'BF': 'PRODUCTION PLATFORM / JACKET (BF)',
                        'CA': 'PRODUCTION PLATFORM / JACKET (CA)',
                        'CD': 'PRODUCTION PLATFORM / JACKET (CD)',
                    },
                },
            ],
        },
    },
    SECTION_INSTRUMENT_INDEX: {
        'name': 'Instrument Index — XX-NNNN[A] SS (default)',
        'description': (
            'Instrument tag numbering (ISA-5.1 style): function code, '
            'loop/sequence number with optional letter suffix, and site '
            'symbol (e.g. LT-8019 TF, PT-8003A TF, PCV-8004B TF).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'function_code',
                    'label': 'Function Code',
                    'regex': r'[A-Z]{1,4}',
                    'notes': 'ISA-5.1 measured-variable + modifier + readout letters (1–4 letters)',
                    'lookup': {
                        'BDHS': 'BLOWDOWN HAND SWITCH (LOCAL PUSH BUTTON)',
                        'BDV':  'BLOWDOWN VALVE',
                        'BDY':  'BLOWDOWN SOLENOID / RELAY',
                        'BPG':  'BLOWDOWN PRESSURE GAUGE',
                        'BPSV': 'BLOWDOWN PRESSURE SAFETY VALVE',
                        'BZSC': 'BLOWDOWN POSITION SWITCH — CLOSED',
                        'BZSO': 'BLOWDOWN POSITION SWITCH — OPEN',
                        'FE':   'FLOW ELEMENT',
                        'FIT':  'FLOW INDICATING TRANSMITTER',
                        'FT':   'FLOW TRANSMITTER',
                        'FV':   'FLOW CONTROL VALVE',
                        'FCV':  'FLOW CONTROL VALVE',
                        'HCV':  'HAND CONTROL VALVE',
                        'HS':   'HAND SWITCH',
                        'HY':   'HAND CONVERTER / RELAY',
                        'HZT':  'HAND VALVE POSITION TRANSMITTER',
                        'LG':   'LEVEL GAUGE',
                        'LI':   'LEVEL INDICATOR',
                        'LT':   'LEVEL TRANSMITTER',
                        'LV':   'LEVEL CONTROL VALVE',
                        'LCV':  'LEVEL CONTROL VALVE',
                        'LY':   'LEVEL CONVERTER / POSITIONER',
                        'PCV':  'PRESSURE CONTROL VALVE',
                        'PG':   'PRESSURE GAUGE',
                        'PI':   'PRESSURE INDICATOR',
                        'PSV':  'PRESSURE SAFETY VALVE',
                        'PT':   'PRESSURE TRANSMITTER',
                        'PY':   'PRESSURE CONVERTER / POSITIONER',
                        'RO':   'RESTRICTION ORIFICE',
                        'SDHS': 'SHUTDOWN HAND SWITCH',
                        'SDV':  'SHUTDOWN VALVE',
                        'SDY':  'SHUTDOWN SOLENOID / RELAY',
                        'SHY':  'SHUTDOWN HAND CONVERTER / RELAY',
                        'SPG':  'SHUTDOWN PRESSURE GAUGE',
                        'SPSV': 'SHUTDOWN PRESSURE SAFETY VALVE',
                        'SXA':  'SHUTDOWN ALARM',
                        'SZCA': 'SHUTDOWN POSITION CONFIRM ALARM',
                        'SZSC': 'SHUTDOWN POSITION SWITCH — CLOSED',
                        'SZSO': 'SHUTDOWN POSITION SWITCH — OPEN',
                        'TG':   'TEMPERATURE GAUGE',
                        'TI':   'TEMPERATURE INDICATOR',
                        'TT':   'TEMPERATURE TRANSMITTER',
                        'TW':   'THERMOWELL',
                        'XV':   'ON/OFF ISOLATION VALVE',
                    },
                },
                {
                    'key': 'loop_number',
                    'label': 'Loop / Sequence Number',
                    'regex': r'\d{3,4}[A-Z]?',
                    'notes': (
                        'Loop number (3–4 digits) with optional trailing '
                        'letter suffix for parallel or A/B trains (e.g. 8003A, 8004B).'
                    ),
                },
                {
                    'key': 'site_symbol',
                    'label': 'Site Symbol',
                    'regex': r'[A-Z]{2}',
                    'optional': True,
                    'notes': (
                        'Site / platform / jacket identifier (2 letters). '
                        "May appear space-separated ('LT-8019 TF') or "
                        "joined ('PT-8003ATF')."
                    ),
                    'lookup': {
                        'CF': 'CFP',
                        'HF': 'HAIL FIELD / HAIL SITE TERMINAL',
                        'TF': 'MUBARRAZ ISLAND',
                        'AA': 'PRODUCTION PLATFORM / JACKET (AA)',
                        'BA': 'PRODUCTION PLATFORM / JACKET (BA)',
                        'BB': 'PRODUCTION PLATFORM / JACKET (BB)',
                        'BC': 'PRODUCTION PLATFORM / JACKET (BC)',
                        'BD': 'PRODUCTION PLATFORM / JACKET (BD)',
                        'BF': 'PRODUCTION PLATFORM / JACKET (BF)',
                        'CA': 'PRODUCTION PLATFORM / JACKET (CA)',
                        'CD': 'PRODUCTION PLATFORM / JACKET (CD)',
                    },
                },
            ],
        },
    },
    SECTION_VALVE: {
        'name': 'Valve — Valve Type (default)',
        'description': (
            'Valve type, matched directly against Legend.xlsx (no Symbol '
            'column / short code in the source sheet, so the full valve '
            'name is used as both the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'valve_type',
                    'label': 'Valve Type',
                    'regex': r'[A-Z0-9 ()&]+',
                    'notes': (
                        'Valve type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'FLANGED END VALVE': 'FLANGED END VALVE',
                        'SCREWED END VALVE': 'SCREWED END VALVE',
                        'WELD END VALVE': 'WELD END VALVE',
                        'GLOBE VALVE (NORMAL OPEN)': 'GLOBE VALVE (NORMAL OPEN)',
                        'GLOBE VALVE (NORMAL CLOSE)': 'GLOBE VALVE (NORMAL CLOSE)',
                        'GATE VALVE (NORMAL OPEN)': 'GATE VALVE (NORMAL OPEN)',
                        'GATE VALVE (NORMAL CLOSE)': 'GATE VALVE (NORMAL CLOSE)',
                        'BALL VALVE (NORMAL OPEN)': 'BALL VALVE (NORMAL OPEN)',
                        'BALL VALVE (NORMAL CLOSE)': 'BALL VALVE (NORMAL CLOSE)',
                        'NEEDLE VALVE (NORMAL OPEN)': 'NEEDLE VALVE (NORMAL OPEN)',
                        'NEEDLE VALVE (NORMAL CLOSE)': 'NEEDLE VALVE (NORMAL CLOSE)',
                        'BUTTERFLY VALVE': 'BUTTERFLY VALVE',
                        'CHECK VALVE': 'CHECK VALVE',
                        'ADJUSTABLE CHOKE': 'ADJUSTABLE CHOKE',
                        'ROTARY CHOKE': 'ROTARY CHOKE',
                        'MULTIPLE ORIFICE VALVE (CHOKE VALVE)': 'MULTIPLE ORIFICE VALVE (CHOKE VALVE)',
                        'MIXING VALVE': 'MIXING VALVE',
                        'THREE WAY VALVE': 'THREE WAY VALVE',
                        'FOUR WAY VALVE': 'FOUR WAY VALVE',
                        'PRESSURE RELIEF VALVE': 'PRESSURE RELIEF VALVE',
                        'PRESSURE AND VACUUM RELIEF VALVE': 'PRESSURE AND VACUUM RELIEF VALVE',
                        'BREATHER VALVE': 'BREATHER VALVE',
                        'INTEGRAL DOUBLE BLOCK & BLEED VALVE': 'INTEGRAL DOUBLE BLOCK & BLEED VALVE',
                    },
                },
            ],
        },
    },
    SECTION_PIPING: {
        'name': 'Piping — Piping Type (default)',
        'description': (
            'Piping type, matched directly against Legend (no Symbol '
            'column / short code in the source sheet, so the full piping '
            'type name is used as both the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'piping_type',
                    'label': 'Piping Type',
                    'regex': r'[A-Z0-9 /()&]+',
                    'notes': (
                        'Piping type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'MAIN FLOW': 'MAIN FLOW',
                        'OTHERS FLOW': 'OTHERS FLOW',
                        'INSULATED PIPING WITH THICKNESS': 'INSULATED PIPING WITH THICKNESS',
                        'HEAT TRACE': 'HEAT TRACE',
                        'PRESSURE LEAD TUBING': 'PRESSURE LEAD TUBING',
                        'PIPING/SIGNAL JUNCTION': 'PIPING/SIGNAL JUNCTION',
                        'LINE ABOVE GROUND (A/G)': 'LINE ABOVE GROUND (A/G)',
                        'LINE UNDER GROUND (U/G)': 'LINE UNDER GROUND (U/G)',
                        'PIPING CLASS': 'PIPING CLASS',
                        'PIPING SPECIFICATION BREAK': 'PIPING SPECIFICATION BREAK',
                    },
                },
            ],
        },
    },
    SECTION_ACTUATOR_SYMBOLS: {
        'name': 'Actuator Symbols — Actuator Type (default)',
        'description': (
            'Actuator type, matched directly against Legend (no Symbol '
            'column / short code in the source sheet, so the full actuator '
            'type name is used as both the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'actuator_type',
                    'label': 'Actuator Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Actuator symbol matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'DIAPHRAGM SPRING-OPPOSED OR UNSPECIFIED ACTUATOR': 'DIAPHRAGM SPRING-OPPOSED OR UNSPECIFIED ACTUATOR',
                        'DIAPHRAGM PRESSURE-BALANCED': 'DIAPHRAGM PRESSURE-BALANCED',
                        'CYLINDER WITHOUT POSITIONER OR OTHER PILOT': 'CYLINDER WITHOUT POSITIONER OR OTHER PILOT',
                        'ROTARY MOTOR': 'ROTARY MOTOR',
                        'SOLENOID': 'SOLENOID',
                    },
                },
            ],
        },
    },
    SECTION_FLOW_DETECTOR: {
        'name': 'Flow Detector — Flow Detector Type (default)',
        'description': (
            'Flow detector type, matched directly against Legend (no Symbol '
            'column / short code in the source sheet, so the full type name '
            'is used as both the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'flow_detector_type',
                    'label': 'Flow Detector Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Flow detector type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'ORIFICE PLATE OR RESTRICTION ORIFICE': 'ORIFICE PLATE OR RESTRICTION ORIFICE',
                        'ORIFICE PLATE IN QUICK-CHANGE FITTING': 'ORIFICE PLATE IN QUICK-CHANGE FITTING',
                        'VENTURI TUBE OR FLOW NOZZLE': 'VENTURI TUBE OR FLOW NOZZLE',
                        'TURBINE TYPE FLOW METER': 'TURBINE TYPE FLOW METER',
                        'VOLTEX TYPE FLOW METER': 'VOLTEX TYPE FLOW METER',
                        'POSITIVE DISPLACEMENT TYPE FLOW METER': 'POSITIVE DISPLACEMENT TYPE FLOW METER',
                        'MAGNETIC FLOW METER': 'MAGNETIC FLOW METER',
                        'CORIOLIS FLOW METER': 'CORIOLIS FLOW METER',
                        'ULTRASONIC FLOW METER': 'ULTRASONIC FLOW METER',
                        'VARIABLE FLOW INDICATOR': 'VARIABLE FLOW INDICATOR',
                        'SIGHT GLASS': 'SIGHT GLASS',
                        'V-CONE FLOW METER': 'V-CONE FLOW METER',
                    },
                },
            ],
        },
    },
    SECTION_CONTROL_VALVE_REGULATOR: {
        'name': 'Control Valve & Regulator — Control Valve Type (default)',
        'description': (
            'Control valve type, matched directly against Legend (no Symbol '
            'column / short code in the source sheet, so the full type name '
            'is used as both the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'control_valve_type',
                    'label': 'Control Valve Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Control valve/regulator matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'CONTROL VALVE': 'CONTROL VALVE',
                        'CONTROL VALVE WITH HAND WHEEL': 'CONTROL VALVE WITH HAND WHEEL',
                        'CONTROL VALVE WITH ELECTRO-PNEUMATIC CONVERTER': 'CONTROL VALVE WITH ELECTRO-PNEUMATIC CONVERTER',
                        'PRESSURE REDUCING REGULATOR (SELF-CONTAINED)': 'PRESSURE REDUCING REGULATOR (SELF-CONTAINED)',
                        'PRESSURE REDUCING REGULATOR WITH EXTERNAL PRESSURE TAP': 'PRESSURE REDUCING REGULATOR WITH EXTERNAL PRESSURE TAP',
                        'TEMPERATURE REGULATOR (FILLED SYSTEM TYPE)': 'TEMPERATURE REGULATOR (FILLED SYSTEM TYPE)',
                        'LEVEL REGULATOR WITH MECHANICAL LINKAGE': 'LEVEL REGULATOR WITH MECHANICAL LINKAGE',
                        'CONTROL VALVE (ANGLE TYPE)': 'CONTROL VALVE (ANGLE TYPE)',
                    },
                },
            ],
        },
    },
    SECTION_INSTRUMENT_SIGNAL: {
        'name': 'Instrument Signal — Signal Type (default)',
        'description': (
            'Instrument signal type matched directly against Legend. This '
            'sheet has descriptions only (no picture/symbol column), so the '
            'full name is used as both the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'signal_type',
                    'label': 'Signal Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Instrument signal type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'CONNECTION TO PROCESS': 'CONNECTION TO PROCESS',
                        'INSTRUMENT AIR SUPPLY': 'INSTRUMENT AIR SUPPLY',
                        'AIR SUPPLY TUBING / PNEUMATIC SIGNAL TUBING': 'AIR SUPPLY TUBING / PNEUMATIC SIGNAL TUBING',
                        'ELECTRICAL SIGNAL': 'ELECTRICAL SIGNAL',
                        'CAPILLARY TUBING (FIELD SYSTEM)': 'CAPILLARY TUBING (FIELD SYSTEM)',
                        'HYDRAULIC SIGNAL': 'HYDRAULIC SIGNAL',
                        'ELECTROMAGNETIC OR SONIC SIGNAL (GUIDED)': 'ELECTROMAGNETIC OR SONIC SIGNAL (GUIDED)',
                        'ELECTROMAGNETIC OR SONIC SIGNAL (NOT GUIDED)': 'ELECTROMAGNETIC OR SONIC SIGNAL (NOT GUIDED)',
                        'SOFTWARE OR DATA LINK': 'SOFTWARE OR DATA LINK',
                    },
                },
            ],
        },
    },
    SECTION_EQUIPMENT_SYMBOLS: {
        'name': 'Equipment Symbols — Equipment Type (default)',
        'description': (
            'Equipment type matched directly against Legend (no short code '
            'in source sheet, so the full name is used as both the tag and '
            'the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'equipment_type',
                    'label': 'Equipment Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Equipment symbol matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'FLAT ROOF TANK': 'FLAT ROOF TANK',
                        'CONED FIXED ROOF TANK': 'CONED FIXED ROOF TANK',
                        'HORIZONTAL VESSEL': 'HORIZONTAL VESSEL',
                        'PIG RECEIVER': 'PIG RECEIVER',
                        'PIG LAUNCHER': 'PIG LAUNCHER',
                        'MANWAY (MW)': 'MANWAY (MW)',
                        'HANDHOLE (HH)': 'HANDHOLE (HH)',
                        'ELECTRIC MOTOR': 'ELECTRIC MOTOR',
                        'BLOWER': 'BLOWER',
                        'GEAR PUMP': 'GEAR PUMP',
                        'GEAR PUMP (DOUBLE)': 'GEAR PUMP (DOUBLE)',
                        'VERTICAL PUMP': 'VERTICAL PUMP',
                        'PERISTALTIC PUMP': 'PERISTALTIC PUMP',
                        'SHELL AND TUBE EXCHANGER': 'SHELL AND TUBE EXCHANGER',
                        'VERTICAL VESSEL (TOP BOLTED FLAT COVER TYPE)': 'VERTICAL VESSEL (TOP BOLTED FLAT COVER TYPE)',
                        'POSITIVE DISPLACEMENT PUMP': 'POSITIVE DISPLACEMENT PUMP',
                        'FILTER BASIC SYMBOL (PFD ONLY)': 'FILTER BASIC SYMBOL (PFD ONLY)',
                        'CENTRIFUGAL PUMP': 'CENTRIFUGAL PUMP',
                        'HEATER': 'HEATER',
                        'DEGASSING BOOT': 'DEGASSING BOOT',
                        'COMPRESSOR': 'COMPRESSOR',
                        'MIXER': 'MIXER',
                        'MIST ELIMINATOR': 'MIST ELIMINATOR',
                        'SCRUBBER': 'SCRUBBER',
                        'MULTIHEAD PUMP': 'MULTIHEAD PUMP',
                        'COOLER': 'COOLER',
                        'COLUMN': 'COLUMN',
                        'MEMBRANE': 'MEMBRANE',
                        'AIR COOLER': 'AIR COOLER',
                        'ELECTROLYTIC CELL': 'ELECTROLYTIC CELL',
                        'DISENGAGEMENT TANK': 'DISENGAGEMENT TANK',
                        'CARTRIDGE FILTER': 'CARTRIDGE FILTER',
                        'ACTIVATED CARBON FILTER': 'ACTIVATED CARBON FILTER',
                        'HORIZONTAL VESSEL WITH BOOT': 'HORIZONTAL VESSEL WITH BOOT',
                        'HORIZONTAL CARTIDGE FILTER': 'HORIZONTAL CARTIDGE FILTER',
                        'DOUBLE DIAPHRAM PUMP': 'DOUBLE DIAPHRAM PUMP',
                        'ELECTRIC HEATER': 'ELECTRIC HEATER',
                        'PIT': 'PIT',
                        'WILDEN PUMP (AIR DRIVEN)': 'WILDEN PUMP (AIR DRIVEN)',
                    },
                },
            ],
        },
    },
    SECTION_INSTRUMENT_FUNCTION: {
        'name': 'Instrument Function — Instrument Function (default)',
        'description': (
            'Instrument function type matched directly against Legend '
            '(picture and description columns, full name used as both '
            'the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'instrument_function_type',
                    'label': 'Instrument Function',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Instrument function matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'INTERLOCK LOGIC': 'INTERLOCK LOGIC',
                        'SUMMING': 'SUMMING',
                        'PCS INTERLOCK': 'PCS INTERLOCK',
                        'LOCAL STATUS LAMP': 'LOCAL STATUS LAMP',
                        'CURRENT-PNEUMATIC CONVERTER': 'CURRENT-PNEUMATIC CONVERTER',
                        'PUSH BUTTON': 'PUSH BUTTON',
                        'SWITCH': 'SWITCH',
                        'AMMETER': 'AMMETER',
                    },
                },
            ],
        },
    },
    SECTION_PIPE_CONNECTION: {
        'name': 'Pipe Connection — Pipe Connection Type (default)',
        'description': (
            'Pipe connection type matched directly against Legend '
            '(picture and description columns, full name used as both '
            'the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'pipe_connection_type',
                    'label': 'Pipe Connection Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Pipe connection type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'FLEXIBLE JOINT': 'FLEXIBLE JOINT',
                        'EXPANSION JOINT (BELLOW TYPE)': 'EXPANSION JOINT (BELLOW TYPE)',
                    },
                },
            ],
        },
    },
    SECTION_PIPE_END: {
        'name': 'Pipe End — Pipe End Type (default)',
        'description': (
            'Pipe end type matched directly against Legend (picture and '
            'description columns, full name used as both the tag and the '
            'lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'pipe_end_type',
                    'label': 'Pipe End Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Pipe end type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'BLIND PLUG': 'BLIND PLUG',
                        'BLIND CAP': 'BLIND CAP',
                        'BLIND FLANGE': 'BLIND FLANGE',
                    },
                },
            ],
        },
    },
    SECTION_SPECIAL_PIPING: {
        'name': 'Special Piping — Special Piping Type (default)',
        'description': (
            'Special piping type matched directly against Legend (picture '
            'and description columns, full name used as both the tag and '
            'the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'special_piping_type',
                    'label': 'Special Piping Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Special piping type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'SPADE': 'SPADE',
                        'SPACER': 'SPACER',
                        'SPECTACLE BLIND (OPEN)': 'SPECTACLE BLIND (OPEN)',
                        'SPECTACLE BLIND (CLOSE)': 'SPECTACLE BLIND (CLOSE)',
                        'REDUCER': 'REDUCER',
                        'BARRED TEE': 'BARRED TEE',
                        'INSULATION FLANGE / JOINT': 'INSULATION FLANGE / JOINT',
                        'ADAPTER FLANGE': 'ADAPTER FLANGE',
                        'FLOW STRAIGHTENING VANE': 'FLOW STRAIGHTENING VANE',
                        'REMOVAL SPOOL': 'REMOVAL SPOOL',
                        'PIPING SPECIALITY ITEM': 'PIPING SPECIALITY ITEM',
                        'INSULATION KIT': 'INSULATION KIT',
                        'VORTEX BREAKER': 'VORTEX BREAKER',
                        'MECHANICAL KEY TYPE INTERLOCK': 'MECHANICAL KEY TYPE INTERLOCK',
                    },
                },
            ],
        },
    },
    SECTION_SCOPE_SYMBOLS: {
        'name': 'Scope Symbols — Scope Symbol Type (default)',
        'description': 'Scope symbol type matched directly against Legend.',
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'scope_symbol_type',
                    'label': 'Scope Symbol Type',
                    'regex': r'[A-Z0-9 /()&"-]+',
                    'notes': (
                        'Scope symbol matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'SCOPE CLOUD': 'SCOPE CLOUD',
                        'HOLD CLOUD': 'HOLD CLOUD',
                        'DEMOLITION SCOPE': 'DEMOLITION SCOPE',
                        'MOTHBALLING SCOPE': 'MOTHBALLING SCOPE',
                        'REVISION CLOUD': 'REVISION CLOUD',
                        'TEMPORARY TIE-IN AND LINE': 'TEMPORARY TIE-IN AND LINE',
                        'TIE-IN POINT': 'TIE-IN POINT',
                        'VENDOR TIE-IN POINT': 'VENDOR TIE-IN POINT',
                        'IP': 'ISOLATION POINT',
                    },
                },
            ],
        },
    },
    SECTION_MISCELLANEOUS: {
        'name': 'Miscellaneous — Miscellaneous Type (default)',
        'description': (
            'Miscellaneous type matched directly against Legend (picture '
            'and description columns, full name used as both the tag and '
            'the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'miscellaneous_type',
                    'label': 'Miscellaneous Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Miscellaneous symbol matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'CALIBRATION POT': 'CALIBRATION POT',
                        'PULSATION DAMPENER': 'PULSATION DAMPENER',
                        'BIRD SCREEN': 'BIRD SCREEN',
                        'PUMP MOTOR': 'PUMP MOTOR',
                    },
                },
            ],
        },
    },
    SECTION_LIMIT_LINE: {
        'name': 'Limit Line — Limit Line Type (default)',
        'description': (
            'Limit line type matched directly against Legend (picture and '
            'description columns, full name used as both the tag and the '
            'lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'limit_line_type',
                    'label': 'Limit Line Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Limit line type matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'AREA LIMIT / PLATFORM LIMIT': 'AREA LIMIT / PLATFORM LIMIT',
                        'SKID LIMIT / UNIT LIMIT': 'SKID LIMIT / UNIT LIMIT',
                        'FUTURE FACILITY LIMIT': 'FUTURE FACILITY LIMIT',
                    },
                },
            ],
        },
    },
    SECTION_OTHER_SPECIALTIES: {
        'name': 'Other Specialties — Specialty Type (default)',
        'description': (
            'Other specialty type matched directly against Legend. Some '
            'items have short codes used in P&ID drawings.'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'specialty_type',
                    'label': 'Specialty Type',
                    'regex': r'[A-Z0-9 /()&:-]+',
                    'notes': (
                        'Specialty item matched directly against Legend sheet. '
                        'Full type name used as lookup value. Reference '
                        'symbol images can be uploaded manually for AI '
                        'Vision visual recognition.'
                    ),
                    'lookup': {
                        'FA': 'FLAME ARRESTER',
                        'FLAME ARRESTER': 'FLAME ARRESTER',
                        'INJECTOR': 'INJECTOR',
                        'BUCKET TYPE STRAINER': 'BUCKET TYPE STRAINER',
                        'Y-TYPE STRAINER': 'Y-TYPE STRAINER',
                        'T-TYPE STRAINER': 'T-TYPE STRAINER',
                        'ST': 'TEMPORARY STRAINER',
                        'TEMPORARY STRAINER': 'TEMPORARY STRAINER',
                        'FLOW STRAIGHTENING VANES': 'FLOW STRAIGHTENING VANES',
                        'DIAPHRAGM SEAL': 'DIAPHRAGM SEAL',
                        'STEAM TRAP': 'STEAM TRAP',
                        'AT': 'AIR TRAP',
                        'AIR TRAP': 'AIR TRAP',
                        'DT': 'DRAIN TRAP',
                        'DRAIN TRAP': 'DRAIN TRAP',
                        'DRAIN POT': 'DRAIN POT',
                        'OPEN DRAIN': 'OPEN DRAIN',
                        'CLOSED DRAIN': 'CLOSED DRAIN',
                        'FIRE HYDRANT': 'FIRE HYDRANT',
                        'OPEN VENT': 'OPEN VENT',
                        'HOSE CONNECTION': 'HOSE CONNECTION',
                        'FREE DRAINING': 'FREE DRAINING',
                        'SLOPE 1:100': 'SLOPE 1:100',
                    },
                },
            ],
        },
    },
    SECTION_INSTRUMENT_TYPICAL_LETTER: {
        'name': 'Instrument Typical Letter — Instrument Code (default)',
        'description': (
            'ISA-5.1 instrument letter combinations. First letter indicates '
            'measured variable, subsequent letters indicate function.'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'instrument_code',
                    'label': 'Instrument Code',
                    'regex': r'[A-Z]{1,4}',
                    'notes': (
                        'ISA-5.1 instrument letter combinations. First letter '
                        'indicates measured variable, subsequent letters '
                        'indicate function.'
                    ),
                    'lookup': {
                        'A': 'ANALYSIS',
                        'ARC': 'ANALYSIS RECORDING CONTROLLER',
                        'AIC': 'ANALYSIS INDICATING CONTROLLER',
                        'AC': 'ANALYSIS CONTROLLER',
                        'AR': 'ANALYSIS RECORDER',
                        'AI': 'ANALYSIS INDICATOR',
                        'ASH': 'ANALYSIS SWITCH HIGH',
                        'ASL': 'ANALYSIS SWITCH LOW',
                        'AT': 'ANALYSIS TRANSMITTER',
                        'AY': 'ANALYSIS RELAY',
                        'AE': 'ANALYSIS ELEMENT',
                        'F': 'FLOW RATE',
                        'FRC': 'FLOW RECORDING CONTROLLER',
                        'FIC': 'FLOW INDICATING CONTROLLER',
                        'FC': 'FLOW CONTROLLER',
                        'FCV': 'FLOW CONTROL VALVE',
                        'FR': 'FLOW RECORDER',
                        'FI': 'FLOW INDICATOR',
                        'FSH': 'FLOW SWITCH HIGH',
                        'FSL': 'FLOW SWITCH LOW',
                        'FT': 'FLOW TRANSMITTER',
                        'FY': 'FLOW RELAY',
                        'FE': 'FLOW ELEMENT',
                        'P': 'PRESSURE',
                        'PRC': 'PRESSURE RECORDING CONTROLLER',
                        'PIC': 'PRESSURE INDICATING CONTROLLER',
                        'PC': 'PRESSURE CONTROLLER',
                        'PCV': 'PRESSURE CONTROL VALVE',
                        'PR': 'PRESSURE RECORDER',
                        'PI': 'PRESSURE INDICATOR',
                        'PSH': 'PRESSURE SWITCH HIGH',
                        'PSL': 'PRESSURE SWITCH LOW',
                        'PT': 'PRESSURE TRANSMITTER',
                        'PY': 'PRESSURE RELAY',
                        'PE': 'PRESSURE ELEMENT',
                        'PSV': 'PRESSURE SAFETY VALVE',
                        'T': 'TEMPERATURE',
                        'TRC': 'TEMPERATURE RECORDING CONTROLLER',
                        'TIC': 'TEMPERATURE INDICATING CONTROLLER',
                        'TC': 'TEMPERATURE CONTROLLER',
                        'TCV': 'TEMPERATURE CONTROL VALVE',
                        'TR': 'TEMPERATURE RECORDER',
                        'TI': 'TEMPERATURE INDICATOR',
                        'TSH': 'TEMPERATURE SWITCH HIGH',
                        'TSL': 'TEMPERATURE SWITCH LOW',
                        'TT': 'TEMPERATURE TRANSMITTER',
                        'TY': 'TEMPERATURE RELAY',
                        'TE': 'TEMPERATURE ELEMENT',
                        'L': 'LEVEL',
                        'LRC': 'LEVEL RECORDING CONTROLLER',
                        'LIC': 'LEVEL INDICATING CONTROLLER',
                        'LC': 'LEVEL CONTROLLER',
                        'LCV': 'LEVEL CONTROL VALVE',
                        'LR': 'LEVEL RECORDER',
                        'LI': 'LEVEL INDICATOR',
                        'LSH': 'LEVEL SWITCH HIGH',
                        'LSL': 'LEVEL SWITCH LOW',
                        'LT': 'LEVEL TRANSMITTER',
                        'LY': 'LEVEL RELAY',
                        'LE': 'LEVEL ELEMENT',
                        'V': 'VIBRATION',
                        'VSH': 'VIBRATION SWITCH HIGH',
                        'VSL': 'VIBRATION SWITCH LOW',
                        'VT': 'VIBRATION TRANSMITTER',
                        'Z': 'POSITION',
                        'ZIC': 'POSITION INDICATING CONTROLLER',
                        'ZC': 'POSITION CONTROLLER',
                        'ZCV': 'POSITION CONTROL VALVE',
                        'ZI': 'POSITION INDICATOR',
                        'ZSH': 'POSITION SWITCH HIGH',
                        'ZSL': 'POSITION SWITCH LOW',
                        'ZT': 'POSITION TRANSMITTER',
                        'S': 'SPEED',
                        'SIC': 'SPEED INDICATING CONTROLLER',
                        'SI': 'SPEED INDICATOR',
                        'SSH': 'SPEED SWITCH HIGH',
                        'SSL': 'SPEED SWITCH LOW',
                        'ST': 'SPEED TRANSMITTER',
                        'W': 'WEIGHT',
                        'WIC': 'WEIGHT INDICATING CONTROLLER',
                        'WI': 'WEIGHT INDICATOR',
                        'WSH': 'WEIGHT SWITCH HIGH',
                        'WSL': 'WEIGHT SWITCH LOW',
                        'WT': 'WEIGHT TRANSMITTER',
                        'H': 'HAND',
                        'HIC': 'HAND INDICATING CONTROLLER',
                        'HC': 'HAND CONTROLLER',
                        'HS': 'HAND SWITCH',
                        'Y': 'EVENT',
                        'YIC': 'EVENT INDICATING CONTROLLER',
                        'YI': 'EVENT INDICATOR',
                        'YSH': 'EVENT SWITCH HIGH',
                        'YSL': 'EVENT SWITCH LOW',
                        'LALL': 'LEVEL ALARM LOW LOW',
                        'LAHH': 'LEVEL ALARM HIGH HIGH',
                        'PALL': 'PRESSURE ALARM LOW LOW',
                        'PAHH': 'PRESSURE ALARM HIGH HIGH',
                        'FALL': 'FLOW ALARM LOW LOW',
                        'FAHH': 'FLOW ALARM HIGH HIGH',
                        'TALL': 'TEMPERATURE ALARM LOW LOW',
                        'TAHH': 'TEMPERATURE ALARM HIGH HIGH',
                    },
                },
            ],
        },
    },
    SECTION_DRAWING_CONT: {
        'name': 'Drawing Continuations — Drawing Cont Type (default)',
        'description': (
            'Drawing continuation type matched directly against Legend '
            '(picture and description columns, full name used as both '
            'the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'drawing_cont_type',
                    'label': 'Drawing Cont Type',
                    'regex': r'[A-Z0-9 /()&-]+',
                    'notes': (
                        'Drawing continuation type matched directly against '
                        'Legend (picture and description columns, full name '
                        'used as both key and value)'
                    ),
                    'lookup': {
                        'OFF DRAWING PIPING/INSTRUMENT CONNECTOR WITH REMARKS':
                            'OFF DRAWING PIPING/INSTRUMENT CONNECTOR WITH REMARKS',
                    },
                },
            ],
        },
    },
    SECTION_GENERAL_INSTRUMENT: {
        'name': 'General Instrument — Instrument Bubble Type (default)',
        'description': (
            'General instrument bubble type matched directly against Legend '
            '(picture and description columns, full name used as both '
            'the tag and the lookup value).'
        ),
        'definition': {
            'separator': '-',
            'fields': [
                {
                    'key': 'instrument_bubble_type',
                    'label': 'Instrument Bubble Type',
                    'regex': r'[A-Z0-9 /()&,-]+',
                    'notes': (
                        'General instrument bubble type matched against '
                        'Legend. 12 standard symbols across 4 types and 3 '
                        'locations, plus 3 behind-panel variants.'
                    ),
                    'lookup': {
                        'DISCRETE INSTRUMENTS (PRIMARY LOCATION)': 'DISCRETE INSTRUMENTS (PRIMARY LOCATION)',
                        'DISCRETE INSTRUMENTS (FIELD MOUNTED)': 'DISCRETE INSTRUMENTS (FIELD MOUNTED)',
                        'DISCRETE INSTRUMENTS (AUXILIARY LOCATION)': 'DISCRETE INSTRUMENTS (AUXILIARY LOCATION)',
                        'SHARED DISPLAY SHARED CONTROL (PRIMARY LOCATION)': 'SHARED DISPLAY SHARED CONTROL (PRIMARY LOCATION)',
                        'SHARED DISPLAY SHARED CONTROL (FIELD MOUNTED)': 'SHARED DISPLAY SHARED CONTROL (FIELD MOUNTED)',
                        'SHARED DISPLAY SHARED CONTROL (AUXILIARY LOCATION)': 'SHARED DISPLAY SHARED CONTROL (AUXILIARY LOCATION)',
                        'COMPUTER FUNCTION (PRIMARY LOCATION)': 'COMPUTER FUNCTION (PRIMARY LOCATION)',
                        'COMPUTER FUNCTION (FIELD MOUNTED)': 'COMPUTER FUNCTION (FIELD MOUNTED)',
                        'COMPUTER FUNCTION (AUXILIARY LOCATION)': 'COMPUTER FUNCTION (AUXILIARY LOCATION)',
                        'PROGRAMMABLE LOGIC CONTROL (PRIMARY LOCATION)': 'PROGRAMMABLE LOGIC CONTROL (PRIMARY LOCATION)',
                        'PROGRAMMABLE LOGIC CONTROL (FIELD MOUNTED)': 'PROGRAMMABLE LOGIC CONTROL (FIELD MOUNTED)',
                        'PROGRAMMABLE LOGIC CONTROL (AUXILIARY LOCATION)': 'PROGRAMMABLE LOGIC CONTROL (AUXILIARY LOCATION)',
                        'DISCRETE INSTRUMENTS (BEHIND PANEL)': 'DISCRETE INSTRUMENTS (BEHIND PANEL)',
                        'PROGRAMMABLE LOGIC CONTROL (BEHIND PANEL)': 'PROGRAMMABLE LOGIC CONTROL (BEHIND PANEL)',
                        'COMPUTER FUNCTION (BEHIND PANEL)': 'COMPUTER FUNCTION (BEHIND PANEL)',
                    },
                },
            ],
        },
    },
}


def get_default_template(section: str) -> dict:
    if section not in DEFAULT_TEMPLATES:
        raise KeyError(f"No default template for section '{section}'")
    return DEFAULT_TEMPLATES[section]
