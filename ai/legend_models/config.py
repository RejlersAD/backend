"""Soft-coded configuration for the legend sub-sheet ML pipeline.

Everything data-driven lives here: workbook location, sheet registry,
format-detection signals, per-format model hyperparameters and MLflow
naming. Add a new sheet (or retune a model) by editing this file — no
code changes required anywhere else.
"""
from __future__ import annotations

import os
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# ── Workbook ────────────────────────────────────────────────────────────────
# Default legend workbook; override with --workbook or LEGEND_WORKBOOK env.
DEFAULT_WORKBOOK = os.environ.get(
    'LEGEND_WORKBOOK',
    str(REPO_ROOT / 'Documents' / 'Process' / 'Legends' / 'Legend_Phase1.xlsx'),
)

# Directory where extracted per-sheet datasets and local model exports land.
WORK_DIR = Path(os.environ.get('LEGEND_WORK_DIR', str(REPO_ROOT / 'ai' / 'legend_models' / 'data')))

# ── Format types ────────────────────────────────────────────────────────────
# The four sheet archetypes recognised by the format router.
FORMAT_NUMBERING = 'numbering_scheme'   # EXAMPLE row + segment placeholders + code tables
FORMAT_SYMBOLS = 'symbol_library'       # in-cell symbol image + description rows
FORMAT_MATRIX = 'lookup_matrix'         # wide cross-tab (ISA-5.1 letter combinations)
FORMAT_CODEBOOK = 'code_book'           # plain two-column code → description table
FORMAT_COMBINED = 'combined_numbering'  # one segmenter trained over several schemes

FORMAT_TYPES = [FORMAT_NUMBERING, FORMAT_SYMBOLS, FORMAT_MATRIX, FORMAT_CODEBOOK]

# ── Format-router signals (soft-coded weights) ──────────────────────────────
# Each signal contributes its weight to one or more format scores. The router
# picks argmax(score); ties resolve by FORMAT_PRIORITY order.
FORMAT_SIGNALS = {
    # signal name:             weight per format
    'example_row':        {FORMAT_NUMBERING: 3.0},
    'placeholder_rows':   {FORMAT_NUMBERING: 2.0},
    'sectioned_tables':   {FORMAT_NUMBERING: 1.5},
    'anchored_images':    {FORMAT_SYMBOLS: 3.0},
    'error_image_cells':  {FORMAT_SYMBOLS: 2.0},   # '#VALUE!' cells = in-cell images
    'wide_grid':          {FORMAT_MATRIX: 3.0},
    'header_matrix':      {FORMAT_MATRIX: 2.0},
    'two_col_code_desc':  {FORMAT_CODEBOOK: 2.0, FORMAT_NUMBERING: 0.5},
    'uppercase_codes':    {FORMAT_CODEBOOK: 1.0},
}
FORMAT_PRIORITY = [FORMAT_NUMBERING, FORMAT_SYMBOLS, FORMAT_MATRIX, FORMAT_CODEBOOK]

# Signal thresholds (soft-coded)
SIGNAL_THRESHOLDS = {
    'placeholder_re': r'^[X9A]{1,6}$',      # segment placeholder shape, e.g. XX, XXX
    'section_re': r'^([a-z]\.|\d+\.)\s*\S',  # 'a. ITEM SYMBOLS' / '1. AREA DESIGNATION'
    # 'EXAMPLE:' (Phase 1 style) or 'Format:' (Phase 2 style) introduces the
    # tag pattern row of a numbering scheme.
    'example_re': r'(EXAMPLE|FORMAT)\s*:',
    'wide_grid_min_cols': 5,                 # >= this many columns => wide
    'min_rows': 3,
}

