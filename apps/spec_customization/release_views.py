"""Additive controlled release and disposable-target pilot endpoints."""
import json
from django.conf import settings
from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import serializers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .models import PaperSpecExtractionJob
from .release_models import SpecRelease, SpecAuditEvent, ImportAttempt
from .services import controlled_release as service


def _job(request, job_id):
    from .views import _get_accessible_job
    job = _get_accessible_job(request, job_id)
    return PaperSpecExtractionJob.objects.select_for_update(of=('self',)).select_related('document', 'reference_workbook_set').get(pk=job.pk)


def release_payload(release):
    return {'id': str(release.pk), 'state': release.state, 'manifest': release.manifest,
            'parent_id': str(release.parent_id) if release.parent_id else None,
            'validation_id': str(release.validation_id) if release.validation_id else None,
            'blockers': release.blockers, 'author': release.author_id, 'created_at': release.created_at.isoformat(),
            'invalidated': release.successors.filter(state='invalidated').exists()}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def releases(request, job_id):
    job = _job(request, job_id)
    manifest, previews, errors = service.snapshot(job)
    blockers, validation = service.readiness(job, manifest, previews, errors, request.user)
    if request.method == 'POST':
        draft = SpecRelease.objects.create(job=job, state='draft', manifest=manifest, blockers=blockers, author=request.user)
        service.audit(job, 'release_draft', request.user, release_id=str(draft.pk))
        return Response(release_payload(draft), status=201)
    return Response({'items': [release_payload(row) for row in job.spec_releases.all()[:100]],
        'blockers': blockers, 'current_manifest': manifest,
        'validation': {'id': str(validation.pk), 'error_count': validation.error_count,
                       'severity_counts': validation.severity_counts, 'results': validation.results,
                       'input_sha256': validation.input_sha256} if validation else None,
        'pilot_targets': [{key: target.get(key, '') for key in ('id', 'label', 'sp3d_version', 'import_profile')}
                          for target in getattr(settings, 'S3D_DISPOSABLE_PILOT_TARGETS', []) if target.get('disposable') is True],
        'can_freeze': not blockers})


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def validate(request, job_id):
    job = _job(request, job_id)
    run = service.validate_release(job, request.user)
    return Response({'id': str(run.pk), 'error_count': run.error_count, 'severity_counts': run.severity_counts,
                     'results': run.results, 'input_sha256': run.input_sha256, 'preview_sha256': run.preview_sha256}, status=201)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def freeze(request, job_id, release_id):
    job = _job(request, job_id)
    draft = get_object_or_404(SpecRelease, pk=release_id, job=job)
    release, blockers = service.freeze(job, draft, request.user)
    if blockers:
        service.audit(job, 'release_freeze_blocked', request.user, draft_id=str(draft.pk), blockers=blockers)
        return Response({'error': 'Release readiness requirements are not satisfied.', 'blockers': blockers}, status=409)
    return Response(release_payload(release))


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def history(request, job_id):
    job = _job(request, job_id)
    events = SpecAuditEvent.objects.filter(job_identity=job.pk)[:500]
    validations = job.validation_runs.all()[:100]
    return Response({'events': [{'id': str(row.pk), 'event': row.event, 'details': row.details,
        'author': row.author_id, 'created_at': row.created_at.isoformat()} for row in events],
        'validations': [{'id': str(row.pk), 'results': row.results, 'severity_counts': row.severity_counts,
            'error_count': row.error_count, 'input_sha256': row.input_sha256,
            'trigger_source': row.trigger_source, 'author': row.author_id, 'created_at': row.created_at.isoformat()} for row in validations]})


class Measurements(serializers.Serializer):
    true_positive = serializers.IntegerField(min_value=0, required=False)
    false_positive = serializers.IntegerField(min_value=0, required=False)
    false_negative = serializers.IntegerField(min_value=0, required=False)
    reviewer_minutes = serializers.FloatField(min_value=0, required=False)
    provider_cost_usd = serializers.DecimalField(max_digits=14, decimal_places=6, min_value=0, required=False)
    import_failures = serializers.IntegerField(min_value=0, required=False)
    ground_truth_reference = serializers.CharField(max_length=512, required=False)


class PilotInput(serializers.Serializer):
    release_id = serializers.UUIDField()
    target_id = serializers.CharField(max_length=256)
    status = serializers.ChoiceField(choices=['planned', 'succeeded', 'failed', 'reconciled'])
    parent_id = serializers.UUIDField(required=False)
    rejection_count = serializers.IntegerField(min_value=0, default=0)
    result_details = serializers.JSONField(default=dict)
    measurements = Measurements(required=False)
    report = serializers.FileField(required=False, max_length=256)

    def validate(self, values):
        report = values.get('report')
        if values['status'] != 'planned' and not report:
            raise serializers.ValidationError('A target-generated import/reconciliation report is required.')
        if report and report.size > 5 * 1024 * 1024:
            raise serializers.ValidationError('Report exceeds 5 MB.')
        if values['status'] == 'succeeded' and values['rejection_count']:
            raise serializers.ValidationError('A successful import cannot have rejected records.')
        measurements = values.get('measurements', {})
        if any(key in measurements for key in ('true_positive', 'false_positive', 'false_negative')) and not measurements.get('ground_truth_reference'):
            raise serializers.ValidationError('Accuracy counts require a ground-truth reference.')
        if values['status'] == 'reconciled' and not values.get('parent_id'):
            raise serializers.ValidationError('Reconciliation must reference an earlier attempt.')
        return values


