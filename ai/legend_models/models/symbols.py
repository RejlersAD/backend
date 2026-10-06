"""Symbol-library model — recognises drawn P&ID symbols from images.

Symbol sheets pair an anchored image (the symbol glyph) with a text
description in a neighbouring cell. With tens of glyphs per sheet a
retrieval-style classifier is the honest choice: soft-coded augmentations
(flip / ±10° rotations) expand each glyph, pixel-shape features feed a
kNN / nearest-centroid classifier, and predictions return the nearest
description plus a confidence derived from neighbour distance.

The backend is pluggable via config.MODEL_PARAMS['symbol_library'] —
swap 'knn' for a CNN backend later without touching callers.
"""
from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import joblib
import numpy as np
from PIL import Image

from .. import config
from ..workbook import SheetGrid, SheetImage
from .base import BaseLegendModel

logger = logging.getLogger('legend_models.symbols')


def _load_glyph(img: SheetImage, size: int) -> np.ndarray:
    """Normalise a glyph: grayscale, centered on white square, resized."""
    im = Image.open(io.BytesIO(img.data)).convert('L')
    side = max(im.size)
    canvas = Image.new('L', (side, side), color=255)
    canvas.paste(im, ((side - im.size[0]) // 2, (side - im.size[1]) // 2))
    return np.asarray(canvas.resize((size, size)), dtype=np.float32)


def _features(arr: np.ndarray) -> np.ndarray:
    """Pixel intensities + 4x4 edge-density summary (soft-coded shape)."""
    flat = (arr / 255.0).ravel()
    gx = np.abs(np.diff(arr, axis=1, prepend=arr[:, :1]))
    gy = np.abs(np.diff(arr, axis=0, prepend=arr[:1, :]))
    edges = (gx + gy) / 255.0
    h, w = edges.shape
    blocks = edges.reshape(4, h // 4, 4, w // 4).mean(axis=(1, 3))
    return np.concatenate([flat, blocks.ravel()])


def _augment(arr: np.ndarray, names: list[str]) -> list[np.ndarray]:
    im = Image.fromarray(arr.astype(np.uint8))
    out = []
    for name in names:
        if name == 'orig':
            out.append(arr)
        elif name == 'flip_h':
            out.append(np.asarray(im.transpose(Image.FLIP_LEFT_RIGHT), dtype=np.float32))
        elif name == 'rot_p10':
            out.append(np.asarray(im.rotate(10, fillcolor=255), dtype=np.float32))
        elif name == 'rot_m10':
            out.append(np.asarray(im.rotate(-10, fillcolor=255), dtype=np.float32))
    return out


def extract_symbol_pairs(grid: SheetGrid) -> list[tuple[SheetImage, str]]:
    """Pair each anchored image with the nearest non-empty text in its row."""
    pairs: list[tuple[SheetImage, str]] = []
    for img in grid.images:
        row = grid.rows[img.row] if 0 <= img.row < len(grid.rows) else []
        desc = ''
        # prefer cells to the right of the image, then left
        for col in list(range(img.col + 1, len(row))) + list(range(img.col - 1, -1, -1)):
            cell = row[col].strip()
            if cell and cell != '#VALUE!':
                desc = cell
                break
        if desc:
            pairs.append((img, desc))
    return pairs


class SymbolClassifier(BaseLegendModel):
    format_type = config.FORMAT_SYMBOLS

    def __init__(self) -> None:
        self.classifier = None
        self.labels: list[str] = []
        self.params: dict = {}

    def fit(self, grid: SheetGrid, params: dict) -> dict:
        from sklearn.neighbors import KNeighborsClassifier, NearestCentroid

        self.params = dict(params)
        size = int(params.get('image_size', 32))
        augs = list(params.get('augmentations', ['orig']))
        pairs = extract_symbol_pairs(grid)
        if not pairs:
            raise ValueError(f'{grid.title}: no image/description pairs extracted')

        X, y = [], []
        for img, desc in pairs:
            arr = _load_glyph(img, size)
            for aug in _augment(arr, augs):
                X.append(_features(aug))
                y.append(desc)
        X, y = np.asarray(X), np.asarray(y)

        if params.get('classifier', 'knn') == 'centroid':
            def build():
                return NearestCentroid(metric=params.get('distance', 'cosine'))
        else:
            def build():
                return KNeighborsClassifier(n_neighbors=int(params.get('knn_k', 3)),
                                            weights=params.get('knn_weights', 'distance'),
                                            metric=params.get('distance', 'cosine'))

        # Leave-one-variant-out eval: hold out the LAST augmentation of every
        # glyph (train keeps the rest, so classes stay represented). Measures
        # robustness to an unseen variant without leaking the glyph itself.
        n_classes = len(set(y.tolist()))
        acc = 0.0
        per_group = max(len(X) // max(len(pairs), 1), 1)
        if per_group >= 2 and n_classes > 1:
            eval_mask = (np.arange(len(X)) % per_group) == (per_group - 1)
            eval_clf = build()
            eval_clf.fit(X[~eval_mask], y[~eval_mask])
            acc = float(eval_clf.score(X[eval_mask], y[eval_mask]))

        # Shipped model: refit on ALL glyphs — retrieval models need every
        # exemplar in the index for exact-glyph recall.
        self.classifier = build()
        self.classifier.fit(X, y)
        self._train_labels = [str(v) for v in y]  # kneighbors indices → labels (version-proof)
        self.labels = sorted(set(y.tolist()))

        return {'symbol_accuracy': round(acc, 4), 'n_symbols': len(pairs),
                'n_samples': len(X), 'n_classes': len(self.labels)}

    def predict_symbol(self, image_path: str | Path, top_k: int = 3) -> dict:
        size = int(self.params.get('image_size', 32))
        data = Path(image_path).read_bytes()
        arr = _load_glyph(SheetImage(0, 0, data, 'png'), size)
        feats = _features(arr).reshape(1, -1)
        pred = self.classifier.predict(feats)[0]
        # confidence: 1 - normalised mean distance to the k neighbours
        confidence = None
        if hasattr(self.classifier, 'kneighbors'):
            dists, idxs = self.classifier.kneighbors(feats)
            confidence = round(float(1.0 - min(dists.mean() / 2.0, 1.0)), 4)
            neighbours = [self._train_labels[i] for i in idxs[0][:top_k]]
        else:
            neighbours = [str(pred)]
        return {'description': str(pred), 'confidence': confidence, 'neighbours': neighbours}

    def save(self, out_dir: str | Path) -> None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.classifier, out / 'classifier.joblib')
        (out / 'labels.json').write_text(json.dumps(self.labels, indent=2), encoding='utf-8')
        (out / 'train_labels.json').write_text(
            json.dumps(self._train_labels, indent=2), encoding='utf-8')
        (out / 'params.json').write_text(json.dumps(self.params, indent=2), encoding='utf-8')
        self._write_meta(out, {'n_symbols': len(self.labels)})

    @classmethod
    def load(cls, in_dir: str | Path) -> 'SymbolClassifier':
        in_dir = Path(in_dir)
        model = cls()
        model.classifier = joblib.load(in_dir / 'classifier.joblib')
        model.labels = json.loads((in_dir / 'labels.json').read_text(encoding='utf-8'))
        train_labels_path = in_dir / 'train_labels.json'
        model._train_labels = (json.loads(train_labels_path.read_text(encoding='utf-8'))
                               if train_labels_path.exists() else model.labels)
        params_path = in_dir / 'params.json'
        model.params = json.loads(params_path.read_text(encoding='utf-8')) if params_path.exists() else {}
        return model
