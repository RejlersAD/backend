"""Match explicit deliverable names while retaining exact source positions."""
from functools import lru_cache
import re

from ..config import DELIVERABLE_ALIASES


@lru_cache(maxsize=256)
def _deliverable_patterns(canonical):
    patterns = []
    for term in (canonical, *(DELIVERABLE_ALIASES.get(canonical) or [])):
        # PDF extraction can wrap a title across lines or insert extra spaces.
        phrase = r'\s+'.join(re.escape(part) for part in term.split())
        if not phrase:
            continue
        # Keep ordinary plural titles/acronyms (specifications, PFDs, SLDs)
        # without accepting a keyword embedded inside an unrelated word.
        plural = r's?' if term[-1].isalpha() and not term.lower().endswith('s') else ''
        patterns.append(re.compile(r'(?<!\w)' + phrase + plural + r'(?!\w)', re.I))
    return tuple(patterns)


def find_deliverable_match(text, canonical):
    """Return the earliest complete name/alias match in the original text."""
    matches = [match for pattern in _deliverable_patterns(canonical)
               if (match := pattern.search(text)) is not None]
    # Prefer the complete title over its short alias at the same position.
    return min(matches, key=lambda match: (match.start(), -len(match.group(0)))) if matches else None
