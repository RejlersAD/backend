"""Code-book model — fuzzy lookup over two-column code → description sheets.

Simple legend sheets (abbreviations, service codes, instrument prefixes)
are code tables. The model indexes descriptions with char-n-gram TF-IDF
and answers nearest-neighbour queries, so OCR-extracted or free-text
descriptions map back to canonical legend codes with a similarity score.
"""
from __future__ import annotations

import json
from pathlib import Path

import joblib

from .. import config
from ..workbook import SheetGrid
from .base import BaseLegendModel
from .numbering import _split_codes


def parse_code_book(grid: SheetGrid) -> dict[str, str]:
    """Extract {code: description} from a two-column sheet (headers skipped)."""
    codes: dict[str, str] = {}
    for row in grid.rows:
        first = row[0] if row else ''
        second = row[1] if len(row) > 1 else ''
        pair = _split_codes(first, second)
        if pair and pair[0].upper() not in {'CODE', 'SYMBOL', 'ABBREVIATION', 'ITEM'}:
            codes[pair[0]] = pair[1]
    if not codes:
        raise ValueError(f'{grid.title}: no code rows parsed')
    return codes


class CodeBookModel(BaseLegendModel):
    format_type = config.FORMAT_CODEBOOK

    def __init__(self) -> None:
        self.codes: dict[str, str] = {}
        self.pipeline = None  # {'vectorizer': ..., 'nn': ...}

    def fit(self, grid: SheetGrid, params: dict) -> dict:
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.neighbors import NearestNeighbors

        self.codes = parse_code_book(grid)
        descriptions = list(self.codes.values())
        vectorizer = TfidfVectorizer(analyzer='char_wb',
                                     ngram_range=tuple(params.get('ngram_range', [2, 5])),
                                     lowercase=True)
        X = vectorizer.fit_transform(descriptions)
        nn = NearestNeighbors(metric='cosine', n_neighbors=min(int(params.get('top_k', 3)), len(descriptions)))
        nn.fit(X)
        self.pipeline = {'vectorizer': vectorizer, 'nn': nn}

        # self-consistency: each exact description must retrieve its own code
        hits = sum(
            1 for code, desc in self.codes.items()
            if self.lookup(desc, top_k=1)[0]['code'] == code
        )
        return {'self_retrieval_accuracy': round(hits / len(self.codes), 4),
                'n_codes': len(self.codes)}

    def lookup(self, description: str, top_k: int | None = None) -> list[dict]:
        codes = list(self.codes.keys())
        vec, nn = self.pipeline['vectorizer'], self.pipeline['nn']
        k = min(top_k or int(nn.n_neighbors), len(codes))
        dists, idxs = nn.kneighbors(vec.transform([description]), n_neighbors=k)
        return [
            {'code': codes[i], 'description': self.codes[codes[i]],
             'similarity': round(1.0 - float(d), 4)}
            for d, i in zip(dists[0], idxs[0])
        ]

    def save(self, out_dir: str | Path) -> None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.pipeline, out / 'pipeline.joblib')
        (out / 'codes.json').write_text(json.dumps(self.codes, indent=2), encoding='utf-8')
        self._write_meta(out, {'n_codes': len(self.codes)})

    @classmethod
    def load(cls, in_dir: str | Path) -> 'CodeBookModel':
        in_dir = Path(in_dir)
        model = cls()
        model.pipeline = joblib.load(in_dir / 'pipeline.joblib')
        model.codes = json.loads((in_dir / 'codes.json').read_text(encoding='utf-8'))
        return model
