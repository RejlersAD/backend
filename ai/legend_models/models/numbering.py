"""Numbering-scheme model — learns tag segmentation for sheets like
'Equipment Numbering' (XX-XXX-XX) and 'EXISTING P&IDs' (MRI - O 91 - 011).

Approach
--------
1. Parse the sheet's own specification:
     EXAMPLE row          → separator + segment pattern
     placeholder rows     → segment meanings, in order
     lettered sections    → code tables (value → description) per segment
2. Generate synthetic tags by sampling each segment from its code table
   (or from the placeholder shape when no table maps to it).
3. Train a per-token sklearn classifier with shape + context-window
   features to label each tag segment with its field meaning.
4. Export a pid_checker_v2-compatible legend definition (composite regex
   + ordered fields) so the model's knowledge plugs straight into the
   existing legend engine.

Everything (sample counts, classifier, window) is soft-coded in config.
"""
from __future__ import annotations

import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path

import joblib

from .. import config
from ..workbook import SheetGrid
from .base import BaseLegendModel

logger = logging.getLogger('legend_models.numbering')

_FIELD_SLUG_RE = re.compile(r'[^A-Z0-9]+')
_SECTION_HEADER_RE = re.compile(r'^\s*(?:[a-z]|\d+)\.\s*')  # 'a. ' / '1. ' prefix only


def _word_tokens(text: str) -> set[str]:
    """Upper-cased words with trailing plural 'S' stripped for soft matching."""
    return {w.rstrip('S') if len(w) > 2 else w
            for w in re.findall(r'[A-Z0-9]+', text.upper())}


@dataclass
class SegmentSpec:
    placeholder: str
    meaning: str
    field_key: str
    codes: dict[str, str] = field(default_factory=dict)  # value → description
    optional: bool = False  # e.g. trailing STREAM suffix shown as <XXXX>


def _diagram_meanings(grid: SheetGrid, example_idx: int) -> list[tuple[str, str]]:
    """Extract (placeholder, meaning) pairs from an ASCII-tree format diagram.

    Sheets like LEGEND_PHASE2 'Line Number' draw the segment meanings as a
    tree under the Format row instead of two-cell placeholder/meaning rows:

        Format:  XX - XX - XX - AXXXX - XXXXXXX - XX          | <XXXX> STREAM
                  |    |    |    |       |          └── COATING/INSULATED ...
                  |    |    |    |       └───── PIPING SERVICE CLASS
                  ...
                  └──────────────────── NOMINAL PIPE SIZE (INCHES)

    The tree lists the FIRST segment at the BOTTOM, so the collected order is
    reversed (soft-coded: NUMBERING_PARSE['diagram_reverse_order']).
    Cells that are pure tree art or pure parenthesised notes are skipped.
    Returns [] when the sheet has no diagram (classic layout).
    """
    dp = config.NUMBERING_PARSE
    strip_chars = dp['diagram_chars']
    out: list[tuple[str, str]] = []
    for row in grid.rows[example_idx + 1: example_idx + 1 + int(dp['diagram_max_rows'])]:
        cells = [c for c in row if c and c.strip()]
        if not cells:
            continue
        joined = ' '.join(cells)
        has_marker = ('<' in joined) or any(ch in joined for ch in ('└', '├', '│', '╰'))
        if not has_marker and out:
            break  # diagram region finished
        if not has_marker:
            continue
        for cell in cells:
            ph = ''
            m_ph = re.search(r'<([^>]+)>', cell)
            if m_ph:
                ph = m_ph.group(1).strip()
            text = re.sub(r'<[^>]*>', ' ', cell)
            text = text.strip(strip_chars).strip()
            text = re.sub(r'^[-─\s]+', '', text).strip()
            if not text or text.startswith('('):
                continue  # pure tree art or a '(SEE …)' side note
            if not re.search(r'[A-Z]{3,}', text):
                continue
            out.append((ph, text))
    if out and dp.get('diagram_reverse_order', True):
        out.reverse()
    return out


