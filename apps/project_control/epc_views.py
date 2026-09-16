from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .access import accessible_enterprise_projects
from .epc_serializers import IntegratedBaselineSerializer, WBSActivityLinkSerializer
from .services.epc import (
    activity_link_payload, associate_requisition, baseline_payload, capture_integrated_baseline,
    delete_activity_link, requisition_payload, save_activity_link, setup_epc_project, setup_payload,
)


class EpcProjectViewSet(viewsets.GenericViewSet):
    """Existing project identities; setup never creates a second project."""
    permission_classes = [IsAuthenticated]
    business_approval_actions = {'baseline'}

    def get_queryset(self):
        return accessible_enterprise_projects(self.request.user).select_related('owner')

    @action(detail=True, methods=['get', 'post'])
    def setup(self, request, pk=None):
        project = self.get_object()
        if request.method == 'POST':
            project = setup_epc_project(project, request.data, user=request.user)
        return Response(setup_payload(project, request.user))

    @action(detail=True, methods=['get', 'post', 'delete'])
    def links(self, request, pk=None):
        project = self.get_object()
        if request.method == 'POST':
            row = save_activity_link(project, request.data, user=request.user)
            return Response(WBSActivityLinkSerializer(row).data, status=status.HTTP_201_CREATED)
        if request.method == 'DELETE':
            try:
                link_id = int(request.data.get('id', 0))
            except (TypeError, ValueError):
                raise ValidationError({'id': 'A valid link ID is required.'})
            delete_activity_link(project, link_id, user=request.user)
            return Response(status=status.HTTP_204_NO_CONTENT)
        payload = activity_link_payload(project, request.user)
        payload['results'] = WBSActivityLinkSerializer(payload['results'], many=True).data
        return Response(payload)

    @action(detail=True, methods=['get', 'post'])
    def requisitions(self, request, pk=None):
        project = self.get_object()
        if request.method == 'POST':
            return Response(associate_requisition(project, request.data, user=request.user), status=status.HTTP_201_CREATED)
        return Response(requisition_payload(project, request.user))

    @action(detail=True, methods=['get', 'post'])
    def baseline(self, request, pk=None):
        project = self.get_object()
        if request.method == 'POST':
            row = capture_integrated_baseline(project, request.data, user=request.user)
            return Response(IntegratedBaselineSerializer(row).data, status=status.HTTP_201_CREATED)
        payload = baseline_payload(project, request.user)
        payload['results'] = IntegratedBaselineSerializer(payload['results'], many=True).data
        return Response(payload)
