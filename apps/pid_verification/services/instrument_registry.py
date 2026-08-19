"""
Instrument Symbol Registry
===========================
Takes structured instrument/valve symbol data extracted by instrument_extractor.py
and upserts it into the PIDVInstrumentSymbol database table.

The six input keys map directly to the six Category values in the model:
  control_valves      → 'control_valve'
  manual_valves       → 'manual_valve'
  instruments         → 'instrument'
  instrument_tagging  → 'instrument_tagging'
  equipment_numbering → 'equipment_numbering'
  inline_equipment    → 'inline_equipment'

Use update_or_create so re-running extraction on the same legend sheet is idempotent.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Soft-coded: mapping from extractor keys → model Category values ───────────
_CATEGORY_MAP = {
    'control_valves':       'control_valve',
    'manual_valves':        'manual_valve',
    'instruments':          'instrument',
    'instrument_tagging':   'instrument_tagging',
    'equipment_numbering':  'equipment_numbering',
    'inline_equipment':     'inline_equipment',
    'electrical_components': 'electrical_component',
}


def save_instrument_symbols(legend_sheet, extracted_instruments: dict) -> int:
    """
    Upsert all extracted instrument symbols into the PIDVInstrumentSymbol table.

    Args:
        legend_sheet:          PIDVLegendSheet instance (provides project + legend FK).
        extracted_instruments: Dict returned by instrument_extractor.extract_instrument_symbols().

    Returns:
        Number of symbols created or updated.
    """
    from apps.pid_verification.models import PIDVInstrumentSymbol

    if not extracted_instruments:
        return 0

    project      = legend_sheet.project
    source       = PIDVInstrumentSymbol.Source.AI_EXTRACTION
    saved_count  = 0

    for extractor_key, category_value in _CATEGORY_MAP.items():
        symbols = extracted_instruments.get(extractor_key, [])
        if not isinstance(symbols, list):
            continue

        for sym in symbols:
            symbol_code = (sym.get('symbol_code') or '').strip()
            description = (sym.get('description') or '').strip()

            if not symbol_code:
                logger.debug('[InstrRegistry] Skipping symbol with empty code in category=%s', category_value)
                continue

            try:
                obj, created = PIDVInstrumentSymbol.objects.update_or_create(
                    project     = project,
                    symbol_code = symbol_code,
                    category    = category_value,
                    defaults    = {
                        'description':      description,
                        'symbol_type':      (sym.get('symbol_type') or '').strip(),
                        'drawing_standard': (sym.get('drawing_standard') or 'ISA 5.1').strip(),
                        'attributes':       sym.get('attributes') or {},
                        'source':           source,
                        'legend_sheet':     legend_sheet,
                    },
                )
                saved_count += 1
                action = 'created' if created else 'updated'
                logger.debug('[InstrRegistry] %s symbol=%s category=%s project=%s',
                             action, symbol_code, category_value, project.project_id)

            except Exception as exc:
                logger.warning(
                    '[InstrRegistry] Failed upsert symbol=%s category=%s project=%s: %s',
                    symbol_code, category_value, project.project_id, exc,
                )

    logger.info('[InstrRegistry] Saved %d instrument symbols for project=%s legend=%s',
                saved_count, project.project_id, legend_sheet.legend_id)
    return saved_count


def get_category_counts(project) -> dict:
    """
    Return a dict of {category_value: count} for all symbols in a project.
    Used by the API view to build header badges.
    """
    from django.db.models import Count
    from apps.pid_verification.models import PIDVInstrumentSymbol

    qs = (
        PIDVInstrumentSymbol.objects
        .filter(project=project)
        .values('category')
        .annotate(cnt=Count('id'))
    )
    return {row['category']: row['cnt'] for row in qs}
