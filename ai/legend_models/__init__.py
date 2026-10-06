"""Per-sub-sheet ML models for engineering legend workbooks.

Pipeline: extract (workbook.py) → detect format (formats.py) → train one
model per sheet (models/) → orchestrate with MLflow (train.py / infer.py).
"""
from __future__ import annotations

from . import config  # noqa: F401  (soft-coded registry lives here)

__version__ = '0.1.0'
