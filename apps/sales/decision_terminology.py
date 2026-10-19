"""Display terminology only; persisted decision codes remain unchanged."""
import re


def decision_text(value):
    if not isinstance(value, str):
        return value
    value = re.sub(r'\bno[ -]bid\b', 'No-Go', value, flags=re.I)
    return re.sub(r'\bbid\b', 'Go', value, flags=re.I)


def legacy_decision_text(value):
    """Compare new display wording with the existing contradiction safeguards."""
    value = re.sub(r'\bno[ -]go\b', 'no bid', value, flags=re.I)
    return re.sub(r'\bgo\b', 'bid', value, flags=re.I)
