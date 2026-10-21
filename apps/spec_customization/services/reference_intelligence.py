"""I-06 / s3d-reference-intelligence-release: storage-aware reference evidence.

Only declared workbook columns are interpreted. ServiceLimits.Pressure is a
service pressure, not an ASME pressure class; no unit or rating conversion is
inferred. Header-only templates are not eligible reference data.
"""
import hashlib
import json
from contextlib import contextmanager

import pandas as pd


@contextmanager
def reference_stream(source):
    if hasattr(source, 'storage'):
        with source.storage.open(source.name, 'rb') as stream:
            yield stream
    else:
        source.seek(0)
        try:
            yield source
        finally:
            source.seek(0)


def file_sha256(source):
    with reference_stream(source) as stream:
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
        return digest.hexdigest()


def reference_hash(workbook_set):
    hashes = {kind: getattr(workbook_set, f'{kind}_file_hash') for kind in ('match', 'spec', 'cat')}
    if not all(hashes.values()):
        return ''
    return hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()


def read_reference_sheet(source, sheet_name):
    """Accept ordinary headers and SmartPlant Head/Start/End worksheets."""
    with reference_stream(source) as stream:
        raw = pd.read_excel(stream, sheet_name=sheet_name, header=None, dtype=str).fillna('')
    header = next((i for i, row in raw.iterrows() if str(row.iloc[0]).strip().lower() == 'head'), 0)
    data = raw.iloc[header + 1:].copy()
    data.columns = [str(v).strip() for v in raw.iloc[header]]
    if 'Head' in data.columns:
        data = data[~data['Head'].str.strip().str.lower().isin(['start', 'end'])]
        data = data.drop(columns=['Head'])
    data = data.loc[:, data.columns != '']
    return data.loc[(data != '').any(axis=1)].reset_index(drop=True)


def validate_reference_files(match, spec, cat):
    """Return errors before persisting files or changing the active revision."""
    from .component_matcher import COMPONENT_MATCHING_CONFIG, normalize_catalog_name_to_sheet
    errors = []
    try:
        mappings = read_reference_sheet(match, 0)
        if mappings.shape[1] < 4:
            errors.append('Match: expected component name in column A and catalog name in column D.')
        else:
            usable = mappings[(mappings.iloc[:, 0] != '') & (mappings.iloc[:, 3] != '')]
            if usable.empty:
                errors.append('Match: no usable mappings.')
            if usable.iloc[:, 0].str.casefold().duplicated().any():
                errors.append('Match: duplicate component names are ambiguous mappings.')
            cfg = COMPONENT_MATCHING_CONFIG['cat_xlsx']
            for sheet in {normalize_catalog_name_to_sheet(v) for v in usable.iloc[:, 3]}:
                try:
                    rows = read_reference_sheet(cat, sheet)
                    required = [cfg['commodity_code_col'], cfg['material_grade_col']]
                    missing = [col for col in required if col not in rows.columns]
                    if missing or not any(c in rows.columns for c in cfg['npd_cols']) or not any(c in rows.columns for c in cfg['pressure_rating_cols']):
                        errors.append(f'CAT {sheet}: missing commodity, size, rating or material columns ({missing}).')
                    elif rows.empty or not rows[cfg['commodity_code_col']].str.strip().all():
                        errors.append(f'CAT {sheet}: empty catalog or missing commodity codes.')
                except Exception:
                    errors.append(f'CAT: cannot read mapped sheet {sheet}.')
        with reference_stream(spec) as stream:
            with pd.ExcelFile(stream) as book:
                sheets = book.sheet_names
        has_sizes = False
        if 'PipeNominalDiameters' in sheets:
            rows = read_reference_sheet(spec, 'PipeNominalDiameters')
            has_sizes = 'Npd' in rows.columns and not rows.empty
        if 'ServiceLimits' in sheets:
            rows = read_reference_sheet(spec, 'ServiceLimits')
            has_sizes |= {'NominalPipingDiameterFrom', 'NominalPipingDiameterTo'}.issubset(rows.columns) and not rows.empty
        if not has_sizes:
            errors.append('SPEC: populated PipeNominalDiameters.Npd or ServiceLimits diameter ranges required.')
        rows = read_reference_sheet(spec, 'MaterialsData')
        if 'MaterialsGrade' not in rows.columns or rows.empty:
            errors.append('SPEC: populated MaterialsData.MaterialsGrade required.')
    except Exception as exc:
        errors.append(f'Unreadable reference workbook: {type(exc).__name__}.')
    return errors
