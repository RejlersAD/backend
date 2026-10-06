"""Soft-coded model registry: format type → model class.

Adding a new model family = add a module here and one registry entry;
train.py and infer.py pick it up with zero further changes.
"""
from __future__ import annotations

from .. import config
from .base import BaseLegendModel
from .codebook import CodeBookModel
from .matrix import InstrumentMatrixModel
from .numbering import CombinedNumberingModel, NumberingSchemeModel
from .symbols import SymbolClassifier

MODEL_REGISTRY: dict[str, type[BaseLegendModel]] = {
    config.FORMAT_NUMBERING: NumberingSchemeModel,
    config.FORMAT_SYMBOLS: SymbolClassifier,
    config.FORMAT_MATRIX: InstrumentMatrixModel,
    config.FORMAT_CODEBOOK: CodeBookModel,
    config.FORMAT_COMBINED: CombinedNumberingModel,
}

__all__ = [
    'MODEL_REGISTRY', 'BaseLegendModel', 'NumberingSchemeModel',
    'CombinedNumberingModel',
    'SymbolClassifier', 'InstrumentMatrixModel', 'CodeBookModel',
]
