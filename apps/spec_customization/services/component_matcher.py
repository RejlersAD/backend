"""
Spec Customization — Component Matching Service
=================================================

Soft-coded configuration and logic for matching PDF-extracted components
to CAT workbook commodity codes using Match.xlsx lookup table.

Workflow:
  1. Parse Match.xlsx → create MatchingRule records
  2. Load SPEC.xlsx → cache specification rules
  3. Load CAT.xlsx → cache component catalog
  4. Match component:
       a. Lookup PDF name in Match.xlsx → get catalog name
       b. Find CAT sheet by catalog name
       c. Apply SPEC filters (pressure/temp/material)
       d. Match by Npd, PressureRating, MaterialGrade
       e. Return IndustryCommodityCode

All thresholds, column mappings, and sheet names are soft-coded below.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from django.core.files.base import File

logger = logging.getLogger(__name__)


def _read_excel(source, **kwargs):
    """Read storage-backed uploads without requiring a local filesystem path."""
    if hasattr(source, 'storage') and getattr(source, 'name', None):
        with source.storage.open(source.name, 'rb') as stream:
            return pd.read_excel(stream, **kwargs)
    return pd.read_excel(source, **kwargs)


# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded matching configuration
# ─────────────────────────────────────────────────────────────────────────────
COMPONENT_MATCHING_CONFIG = {
    # Match.xlsx structure
    'match_xlsx': {
        'sheet_name': 0,  # First sheet (or specify name like 'Sheet1')
        'pdf_column': 'A',  # Column containing PDF component names
        'catalog_column': 'D',  # Column containing Excel catalog names
        'header_row': 0,  # 0-indexed row number for headers (0 = first row)
        'skip_rows': 0,  # Rows to skip before reading data
    },
    
    # CAT.xlsx structure
    'cat_xlsx': {
        'commodity_code_col': 'IndustryCommodityCode',  # Unique part identifier
        'npd_cols': ['Npd[1]', 'Npd[2]'],  # Nominal pipe diameter columns
        'pressure_rating_cols': ['PressureRating[1]', 'PressureRating[2]'],
        'material_grade_col': 'MaterialGrade',
        'end_prep_cols': ['EndPreparation[1]', 'EndPreparation[2]'],
        'end_standard_cols': ['EndStandard[1]', 'EndStandard[2]'],
        'commodity_type_col': 'CommodityType',
        'description_col': 'Description',
        'weight_col': 'DryWeight',
        'symbol_col': 'SymbolDefinition',
    },
    
    # SPEC.xlsx critical sheets (for filtering)
    'spec_xlsx': {
        'service_limits_sheet': 'ServiceLimits',
        'materials_data_sheet': 'MaterialsData',
        'gasket_selection_sheet': 'GasketSelectionFilter',
        'bolt_selection_sheet': 'BoltSelectionFilter',
        'pipe_nominal_diameters_sheet': 'PipeNominalDiameters',
    },
    
    # Matching priorities (1 = highest)
    'match_criteria': {
        'component_type': 1,  # Exact match from Match.xlsx
        'nominal_size': 2,  # Npd match
        'pressure_rating': 3,  # Pressure class match
        'material_grade': 4,  # Material match
        'end_preparation': 5,  # Connection type match
    },
    
    # Fuzzy matching thresholds
    'fuzzy': {
        'enabled': True,
        'min_score': 0.80,  # Minimum similarity score (0-1)
        'use_levenshtein': True,
    },
    
    # Catalog sheet name normalization
    'sheet_normalization': {
        'remove_spaces': True,  # "Gate Valve" → "GateValve"
        'remove_degree_symbol': True,  # "90° Elbow" → "90DegElbow"
        'replacements': {
            '90 Degree Direction Change': '90DegLRElbow',  # Special case
            '45 Degree Direction Change': '45DegElbow',
            'Tee': 'Tee',
            'Reducing Tee': 'ReducingTee',
            'Nipple': 'Nipple',
            'Sockolet': 'Sockolet',
            'Weldolet': 'Weldolet',
        }
    },
    
    # Size normalization (convert to standard format)
    'size_normalization': {
        'strip_units': True,  # Remove ", DN, NPS
        'convert_mm_to_inch': False,  # Keep as-is
        'decimal_precision': 2,
    },
    
    # Pressure rating normalization
    'pressure_normalization': {
        'strip_symbols': True,  # Remove #, lbs, etc.
        'standard_ratings': ['150', '300', '600', '900', '1500', '2500'],
    },
}


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Normalize catalog name to CAT sheet name
# ─────────────────────────────────────────────────────────────────────────────
def normalize_catalog_name_to_sheet(catalog_name: str) -> str:
    """
    Convert catalog component name to CAT.xlsx sheet name.
    
    Examples:
        "Gate Valve" → "GateValve"
        "90 Degree Direction Change" → "90DegLRElbow"
        "45 Degree Direction Change" → "45DegElbow"
    """
    cfg = COMPONENT_MATCHING_CONFIG['sheet_normalization']
    
    # Check for exact replacements first
    if catalog_name in cfg['replacements']:
        return cfg['replacements'][catalog_name]
    
    # Default: remove spaces
    result = catalog_name
    if cfg['remove_spaces']:
        result = result.replace(' ', '')
    
    if cfg['remove_degree_symbol']:
        result = result.replace('°', 'Deg')
        result = result.replace(' Deg', 'Deg')
    
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Helper: Normalize size values
# ─────────────────────────────────────────────────────────────────────────────
def normalize_size(size: str) -> str:
    """Normalize numeric spelling without converting DN to NPS or rounding."""
    from fractions import Fraction
    if size is None or str(size).strip() == '':
        return ''
    raw = str(size).strip().upper()
    metric = raw.startswith('DN')
    raw = raw.removeprefix('DN').removeprefix('NPS').replace('"', '').strip()
    try:
        numeric = str(float(sum(Fraction(part) for part in raw.split())))
        numeric = numeric.rstrip('0').rstrip('.') if '.' in numeric else numeric
        return ('DN' if metric else '') + numeric
    except (ValueError, ZeroDivisionError):
        return ('DN' if metric else '') + raw


def normalize_pressure(pressure: str) -> str:
    """Preserve PN vs class identity; no service pressure/rating conversions."""
    if pressure is None:
        return ''
    raw = str(pressure).strip().upper()
    metric = raw.startswith('PN')
    raw = re.sub(r'^(PN|CLASS)\s*', '', raw)
    raw = re.sub(r'\s*(#|LBS?)$', '', raw).strip()
    try:
        from decimal import Decimal
        normalized = format(Decimal(raw).normalize(), 'f')
        return ('PN' if metric else '') + normalized
    except Exception:
        return ('PN' if metric else '') + raw


# ─────────────────────────────────────────────────────────────────────────────
# Service: Parse Match.xlsx and create MatchingRule records
# ─────────────────────────────────────────────────────────────────────────────
def parse_match_xlsx(
    file_path: str,
    workbook_set,
) -> Tuple[int, str]:
    """
    Parse Match.xlsx and create MatchingRule records.
    
    Returns:
        (rules_count, error_message)
    """
    from ..matching_models import MatchingRule
    if workbook_set.frozen_at or workbook_set.pinned_jobs.exists():
        return 0, 'Frozen or pinned reference sets cannot be reparsed.'
    
    cfg = COMPONENT_MATCHING_CONFIG['match_xlsx']
    
    try:
        # Read Excel file
        df = _read_excel(
            file_path, sheet_name=cfg['sheet_name'],
            header=cfg['header_row'] if cfg['header_row'] >= 0 else None,
            skiprows=cfg['skip_rows'] if cfg['skip_rows'] > 0 else None,
        )
        
        # If no header, use column letters
        if cfg['header_row'] < 0:
            # Convert column letters to indices (A=0, D=3)
            pdf_col_idx = ord(cfg['pdf_column'].upper()) - ord('A')
            cat_col_idx = ord(cfg['catalog_column'].upper()) - ord('A')
        else:
            # Use actual column names or indices
            pdf_col_idx = cfg['pdf_column'] if isinstance(cfg['pdf_column'], int) else 0
            cat_col_idx = cfg['catalog_column'] if isinstance(cfg['catalog_column'], int) else 3
        
        # Extract mappings
        rules_created = 0
        if df.shape[1] <= max(pdf_col_idx, cat_col_idx):
            return 0, 'Match workbook is missing the configured component-name columns.'
        for idx, row in df.iterrows():
            try:
                # Get values by column index
                pdf_name = str(row.iloc[pdf_col_idx]).strip() if pd.notna(row.iloc[pdf_col_idx]) else ''
                catalog_name = str(row.iloc[cat_col_idx]).strip() if pd.notna(row.iloc[cat_col_idx]) else ''
                
                # Skip empty rows
                if not pdf_name or not catalog_name:
                    continue
                
                # Derive CAT sheet name
                cat_sheet = normalize_catalog_name_to_sheet(catalog_name)
                
                # Create or update rule
                MatchingRule.objects.update_or_create(
                    workbook_set=workbook_set,
                    pdf_component_name=pdf_name,
                    defaults={
                        'catalog_component_name': catalog_name,
                        'cat_sheet_name': cat_sheet,
                        'row_number': idx + 2,  # Excel row number (1-indexed + header)
                    }
                )
                rules_created += 1
                
            except Exception as e:
                logger.warning(f"[MatchParser] Failed to parse row {idx}: {e}")
                continue
        
        return (rules_created, '') if rules_created else (0, 'Match workbook contains no usable component mappings.')
        
    except Exception as e:
        error_msg = f"Failed to parse Match.xlsx: {str(e)}"
        logger.exception(f"[MatchParser] {error_msg}")
        return 0, error_msg


# ─────────────────────────────────────────────────────────────────────────────
# Service: Load CAT sheet as DataFrame
# ─────────────────────────────────────────────────────────────────────────────
def load_cat_sheet(
    cat_file_path: str,
    sheet_name: str,
) -> Optional[pd.DataFrame]:
    """
    Load a specific sheet from CAT.xlsx.
    
    Returns:
        DataFrame or None if sheet not found
    """
    try:
        from .reference_intelligence import read_reference_sheet
        df = read_reference_sheet(cat_file_path, sheet_name)
        logger.info(f"[CATLoader] Loaded sheet '{sheet_name}' with {len(df)} rows")
        return df
    except Exception as e:
        logger.warning(f"[CATLoader] Failed to load sheet '{sheet_name}': {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Service: Load SPEC sheet as DataFrame
# ─────────────────────────────────────────────────────────────────────────────
def load_spec_sheet(
    spec_file_path: str,
    sheet_name: str,
) -> Optional[pd.DataFrame]:
    """
    Load a specific sheet from SPEC.xlsx.
    
    Returns:
        DataFrame or None if sheet not found
    """
    try:
        from .reference_intelligence import read_reference_sheet
        df = read_reference_sheet(spec_file_path, sheet_name)
        logger.info(f"[SPECLoader] Loaded sheet '{sheet_name}' with {len(df)} rows")
        return df
    except Exception as e:
        logger.warning(f"[SPECLoader] Failed to load sheet '{sheet_name}': {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Service: Match component to CAT commodity code
# ─────────────────────────────────────────────────────────────────────────────
def _clean(value):
    return '' if pd.isna(value) else str(value).strip()


def _spec_eligibility(workbook_set, nominal_size, material_grade, spec_name=''):
    """Conservative eligibility; never treat missing reference evidence as a pass."""
    evidence = {}
    if not workbook_set.spec_file:
        return 'invalid_reference', {'error': 'SPEC workbook not uploaded.'}
    sizes = load_spec_sheet(workbook_set.spec_file, 'PipeNominalDiameters')
    limits = None
    if sizes is not None and 'Npd' in sizes.columns and not sizes.empty:
        if spec_name and 'SpecName' in sizes.columns:
            sizes = sizes[sizes.SpecName.str.casefold() == spec_name.casefold()]
        allowed = [normalize_size(v) for v in sizes.Npd if _clean(v)]
        evidence['nominal_size'] = {'requested': nominal_size, 'allowed': allowed}
        if nominal_size and normalize_size(nominal_size) not in allowed:
            return 'ineligible', evidence
    else:
        limits = load_spec_sheet(workbook_set.spec_file, 'ServiceLimits')
        cols = {'NominalPipingDiameterFrom', 'NominalPipingDiameterTo'}
        if limits is None or not cols.issubset(limits.columns) or limits.empty:
            return 'invalid_reference', {'error': 'No populated SPEC diameter eligibility data.'}
        if spec_name and 'SpecName' in limits.columns:
            limits = limits[limits.SpecName.str.casefold() == spec_name.casefold()]
        ranges = []
        try:
            for _, row in limits.iterrows():
                ranges.append([float(normalize_size(row.NominalPipingDiameterFrom)), float(normalize_size(row.NominalPipingDiameterTo))])
            size = float(normalize_size(nominal_size)) if nominal_size else None
        except ValueError:
            return 'invalid_reference', {'error': 'Unsupported SPEC diameter value; unit/range conversion requires review.'}
        evidence['nominal_size'] = {'requested': nominal_size, 'ranges': ranges}
        if size is not None and not any(lo <= size <= hi for lo, hi in ranges):
            return 'ineligible', evidence
    if material_grade:
        materials = load_spec_sheet(workbook_set.spec_file, 'MaterialsData')
        if materials is None or 'MaterialsGrade' not in materials.columns or materials.empty:
            return 'invalid_reference', {'error': 'No populated SPEC MaterialsData.MaterialsGrade.'}
        allowed = [_clean(v).casefold() for v in materials.MaterialsGrade if _clean(v)]
        evidence['material_grade'] = {'requested': material_grade, 'allowed': allowed}
        if material_grade.strip().casefold() not in allowed:
            return 'ineligible', evidence
    return '', evidence


def match_component(pdf_component_name, nominal_size, pressure_rating, material_grade,
                    workbook_set, sub_type='', end_connection='', spec_name='', codelists=None):
    """Return an explicit state. Scores rank discriminators, not probabilities."""
    from ..matching_models import MatchingRule, ComponentMatchingResult
    result = dict(matched=False, commodity_code='', description='', match_score=0.0,
                  match_method='rule-based', error='', status='unmatched', state='unmatched',
                  candidate_count=0, candidates=[], discriminators_matched={},
                  score_description='Ranking aid only; not calibrated probability.')

    def outcome(state, error=''):
        result.update(status=state, state=state, error=error)
        return result

    try:
        if '..' in nominal_size:
            return outcome('unmatched', 'Extracted size interval requires engineering expansion into nominal sizes before commodity selection.')
        blocked, evidence = _spec_eligibility(workbook_set, nominal_size, '' if codelists else material_grade, spec_name)
        result['discriminators_matched']['spec_eligibility'] = evidence
        if blocked:
            return outcome(blocked, evidence.get('error', 'Component is outside SPEC eligibility.'))
        if codelists:
            from .buhasa_codelists import resolve
            requested = codelists['attributes']['MaterialsGrade']
            if requested['candidates']:
                materials = load_spec_sheet(workbook_set.spec_file, 'MaterialsData')
                if materials is None or 'MaterialsGrade' not in materials.columns or materials.empty:
                    return outcome('invalid_reference', 'No populated SPEC MaterialsData.MaterialsGrade.')
                allowed = {entry['code'] for value in materials.MaterialsGrade for entry in resolve('MaterialsGrade', _clean(value), catalog=True)['candidates']}
                if not allowed.intersection(entry['code'] for entry in requested['candidates']):
                    return outcome('ineligible', 'Material is outside BUHASA-normalized SPEC eligibility.')
        rules = MatchingRule.objects.filter(workbook_set=workbook_set, pdf_component_name__iexact=pdf_component_name)
        if rules.count() > 1:
            return outcome('invalid_reference', 'Multiple name mappings require reference correction.')
        rule = rules.first()
        if not rule:
            return outcome('unmatched', f"No mapping for '{pdf_component_name}'.")
        if not workbook_set.cat_file:
            return outcome('invalid_reference', 'CAT workbook not uploaded.')
        cat = load_cat_sheet(workbook_set.cat_file, rule.cat_sheet_name)
        if cat is None or cat.empty:
            return outcome('invalid_reference', 'Mapped CAT sheet is missing or empty.')
        if codelists:
            from .buhasa_codelists import match_catalog
            return match_catalog(result, cat, codelists, nominal_size, sub_type, rule.catalog_component_name,
                                 normalize_size, _clean)
        cfg = COMPONENT_MATCHING_CONFIG['cat_xlsx']
        criteria = {
            'size': (nominal_size, cfg['npd_cols'], normalize_size),
            'pressure': (pressure_rating, cfg['pressure_rating_cols'], normalize_pressure),
            'material': (material_grade, [cfg['material_grade_col']], lambda v: v.casefold()),
            'end_connection': (end_connection, cfg['end_prep_cols'], lambda v: v.casefold()),
            'sub_type': (sub_type, [cfg['commodity_type_col']], lambda v: v.casefold()),
        }
        # A mapped component sheet supplies subtype evidence when it has no
        # CommodityType column. Never invent a subtype from catalog row order.
        if sub_type and cfg['commodity_type_col'] not in cat.columns:
            criteria['sub_type'] = (sub_type, [], lambda v: v.casefold())
        for key, (wanted, columns, _) in criteria.items():
            if wanted and columns and not any(c in cat.columns for c in columns):
                return outcome('invalid_reference', f'CAT sheet lacks {key} discriminator columns.')
        if cfg['commodity_code_col'] not in cat.columns:
            return outcome('invalid_reference', 'CAT sheet lacks commodity codes.')
        viable = []
        for _, row in cat.drop_duplicates().iterrows():
            details = {}
            for key, (wanted, columns, normalize) in criteria.items():
                actual = [_clean(row.get(c, '')) for c in columns if c in cat.columns]
                matches = None if not wanted else (re.sub(r'[^a-z0-9]', '', wanted.casefold()) in re.sub(r'[^a-z0-9]', '', rule.catalog_component_name.casefold()) if not columns else any(normalize(v) == normalize(wanted.strip()) for v in actual))
                details[key] = {'requested': wanted, 'values': actual or ([rule.catalog_component_name] if key == 'sub_type' else []), 'matched': matches}
            score = sum(d['matched'] is True for d in details.values()) / len(criteria)
            candidate = {'commodity_code': _clean(row.get(cfg['commodity_code_col'])),
                         'description': _clean(row.get(cfg['description_col'], rule.catalog_component_name)),
                         'discriminators': details, 'ranking_score': score}
            if all(d['matched'] is not False for d in details.values()):
                if not candidate['commodity_code']:
                    return outcome('invalid_reference', 'Matching CAT row lacks a commodity code.')
                viable.append(candidate)
        result['candidates'] = sorted(viable, key=lambda c: (-c['ranking_score'], c['commodity_code']))
        result['candidate_count'] = len(viable)
        result['candidate_commodity_codes'] = list(dict.fromkeys(c['commodity_code'] for c in viable))
        if not viable:
            return outcome('unmatched', 'No CAT candidates satisfy the supplied discriminators.')
        if len(viable) > 1:
            return outcome('ambiguous', 'Engineering review must select a candidate; no automatic resolution.')
        candidate = viable[0]
        result.update(matched=True, commodity_code=candidate['commodity_code'], description=candidate['description'], match_score=candidate['ranking_score'])
        result['discriminators_matched'].update(candidate['discriminators'])
        outcome('unique_match')
        ComponentMatchingResult.objects.create(
            workbook_set=workbook_set, pdf_component_name=pdf_component_name,
            nominal_size=nominal_size, pressure_rating=pressure_rating, material_grade=material_grade,
            matched_commodity_code=result['commodity_code'], matched_description=result['description'],
            match_score=result['match_score'], match_method=result['match_method'], result_data=result)
        return result
    except Exception:
        logger.exception('Reference matching failed')
        return outcome('invalid_reference', 'Reference matching failed; inspect the reference workbook.')


def run_job_matching(job, workbook_set, codelist_profile=None, selection_updates=None):
    """Pin on matching and upsert evidence without changing extracted records."""
    from django.db import transaction
    from ..models import PaperSpecExtractionJob, PipingClassComponent
    from ..matching_models import MatchingWorkbookSet, ComponentMatchDecision
    from .reference_intelligence import file_sha256
    with transaction.atomic():
        from ..project_models import SpecProject
        SpecProject.objects.select_for_update().get(pk=workbook_set.project_id)
        job = PaperSpecExtractionJob.objects.select_for_update().select_related('document').get(pk=job.pk)
        workbook_set = MatchingWorkbookSet.objects.select_for_update().get(pk=workbook_set.pk)
        existing = list(job.match_decisions.filter(workbook_set=workbook_set))
        profiled = any(d.discriminators_matched.get('codelists', {}).get('profile') == 'BUHASA' for d in existing)
        if codelist_profile is None and profiled:
            codelist_profile = 'BUHASA'
        if profiled and codelist_profile != 'BUHASA':
            raise ValueError('An existing BUHASA batch cannot downgrade to legacy matching.')
        if codelist_profile not in (None, 'BUHASA'):
            raise ValueError('Unsupported codelist profile.')
        if job.status != 'completed':
            raise ValueError('Matching requires a completed extraction job.')
        if str(job.document.project_id) != str(workbook_set.project_id):
            raise ValueError('Job and reference set must belong to the same project.')
        if job.reference_workbook_set_id and job.reference_workbook_set_id != workbook_set.pk:
            raise ValueError('Job is already pinned to another reference revision.')
        if not workbook_set.schema_valid or not workbook_set.is_parsed:
            raise ValueError('Reference schema validation and parsing must succeed before matching.')
        for kind in ('match', 'spec', 'cat'):
            if file_sha256(getattr(workbook_set, f'{kind}_file')) != getattr(workbook_set, f'{kind}_file_hash'):
                raise ValueError('Reference file integrity check failed.')
        job.reference_workbook_set = workbook_set
        job.save(update_fields=['reference_workbook_set'])
        summary = {state: 0 for state, _ in ComponentMatchDecision.STATE_CHOICES}
        for component in PipingClassComponent.objects.filter(piping_class__job=job).select_related('piping_class'):
            pc = component.piping_class
            names = [f'{component.sub_type} {component.component_type}'.strip(), component.sub_type, component.component_type]
            name = next((n for n in names if workbook_set.rules.filter(pdf_component_name__iexact=n).exists()), names[0]).upper()
            # Preserve ranges verbatim: an extracted interval is not a single part.
            size = component.size_from
            if component.size_to and normalize_size(component.size_to) != normalize_size(size):
                size = f'{size}..{component.size_to}'
            criteria = dict(job=job, workbook_set=workbook_set, pdf_component_name=name,
                            nominal_size=size, pressure_rating=pc.pressure_rating,
                            material_grade=component.material_standard or pc.material_grade)
            if len(criteria['material_grade']) > 64 or len(criteria['pressure_rating']) > 32 or len(size) > 32:
                raise ValueError('Extracted criteria exceed decision field limits; engineering normalization is required.')
            previous = ComponentMatchDecision.objects.filter(**criteria).first()
            codelists = None
            if codelist_profile == 'BUHASA':
                from .buhasa_codelists import normalize, raw_attributes, dictionary
                old = previous.discriminators_matched.get('codelists', {}) if previous else {}
                attributes = raw_attributes(component)
                # A changed source invalidates any prior canonical selections.
                selections = old.get('selections', {}) if old.get('raw_attributes') == attributes and old.get('dictionary_hash') == dictionary()[2] else {}
                if previous and selection_updates and str(previous.pk) in selection_updates:
                    selections = {**selections, **selection_updates[str(previous.pk)]}
                codelists = normalize(attributes, selections)
                codelists['raw_attributes'] = attributes
                codelists['component_id'] = str(component.pk)
            result = match_component(name, size, criteria['pressure_rating'], criteria['material_grade'], workbook_set,
                                     component.sub_type, component.end_connection, pc.class_code, codelists=codelists)
            if codelists:
                # Attach evidence even when SPEC eligibility stops before CAT.
                result['discriminators_matched']['codelists'] = codelists
            defaults = dict(sub_type=component.sub_type, end_connection=component.end_connection,
                            state=result['state'], matched_commodity_code=result['commodity_code'],
                            matched_description=result['description'], match_score=result['match_score'],
                            candidates=result['candidates'], discriminators_matched={**result['discriminators_matched'], 'reason': result['error']})
            decision, created = ComponentMatchDecision.objects.get_or_create(**criteria, defaults=defaults)
            if not created:
                if codelists:
                    old = decision.discriminators_matched.get('codelists', {})
                    if old.get('component_id') not in (None, str(component.pk)):
                        raise ValueError('BUHASA source rows share a decision key; normalize source records before matching.')
                    # Retain a reviewed row selection only for unchanged evidence.
                    selected = old.get('selected_candidate_id')
                    if selected:
                        proposed = defaults['discriminators_matched']['codelists']
                        comparable = {k: v for k, v in old.items() if k != 'selected_candidate_id'}
                        if comparable == proposed and defaults['candidates'] == decision.candidates:
                            proposed['selected_candidate_id'] = selected
                old_evidence = decision.discriminators_matched
                if old_evidence.get('source_reference') and {k: v for k, v in old_evidence.items() if k != 'source_reference'} == defaults['discriminators_matched'] and decision.candidates == defaults['candidates']:
                    defaults['discriminators_matched']['source_reference'] = old_evidence['source_reference']
                changed = any(getattr(decision, k) != v for k, v in defaults.items())
                # The requested key omits subtype/end. Do not silently combine conflicting criteria.
                if decision.sub_type != component.sub_type or decision.end_connection != component.end_connection:
                    defaults.update(state='invalid_reference', candidates=[], matched_commodity_code='',
                                    discriminators_matched={'reason': 'Duplicate criteria have conflicting subtype or end connection; normalize source records.'})
                    changed = True
                if changed:
                    for key, value in defaults.items():
                        setattr(decision, key, value)
                    decision.review_decision = decision.final_commodity_code = decision.review_notes = ''
                    decision.reviewed_by = decision.reviewed_at = None
                    decision.save()
        from collections import Counter
        for state, count in Counter(job.match_decisions.filter(workbook_set=workbook_set).values_list('state', flat=True)).items():
            summary[state] = count
        return {'job_id': str(job.pk), 'workbook_set_id': str(workbook_set.pk), 'summary': summary, 'count': sum(summary.values())}
