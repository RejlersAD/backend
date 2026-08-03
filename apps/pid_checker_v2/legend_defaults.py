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

SECTIONS = (
    SECTION_LINE_LIST,
    SECTION_EQUIPMENT_LIST,
    SECTION_INSTRUMENT_INDEX,
)

SECTION_LABELS = {
    SECTION_LINE_LIST: 'Line List',
    SECTION_EQUIPMENT_LIST: 'Equipment List',
    SECTION_INSTRUMENT_INDEX: 'Instrument Index',
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
                        'P':  'CRUDE OIL',
                        'RW': 'REJECT WATER',
                        'SG': 'SOUR GAS',
                        'SW': 'SEAWATER',
                        'TW': 'TREATED WATER',
                        'UA': 'PLANT AIR',
                        'UW': 'UTILITY WATER',
                        'VG': 'VENT GAS',
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
                        'SDV':  'SHUTDOWN VALVE',
                        'SDY':  'SHUTDOWN SOLENOID / RELAY',
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
}


def get_default_template(section: str) -> dict:
    if section not in DEFAULT_TEMPLATES:
        raise KeyError(f"No default template for section '{section}'")
    return DEFAULT_TEMPLATES[section]
