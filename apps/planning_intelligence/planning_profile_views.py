"""Project-scoped profile commands; selection never generates or publishes work."""
from django.shortcuts import get_object_or_404
from rest_framework.exceptions import MethodNotAllowed, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from .access import accessible_projects
from .planning_profile_models import PlanningProfile
from .planning_profile_serializers import (
    PlanningProfileDecisionSerializer, PlanningProfileInputSerializer, PlanningProfileSelectionSerializer,
)
from .services.planning_profiles import (
    create_profile, decide_profile, planning_profile_selection, profile_collection, select_profile,
    serialize_profile, update_profile,
)


class PlanningProfileView(APIView):
    permission_classes = [IsAuthenticated]
    business_approval_actions = {'PlanningProfileView'}
    operation = None

    @property
    def permission_action(self):
        if self.request.method in {'GET', 'HEAD', 'OPTIONS'}:
            return 'read'
        return 'approve' if self.operation in {'approve', 'reject', 'select'} else 'update'

    def project(self, request, project_id):
        if not module_action_allowed(request.user, 'planning_package', self.permission_action):
            raise PermissionDenied('Your access does not permit this planning profile action.')
        return get_object_or_404(accessible_projects(request.user), pk=project_id)

    def get(self, request, project_id, profile_id=None):
        if self.operation:
            raise MethodNotAllowed('GET')
        project = self.project(request, project_id)
        if profile_id is None:
            return Response(profile_collection(project, request.user))
        profile = get_object_or_404(PlanningProfile.objects.select_related('project'), project=project, pk=profile_id)
        return Response({'profile': serialize_profile(profile, request.user), 'selection': planning_profile_selection(project),
                         'permissions': serialize_profile(profile, request.user)['permissions']})

    def patch(self, request, project_id, profile_id=None):
        if self.operation or profile_id is None:
            raise MethodNotAllowed('PATCH')
        project = self.project(request, project_id)
        serializer = PlanningProfileInputSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        if 'revision' not in serializer.validated_data:
            raise ValidationError({'revision': 'Supply the current draft revision.'})
        profile = update_profile(project, request.user, profile_id, serializer.validated_data)
        return Response({**profile_collection(project, request.user), 'profile': serialize_profile(profile, request.user)})

    def post(self, request, project_id, profile_id=None):
        project = self.project(request, project_id)
        response_status = 200
        if self.operation == 'select' and profile_id is None:
            serializer = PlanningProfileSelectionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            profile = select_profile(project, request.user, **serializer.validated_data)
        elif self.operation in {'propose', 'approve', 'reject', 'revise'} and profile_id is not None:
            serializer = PlanningProfileDecisionSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            profile = decide_profile(project, request.user, profile_id, self.operation, **serializer.validated_data)
            response_status = 201 if self.operation == 'revise' else 200
        elif self.operation is None and profile_id is None:
            serializer = PlanningProfileInputSerializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            if 'revision' in serializer.validated_data:
                raise ValidationError({'revision': 'New profiles receive their initial revision on the server.'})
            profile = create_profile(project, request.user, serializer.validated_data)
            response_status = 201
        else:
            raise MethodNotAllowed('POST')
        return Response({**profile_collection(project, request.user), 'profile': serialize_profile(profile, request.user)}, status=response_status)
