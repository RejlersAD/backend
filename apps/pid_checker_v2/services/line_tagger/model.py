"""Model definition for line_tagger — LayoutLMv3 token classification.

Thin wrapper so the base checkpoint + label count come from config and the
model can be constructed identically for training and inference.
"""
from __future__ import annotations

from .config import BASE_MODEL, NUM_LABELS, ID2LABEL, LABEL2ID


def build_model():
    """Return a LayoutLMv3ForTokenClassification configured for our labels."""
    from transformers import LayoutLMv3ForTokenClassification
    model = LayoutLMv3ForTokenClassification.from_pretrained(
        BASE_MODEL,
        num_labels=NUM_LABELS,
        id2label=ID2LABEL,
        label2id=LABEL2ID,
    )
    return model


def resolve_device(preference: str = 'auto'):
    """Pick the compute device ('cuda' if available, else 'cpu')."""
    import torch
    if preference == 'cpu':
        return torch.device('cpu')
    if preference == 'cuda' and torch.cuda.is_available():
        return torch.device('cuda')
    return torch.device('cuda' if torch.cuda.is_available() else 'cpu')
