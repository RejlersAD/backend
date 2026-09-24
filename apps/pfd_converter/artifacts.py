"""Exact-output reads and regeneration within the existing conversion store.

No provider calls, approval policy, or general document platform lives here.
"""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import json
import logging
import re
import uuid

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework.exceptions import APIException, ValidationError

from .models import PIDConversion

logger = logging.getLogger(__name__)
INTEGRITY_KEY = '_radai_artifact_v1'


class ArtifactUnavailable(APIException):
    status_code = 503
    default_detail = 'The output could not be read or saved. Existing evidence is unchanged.'
    default_code = 'artifact_unavailable'


class ArtifactMissing(ArtifactUnavailable):
    status_code = 404
    default_detail = 'No stored output is available for this conversion.'


class ArtifactConflict(APIException):
    status_code = 409
    default_detail = 'This output changed. Reload its details before trying again.'
    default_code = 'stale_output'


def integrity_metadata(conversion):
    data = conversion.conversion_data
    value = data.get(INTEGRITY_KEY, {}) if isinstance(data, dict) else {}
    return value if isinstance(value, dict) else {}


def read_artifact(conversion):
    """Read the authoritative stored bytes without changing any model or file."""
    try:
        if conversion.pid_file:
            with conversion.pid_file.storage.open(conversion.pid_file.name, 'rb') as handle:
                content = handle.read()
        elif conversion.pid_pdf:
            content = bytes(conversion.pid_pdf)
        else:
            raise ArtifactMissing()
    except ArtifactUnavailable:
        raise
    except FileNotFoundError as exc:
        raise ArtifactMissing() from exc
    except Exception as exc:
        raise ArtifactUnavailable() from exc
    if not content:
        raise ArtifactMissing()
    return content


def artifact_digest(content):
    return sha256(content).hexdigest()


def verified_artifact(conversion):
    content = read_artifact(conversion)
    digest = artifact_digest(content)
    recorded = integrity_metadata(conversion).get('sha256')
    if recorded and recorded != digest:
        raise ArtifactConflict('Stored output differs from its recorded fingerprint. Review evidence is retained; download and regeneration are blocked.')
    return content, digest


def artifact_summary(conversion):
    metadata = integrity_metadata(conversion)
    result = {'identity': str(conversion.pk), 'sha256': None, 'available': False,
              'review_state': 'unavailable', 'source_conversion_id': metadata.get('source_conversion_id')}
    try:
        _, result['sha256'] = verified_artifact(conversion)
        result['available'] = True
        if conversion.status == 'approved':
            result['review_state'] = ('approved' if metadata.get('reviewed_sha256') == result['sha256']
                                      else 'legacy_approved')
        else:
            result['review_state'] = 'unreviewed'
    except ArtifactConflict:
        result['review_state'] = 'integrity_mismatch'
    except ArtifactUnavailable:
        pass
    return result


def regeneration_supported(conversion):
    groups = [conversion.equipment_list, conversion.instrument_list,
              conversion.piping_details, conversion.safety_systems]
    return (conversion.status in {'completed', 'approved'}
            and bool(conversion.equipment_list)
            and stored_valves(conversion) is not None
            and all(isinstance(group, list) and all(isinstance(row, dict) for row in group) for group in groups))


def stored_valves(conversion):
    if not conversion.valve_list:
        return []
    try:
        value = json.loads(conversion.valve_list)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, list) and all(isinstance(row, dict) for row in value) else None


def project_snapshot(conversion):
    project = conversion.pfd_document
    return {'project_name': project.project_name, 'project_code': project.project_code}


def allowed_actions(conversion, request, artifact=None):
    if request is None:
        return []
    from apps.rbac.action_policy import request_action_allowed
    artifact = artifact or artifact_summary(conversion)
    if not artifact['available']:
        return []
    result = []
    if request_action_allowed(request, 'pfd_to_pid', 'export'):
        result.append('download')
    if regeneration_supported(conversion) and request_action_allowed(request, 'pfd_to_pid', 'update'):
        result.append('regenerate')
    return result


