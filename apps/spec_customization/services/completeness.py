"""Processing completeness is distinct from engineering correctness/approval."""


def extraction_completeness(job):
    snapshot = job.config_snapshot if isinstance(job.config_snapshot, dict) else {}
    report = snapshot.get('extraction_outcome') or {}
    failed = report.get('failed_chunks') or []
    partial = bool(failed) or bool(job.status == 'completed' and job.chunks_total and job.chunks_done < job.chunks_total)
    has_components = job.piping_classes.filter(components__isnull=False).exists()
    empty = bool(report.get('empty_result')) or (job.status == 'completed' and not has_components)
    return {
        'state': 'partial' if partial else 'empty' if empty else 'complete' if report and job.status == 'completed' else 'unknown',
        'failed_chunks': failed,
        'chunks_total': job.chunks_total,
        'chunks_done': job.chunks_done,
        'processing_report_available': bool(report),
    }
