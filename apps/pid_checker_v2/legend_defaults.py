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

SECTIONS = (
    SECTION_LINE_LIST,
)

SECTION_LABELS = {
    SECTION_LINE_LIST: 'Line List',
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
}


def get_default_template(section: str) -> dict:
    if section not in DEFAULT_TEMPLATES:
        raise KeyError(f"No default template for section '{section}'")
    return DEFAULT_TEMPLATES[section]
