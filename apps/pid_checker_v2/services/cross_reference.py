"""Cross-reference line-tag text extraction against symbol identification.

Both `extract_line_tags_via_vision` (vision_extractor.py) and
`identify_symbols_via_vision` (symbol_shape_extractor.py) run as separate,
independently-triggered Vision passes — this module does NOT change either
pipeline. It's a small, additive, pure-function layer that takes the two
already-computed result lists and links up ones that landed in the same
coarse drawing region, e.g. a "PSV-8003" tag and a "SAFETY VALVE" symbol
both reported at "top-left" become one CONFIRMED pair.

Neither extractor currently reports precise pixel coordinates — only a
coarse "top/middle/bottom, left/center/right" region string — so matching
is necessarily coarse (same named region on the same page), not a tight
spatial proximity check. That's an inherent limit of the existing prompts/
outputs, not something this module can improve without changing what
Vision is asked to report.
"""
from __future__ import annotations

CONFIRMED_LABEL_TEMPLATE = '{tag} + {symbol_type} symbol = CONFIRMED'


def _normalise_location(loc: str | None) -> str:
    return str(loc or '').strip().lower()


def cross_reference_results(line_tags: list[dict], symbols: list[dict]) -> dict:
    """Link text tags and identified symbols that share a drawing region.

    Args:
        line_tags: items from extract_line_tags_via_vision()['tags'] — each
            needs at least 'tag', 'location', 'confidence'.
        symbols: items from identify_symbols_via_vision()['symbols'] — each
            needs at least 'symbol_type', 'location', 'confidence'.

    Returns::

        {
            'matches': [
                {
                    'tag': str, 'symbol_type': str, 'location': str,
                    'confidence': 'high',   # a confirmed pair is always HIGH
                    'label': 'PSV-8003 + Safety Valve symbol = CONFIRMED',
                },
                ...
            ],
            'unmatched_tags': [tag dicts with no symbol in the same region],
            'unmatched_symbols': [symbol dicts with no tag in the same region],
            'match_count': int,
        }
    """
    symbols_by_location: dict[str, list[dict]] = {}
    for sym in symbols:
        loc = _normalise_location(sym.get('location'))
        if not loc or loc == 'unspecified':
            continue
        symbols_by_location.setdefault(loc, []).append(sym)

    matches: list[dict] = []
    unmatched_tags: list[dict] = []
    matched_symbol_ids: set[int] = set()

    for tag in line_tags:
        loc = _normalise_location(tag.get('location'))
        candidates = symbols_by_location.get(loc) if loc and loc != 'unspecified' else None
        if not candidates:
            unmatched_tags.append(tag)
            continue
        sym = candidates[0]
        matched_symbol_ids.add(id(sym))
        matches.append({
            'tag': tag.get('tag'),
            'symbol_type': sym.get('symbol_type'),
            'location': loc,
            'confidence': 'high',  # a confirmed text+symbol pair is always reported as HIGH
            'label': CONFIRMED_LABEL_TEMPLATE.format(tag=tag.get('tag'), symbol_type=sym.get('symbol_type')),
        })

    unmatched_symbols = [s for s in symbols if id(s) not in matched_symbol_ids]

    return {
        'matches': matches,
        'unmatched_tags': unmatched_tags,
        'unmatched_symbols': unmatched_symbols,
        'match_count': len(matches),
    }