# ── Numbering-scheme sheet parsing extensions (soft-coded) ──────────────────
# Some legend sheets (e.g. LEGEND_PHASE2.xlsx "Line Number") draw the segment
# meanings as an ASCII tree under the Format row and lay their code tables out
# as several code→description column pairs under plain-text category headers
# (no 'a.'/'1.' prefixes).  These switches enable that layout without
# disturbing the classic Phase 1 layout parsing.
NUMBERING_PARSE = {
    # characters that make up the ASCII tree art in meaning rows
    'diagram_chars': '|│├└─╰╭ ',
    # tree diagrams list the FIRST segment at the BOTTOM — reverse row order
    'diagram_reverse_order': True,
    # meaning text that points at the sheet's code tables (codes get merged
    # into that segment), e.g. 'LINE DESIGNATION CODE (SEE BELOW)'.
    # 'SEE ABOVE' is deliberately excluded — it refers to notes, not tables.
    'see_table_re': r'SEE\s+BELOW',
    # a cell is a code when it matches this (else it may be a header/desc)
    'code_cell_re': r'^[A-Z0-9]{1,6}$',
    # max rows of tree diagram expected right below the example/format row
    'diagram_max_rows': 20,
}

# ── Sheet registry ──────────────────────────────────────────────────────────
# Soft-coded per-sheet overrides. Key = exact worksheet title.
#   key:         slug used for dataset dirs, MLflow experiment & model names
#   format_hint: skip auto-detection and force this format (None = auto)
#   enabled:     set False to skip a sheet entirely
# Sheets absent from this registry are auto-keyed (slugified title) and
# auto-detected — the pipeline never hard-codes the workbook's sheet list.
SHEET_REGISTRY = {
    'Equipment Numbering':        {'key': 'equipment_numbering', 'format_hint': None, 'enabled': True},
    'EXISTING P&IDs':             {'key': 'existing_pids',       'format_hint': None, 'enabled': True},
    'Line Numbering':             {'key': 'line_numbering',      'format_hint': None, 'enabled': True},
    'INSTRUMENT TYPICAL LETTER':  {'key': 'instrument_letters',  'format_hint': None, 'enabled': True},
    # LEGEND_PHASE2.xlsx — Phase 2 / polyolefins line-list scheme.
    # Trained standalone as legend_line_list_phase2 and also merged into the
    # combined "Line List" model (legend_line_list, see COMBINED_MODELS).
    'Line Number':                {'key': 'line_list_phase2',    'format_hint': FORMAT_NUMBERING, 'enabled': True},
    # LEGEND_PHASE2.xlsx — second line-number sequence observed on Phase 2
    # P&IDs: SIZE-FLUID-SPEC-SERIAL-DEVIATION-INSULATION
    # (e.g. 3"-VG-XXXX-013461-Y-N).  Trained standalone as
    # legend_line_list_phase2_seq2 and merged into legend_line_list.
    'Line Number Seq 2':          {'key': 'line_list_phase2_seq2', 'format_hint': FORMAT_NUMBERING, 'enabled': True},
    # Every other sheet (VALVE, EQUIPMENT SYMBOLS, Actuator Symbols, ...)
    # is auto-keyed and auto-detected — listed nowhere.
}

# ── Per-format model hyperparameters (soft-coded) ───────────────────────────
MODEL_PARAMS = {
    FORMAT_NUMBERING: {
        'synthetic_samples': 2000,      # synthetic tags generated from the sheet's own code tables
        'eval_fraction': 0.25,
        'classifier': 'logreg',         # 'logreg' | 'rf'
        'max_iter': 500,
        'context_window': 1,            # prev/next token shape features
        'optional_keep_prob': 0.5,      # how often optional segments appear in synthetic tags
        'random_state': 42,
    },
    FORMAT_SYMBOLS: {
        'image_size': 32,               # px, grayscale square
        'augmentations': ['orig', 'flip_h', 'rot_p10', 'rot_m10'],  # soft-coded augmentation set
        'classifier': 'knn',            # 'knn' | 'centroid'
        'knn_k': 3,
        'knn_weights': 'distance',      # exact glyph must outvote near-duplicate augmentations
        'distance': 'cosine',
        'eval_fraction': 0.3,
    },
    FORMAT_MATRIX: {
        'ngram_range': [1, 3],          # char n-grams over letter combinations
        'negative_ratio': 2.0,          # synthetic invalid combos per valid combo
        'classifier': 'logreg',
        'max_iter': 500,
        'eval_fraction': 0.25,
        'random_state': 42,
    },
    FORMAT_CODEBOOK: {
        'ngram_range': [2, 5],          # char n-grams over descriptions
        'top_k': 3,
        'min_similarity': 0.0,
    },
}

