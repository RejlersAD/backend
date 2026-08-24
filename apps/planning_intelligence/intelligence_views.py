from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .access import PlanningObjectPermission, accessible_projects
from .intelligence_serializers import (
    ConflictResolutionSerializer, DocumentIntelligenceRunSerializer,
    DocumentProfileSerializer, FactReviewSerializer, IntelligenceConflictSerializer,
    IntelligenceFactSerializer, ManualIntelligenceFactSerializer,
)
from .models import DocumentIntelligenceRun, DocumentProfile, IntelligenceConflict, IntelligenceFact
from .services.audit import record_event


class DocumentProfileViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = DocumentProfileSerializer
    queryset = DocumentProfile.objects.filter(is_deleted=False).select_related('file__project')

    def get_queryset(self):
        queryset = super().get_queryset().filter(file__project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        file_id = self.request.query_params.get('file')
        if project_id:
            queryset = queryset.filter(file__project_id=project_id)
        if file_id:
            queryset = queryset.filter(file_id=file_id)
        return queryset


class DocumentIntelligenceRunViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = DocumentIntelligenceRunSerializer
    queryset = DocumentIntelligenceRun.objects.filter(is_deleted=False).select_related('project', 'requested_by')

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    @action(detail=True, methods=['post'], url_path='add-fact')
    def add_fact(self, request, pk=None):
        run = self.get_object()
        serializer = ManualIntelligenceFactSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        fact = IntelligenceFact.objects.create(
            run=run, fact_type=data['fact_type'], key=data['key'], value=data['value'],
            normalized_value=str(data['value']).strip().casefold()[:500], confidence=1,
            extraction_method='manual', source_excerpt=data.get('source_excerpt', ''),
            source_locator={'source': 'planner'}, status='confirmed', reviewed_by=request.user,
            reviewed_at=timezone.now(),
        )
        run.fact_count = run.facts.filter(is_deleted=False).count()
        run.save(update_fields=['fact_count', 'updated_at'])
        record_event(project=run.project, actor=request.user, action='intelligence.fact_added', entity=fact)
        return Response(IntelligenceFactSerializer(fact).data, status=status.HTTP_201_CREATED)


class IntelligenceFactViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = IntelligenceFactSerializer
    queryset = IntelligenceFact.objects.filter(is_deleted=False).select_related('run__project', 'source_file', 'reviewed_by')

    def get_queryset(self):
        queryset = super().get_queryset().filter(run__project__in=accessible_projects(self.request.user))
        run_id = self.request.query_params.get('run')
        fact_type = self.request.query_params.get('fact_type')
        review_status = self.request.query_params.get('status')
        if run_id:
            queryset = queryset.filter(run_id=run_id)
        if fact_type:
            queryset = queryset.filter(fact_type=fact_type)
        if review_status:
            queryset = queryset.filter(status=review_status)
        return queryset

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        fact = self.get_object()
        serializer = FactReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        fact.status = serializer.validated_data['status']
        fact.reviewed_by = request.user
        fact.reviewed_at = timezone.now()
        fact.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        record_event(
            project=fact.run.project, actor=request.user, action=f'intelligence.fact_{fact.status}', entity=fact,
            after={'fact_type': fact.fact_type, 'key': fact.key},
        )
        return Response(self.get_serializer(fact).data)


class IntelligenceConflictViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = IntelligenceConflictSerializer
    queryset = IntelligenceConflict.objects.filter(is_deleted=False).select_related('run__project', 'resolved_by')

    def get_queryset(self):
        queryset = super().get_queryset().filter(run__project__in=accessible_projects(self.request.user))
        run_id = self.request.query_params.get('run')
        conflict_status = self.request.query_params.get('status')
        if run_id:
            queryset = queryset.filter(run_id=run_id)
        if conflict_status:
            queryset = queryset.filter(status=conflict_status)
        return queryset

    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        conflict = self.get_object()
        serializer = ConflictResolutionSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        with transaction.atomic():
            conflict = IntelligenceConflict.objects.select_for_update().get(pk=conflict.pk)
            if conflict.status != 'open':
                return Response({'error': 'This conflict has already been reviewed.'}, status=status.HTTP_409_CONFLICT)
            if data['action'] == 'ignore':
                conflict.status = 'ignored'
                conflict.resolution = {'action': 'ignore'}
            else:
                selected_id = data['selected_fact_id']
                if selected_id not in conflict.fact_ids:
                    return Response({'error': 'Selected fact does not belong to this conflict.'}, status=status.HTTP_400_BAD_REQUEST)
                facts = IntelligenceFact.objects.filter(id__in=conflict.fact_ids, run=conflict.run)
                facts.exclude(pk=selected_id).update(status='rejected', reviewed_by=request.user, reviewed_at=timezone.now())
                selected = facts.get(pk=selected_id)
                selected.status = 'confirmed'
                selected.reviewed_by = request.user
                selected.reviewed_at = timezone.now()
                selected.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
                conflict.status = 'resolved'
                conflict.resolution = {'action': 'select_fact', 'selected_fact_id': selected_id}
            conflict.resolved_by = request.user
            conflict.resolved_at = timezone.now()
            conflict.save(update_fields=['status', 'resolution', 'resolved_by', 'resolved_at', 'updated_at'])
        record_event(project=conflict.run.project, actor=request.user, action='intelligence.conflict_resolved', entity=conflict, after=conflict.resolution)
        return Response(self.get_serializer(conflict).data)