def _slug(text: str) -> str:
    return _FIELD_SLUG_RE.sub('_', text.strip().upper()).strip('_').lower()


def _split_codes(cell_a: str, cell_b: str) -> tuple[str, str] | None:
    """Normalise a code-table row; handles 'MRF- MUBARRAZ FIELD' single-cell form."""
    a, b = cell_a.strip(), cell_b.strip()
    if not a:
        return None
    if not b:
        m = re.match(r'^([A-Z0-9]{1,8})\s*[-–:]\s*(.+)$', a)
        if m:
            a, b = m.group(1), m.group(2)
        else:
            return None
    return a, b


def parse_numbering_scheme(grid: SheetGrid) -> dict:
    """Extract (example, segments, sections) from a numbering-scheme sheet."""
    th = config.SIGNAL_THRESHOLDS
    example_re = re.compile(th['example_re'], re.I)
    placeholder_re = re.compile(th['placeholder_re'])
    section_re = re.compile(th['section_re'])

    example = ''
    example_idx = -1
    for i, row in enumerate(grid.rows):
        joined = ' '.join(c for c in row if c)
        if example_re.search(joined):
            # pattern = the non-empty cell that isn't the 'EXAMPLE :' label,
            # with any inline 'EXAMPLE:' prefix stripped (single-cell form)
            parts = [c for c in row if c and not example_re.fullmatch(c.strip())]
            example = example_re.sub('', parts[-1]).strip() if parts else ''
            example_idx = i
            break
    if not example:
        raise ValueError(f'{grid.title}: no EXAMPLE row found')

    sep_match = re.search(r'[^A-Za-z0-9X]+', example)
    separator = sep_match.group(0).strip() or '-' if sep_match else '-'
    pattern_segments = [s.strip() for s in re.split(r'[^A-Za-z0-9]+', example) if s.strip()]

    # placeholder meaning rows (immediately after the example row).
    # Two layouts are supported:
    #   classic — two-cell rows ('XX' | 'ITEM SYMBOLS'), possibly lettered
    #   diagram — ASCII-tree meaning rows (Phase 2 style, see _diagram_meanings)
    diagram_meanings = _diagram_meanings(grid, example_idx)
    meanings: list[tuple[str, str]] = diagram_meanings
    if not meanings:
        for row in grid.rows[example_idx + 1:]:
            first = row[0].strip() if row else ''
            if section_re.match(first):
                break
            second = row[1].strip() if len(row) > 1 else ''
            if first and second and len(first) <= 8 and re.match(r'^[A-Z0-9 ]+$', first):
                meanings.append((first, second))
            elif not first and not second:
                continue

    # sequential alignment: pattern segment i takes meaning i
    segments: list[SegmentSpec] = []
    for i, ph in enumerate(pattern_segments):
        meaning = meanings[i][1] if i < len(meanings) else f'SEGMENT {i + 1}'
        segments.append(SegmentSpec(placeholder=ph, meaning=meaning, field_key=_slug(meaning)))
    # extra diagram meanings beyond the pattern = optional suffix segments
    # (e.g. the <XXXX> STREAM token shown beside the Phase 2 format row)
    for ph, meaning in meanings[len(pattern_segments):]:
        segments.append(SegmentSpec(placeholder=ph or 'XXXX', meaning=meaning,
                                    field_key=_slug(meaning), optional=True))

    # code tables — two layouts:
    #   classic — lettered/numbered sections with single code→desc pairs
    #   multi-column — several code→desc column pairs under plain-text
    #     category headers (Phase 2 style); used when a diagram was found
    sections: dict[str, dict[str, str]] = {}
    if diagram_meanings:
        # diagram-layout sheet: multi-column code→desc tables under
        # plain-text category headers (Phase 2 style)
        dp = config.NUMBERING_PARSE
        code_re = re.compile(dp['code_cell_re'])
        col_section: dict[int, str] = {}
        diagram_end = example_idx + 1 + int(dp['diagram_max_rows'])
        for row in grid.rows[example_idx + 1:]:
            # skip the diagram region itself
            joined = ' '.join(c for c in row if c)
            if ('└' in joined or '├' in joined) or \
                    ('<' in joined and '>' in joined and '|' in joined):
                continue
            for c, val in enumerate(row):
                val = val.strip()
                if not val:
                    continue
                nxt = row[c + 1].strip() if c + 1 < len(row) else ''
                is_desc_cell = c > 0 and bool(code_re.match(row[c - 1].strip()))
                if code_re.match(val) and nxt and not code_re.match(nxt):
                    section = col_section.get(c)
                    if section:
                        sections[section][val] = nxt
                elif not code_re.match(val) and re.search(r'[A-Za-z]{3,}', val) \
                        and not is_desc_cell:
                    col_section[c] = val
                    sections.setdefault(val, {})
    else:
        current: str | None = None
        for row in grid.rows[example_idx + 1:]:
            first = row[0].strip() if row else ''
            if section_re.match(first):
                current = _SECTION_HEADER_RE.sub('', first).strip()
                sections.setdefault(current, {})
                continue
            if current:
                pair = _split_codes(first, row[1] if len(row) > 1 else '')
                if pair:
                    sections[current][pair[0]] = pair[1]

    # map sections onto segments: exact/substring first, then word overlap
    for seg in segments:
        target = seg.meaning.strip().upper()
        target_words = _word_tokens(target)
        best_title, best_score = None, 0.0
        for title in sections:
            t = title.strip().upper()
            if t == target:
                score = 2.0
            elif target in t or t in target:
                score = 1.0
            else:
                overlap = _word_tokens(t) & target_words
                score = len(overlap) / max(len(target_words), 1) * 0.9
            if score > best_score:
                best_title, best_score = title, score
        if best_title and best_score >= 0.5:
            seg.codes = sections[best_title]

    # Meanings like 'LINE DESIGNATION CODE (SEE BELOW)' point at the sheet's
    # code tables explicitly — merge ALL section codes into that segment.
    # Only applied for diagram-layout sheets (soft-coded via
    # NUMBERING_PARSE['see_table_re']).
    if diagram_meanings:
        see_re = re.compile(config.NUMBERING_PARSE['see_table_re'], re.I)
        for seg in segments:
            if not seg.codes and see_re.search(seg.meaning) and sections:
                merged: dict[str, str] = {}
                for table in sections.values():
                    merged.update(table)
                seg.codes = merged

    return {
        'example': example,
        'separator': separator,
        'segments': [
            {'placeholder': s.placeholder, 'meaning': s.meaning,
             'field_key': s.field_key, 'codes': s.codes,
             'optional': s.optional}
            for s in segments
        ],
        'unmapped_sections': [t for t in sections
                              if not any(s.codes is sections[t] for s in segments)],
    }


