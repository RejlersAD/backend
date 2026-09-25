"""Bounded, read-only projections for monitoring and analysis discovery.

JSON leaves are selected in SQL. Large job results, progress histories, request
bodies and analysis summaries never enter these response paths.
"""
from django.db.models import CharField, F
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Cast, Substr
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.renderers import JSONRenderer

from ..access import accessible_projects


class OptionalObjectJSONRenderer(JSONRenderer):
    """An absent discovery result is JSON null, rather than an empty body."""

    def render(self, data, accepted_media_type=None, renderer_context=None):
        if data is None and getattr((renderer_context or {}).get('response'), 'status_code', 200) == 200:
            return b'null'
        return super().render(data, accepted_media_type, renderer_context)


JOB_FIELDS = (
    'id', 'project_id', 'job_type', 'status', 'progress', 'message', 'error_code',
    'result_generation_id', 'heartbeat_at', 'attempt_count', 'started_at',
    'finished_at', 'created_at', 'updated_at',
)
RUN_FIELDS = (
    'id', 'project_id', 'status', 'engine_version', 'fact_count', 'conflict_count',
    'started_at', 'finished_at', 'created_at', 'updated_at',
)
PROGRESS_TEXT_FIELDS = ('phase', 'provider', 'chunk_status', 'stage')
PROGRESS_COUNT_FIELDS = (
    'chunks_total', 'chunks_finished', 'chunks_processed', 'chunks_failed',
    'chunks_skipped', 'chunks_partial', 'chunk_number', 'response_characters_received',
    'characters_finished', 'characters_total', 'calls_this_pass', 'call_budget',
    'split_count', 'active_requests', 'max_concurrency', 'completed_groups',
    'total_groups', 'batch_index', 'batch_count', 'groups', 'file_count',
)
BULK_COUNT_FIELDS = ('processed', 'total', 'accepted_verified', 'accepted_ai', 'unresolved', 'skipped')
RESULT_PATHS = {
    'intelligence_run_id': ('intelligence_run_id', 'intelligence__document_intelligence_run_id'),
    'generation_id': ('generation_id',),
    'schedule_basis_id': ('schedule_basis_id',),
    'generation_plan_id': ('generation_plan_id',),
    'schedule_version_id': ('schedule_version_id', 'summary__schedule_version_id'),
}


def required_project_id(request):
    """Distinguish an empty authorized project from an inaccessible project."""
    supplied = request.query_params.get('project')
    try:
        project_id = int(supplied)
        if project_id <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValidationError({'project': 'A positive project ID is required.'})
    if not accessible_projects(request.user).filter(pk=project_id).exists():
        raise NotFound()
    return project_id


def _json_text(path, limit=64):
    return Substr(Cast(KeyTextTransform.from_lookup(path), CharField()), 1, limit)


def compact_job_queryset(queryset):
    annotations = {'_compact_error_message': Substr(F('error_message'), 1, 1000)}
    for key in (*PROGRESS_TEXT_FIELDS, *PROGRESS_COUNT_FIELDS):
        annotations[f'_progress_{key}'] = _json_text(f'result_data__progress_context__{key}')
    for key in BULK_COUNT_FIELDS:
        annotations[f'_count_{key}'] = _json_text(f'result_data__progress_context__counts__{key}')
    for key, paths in RESULT_PATHS.items():
        for index, path in enumerate(paths):
            annotations[f'_result_{key}_{index}'] = _json_text(f'result_data__{path}')
    return queryset.select_related(None).annotate(**annotations).values(*JOB_FIELDS, *annotations)


def compact_run_queryset(queryset):
    return queryset.select_related(None).values(*RUN_FIELDS)


def _count(value):
    if value is None or not str(value).isdecimal():
        return None
    return int(value)


def progress_context(row):
    context = {key: row[f'_progress_{key}'] for key in PROGRESS_TEXT_FIELDS
               if row.get(f'_progress_{key}') not in (None, '', 'null')}
    for key in PROGRESS_COUNT_FIELDS:
        value = _count(row.get(f'_progress_{key}'))
        if value is not None:
            context[key] = value
    counts = {key: value for key in BULK_COUNT_FIELDS
              if (value := _count(row.get(f'_count_{key}'))) is not None}
    if counts:
        context['counts'] = counts
    return context


def result_references(row):
    result = {key: next((value for index in range(len(paths))
                        if (value := _count(row.get(f'_result_{key}_{index}'))) is not None), None)
              for key, paths in RESULT_PATHS.items()}
    result['generation_id'] = row.get('result_generation_id') or result['generation_id']
    return result