def reject_legacy_regeneration(request):
    if 'force_regenerate' not in request.query_params:
        return
    values = request.query_params.getlist('force_regenerate')
    if len(values) == 1 and values[0].strip().lower() in {'false', '0', 'no', 'off'}:
        return
    raise ValidationError({'force_regenerate': 'Download never regenerates output. Use the separate authorized Regenerate command.'})


def download_response(conversion, request):
    reject_legacy_regeneration(request)
    content, digest = verified_artifact(conversion)
    is_png = content.startswith(b'\x89PNG\r\n\x1a\n')
    suffix, content_type = ('png', 'image/png') if is_png else ('pdf', 'application/pdf')
    response = HttpResponse(content, content_type=content_type)
    response['Content-Disposition'] = f'attachment; filename="PID_{conversion.pk}.{suffix}"'
    response['Cache-Control'] = 'private, no-store'
    response['X-Content-Type-Options'] = 'nosniff'
    response['X-Output-Id'] = str(conversion.pk)
    response['X-Artifact-SHA256'] = digest
    return response


def require_fresh_output(conversion, data):
    expected = data.get('expected_updated_at')
    expected_digest = data.get('expected_artifact_sha256')
    try:
        timestamp = parse_datetime(expected) if isinstance(expected, str) else None
    except ValueError:
        timestamp = None
    if timestamp is None or timezone.is_naive(timestamp):
        raise ValidationError({'expected_updated_at': 'Supply the exact timestamp from the output details.'})
    if not isinstance(expected_digest, str) or not re.fullmatch(r'[a-f0-9]{64}', expected_digest):
        raise ValidationError({'expected_artifact_sha256': 'Supply the output fingerprint from the output details.'})
    if timestamp != conversion.updated_at:
        raise ArtifactConflict()
    _, digest = verified_artifact(conversion)
    if digest != expected_digest:
        raise ArtifactConflict()
    return digest


def source_snapshot(conversion):
    """Include content, review, and source identity, even writes bypassing updated_at."""
    values = {}
    for field in conversion._meta.concrete_fields:
        value = getattr(conversion, field.attname)
        if field.name == 'pid_pdf':
            value = artifact_digest(bytes(value)) if value else None
        elif hasattr(value, 'name'):
            value = value.name
        values[field.attname] = value
    return sha256(json.dumps(values, sort_keys=True, default=str).encode('utf-8')).hexdigest()


def render_existing_specifications(source, output_path):
    """Use the existing local renderer only; no extraction/enrichment/provider fallback."""
    from .graph_based_pid_generator import GraphBasedPIDGenerator
    specs = {'drawing_number': source.pid_drawing_number, 'drawing_title': source.pid_title,
             'revision': source.pid_revision, 'project_name': source.pfd_document.project_name,
             'project_code': source.pfd_document.project_code,
             'equipment': deepcopy(source.equipment_list),
             'instrumentation': deepcopy(source.instrument_list),
             'piping': deepcopy(source.piping_details), 'valves': deepcopy(stored_valves(source))}
    # The wrapper adds engineering defaults; use only the existing renderer.
    GraphBasedPIDGenerator(specs).generate(str(output_path))


