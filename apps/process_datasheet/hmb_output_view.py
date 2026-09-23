import os
import tempfile

from django.db import transaction
from django.core.exceptions import ValidationError
from django.http import HttpResponse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .hmb_extractor_view import _get_accessible_project, _is_admin, _build_hmb_stream_comparison
from .models import HMBMasterTemplateProfile, HMBSourceUpload
from .services.hmb_export import inspect_output_template, build_final_workbook
from .services.hmb_storage import store_hmb_source, read_hmb_source, delete_hmb_source, HMBStorageError


def _serialize_output(source):
    if source is None:
        return None
    return {'id': str(source.id), 'filename': source.original_filename,
            'sha256': source.file_sha256, 'created_at': source.created_at,
            'storage_backend': source.metadata.get('storage_backend', 's3'),
            'schema': source.metadata.get('schema', []),
            'case_slots': source.metadata.get('case_slots', [])}


@api_view(['GET', 'POST'])
@permission_classes([IsAuthenticated])
def hmb_output_template_view(request, project_id):
    project, error = _get_accessible_project(request.user, project_id)
    if error:
        return error
    if request.method == 'GET':
        source = HMBSourceUpload.objects.filter(
            project=project, upload_kind=HMBSourceUpload.KIND_OUTPUT_TEMPLATE,
        ).first()
        return Response({'success': True, 'output_template': _serialize_output(source)})
    upload = request.FILES.get('output_template_file')
    if not upload or not upload.name.lower().endswith('.xlsx') or upload.size > 20 * 1024 * 1024:
        return Response({'error': 'Select a final .xlsx template of at most 20 MB.'}, status=400)
    path = None
    result = None
    try:
        content = upload.read()
        layout = inspect_output_template(content)
        with tempfile.NamedTemporaryFile(suffix='.xlsx', delete=False) as temporary:
            temporary.write(content)
            path = temporary.name
        result = store_hmb_source(path, upload_kind='output_template', original_filename=upload.name,
                                  project_id=project.pk, user_id=request.user.pk)
        with transaction.atomic():
            source = HMBSourceUpload.objects.create(
                project=project, uploaded_by=request.user, upload_kind='output_template',
                original_filename=upload.name, storage_key=result['key'],
                file_sha256=result['sha256'], size_bytes=result['size'], content_type=result['content_type'],
                metadata={'storage_backend': result.get('backend', 's3'), 'case_slots': list(layout['cases']),
                          'schema': [{'section': prop['identity'][0], 'property': prop['property'], 'unit': prop['unit']} for prop in layout['properties']]},
            )
        return Response({'success': True, 'output_template': _serialize_output(source)}, status=201)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=400)
    except Exception:
        if result:
            delete_hmb_source(result['key'], result.get('backend', 's3'))
        return Response({'error': 'Unable to save the final template. Check workbook and private storage.'}, status=503)
    finally:
        if path:
            os.unlink(path)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def hmb_final_export_view(request, project_id):
    project, error = _get_accessible_project(request.user, project_id)
    if error:
        return error
    try:
        profile = HMBMasterTemplateProfile.objects.get(pk=request.data.get('template_profile_id'), is_active=True)
        source = HMBSourceUpload.objects.get(pk=request.data.get('output_template_id'), project=project,
                                             upload_kind='output_template')
    except (HMBMasterTemplateProfile.DoesNotExist, HMBSourceUpload.DoesNotExist, ValueError, TypeError, ValidationError):
        return Response({'error': 'Select a valid master and saved final template.'}, status=400)
    if profile.project_id and profile.project_id != project.pk:
        return Response({'error': 'Master belongs to another project.'}, status=403)
    if not profile.project_id and not _is_admin(request.user) and profile.created_by_id != request.user.pk:
        return Response({'error': 'Access denied for this master.'}, status=403)
    requested = request.data.get('stream_ids')
    available = [str(stream['stream_id']) for stream in profile.analysis_payload.get('stream_columns', [])]
    if requested == 'all':
        requested = available
    if not isinstance(requested, list) or not requested or len(requested) > 200 or any(str(item) not in available for item in requested):
        return Response({'error': 'Select 1 to 200 streams from the active master.'}, status=400)
    try:
        comparisons = [_build_hmb_stream_comparison(project, profile, stream) for stream in dict.fromkeys(map(str, requested))]
        content = build_final_workbook(read_hmb_source(source), comparisons)
    except ValueError as exc:
        return Response({'error': str(exc)}, status=400)
    except HMBStorageError:
        return Response({'error': 'Saved final template is unavailable in private storage.'}, status=503)
    response = HttpResponse(content, content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    response['Content-Disposition'] = 'attachment; filename="HMB_Final.xlsx"'
    return response