"""Inference entry point for line_tagger.

The backend calls tag_line_text() for rows the regex parser flagged as
low-confidence. It returns structured fields keyed by the backend's column
names (size, fluid_code, piping_spec, sequence_no, from_line, to_line), plus a
per-field confidence so the caller can decide whether to override.

Design: lazy-loads the fine-tuned model once per process; if no trained
weights exist yet (config.EXPORT_DIR empty), it returns an empty dict and the
caller silently falls back to the regex result — so shipping this package
before training never breaks extraction.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from .config import EXPORT_DIR, INFER, LABELS, BASE_MODEL
from .model import resolve_device

logger = logging.getLogger('line_tagger.infer')

_cache = None  # (model, tokenizer_backend, id2label, device)


def _model_available() -> bool:
    return (EXPORT_DIR / 'config.json').exists()


def _load():
    """Lazy-load model + tokenizer once per process.

    transformers v5 removed the fast-tokenizer is_split_into_words path, so
    instead of the token-classification pipeline we run the model forward
    directly, encoding words via the tokenizers-library backend
    (tokenizer._tokenizer) which accepts plain strings.
    """
    global _cache
    if _cache is not None:
        return _cache
    if not _model_available():
        return None
    import torch
    from transformers import AutoTokenizer, LayoutLMv3ForTokenClassification

    device = resolve_device(INFER['device'])
    # Tokenizer always comes from the base checkpoint — the export dir may
    # not contain tokenizer files, and falling back to config.json gives a
    # degenerate tokenizer.
    tok = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = LayoutLMv3ForTokenClassification.from_pretrained(str(EXPORT_DIR))
    model.to(device).eval()
    id2label = {int(k): v for k, v in model.config.id2label.items()}
    backend = getattr(tok, '_tokenizer', None)  # tokenizers.Tokenizer core
    if backend is None:
        logger.warning('line_tagger: tokenizer backend unavailable; ML disabled')
        return None
    _cache = (model, backend, id2label, device, tok)
    return _cache


def tag_line_text(text: str) -> dict[str, Any]:
    """Tag a single raw line-detection string into structured fields.

    Returns {} when no trained model is present (graceful no-op) or when the
    input is blank. Otherwise returns e.g.:
        {'size': '2"', 'fluid_code': 'D', 'piping_spec': '033842',
         'sequence_no': '6152', 'from_line': '', 'to_line': '',
         'confidence': 0.92}
    """
    text = (text or '').strip()
    if not text or not _model_available():
        return {}
    loaded = _load()
    if loaded is None:
        return {}
    model, backend, id2label, device, tok = loaded

    # Split the raw tag on its separators into word tokens (matching the
    # training-time granularity).
    tokens = [t for t in re.split(r'[-/\s"]+', text) if t]
    if not tokens:
        return {}

    try:
        import torch

        max_len = INFER['max_seq_len']
        ids = [tok.cls_token_id]
        word_ids = [None]
        for i, t in enumerate(tokens):
            piece = t if i == 0 else ' ' + t
            sub = backend.encode(piece, add_special_tokens=False).ids
            if not sub or len(ids) + len(sub) >= max_len:
                break
            ids.extend(sub)
            word_ids.extend([i] * len(sub))
        ids.append(tok.sep_token_id)
        word_ids.append(None)
        n = len(ids)

        input_ids = torch.tensor([ids], dtype=torch.long, device=device)
        attention_mask = torch.ones((1, n), dtype=torch.long, device=device)
        bbox = torch.zeros((1, n, 4), dtype=torch.long, device=device)

        with torch.no_grad():
            logits = model(input_ids=input_ids, attention_mask=attention_mask, bbox=bbox).logits
        probs = torch.softmax(logits, dim=-1)[0]  # (seq, num_labels)

        # First-subword label per word token (standard BIO practice).
        word_preds: dict[int, tuple[str, float]] = {}
        prev = None
        for pos, wid in enumerate(word_ids):
            if wid is None or wid == prev:
                prev = wid
                continue
            prev = wid
            conf, lid = probs[pos].max(dim=-1)
            word_preds[wid] = (id2label[int(lid)], float(conf))

        entities = [
            {'entity_group': word_preds[i][0], 'word': tokens[i], 'score': word_preds[i][1]}
            for i in range(len(tokens))
        ]
    except Exception as e:  # noqa: BLE001 — never break extraction on ML error
        logger.warning('line_tagger inference failed: %s', e)
        return {}

    # Aggregate entity spans → field values (LayoutLM labels carry the field).
    label_to_field = {
        'SIZE': 'size', 'SERVICE': 'fluid_code', 'SPEC': 'piping_spec',
        'SERIAL': 'sequence_no', 'FROM': 'from_line', 'TO': 'to_line',
    }
    out: dict[str, Any] = {}
    scores = []
    for ent in entities:
        grp = str(ent.get('entity_group') or ent.get('entity') or '').upper().lstrip('BI-')
        field = label_to_field.get(grp)
        if not field:
            continue
        word = str(ent.get('word') or '').strip()
        score = float(ent.get('score') or 0.0)
        if score >= INFER['accept_field_threshold']:
            out[field] = (out.get(field, '') + word).strip()
        scores.append(score)

    if scores:
        out['confidence'] = round(sum(scores) / len(scores), 3)
    return out