# ── Combined cross-scheme models (soft-coded) ───────────────────────────────
# One segmenter trained over SEVERAL line-numbering schemes (possibly from
# different workbooks), with canonical field labels and automatic scheme
# routing at inference.  Each entry produces one registered model
# legend_<key>.  Sources are parsed with the same numbering-scheme rules;
# field_map translates each sheet's field keys to canonical fields so one
# classifier learns "size / fluid_code / serial / …" across every format.
LEGENDS_DIR = REPO_ROOT / 'Documents' / 'Process' / 'Legends'

COMBINED_MODELS = {
    # The "Line List" model — one model for every line-list format.
    'line_list': {
        'description': 'Phase 1 (Line Numbering) + Phase 2 (Line Number, Line Number Seq 2) line-list schemes',
        'sources': [
            {'workbook': LEGENDS_DIR / 'Legend_Phase1.xlsx', 'sheet': 'Line Numbering'},
            {'workbook': LEGENDS_DIR / 'LEGEND_PHASE2.xlsx', 'sheet': 'Line Number'},
            {'workbook': LEGENDS_DIR / 'LEGEND_PHASE2.xlsx', 'sheet': 'Line Number Seq 2'},
        ],
        'field_map': {
            # Phase 1: XX-XX-XXXX-XXXX-X
            'Line Numbering': {
                'pipe_size': 'size',
                'services_identifier': 'fluid_code',
                'line_classification': 'piping_class',
                'line_number': 'sequence_no',
                'insulation_class': 'insulation',
            },
            # Phase 2: XX-XX-XX-AXXXX-XXXXXXX-XX (-XXXX)?
            'Line Number': {
                'nominal_pipe_size_inches': 'size',
                'unit_system_number': 'unit',
                'line_designation_code_see_below': 'fluid_code',
                'serial_number_first_character_for_area': 'serial',
                'piping_service_class': 'piping_class',
                'coating_insulated_traced_or_jacketed_see_above': 'coating',
                'stream_polyolefins_only': 'stream',
            },
            # Phase 2 second sequence: XX-XX-XXXX-XXXXXX-X-X
            # e.g. 3"-VG-XXXX-013461-Y-N
            'Line Number Seq 2': {
                'nominal_pipe_size_inches': 'size',
                'line_designation_code_see_below': 'fluid_code',
                'piping_specification': 'piping_class',
                'line_sequence_number': 'sequence_no',
                'department_deviation': 'deviation',
                'insulation_class': 'insulation',
            },
        },
    },
}

# ── MLflow naming (soft-coded) ──────────────────────────────────────────────
EXPERIMENT_PREFIX = 'legend'                    # experiment per sheet: legend/<sheet_key>
ORCHESTRATOR_EXPERIMENT = 'legend_orchestrator' # single meta-run tying all sheets together
ROUTER_MODEL_NAME = 'legend_format_router'      # registered name of the format router model
MODEL_NAME_TEMPLATE = 'legend_{sheet_key}'      # registered model name per sheet
CHAMPION_ALIAS = 'champion'
EXPORT_DIRNAME = 'export'                       # artifact sub-path inside each run


def sheet_key(title: str) -> str:
    """Soft-coded sheet title → stable slug (registry override, else slugify)."""
    entry = SHEET_REGISTRY.get(title)
    if entry and entry.get('key'):
        return entry['key']
    slug = re.sub(r'[^a-z0-9]+', '_', title.strip().lower()).strip('_')
    return slug or 'sheet'


def sheet_enabled(title: str) -> bool:
    return SHEET_REGISTRY.get(title, {}).get('enabled', True)


def sheet_format_hint(title: str) -> str | None:
    return SHEET_REGISTRY.get(title, {}).get('format_hint')


def experiment_name(key: str) -> str:
    return f'{EXPERIMENT_PREFIX}/{key}'


def model_name(key: str) -> str:
    return MODEL_NAME_TEMPLATE.format(sheet_key=key)