def attempt_payload(row):
    return {key: getattr(row, key) for key in ('sp3d_version', 'import_profile', 'disposable_target',
            'status', 'rejection_count', 'result_details', 'measurements', 'report_name', 'report_sha256')} | {
            'id': str(row.pk), 'release_id': str(row.release_id), 'parent_id': str(row.parent_id) if row.parent_id else None,
            'created_at': row.created_at.isoformat(), 'author': row.author_id}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def import_attempts(request, job_id):
    job = _job(request, job_id)
    if request.method == 'GET':
        return Response({'items': [attempt_payload(row) for row in ImportAttempt.objects.filter(release__job=job)[:100]],
                         'accuracy_publication': 'No public accuracy percentages until reviewed benchmarking is complete.'})
    raw = dict(request.data.items())
    for key in ('measurements', 'result_details'):
        if isinstance(raw.get(key), str):
            try:
                raw[key] = json.loads(raw[key])
            except ValueError:
                return Response({'error': f'{key} must be valid JSON.'}, status=400)
    serializer = PilotInput(data=raw)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    release = get_object_or_404(SpecRelease, pk=values['release_id'], job=job, state='frozen')
    _, blockers, _ = service.released_bytes(job, 'spec', release.pk)
    _, cat_blockers, _ = service.released_bytes(job, 'cat', release.pk)
    targets = {str(target.get('id')): target for target in getattr(settings, 'S3D_DISPOSABLE_PILOT_TARGETS', [])}
    target = targets.get(values['target_id'])
    if not target or target.get('disposable') is not True or not target.get('sp3d_version') or not target.get('import_profile'):
        blockers.append('An agreed disposable SP3D target/version/import profile must be configured.')
    if blockers or cat_blockers:
        return Response({'error': 'Import pilot is not ready.', 'blockers': list(dict.fromkeys(blockers + cat_blockers))}, status=409)
    parent = get_object_or_404(ImportAttempt, pk=values['parent_id'], release=release) if values.get('parent_id') else None
    if values['status'] == 'reconciled' and parent and parent.status not in ('succeeded', 'failed'):
        return Response({'error': 'Reconciliation requires an earlier succeeded or failed target import report.'}, status=409)
    if parent and (parent.disposable_target != values['target_id'] or parent.status == 'reconciled'):
        return Response({'error': 'Follow-up must use the same target and an unreconciled parent attempt.'}, status=409)
    report = values.get('report')
    report_bytes = report.read() if report else b''
    metrics = dict(values.get('measurements') or {})
    if 'provider_cost_usd' in metrics:
        metrics['provider_cost_usd'] = str(metrics['provider_cost_usd'])
    metrics['match_decisions'] = job.match_decisions.count()
    metrics['ambiguous_decisions'] = job.match_decisions.filter(state='ambiguous').count()
    report_hash = service.digest(report_bytes) if report else ''
    previous = ImportAttempt.objects.filter(release=release, parent=parent, author=request.user,
        disposable_target=values['target_id'], status=values['status'], report_sha256=report_hash).first()
    if previous and previous.rejection_count == values['rejection_count'] and previous.result_details == values['result_details'] and previous.measurements == metrics:
        return Response(attempt_payload(previous))
    attempt = ImportAttempt.objects.create(release=release, parent=parent, author=request.user,
        sp3d_version=target['sp3d_version'], import_profile=target['import_profile'], disposable_target=values['target_id'],
        status=values['status'], rejection_count=values['rejection_count'], result_details=values['result_details'],
        measurements=metrics, report_name=report.name if report else '', report_bytes=report_bytes,
        report_sha256=report_hash)
    service.audit(job, 'import_attempt_recorded', request.user, attempt_id=str(attempt.pk), release_id=str(release.pk),
                  status=attempt.status, report_sha256=attempt.report_sha256)
    return Response(attempt_payload(attempt), status=201)


def frozen_export(request, job, kind):
    from io import BytesIO
    from .views import _reference_export_response
    release_id = request.query_params.get('release_id')
    if release_id:
        release_id = serializers.UUIDField().run_validation(release_id)
    data, blockers, release = service.released_bytes(job, kind, release_id)
    if blockers:
        return Response({'error': 'Released export is blocked.', 'blockers': blockers}, status=409)
    service.audit(job, 'released_export', request.user, release_id=str(release.pk), workbook=kind,
                  sha256=release.manifest[f'{kind}_sha256'])
    response = _reference_export_response(job, BytesIO(data), f'RELEASED_{release.pk}_{kind.upper()}.xlsx')
    response['X-Spec-Export-State'] = 'frozen'
    response['X-Spec-Release-ID'] = str(release.pk)
    response['X-Content-SHA256'] = release.manifest[f'{kind}_sha256']
    return response


@api_view(['GET'])
@permission_classes([IsAuthenticated])
@transaction.atomic
def import_report(request, job_id, attempt_id):
    from django.http import HttpResponse
    job = _job(request, job_id)
    attempt = get_object_or_404(ImportAttempt, pk=attempt_id, release__job=job)
    data = bytes(attempt.report_bytes)
    if not data or service.digest(data) != attempt.report_sha256:
        return Response({'error': 'Import report is unavailable or failed integrity verification.'}, status=409)
    response = HttpResponse(data, content_type='application/octet-stream')
    response['Content-Disposition'] = f'attachment; filename="import-report-{attempt.pk}.bin"'
    response['X-Content-SHA256'] = attempt.report_sha256
    response['X-Content-Type-Options'] = 'nosniff'
    service.audit(job, 'import_report_download', request.user, attempt_id=str(attempt.pk), report_sha256=attempt.report_sha256)
    return response
