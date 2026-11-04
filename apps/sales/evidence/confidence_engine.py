"""Deterministic confidence scoring from evidence quality, never probability."""

SOURCE_SCORES = {
    'email': 88.0, 'thread': 82.0, 'pdf': 90.0, 'docx': 88.0,
    'xlsx': 86.0, 'eml_attachment': 84.0, 'msg_attachment': 84.0,
}


def score_evidence(record):
    """Return a bounded review score. It is not a calibrated probability."""
    score = SOURCE_SCORES.get(record.get('source_type'), 70.0)
    if not record.get('source_id'):
        score -= 8
    if not record.get('source_location') or record.get('source_location') == 'Unspecified location':
        score -= 8
    return round(max(0.0, min(100.0, score)), 1)