def _shape(value: str) -> str:
    return ''.join('A' if c.isalpha() else '9' if c.isdigit() else c for c in value)


def _sample_value(seg: dict, rng: random.Random) -> str:
    if seg['codes']:
        return rng.choice(list(seg['codes'].keys()))
    # generate from placeholder shape: X → ALPHANUMERIC (matches the exported
    # legend_definition regex [A-Z0-9]), 9 → digit, A → letter.
    # Sampling X as letters-only would teach the classifier that sizes/units
    # are alpha tokens and break on real numeric tags (12, 10, …).
    out = []
    for ch in seg['placeholder']:
        if ch == '9':
            out.append(rng.choice('0123456789'))
        elif ch == 'X':
            out.append(rng.choice('0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
        elif ch.isdigit():
            out.append(rng.choice('0123456789'))
        elif ch.isalpha():
            out.append(rng.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ'))
        else:
            out.append(ch)
    return ''.join(out)


def _token_features(tokens: list[str], i: int, window: int,
                    code_sets: dict[str, set] | None = None) -> dict[str, float | str]:
    tok = tokens[i]
    feats: dict[str, float | str] = {
        'bias': 1.0,
        'len': len(tok),
        'shape': _shape(tok),
        'is_alpha': tok.isalpha(),
        'is_digit': tok.isdigit(),
        'is_mixed': any(c.isalpha() for c in tok) and any(c.isdigit() for c in tok),
        'pos_first': i == 0,
        'pos_last': i == len(tokens) - 1,
        'rel_pos': round(i / max(len(tokens) - 1, 1), 2),
        'n_tokens': len(tokens),
    }
    for offset in range(1, window + 1):
        feats[f'shape_-{offset}'] = _shape(tokens[i - offset]) if i - offset >= 0 else '<BOS>'
        feats[f'shape_+{offset}'] = _shape(tokens[i + offset]) if i + offset < len(tokens) else '<EOS>'
    # Code-table membership — the strongest signal when two segments share a
    # shape (e.g. a 2-letter FLUID code vs a 2-char COATING code): only the
    # fluid appears in the legend's code table.
    if code_sets:
        for field_key, codes in code_sets.items():
            feats[f'in_codes={field_key}'] = tok.upper() in codes
    return feats


class NumberingSchemeModel(BaseLegendModel):
    format_type = config.FORMAT_NUMBERING

    def __init__(self) -> None:
        self.scheme: dict = {}
        self.classifier = None
        self.label_set: list[str] = []

    # ── training ────────────────────────────────────────────────────────────
    def fit(self, grid: SheetGrid, params: dict) -> dict:
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline

        self.scheme = parse_numbering_scheme(grid)
        segments = self.scheme['segments']
        if not segments:
            raise ValueError(f'{grid.title}: no segments parsed')

        rng = random.Random(params.get('random_state', 42))
        window = int(params.get('context_window', 1))
        n = int(params.get('synthetic_samples', 2000))
        # Optional segments (e.g. the STREAM suffix) appear only in some tags —
        # sample them with a soft-coded keep probability so the model learns
        # both tag lengths instead of shifting labels on shorter tags.
        keep_opt = float(params.get('optional_keep_prob', 0.5))
        code_sets = {s['field_key']: set(s['codes']) for s in segments if s['codes']}

        def _sample_tag() -> tuple[list[str], list[str]]:
            chosen = [s for s in segments
                      if not s.get('optional') or rng.random() < keep_opt]
            return [_sample_value(seg, rng) for seg in chosen], \
                   [seg['field_key'] for seg in chosen]

        X, y = [], []
        for _ in range(n):
            tokens, field_keys = _sample_tag()
            for i, (tok, fkey) in enumerate(zip(tokens, field_keys)):
                X.append(_token_features(tokens, i, window, code_sets))
                y.append(fkey)

        X_train, X_eval, y_train, y_eval = train_test_split(
            X, y, test_size=float(params.get('eval_fraction', 0.25)),
            random_state=params.get('random_state', 42), stratify=y,
        )
        if params.get('classifier', 'logreg') == 'rf':
            clf = RandomForestClassifier(n_estimators=200, random_state=params.get('random_state', 42))
        else:
            clf = LogisticRegression(max_iter=int(params.get('max_iter', 500)))
        self.classifier = Pipeline([('vec', DictVectorizer()), ('clf', clf)])
        self.classifier.fit(X_train, y_train)
        self.label_set = sorted(set(y))

        token_acc = float(self.classifier.score(X_eval, y_eval))
        # tag-level exact match on a fresh synthetic holdout
        exact = total = 0
        for _ in range(max(n // 4, 50)):
            tokens, field_keys = _sample_tag()
            pred = self.predict_tag(self.scheme['separator'].join(tokens))
            exact += int(pred['fields'] == field_keys)
            total += 1

        return {
            'token_accuracy': round(token_acc, 4),
            'tag_exact_match': round(exact / total, 4),
            'n_synthetic': n,
            'n_segments': len(segments),
            'n_code_values': sum(len(s['codes']) for s in segments),
        }

    # ── inference ───────────────────────────────────────────────────────────
    def predict_tag(self, tag: str) -> dict:
        # Split on the scheme separator / whitespace / inch-marks — other
        # punctuation (e.g. the '/' in fractional sizes like 3/4) belongs to
        # the token.  Inch-marks (" ”) ride on the size token in canonical
        # line numbers (12"-…) and must not distort its shape features.
        sep = self.scheme.get('separator', '-') or '-'
        tokens = [t for t in re.split(r'[\s"”' + re.escape(sep) + r']+', tag.strip()) if t]
        window = 1
        code_sets = {s['field_key']: set(s['codes'])
                     for s in self.scheme.get('segments', []) if s.get('codes')}
        feats = [_token_features(tokens, i, window, code_sets) for i in range(len(tokens))]
        fields = list(self.classifier.predict(feats)) if feats else []
        return {'tag': tag, 'tokens': tokens, 'fields': fields,
                'parsed': dict(zip(fields, tokens))}

    def legend_definition(self) -> dict:
        """Export a pid_checker_v2-compatible compiled-legend definition."""
        fields = []
        for seg in self.scheme['segments']:
            ph = seg['placeholder']
            regex = ''.join(r'\d' if (c == '9' or c.isdigit()) else r'[A-Z0-9]'
                            for c in (ph if set(ph) != {'X'} else 'X' * len(ph)))
            fields.append({
                'key': seg['field_key'], 'label': seg['meaning'],
                'regex': regex, 'lookup': seg['codes'],
                'optional': bool(seg.get('optional', False)),
            })
        return {'separator': self.scheme['separator'], 'fields': fields}

    # ── persistence ─────────────────────────────────────────────────────────
    def save(self, out_dir: str | Path) -> None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.classifier, out / 'classifier.joblib')
        (out / 'scheme.json').write_text(json.dumps(self.scheme, indent=2), encoding='utf-8')
        (out / 'legend_definition.json').write_text(
            json.dumps(self.legend_definition(), indent=2), encoding='utf-8')
        self._write_meta(out, {'n_segments': len(self.scheme.get('segments', []))})

    @classmethod
    def load(cls, in_dir: str | Path) -> 'NumberingSchemeModel':
        in_dir = Path(in_dir)
        model = cls()
        model.classifier = joblib.load(in_dir / 'classifier.joblib')
        model.scheme = json.loads((in_dir / 'scheme.json').read_text(encoding='utf-8'))
        model.label_set = [s['field_key'] for s in model.scheme.get('segments', [])]
        return model


class CombinedNumberingModel(NumberingSchemeModel):
    """One segmenter trained over SEVERAL numbering schemes (even across
    workbooks) — e.g. the "Line List" model covering Phase 1
    (XX-XX-XXXX-XXXX-X) and Phase 2 (XX-XX-XX-AXXXX-XXXXXXX-XX) line lists.

    How it works
    ------------
    1. Each source sheet is parsed with the normal numbering-scheme rules.
    2. Synthetic tags are generated from EVERY scheme; per-sheet field keys
       are translated to CANONICAL labels via the soft-coded field_map
       (config.COMBINED_MODELS), so one classifier learns 'size',
       'fluid_code', 'serial', … regardless of source format.
    3. predict_tag() segments with canonical labels, then routes the tag to
       the best-matching scheme (required-field coverage + length fit +
       code-table hits) and also returns the scheme-native field mapping.

    Schemes to merge live in config.COMBINED_MODELS — adding another
    line-list format is a config edit, not a code change.
    """

    format_type = config.FORMAT_COMBINED

    def __init__(self) -> None:
        super().__init__()
        self.schemes: list[dict] = []   # [{'key','title','scheme','field_map'}]

    # ── helpers ─────────────────────────────────────────────────────────────
    @staticmethod
    def _canon(field_map: dict, field_key: str) -> str:
        return field_map.get(field_key, field_key)

    def _scheme_canon_fields(self, entry: dict, optional: bool | None = None) -> list[str]:
        out = []
        for seg in entry['scheme'].get('segments', []):
            if optional is None or bool(seg.get('optional')) == optional:
                out.append(self._canon(entry.get('field_map', {}), seg['field_key']))
        return out

    def _code_sets(self) -> dict[str, set]:
        """canonical field → union of code tables across all schemes."""
        sets: dict[str, set] = {}
        for entry in self.schemes:
            fmap = entry.get('field_map', {})
            for seg in entry['scheme'].get('segments', []):
                if seg.get('codes'):
                    canon = self._canon(fmap, seg['field_key'])
                    sets.setdefault(canon, set()).update(seg['codes'])
        return sets

    # ── training ────────────────────────────────────────────────────────────
    def fit_schemes(self, schemes: list[dict], params: dict) -> dict:
        from sklearn.linear_model import LogisticRegression
        from sklearn.ensemble import RandomForestClassifier
        from sklearn.feature_extraction import DictVectorizer
        from sklearn.model_selection import train_test_split
        from sklearn.pipeline import Pipeline

        self.schemes = schemes
        rng = random.Random(params.get('random_state', 42))
        window = int(params.get('context_window', 1))
        n_each = max(int(params.get('synthetic_samples', 2000)) // max(len(schemes), 1), 200)
        keep_opt = float(params.get('optional_keep_prob', 0.5))
        code_sets = self._code_sets()

        def _sample(entry):
            fmap = entry.get('field_map', {})
            segs = entry['scheme']['segments']
            chosen = [s for s in segs if not s.get('optional') or rng.random() < keep_opt]
            tokens = [_sample_value(seg, rng) for seg in chosen]
            labels = [self._canon(fmap, seg['field_key']) for seg in chosen]
            return tokens, labels

        X, y = [], []
        for entry in schemes:
            for _ in range(n_each):
                tokens, labels = _sample(entry)
                for i, (tok, lab) in enumerate(zip(tokens, labels)):
                    X.append(_token_features(tokens, i, window, code_sets))
                    y.append(lab)

        X_train, X_eval, y_train, y_eval = train_test_split(
            X, y, test_size=float(params.get('eval_fraction', 0.25)),
            random_state=params.get('random_state', 42), stratify=y,
        )
        if params.get('classifier', 'logreg') == 'rf':
            clf = RandomForestClassifier(n_estimators=200, random_state=params.get('random_state', 42))
        else:
            clf = LogisticRegression(max_iter=int(params.get('max_iter', 500)))
        self.classifier = Pipeline([('vec', DictVectorizer()), ('clf', clf)])
        self.classifier.fit(X_train, y_train)
        self.label_set = sorted(set(y))
        # back-compat: primary scheme = first source
        self.scheme = schemes[0]['scheme'] if schemes else {}

        token_acc = float(self.classifier.score(X_eval, y_eval))
        # per-scheme tag exact match + blind scheme-routing accuracy
        per_scheme, routed_ok, routed_total = {}, 0, 0
        for entry in schemes:
            exact = total = 0
            for _ in range(max(n_each // 4, 50)):
                tokens, labels = _sample(entry)
                pred = self.predict_tag(entry['scheme']['separator'].join(tokens))
                exact += int(pred['fields'] == labels)
                routed_ok += int(pred.get('scheme') == entry['key'])
                routed_total += 1
                total += 1
            per_scheme[entry['key']] = round(exact / max(total, 1), 4)

        return {
            'token_accuracy': round(token_acc, 4),
            'tag_exact_match_by_scheme': per_scheme,
            'scheme_routing_accuracy': round(routed_ok / max(routed_total, 1), 4),
            'n_synthetic_per_scheme': n_each,
            'n_schemes': len(schemes),
            'n_code_values': sum(len(v) for v in code_sets.values()),
        }

    # ── inference ───────────────────────────────────────────────────────────
    def _route_scheme(self, tokens: list[str], canon_fields: list[str]) -> dict | None:
        """Pick the scheme this tag best fits: required-field coverage first,
        then segment-count fit, then fluid-code table hits."""
        best, best_score = None, -1.0
        canon_set = set(canon_fields)
        for entry in self.schemes:
            segs = entry['scheme'].get('segments', [])
            required = self._scheme_canon_fields(entry, optional=False)
            if not required:
                continue
            coverage = sum(1 for f in required if f in canon_set) / len(required)
            n_opt = len(self._scheme_canon_fields(entry, optional=True))
            length_fit = 1.0 if len(segs) - n_opt <= len(tokens) <= len(segs) else 0.0
            # code-table evidence: predicted fluid tokens found in this
            # scheme's fluid code table
            fmap = entry.get('field_map', {})
            fluid_codes = {}
            for seg in segs:
                if seg.get('codes') and self._canon(fmap, seg['field_key']) == 'fluid_code':
                    fluid_codes = seg['codes']
            fluid_tok = next((t for t, f in zip(tokens, canon_fields) if f == 'fluid_code'), '')
            code_hit = 1.0 if fluid_codes and fluid_tok.upper() in fluid_codes else 0.0
            score = 2.0 * coverage + 0.5 * length_fit + code_hit
            if score > best_score:
                best, best_score = entry, score
        return best

    def predict_tag(self, tag: str) -> dict:
        sep = '-'
        tokens = [t for t in re.split(r'[\s"”' + re.escape(sep) + r']+', tag.strip()) if t]
        window = 1
        code_sets = self._code_sets()
        feats = [_token_features(tokens, i, window, code_sets) for i in range(len(tokens))]
        canon_fields = [str(f) for f in self.classifier.predict(feats)] if feats else []
        parsed = dict(zip(canon_fields, tokens))

        entry = self._route_scheme(tokens, canon_fields)
        scheme_parsed: dict[str, str] = {}
        scheme_key = ''
        if entry:
            scheme_key = entry['key']
            fmap = entry.get('field_map', {})
            for seg in entry['scheme'].get('segments', []):
                canon = self._canon(fmap, seg['field_key'])
                if canon in parsed:
                    scheme_parsed[seg['field_key']] = parsed[canon]
        return {'tag': tag, 'tokens': tokens, 'fields': canon_fields,
                'parsed': parsed, 'scheme': scheme_key, 'scheme_parsed': scheme_parsed}

    def legend_definition(self) -> dict:
        """Per-scheme pid_checker_v2-compatible definitions."""
        out = {}
        for entry in self.schemes:
            self.scheme = entry['scheme']  # reuse single-scheme exporter
            out[entry['key']] = super().legend_definition()
        if self.schemes:
            self.scheme = self.schemes[0]['scheme']
        return {'schemes': out}

    # ── persistence ─────────────────────────────────────────────────────────
    def save(self, out_dir: str | Path) -> None:
        out = Path(out_dir)
        out.mkdir(parents=True, exist_ok=True)
        joblib.dump(self.classifier, out / 'classifier.joblib')
        (out / 'schemes.json').write_text(json.dumps(self.schemes, indent=2), encoding='utf-8')
        # back-compat artifacts for single-scheme readers
        (out / 'scheme.json').write_text(json.dumps(self.scheme, indent=2), encoding='utf-8')
        (out / 'legend_definition.json').write_text(
            json.dumps(self.legend_definition(), indent=2), encoding='utf-8')
        self._write_meta(out, {'n_schemes': len(self.schemes)})

    @classmethod
    def load(cls, in_dir: str | Path) -> 'CombinedNumberingModel':
        in_dir = Path(in_dir)
        model = cls()
        model.classifier = joblib.load(in_dir / 'classifier.joblib')
        model.schemes = json.loads((in_dir / 'schemes.json').read_text(encoding='utf-8'))
        model.scheme = json.loads((in_dir / 'scheme.json').read_text(encoding='utf-8')) \
            if (in_dir / 'scheme.json').exists() else (model.schemes[0]['scheme'] if model.schemes else {})
        model.label_set = []
        return model