def regenerate(source, user, data):
    source_digest = require_fresh_output(source, data)
    if not regeneration_supported(source):
        raise ValidationError({'detail': 'Regeneration is unavailable without completed output and stored equipment specifications.'})
    snapshot = source_snapshot(source)
    project_basis = project_snapshot(source)
    # Source is a detached snapshot; generation must never mutate its model/files.
    frozen = deepcopy(source)
    child_id = uuid.uuid4()
    storage = PIDConversion._meta.get_field('pid_file').storage
    stored_name = None
    started = timezone.now()
    try:
        with TemporaryDirectory(prefix='radai-pid-regenerate-') as directory:
            output_path = Path(directory) / f'{child_id}.pdf'
            render_existing_specifications(frozen, output_path)
            output = output_path.read_bytes()
            if not output.startswith(b'%PDF-') or not output.rstrip().endswith(b'%%EOF'):
                raise ArtifactUnavailable('Generation did not produce a complete PDF. Existing evidence is unchanged.')
        digest = artifact_digest(output)
        stored_name = storage.save(f'pid_generated/regenerated/{child_id}.pdf', ContentFile(output))
        with storage.open(stored_name, 'rb') as handle:
            if artifact_digest(handle.read()) != digest:
                raise ArtifactUnavailable('Stored output verification failed. Existing evidence is unchanged.')
        with transaction.atomic():
            current = PIDConversion.objects.select_for_update().get(pk=source.pk)
            require_fresh_output(current, data)
            if source_snapshot(current) != snapshot or project_snapshot(current) != project_basis:
                raise ArtifactConflict()
            completed = timezone.now()
            child = PIDConversion.objects.create(
                id=child_id, pfd_document_id=frozen.pfd_document_id, converted_by=user,
                pid_drawing_number=frozen.pid_drawing_number, pid_title=frozen.pid_title,
                pid_revision=frozen.pid_revision, pid_file=stored_name, status='completed',
                generation_completed_at=completed, generation_duration=(completed - started).total_seconds(),
                equipment_list=frozen.equipment_list, instrument_list=frozen.instrument_list,
                piping_details=frozen.piping_details, safety_systems=frozen.safety_systems,
                valve_list=frozen.valve_list,
                design_parameters=frozen.design_parameters, conversion_method='stored_specs_regeneration',
                conversion_data={INTEGRITY_KEY: {'sha256': digest, 'source_conversion_id': str(source.pk),
                    'source_sha256': source_digest, 'source_updated_at': source.updated_at.isoformat(),
                    'source_snapshot': snapshot, 'project_basis': project_basis,
                    'renderer': 'GraphBasedPIDGenerator',
                    'limitations': 'Local re-render of stored specifications; no new extraction, AI enrichment, engineering validation or approval.'}},
                reviewed_by=None, reviewed_at=None, review_notes='', confidence_score=None,
                compliance_checks={},
            )
        return child
    except Exception as exc:
        # Delete only the new unique candidate. Never delete a source artifact.
        if stored_name and stored_name != source.pid_file.name and str(child_id) in stored_name:
            try:
                storage.delete(stored_name)
            except Exception:
                logger.warning('Unpublished PFD candidate cleanup failed for conversion %s', child_id)
        if isinstance(exc, (ArtifactConflict, ArtifactUnavailable, ValidationError)):
            raise
        raise ArtifactUnavailable('Regeneration could not be completed. Existing output and review evidence are unchanged.') from exc


def approve_exact_output(conversion, user, data):
    from apps.rbac.approval_eligibility import require_configured_approval
    with transaction.atomic():
        current = PIDConversion.objects.select_for_update().get(pk=conversion.pk)
        require_configured_approval(user, 'pfd_to_pid', current, 'approve')
        if current.status != 'completed' or current.reviewed_at or current.reviewed_by_id:
            raise ArtifactConflict('Only completed, unreviewed output can be approved. Existing review evidence is preserved.')
        digest = require_fresh_output(current, data)
        metadata = deepcopy(integrity_metadata(current))
        metadata.update({'sha256': digest, 'reviewed_sha256': digest})
        content = deepcopy(current.conversion_data) if isinstance(current.conversion_data, dict) else {}
        content[INTEGRITY_KEY] = metadata
        current.conversion_data = content
        current.reviewed_by = user
        current.reviewed_at = timezone.now()
        current.review_notes = data.get('review_notes', '')
        current.status = 'approved'
        current.save(update_fields=['conversion_data', 'reviewed_by', 'reviewed_at', 'review_notes', 'status', 'updated_at'])
        return current
