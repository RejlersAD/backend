import logging
import os
import tempfile
import uuid

from django.db import DatabaseError, transaction
from django.utils import timezone
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from apps.rbac.data_visibility_config import user_has_module_access
from .services.hmb_storage import HMBStorageError, delete_hmb_source, read_hmb_source, store_hmb_source
from .services.separator_snapshots import EXPORT_KIND, MAX_BYTES, build_snapshot, decode_snapshot

logger = logging.getLogger(__name__)
SOURCE_TOOL = "hmb_separator_sizing"


def source_model():
    from .models import HMBSourceUpload
    return HMBSourceUpload


def accessible_project(user, project_id):
    from .hmb_extractor_view import _get_accessible_project
    return _get_accessible_project(user, project_id)


def project_scope(request):
    data = request.query_params if request.method == "GET" else request.data
    if not isinstance(data, dict):
        return None, Response({"error": "Provide a project-scoped JSON object."}, status=400)
    raw = data.get("project_id")
    try:
        project_id = str(uuid.UUID(raw)) if isinstance(raw, str) else None
    except ValueError:
        project_id = None
    if project_id is None:
        return None, Response({"error": "Select a valid project before accessing separator snapshots."}, status=400)
    return accessible_project(request.user, project_id)


def serialize(source):
    return {"id": str(source.id), "project_id": str(source.project.project_id),
            "created_at": source.created_at, "size_bytes": source.size_bytes,
            "tag": source.metadata["tag"], "service": source.metadata["service"],
            "stream": source.metadata["stream"], "calculator_version": source.metadata["calculator_version"]}


@api_view(["GET", "POST"])
@permission_classes([IsAuthenticated])
def separator_snapshots_view(request):
    if not request.user.is_superuser and not user_has_module_access(request.user, "process_datasheet"):
        return Response({"error": "Process Datasheet module access is required."}, status=403)
    project, error = project_scope(request)
    if error is not None:
        return error
    model = source_model()
    if request.method == "GET":
        sources = model.objects.filter(
            project=project, metadata__source_tool=SOURCE_TOOL, metadata__export_kind=EXPORT_KIND,
        ).select_related("project").order_by("-created_at")[:100]
        return Response({"results": [serialize(source) for source in sources], "limit": 100})
    try:
        snapshot, content = build_snapshot(request.data.get("basis"), request.data.get("calculator_version"), timezone.now().isoformat())
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        return Response({"error": str(exc)}, status=400)
    stored = None
    path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as temporary:
            path = temporary.name
            temporary.write(content)
        stored = store_hmb_source(path, upload_kind=model.KIND_OUTPUT_TEMPLATE,
                                  original_filename="Separator_snapshot.json",
                                  project_id=project.project_id, user_id=request.user.id)
        if not stored.get("stored") or not stored.get("key"):
            raise HMBStorageError("Private storage did not retain the separator snapshot.")
        with transaction.atomic():
            source = model.objects.create(
                project=project, uploaded_by=request.user, upload_kind=model.KIND_OUTPUT_TEMPLATE,
                original_filename="Separator_snapshot.json", storage_key=stored["key"],
                file_sha256=stored["sha256"], size_bytes=stored["size"], content_type="application/json",
                status=model.STATUS_IMPORTED, imported_at=timezone.now(),
                metadata={"source_tool": SOURCE_TOOL, "export_kind": EXPORT_KIND,
                          "storage_backend": stored["backend"], "calculator_version": snapshot["calculator_version"],
                          **snapshot["basis"]["metadata"]},
            )
        return Response({"snapshot": serialize(source)}, status=201)
    except (DatabaseError, HMBStorageError, OSError):
        logger.exception("Separator snapshot could not be saved.")
        if stored and stored.get("key"):
            try:
                delete_hmb_source(stored["key"], stored["backend"], strict=True)
            except (HMBStorageError, OSError):
                logger.exception("Separator snapshot compensating cleanup failed.")
        return Response({"error": "Separator snapshot was not saved. Check private storage and retry."}, status=503)
    finally:
        if path:
            try:
                os.unlink(path)
            except OSError:
                logger.warning("Separator snapshot temporary file cleanup failed.")


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def reopen_separator_snapshot_view(request, upload_id):
    if not request.user.is_superuser and not user_has_module_access(request.user, "process_datasheet"):
        return Response({"error": "Process Datasheet module access is required."}, status=403)
    project, error = project_scope(request)
    if error is not None:
        return error
    model = source_model()
    try:
        source = model.objects.select_related("project").get(
            pk=upload_id, project=project, metadata__source_tool=SOURCE_TOOL, metadata__export_kind=EXPORT_KIND,
        )
    except model.DoesNotExist:
        return Response({"error": "Separator snapshot was not found in the selected project."}, status=404)
    if source.size_bytes > MAX_BYTES:
        return Response({"error": "Saved snapshot exceeds the supported size limit."}, status=503)
    try:
        snapshot = decode_snapshot(read_hmb_source(source))
    except (HMBStorageError, ValueError, UnicodeError):
        logger.exception("Saved separator snapshot could not be read or validated.")
        return Response({"error": "Saved separator snapshot is unavailable or invalid. No calculation was restored."}, status=503)
    return Response({"archive": serialize(source), "snapshot": snapshot,
                     "historical": True, "message": "Historical snapshot. Confirm and recalculate with the current calculator."})
