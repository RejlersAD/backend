"""Legend Pack Splitter — one upload → per-section project legends.

Solves the "configure a legend per application" pain: an engineer uploads ONE
project legend pack (PDF/image) once, and this module fans the AI-extracted
content out into per-section ``PidCheckerV2LegendSheet`` rows bound to the
shared Project Organizer project. Every tool (Line List, Equipment List,
Instrument Index, P&ID QC) then inherits them via ``_resolve_legend_smart``.

Pipeline:
  1. Reuse pid_verification_v2.services.legend_extractor.extract_legend_sheet
     to turn the file into structured content (service_codes, insulation_codes,
     line numbering, instrument/valve prefixes, symbols …).
  2. Map the recognised content onto the pid_checker_v2 per-section
     ``definition`` schema (separator + fields[] with regex/lookup) by
     *overlaying the extracted lookup tables onto the built-in default
     template* for each section — so every generated legend is guaranteed to
     pass ``compile_legend`` (the regex/format skeleton comes from the default,
     the project-specific code tables come from the uploaded pack).
  3. Persist one legend per mapped section, scoped to the project, activated
     (deactivating sibling project legends for that section).

All thresholds / section mapping are soft-coded below.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path

from django.db import transaction

from ..legend_defaults import (
    SECTIONS, SECTION_LABELS, DEFAULT_TEMPLATES,
    SECTION_LINE_LIST, SECTION_EQUIPMENT_LIST, SECTION_INSTRUMENT_INDEX,
)
from ..models import PidCheckerV2LegendSheet

logger = logging.getLogger(__name__)

# ── Soft-coded: which lookup tables from the AI extraction feed which field
#    of which section's default template.  `lookup_key` is the field key in the
#    section's default template whose `lookup` dict gets replaced by the
#    project's extracted codes.  Extend here to cover more sections/fields.
SECTION_LOOKUP_OVERLAYS = {
    SECTION_LINE_LIST: [
        # default line_list has a `service` field whose lookup is the fluid codes
        {'field_key': 'service', 'extract_key': 'service_codes'},
        {'field_key': 'insulation', 'extract_key': 'insulation_codes'},
    ],
    SECTION_INSTRUMENT_INDEX: [
        {'field_key': 'function', 'extract_key': 'instrument_prefixes'},
        {'field_key': 'type', 'extract_key': 'instrument_prefixes'},
    ],
    SECTION_VALVE: [
        {'field_key': 'type', 'extract_key': 'valve_prefixes'},
    ],
}

# Sections that receive a pack legend even without a lookup overlay (they keep
# the default regex skeleton so the project has an explicit, named row).
PACK_SECTIONS = (SECTION_LINE_LIST, SECTION_EQUIPMENT_LIST, SECTION_INSTRUMENT_INDEX)

# Accepted upload extensions for the pack file.
PACK_ACCEPTED_EXT = {'.pdf', '.png', '.jpg', '.jpeg', '.tiff', '.tif', '.bmp'}


def _normalise_lookup(raw) -> dict:
    """Coerce an extracted code table into a {CODE: label} dict (upper keys)."""
    out = {}
    if isinstance(raw, dict):
        items = raw.items()
    elif isinstance(raw, list):
        # list of {code, description/meaning/label} or [code, label] pairs
        def _pair(x):
            if isinstance(x, dict):
                code = x.get('code') or x.get('abbrev') or x.get('symbol') or x.get('prefix')
                label = x.get('description') or x.get('meaning') or x.get('label') or x.get('service')
                return code, label
            if isinstance(x, (list, tuple)) and len(x) >= 2:
                return x[0], x[1]
            return None, None
        items = filter(lambda kv: kv[0], (_pair(x) for x in raw))
    else:
        return out
    for k, v in items:
        code = str(k or '').strip().upper()
        if code:
            out[code] = str(v if v is not None else '').strip()
    return out


def _build_section_definition(section: str, extracted: dict) -> dict | None:
    """Return a compile-safe definition for `section`, overlaying extracted
    lookup tables onto the section's default template.  None if no default
    template exists for the section.
    """
    tpl = DEFAULT_TEMPLATES.get(section)
    if not tpl:
        return None
    # Deep-copy the default definition so overlays never mutate the template.
    import copy
    definition = copy.deepcopy(tpl['definition'])
    fields = definition.get('fields') or []

    overlays = SECTION_LOOKUP_OVERLAYS.get(section, [])
    overlaid = 0
    for ov in overlays:
        codes = _normalise_lookup(extracted.get(ov['extract_key']))
        if not codes:
            continue
        for field in fields:
            if field.get('key') == ov['field_key']:
                field['lookup'] = codes
                overlaid += 1
                break
    logger.info('[LegendPack] %s: %d lookup overlay(s) applied', section, overlaid)
    return definition


def split_legend_pack(file_bytes: bytes, filename: str, *, user, project, name: str = '',
                      use_ai: bool = True) -> dict:
    """Parse one legend pack file and create per-section project legends.

    Returns a summary dict: { created: [...], sections: n, method, file }.
    Raises ValueError on unsupported file / empty extraction.
    """
    suffix = Path(filename or '').suffix.lower()
    if suffix not in PACK_ACCEPTED_EXT:
        raise ValueError(f'Unsupported legend pack file type {suffix!r}. Use PDF or an image.')

    # Reuse the proven AI/text extractor (writes to a temp file it can read).
    try:
        from apps.pid_verification_v2.services.legend_extractor import extract_legend_sheet
    except Exception as exc:  # pragma: no cover - defensive
        raise ValueError(f'Legend extraction engine unavailable: {exc}') from exc

    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
        tmp.write(file_bytes)
        tmp_path = tmp.name

    try:
        extracted = extract_legend_sheet(tmp_path, use_ai=use_ai)
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

    if not extracted:
        raise ValueError('Could not extract any legend content from the uploaded file.')

    pack_name = name or extracted.get('file_name') or Path(filename or 'Legend pack').stem
    created = []

    with transaction.atomic():
        for section in PACK_SECTIONS:
            definition = _build_section_definition(section, extracted)
            if not definition:
                continue
            # Deactivate existing project legends for this section, then create
            # the new active pack legend.
            (PidCheckerV2LegendSheet.objects
                .filter(project=project, section=section)
                .update(is_active=False))
            obj = PidCheckerV2LegendSheet.objects.create(
                created_by=user,
                project=project,
                section=section,
                name=f'{pack_name} — {SECTION_LABELS.get(section, section)}',
                description=f'Auto-generated from project legend pack "{pack_name}".',
                definition=definition,
                is_active=True,
            )
            created.append({
                'legend_id': str(obj.legend_id),
                'section': section,
                'section_label': SECTION_LABELS.get(section, section),
                'name': obj.name,
            })

    logger.info('[LegendPack] created %d project legends for project=%s',
                len(created), getattr(project, 'project_id', None))
    return {
        'created': created,
        'sections': len(created),
        'method': extracted.get('extraction_method', 'unknown'),
        'file': extracted.get('file_name', filename),
        'pack_name': pack_name,
    }
