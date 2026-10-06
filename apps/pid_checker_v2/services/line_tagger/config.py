"""Soft-coded configuration for the line_tagger package.

Every tunable lives here — model id, label set, training hyperparameters,
paths. No magic values in the training/inference code; edit this file only.
"""
from __future__ import annotations

import os
from pathlib import Path

# ─── Paths (resolved relative to this package so it runs from anywhere) ─────
PACKAGE_DIR = Path(__file__).resolve().parent
DATA_DIR    = Path(os.environ.get('LINE_TAGGER_DATA_DIR', PACKAGE_DIR / 'data'))
EXPORT_DIR  = Path(os.environ.get('LINE_TAGGER_EXPORT_DIR', PACKAGE_DIR / 'export'))
EXPORT_DIR.mkdir(parents=True, exist_ok=True)

# ─── Labels (BIO) ───────────────────────────────────────────────────────────
# Order matters — index 0 must be the 'O' (outside) label for stability.
LABELS = [
    'O',
    'B-SIZE', 'I-SIZE',
    'B-SERVICE', 'I-SERVICE',
    'B-SPEC', 'I-SPEC',
    'B-SERIAL', 'I-SERIAL',
    'B-FROM', 'I-FROM',
    'B-TO', 'I-TO',
]
LABEL2ID = {l: i for i, l in enumerate(LABELS)}
ID2LABEL = {i: l for l, i in LABEL2ID.items()}
NUM_LABELS = len(LABELS)

# ─── Base model ─────────────────────────────────────────────────────────────
# LayoutLMv3 uses word text + bounding-box position — exactly what a P&ID tag
# needs (position disambiguates identical characters). 'base' is a good
# accuracy/speed balance; drop to a smaller checkpoint if training is slow.
BASE_MODEL = os.environ.get('LINE_TAGGER_BASE_MODEL', 'microsoft/layoutlmv3-base')

# ─── Training hyperparameters ───────────────────────────────────────────────
TRAIN = {
    'max_seq_len':        512,
    'batch_size':         4,
    'epochs':             8,
    'learning_rate':      3e-5,
    'weight_decay':       0.01,
    'warmup_ratio':       0.1,
    'seed':               42,
    'eval_split':         0.15,   # fraction of samples held out for eval
    'early_stop_patience': 3,
    # Mixed precision when a GPU is available; harmless on CPU.
    'fp16':               True,
}

# ─── Inference ──────────────────────────────────────────────────────────────
INFER = {
    # Only rows whose regex-parse confidence is below this go to the model.
    'fallback_confidence_threshold': 0.6,
    # Minimum mean token-score for a model field to override the regex value.
    'accept_field_threshold':        0.75,
    'max_seq_len':                   512,
    'device':                        os.environ.get('LINE_TAGGER_DEVICE', 'auto'),  # auto|cpu|cuda
}

# ─── Field → label mapping (for assembling structured output) ───────────────
# Maps the model's entity labels to the backend's line-list column keys
# (backend COLUMNS / line_list_parser row fields).
FIELD_LABELS = {
    'size':        'SIZE',
    'fluid_code':  'SERVICE',
    'piping_spec': 'SPEC',
    'sequence_no': 'SERIAL',
    'from_line':   'FROM',
    'to_line':     'TO',
}
