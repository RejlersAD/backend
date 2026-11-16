"""Project-scoped P&ID engineering evidence API."""
from uuid import UUID

from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.project_models import Project
from apps.rbac.action_policy import module_action_allowed
from .models import PidEngineeringEvidenceLink, PidCheckerV2LineListUpload, PidLineListProvenance
from .services.engineering_evidence import _integer, associate, payload, project_access
from .services.line_list_provenance import (associate_line_list, line_list_lookup,
                                            provenance_payload)


def _extraction_uuid(value):
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        raise ValidationError({'extraction_id': 'A UUID is required.'})


class EngineeringEvidenceCollectionView(APIView):
    permission_classes = [IsAuthenticated]

    @property
    def permission_action(self):
        return 'create' if self.request.method == 'POST' else 'read'

    def get(self, request):
        project = get_object_or_404(Project, pk=_integer(request.query_params.get('project_id'),
                                                         'project_id'), is_deleted=False)
        if not project_access(request.user, project):
            raise PermissionDenied('Project access is required.')
        if not module_action_allowed(request.user, 'crs_documents', 'read'):
            raise PermissionDenied('CRS document read access is required.')
        rows = PidEngineeringEvidenceLink.objects.filter(project=project).select_related(
            'extraction', 'revision__document', 'revision__chain', 'project',
        ).order_by('pk')
        return Response([payload(row) for row in rows])

    def post(self, request):
        link, created = associate(
            user=request.user,
            extraction_id=_extraction_uuid(request.data.get('extraction_id')),
            project_id=request.data.get('project_id'),
            work_item_id=request.data.get('work_item_id'),
            revision_id=request.data.get('revision_id'),
        )
        return Response(payload(link), status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class EngineeringEvidenceDetailView(APIView):
    permission_classes = [IsAuthenticated]

    @property
    def permission_action(self):
        return 'update' if self.request.method == 'PATCH' else 'read'

    def get(self, request, extraction_id):
        link = get_object_or_404(PidEngineeringEvidenceLink.objects.select_related(
            'extraction', 'revision__document', 'revision__chain', 'project',
        ), extraction__extraction_id=extraction_id)
        if not project_access(request.user, link.project):
            raise PermissionDenied('Project access is required.')
        if not module_action_allowed(request.user, 'crs_documents', 'read'):
            raise PermissionDenied('CRS document read access is required.')
        return Response(payload(link))

    def patch(self, request, extraction_id):
        current = get_object_or_404(PidEngineeringEvidenceLink, extraction__extraction_id=extraction_id)
        if not project_access(request.user, current.project, write=True):
            raise PermissionDenied('Project write access is required.')
        project_id = request.data.get('project_id', current.project_id)
        if str(project_id) != str(current.project_id):
            raise ValidationError({'project_id': 'An existing evidence link cannot change project.'})
        link, _ = associate(
            user=request.user, extraction_id=extraction_id, project_id=project_id,
            work_item_id=request.data.get('work_item_id', current.work_item_id),
            revision_id=request.data.get('revision_id', current.revision_id),
            allow_update=True,
        )
        return Response(payload(link))


class LineListProvenanceView(APIView):
    """Explicit canonical project and exact CRS revision for one master Line List."""

    permission_classes = [IsAuthenticated]

    @property
    def permission_action(self):
        return 'update' if self.request.method == 'POST' else 'read'

    def get(self, request, line_list_ref):
        line_list = get_object_or_404(PidCheckerV2LineListUpload,
                                      **line_list_lookup(line_list_ref))
        provenance = PidLineListProvenance.objects.select_related(
            'line_list', 'project', 'revision__document', 'revision__chain',
        ).filter(line_list=line_list).first()
        if line_list.created_by_id != request.user.pk:
            if (provenance is None or not project_access(request.user, provenance.project)
                    or not module_action_allowed(request.user, 'crs_documents', 'read')):
                raise PermissionDenied('Line list access is required.')
        if provenance is None:
            from rest_framework.exceptions import NotFound
            raise NotFound('The line list has no canonical project association.')
        return Response(provenance_payload(provenance))

    def post(self, request, line_list_ref):
        provenance, created = associate_line_list(
            user=request.user, line_list_ref=line_list_ref,
            project_id=request.data.get('project_id'),
            revision_id=request.data.get('revision_id'),
        )
        return Response(provenance_payload(provenance),
                        status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)
