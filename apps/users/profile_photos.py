"""Private photo delivery shared by self-service and managed user views."""
import mimetypes
from django.conf import settings
from django.core.files.storage import default_storage
from django.http import FileResponse, StreamingHttpResponse
from rest_framework.response import Response


def profile_photo_response(employee, legacy_photo=None):
    path = getattr(employee, 'photo_file_path', None)
    content_type = getattr(employee, 'photo_mime_type', None)
    if not path and legacy_photo:
        path = legacy_photo.name
    if not path:
        return Response({'error': 'Profile photo not found'}, status=404)
    content_type = content_type or mimetypes.guess_type(path)[0] or 'application/octet-stream'
    try:
        if getattr(settings, 'USE_S3', False) and getattr(employee, 'photo_file_path', None):
            from apps.core.s3_service import S3Service
            result = S3Service().download_file(path)
            body = result.get('body') if result.get('success') else None
            if body is None:
                return Response({'error': 'Profile photo could not be loaded'}, status=503)
            response = StreamingHttpResponse(body.iter_chunks(), content_type=content_type)
        else:
            storage = legacy_photo.storage if legacy_photo and not getattr(employee, 'photo_file_path', None) else default_storage
            response = FileResponse(storage.open(path, 'rb'), content_type=content_type)
        response['Cache-Control'] = 'private, no-cache, must-revalidate'
        response['Content-Disposition'] = 'inline'
        return response
    except FileNotFoundError:
        return Response({'error': 'Profile photo file not found'}, status=404)
    except Exception:
        return Response({'error': 'Profile photo could not be loaded'}, status=503)
