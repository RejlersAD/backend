"""
Fluid Code reference — SINGLE SOURCE OF TRUTH.

BUG FIX: these 47 customer-confirmed codes used to be hand-duplicated in
two places — a Python literal inside
apps.pid_verification.services.piping_valve_mto_extractor's Vision
prompt, and a matching JavaScript object in ValveMTO.jsx (for the
auto-created "Fluid Code - Standard" default legend) — with no
mechanism keeping them in sync; a future edit to one could silently
diverge from the other.

Now there is exactly ONE place the actual code→description mapping
lives: here. Everything else reads it, never re-declares it:
  - piping_valve_mto_extractor.py imports FLUID_CODES directly (plain
    Python import, cross-app but read-only — apps.valve_mto is never
    modified by that module) to build the Vision prompt's "FLUID CODES"
    section.
  - ValveMTO.jsx fetches the SAME dict at runtime via
    GET /api/v1/valve-mto/fluid-codes/ (apps/valve_mto/views.py) when it
    needs to auto-create the default "Fluid Code - Standard" legend.

To change the customer's fluid code list, edit ONLY the dict below —
both the Vision prompt and the frontend's auto-created legend pick up
the change automatically (the prompt on next Python process restart via
the import; the frontend on its next fetch of the endpoint).
"""
from __future__ import annotations

FLUID_CODES: dict[str, str] = {
    'A': 'COMBUSTION AIR', 'AC': 'ACIDISER', 'AGC': 'ACID GASES', 'AM': 'AMINE',
    'ATK': 'AVIATION TURBINE KEROSENE', 'BI': 'BIOCIDE', 'BD': 'BLOWDOWN',
    'BG': 'BLANKET GAS', 'BKF': 'BUNKER FUEL', 'CD': 'HYDROCARBON CLOSED DRAIN',
    'CHW': 'CHILLED WATER', 'CIN': 'CHEMICAL INJECTION', 'CW': 'COOLING WATER',
    'DF': 'DIESEL FUEL', 'DR': 'NON-HAZARDOUS OPEN DRAIN', 'DM': 'DEMINERALIZED WATER',
    'DW': 'POTABLE WATER', 'EC': 'ELECTROCHLORINATION', 'FG': 'FUEL GAS',
    'FM': 'FIRE MAIN', 'FO': 'FUEL OIL', 'FP': 'FIRE FIGHTING FOAM',
    'GC': 'GAS CONDENSATE', 'GL': 'GLYCOL', 'GN': 'NITROGEN', 'HF': 'HYDRAULIC FLUID',
    'HO': 'HOT OIL', 'IA': 'INSTRUMENT AIR', 'ING': 'INERT GAS', 'LO': 'LUBE OIL',
    'OD': 'HAZARDOUS OPEN DRAIN', 'MNL': 'METHANOL', 'P': 'PROCESS GAS/LIQUID',
    'PA': 'PLANT AIR', 'PG': 'PILOT GAS', 'PO': 'PRODUCED OIL', 'PW': 'PRODUCED WATER',
    'RO': 'RECOVERED OIL', 'RV': 'RELIEF GAS', 'SEW': 'SEWAGE', 'SG': 'STRIPPING GAS',
    'SH': 'SODIUM HYPOCHLORITE', 'SW': 'SEA WATER', 'TW': 'TREATED WATER',
    'VG': 'VENT GAS', 'WG': 'WASTE GAS', 'WW': 'WASHDOWN WATER',
}


def format_fluid_codes_for_prompt(codes: dict[str, str] = FLUID_CODES) -> str:
    """Renders the fluid-code table exactly the way the Vision prompt
    displays it — comma-separated 'CODE=DESCRIPTION' pairs, wrapped at a
    readable width. Kept here (not duplicated in the extractor) so the
    formatting rule and the data itself never drift apart either."""
    pairs = [f'{code}={desc}' for code, desc in codes.items()]
    lines: list[str] = []
    current = '    '
    for pair in pairs:
        candidate = f'{current}{pair}, '
        if len(candidate) > 78 and current.strip():
            lines.append(current.rstrip())
            current = f'    {pair}, '
        else:
            current = candidate
    if current.strip():
        lines.append(current.rstrip().rstrip(','))
    return '\n'.join(lines)
