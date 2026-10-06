"""Lookup-matrix model — ISA-5.1 style instrument letter combination sheets.

The matrix crosses first letters (rows) with device categories (columns);
each populated cell is a valid letter combination (e.g. F + Recording →
'FRC'). The model learns char-n-gram patterns of valid combinations and
scores arbitrary combinations as valid/invalid, while a combo index keeps
exact category memberships for deterministic lookup.
"""
from __future__ import annotations

import json
import random
import re
import string
from pathlib import Path

import joblib

from .. import config
from ..workbook import SheetGrid
from .base import BaseLegendModel


def parse_matrix(grid: SheetGrid) -> dict:
    """Extract {first_letter: {category: combo}} from an ISA-style cross-tab."""
    rows = [r for r in grid.rows if any(r)]
    if len(rows) < 3:
        raise ValueError(f'{grid.title}: matrix too small')

    # header row = the row with the most non-empty cells among the first 3
    header_idx = max(range(min(3, len(rows))),
                     key=lambda i: sum(1 for c in rows[i] if c))
    header = rows[header_idx]
    categories = [c.strip() or f'COL_{i}' for i, c in enumerate(header)]

    combos: dict[str, dict[str, str]] = {}
    for row in rows[header_idx + 1:]:
        first = row[0].strip() if row else ''
        if not first or len(first) > 4 or not re.match(r'^[A-Z]+$', first):
            continue
        for col, cell in enumerate(row[1:], start=1):
            combo = cell.strip()
            if combo and re.match(r'^[A-Z][A-Z0-9/]*$', combo):
                combos.setdefault(first, {})[categories[col]] = combo
    if not combos:
        raise ValueError(f'{grid.title}: no letter combinations parsed')
    return {'categories': categories, 'combos': combos}


class InstrumentMatrixModel(BaseLegendModel):
    format_type = config.FORMAT_MATRIX

    def __init__(self) -> None:
        self.matrix: dict = {}
        self.pipeline = None

    def fit(self, grid: SheetGrid, params: dict) -> dict:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.linear_model import LogisticRegression
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline

        self.matrix = parse_matrix(grid)
        valid = sorted({combo for cats in self.matrix['combos'].values() for combo in cats.values()})
        rng = random.Random(params.get('random_state', 42))

        # synthetic negatives: random letter strings of matching length
        negatives = set()
        n_neg = int(len(valid) * float(params.get('negative_ratio', 2.0)))
        while len(negatives) < n_neg:
            cand = ''.join(rng.choices(string.ascii_uppercase, k=rng.randint(2, 4)))
            if cand not in valid:
                negatives.add(cand)

        X = valid + sorted(negatives)
        y = [1] * len(valid) + [0] * len(negatives)
        X_tr, X_ev, y_tr, y_ev = train_test_split(
            X, y, test_size=float(params.get('eval_fraction', 0.25)),
            random_state=params.get('random_state', 42), stratify=y)

        self.pipeline = Pipeline([
            ('tfidf', TfidfVectorizer(analyzer='char',
                                      ngram_range=tuple(params.get('ngram_range', [1, 3])))),
            ('clf', LogisticRegression(max_iter=int(params.get('max_iter', 500)))),
        ])
        self.pipeline.fit(X_tr, y_tr)
        acc = float(self.pipeline.score(X_ev, y_ev))
        return {'combo_accuracy': round(acc, 4), 'n_valid_combos': len(valid),
                'n_first_letters': len(self.matrix['combos'])}

    def predict_combo(self, letters: str) -> dict:
        letters = letters.strip().upper()
        proba = float(self.pipeline.predict_proba([letters])[0][1])
        # deterministic membership: which (first_letter, category) cells hold it
        memberships = [
            {'first_letter': fl, 'category': cat}
            for fl, cats in self.matrix.get('combos', {}).items()
            for cat, combo in cats.items() if combo == letters
        ]
        return {'letters': letters, 'valid_probability': round(proba, 4),
                'is_known_combo': bool(memberships), 'memberships': memberships}

    def save(self, out_dir: str | Path) -> None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, out / 'pipeline.joblib')
        (out / 'matrix.json').write_text(json.dumps(self.matrix, indent=2), encoding='utf-8')
        self._write_meta(out, {'n_first_letters': len(self.matrix.get('combos', {}))})

    @classmethod
    def load(cls, in_dir: str | Path) -> 'InstrumentMatrixModel':
        in_dir = Path(in_dir)
        model = cls()
        model.pipeline = joblib.load(in_dir / 'pipeline.joblib')
        model.matrix = json.loads((in_dir / 'matrix.json').read_text(encoding='utf-8'))
        return model
