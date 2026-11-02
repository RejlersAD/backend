"""Read-only local source probe, optionally compared to supplied engineering labels.

No database, paid providers, reference approval or SP3D connection is involved.
Counts without reviewed labels are extraction diagnostics, never accuracy.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from apps.spec_customization.services.config import SPEC_EXTRACTION_CONFIG
from apps.spec_customization.services.local_extraction import run_local_engine


FIELDS = ('class_code', 'component_type', 'description', 'size_from', 'size_to',
          'material_standard', 'schedule_or_rating', 'end_connection')


def signature(row):
    return tuple(' '.join(str(row.get(field, '')).split()) for field in FIELDS)


def compare_labels(labels, source_hash, actual):
    if labels.get('source_sha256') != source_hash:
        raise ValueError('Labels do not identify these exact source bytes.')
    if labels.get('label_status') != 'engineering_reviewed' or not labels.get('reviewer') or not labels.get('reviewed_at'):
        raise ValueError('Independently reviewed labels and reviewer/date are required for acceptance comparison.')
    expected_rows = labels.get('components')
    if not isinstance(expected_rows, list) or not expected_rows or any(set(FIELDS) - set(row) for row in expected_rows):
        raise ValueError('Every expected component must supply all comparison fields, including explicit empty values.')
    expected = Counter(map(signature, expected_rows))
    extracted = Counter(map(signature, actual))
    return {'reviewer': labels['reviewer'], 'reviewed_at': labels['reviewed_at'],
            'label_provenance': 'supplied_engineering_review',
            'comparison_fields': FIELDS, 'exact_matches': sum((expected & extracted).values()),
            'missing': sum((expected - extracted).values()),
            'unexpected': sum((extracted - expected).values()),
            'missing_rows': [dict(zip(FIELDS, row)) for row in (expected - extracted).elements()]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--labels', type=Path)
    parser.add_argument('--engine', choices=['pymupdf_text', 'rapidocr', 'paddleocr', 'tesseract'], default='pymupdf_text')
    args = parser.parse_args()
    import pymupdf
    with args.source.open('rb') as source:
        source_hash = hashlib.file_digest(source, 'sha256').hexdigest()
    with pymupdf.open(args.source) as doc:
        pages = len(doc)
    started = time.monotonic()
    rows, issues, page_counts = [], [], []
    deadline = started + SPEC_EXTRACTION_CONFIG['job_total_timeout_s']
    # Nonoverlapping probes avoid counting rows twice; production chunk union
    # independently handles overlap using the source item/cell identity.
    for page in range(0, pages, SPEC_EXTRACTION_CONFIG['chunk_size_pages']):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError('Source probe exhausted the configured job budget.')
        result = run_local_engine(args.source, page, min(page + SPEC_EXTRACTION_CONFIG['chunk_size_pages'], pages) - 1,
                                  args.engine, SPEC_EXTRACTION_CONFIG, min(remaining, SPEC_EXTRACTION_CONFIG['chunk_timeout_s']))
        for cls in result['piping_classes']:
            rows.extend({**component, 'class_code': cls['class_code']} for component in cls['components'])
        issues.extend(result.get('local_report', {}).get('issues', []))
        page_counts.append({'from': page + 1, 'to': min(page + SPEC_EXTRACTION_CONFIG['chunk_size_pages'], pages),
                            'components': result.get('local_report', {}).get('components', 0)})
    report = {'source_filename': args.source.name, 'source_sha256': source_hash,
              'pages': pages, 'engine': args.engine, 'components': len(rows),
              'class_codes': sorted({row['class_code'] for row in rows}),
              'elapsed_seconds': round(time.monotonic() - started, 2),
              'engineering_acceptance': 'not_verified', 'issues': issues, 'chunks': page_counts,
              'extracted_components': rows}
    if args.labels:
        report['label_comparison'] = compare_labels(json.loads(args.labels.read_text(encoding='utf-8')), source_hash, rows)
        report['engineering_acceptance'] = 'comparison_recorded_requires_engineering_signoff'
    args.output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({key: value for key, value in report.items() if key not in ('extracted_components', 'issues', 'chunks')}))


if __name__ == '__main__':
    main()
