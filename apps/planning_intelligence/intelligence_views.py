from django.db import transaction
from django.utils import timezone
from rest_framework import status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .access import PlanningObjectPermission, accessible_projects
from .intelligence_serializers import (
    AddGenerationDependencySerializer, BasisDeliverableReviewSerializer, BasisDeliverableSerializer,
    BulkBasisDeliverableReviewSerializer,
    ConflictResolutionSerializer,
    DocumentAuthorityRuleSerializer, DocumentIntelligenceRunSerializer, DocumentProfileSerializer,
    FactReviewSerializer, IntelligenceConflictSerializer, IntelligenceFactSerializer,
    GenerationDependencyReviewSerializer, GenerationDependencySerializer,
    GenerationPlanSerializer, ManualIntelligenceFactSerializer, PlanDeliverableSerializer,
    ScheduleBasisSerializer,
)
from .models import (
    BasisDeliverable, DocumentAuthorityRule, DocumentIntelligenceRun, DocumentProfile,
    GenerationDependency, GenerationPlan, IntelligenceConflict, IntelligenceFact,
    PlanDeliverable, ScheduleBasis,
)
from .services.audit import record_event
from .services.schedule_basis import approve_schedule_basis, build_schedule_basis, refresh_basis_readiness
from .services.generation_plan import (
    approve_generation_plan, refresh_generation_plan_readiness,
)
from .serializers import PlanningJobSerializer
from .services.operational_jobs import (
    dispatch_job, generation_plan_build_fingerprint, get_or_create_job,
)


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

    @action(detail=True, methods=['post'], url_path='build-schedule-basis')
    def build_schedule_basis_action(self, request, pk=None):
        run = self.get_object()
        if run.status != 'succeeded':
            return Response({'error': 'Only a successful intelligence run can build a Schedule Basis.'}, status=status.HTTP_409_CONFLICT)
        basis = build_schedule_basis(run)
        record_event(
            project=run.project, actor=request.user, action='schedule_basis.created', entity=basis,
            after={'version': basis.version, 'readiness': basis.readiness},
        )
        return Response(ScheduleBasisSerializer(basis).data, status=status.HTTP_201_CREATED)


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
        for basis in fact.run.schedule_bases.filter(is_deleted=False).exclude(status='superseded'):
            refresh_basis_readiness(basis)
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
        for basis in conflict.run.schedule_bases.filter(is_deleted=False).exclude(status='superseded'):
            refresh_basis_readiness(basis)
        return Response(self.get_serializer(conflict).data)


class DocumentAuthorityRuleViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [IsAuthenticated]
    serializer_class = DocumentAuthorityRuleSerializer
    queryset = DocumentAuthorityRule.objects.filter(is_deleted=False)

    def get_queryset(self):
        queryset = super().get_queryset()
        information_type = self.request.query_params.get('information_type')
        return queryset.filter(information_type=information_type) if information_type else queryset


class ScheduleBasisViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = ScheduleBasisSerializer
    http_method_names = ['get', 'patch', 'post', 'head', 'options']
    queryset = ScheduleBasis.objects.filter(is_deleted=False).select_related(
        'project', 'source_run', 'approved_by',
    ).prefetch_related('deliverables')

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        basis_status = self.request.query_params.get('status')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        if basis_status:
            queryset = queryset.filter(status=basis_status)
        return queryset

    def create(self, request, *args, **kwargs):
        return Response(
            {'error': 'Create a Schedule Basis from a successful Document Intelligence run.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def partial_update(self, request, *args, **kwargs):
        basis = self.get_object()
        if basis.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Schedule Basis versions are immutable.'}, status=status.HTTP_409_CONFLICT)
        response = super().partial_update(request, *args, **kwargs)
        refresh_basis_readiness(self.get_object())
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        basis = self.get_object()
        try:
            basis = approve_schedule_basis(basis, request.user)
        except ValueError as exc:
            return Response({'error': str(exc), 'readiness': basis.readiness}, status=status.HTTP_409_CONFLICT)
        record_event(
            project=basis.project, actor=request.user, action='schedule_basis.approved', entity=basis,
            after={'version': basis.version},
        )
        return Response(self.get_serializer(basis).data)

    @action(detail=True, methods=['post'], url_path='review-deliverables')
    def review_deliverables(self, request, pk=None):
        basis = self.get_object()
        if basis.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Schedule Basis versions are immutable.'}, status=status.HTTP_409_CONFLICT)
        serializer = BulkBasisDeliverableReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        queryset = basis.deliverables.filter(is_deleted=False)
        ids = serializer.validated_data.get('deliverable_ids')
        if ids:
            queryset = queryset.filter(id__in=ids)
        reviewed_at = timezone.now()
        updated = queryset.update(
            status=serializer.validated_data['status'], reviewed_by=request.user,
            reviewed_at=reviewed_at, updated_at=reviewed_at,
        )
        refresh_basis_readiness(basis)
        record_event(
            project=basis.project, actor=request.user, action='schedule_basis.deliverables_reviewed',
            entity=basis, after={'status': serializer.validated_data['status'], 'count': updated},
        )
        return Response(self.get_serializer(basis).data)

    @action(detail=True, methods=['post'], url_path='build-generation-plan')
    def build_generation_plan_action(self, request, pk=None):
        basis = self.get_object()
        if basis.status != 'approved':
            return Response(
                {'error': 'Approve the Schedule Basis before building a Generation Plan.'},
                status=status.HTTP_409_CONFLICT,
            )
        fingerprint = generation_plan_build_fingerprint(basis)
        job, created = get_or_create_job(
            basis.project, 'build_plan', {'basis_id': basis.id}, request.user,
            idempotency_key=fingerprint,
        )
        if created or job.status == 'failed':
            if job.status == 'failed':
                job.status, job.error_code, job.error_message, job.finished_at = 'queued', '', '', None
                job.save(update_fields=['status', 'error_code', 'error_message', 'finished_at', 'updated_at'])
            try:
                dispatch_job(job)
            except RuntimeError:
                pass
        record_event(
            project=basis.project, actor=request.user, action='generation_plan.queued', entity=job,
            after={'basis_id': basis.id, 'job_id': job.id, 'async': True},
        )
        job.refresh_from_db()
        return Response(PlanningJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)


class BasisDeliverableViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = BasisDeliverableSerializer
    http_method_names = ['get', 'patch', 'post', 'head', 'options']
    queryset = BasisDeliverable.objects.filter(is_deleted=False).select_related('basis__project', 'reviewed_by')

    def get_queryset(self):
        queryset = super().get_queryset().filter(basis__project__in=accessible_projects(self.request.user))
        basis_id = self.request.query_params.get('basis')
        return queryset.filter(basis_id=basis_id) if basis_id else queryset

    def create(self, request, *args, **kwargs):
        return Response({'error': 'Deliverables are created by Schedule Basis compilation.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        deliverable = self.get_object()
        if deliverable.basis.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Schedule Basis versions are immutable.'}, status=status.HTTP_409_CONFLICT)
        response = super().partial_update(request, *args, **kwargs)
        refresh_basis_readiness(deliverable.basis)
        return response

    @action(detail=True, methods=['post'])
    def review(self, request, pk=None):
        deliverable = self.get_object()
        if deliverable.basis.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Schedule Basis versions are immutable.'}, status=status.HTTP_409_CONFLICT)
        serializer = BasisDeliverableReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        deliverable.status = serializer.validated_data['status']
        deliverable.reviewed_by = request.user
        deliverable.reviewed_at = timezone.now()
        deliverable.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        refresh_basis_readiness(deliverable.basis)
        record_event(
            project=deliverable.basis.project, actor=request.user,
            action=f'schedule_basis.deliverable_{deliverable.status}', entity=deliverable,
            after={'basis_id': deliverable.basis_id, 'canonical_name': deliverable.canonical_name},
        )
        return Response(self.get_serializer(deliverable).data)


class GenerationPlanViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = GenerationPlanSerializer
    http_method_names = ['get', 'patch', 'post', 'head', 'options']
    queryset = GenerationPlan.objects.filter(is_deleted=False).select_related(
        'project', 'basis', 'approved_by',
    ).prefetch_related(
        'deliverables__basis_deliverable', 'dependencies__predecessor__basis_deliverable',
        'dependencies__successor__basis_deliverable', 'phases', 'decision_gates',
    )

    def get_queryset(self):
        queryset = super().get_queryset().filter(project__in=accessible_projects(self.request.user))
        project_id = self.request.query_params.get('project')
        return queryset.filter(project_id=project_id) if project_id else queryset

    def create(self, request, *args, **kwargs):
        return Response({'error': 'Build a Generation Plan from an approved Schedule Basis.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        plan = self.get_object()
        if plan.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Generation Plans are immutable.'}, status=status.HTTP_409_CONFLICT)
        response = super().partial_update(request, *args, **kwargs)
        refresh_generation_plan_readiness(self.get_object())
        return Response(self.get_serializer(self.get_object()).data)

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        plan = self.get_object()
        try:
            plan = approve_generation_plan(plan, request.user)
        except ValueError as exc:
            return Response({'error': str(exc), 'readiness': plan.readiness}, status=status.HTTP_409_CONFLICT)
        record_event(project=plan.project, actor=request.user, action='generation_plan.approved', entity=plan, after={'version': plan.version})
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=['post'], url_path='review-dependencies')
    def review_dependencies(self, request, pk=None):
        plan = self.get_object()
        if plan.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Generation Plans are immutable.'}, status=status.HTTP_409_CONFLICT)
        serializer = GenerationDependencyReviewSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        queryset = plan.dependencies.filter(is_deleted=False)
        ids = serializer.validated_data.get('dependency_ids')
        if ids:
            queryset = queryset.filter(id__in=ids)
        now = timezone.now()
        updated = queryset.update(
            status=serializer.validated_data['status'], reviewed_by=request.user,
            reviewed_at=now, updated_at=now,
        )
        refresh_generation_plan_readiness(plan)
        record_event(
            project=plan.project, actor=request.user, action='generation_plan.dependencies_reviewed',
            entity=plan, after={'status': serializer.validated_data['status'], 'count': updated},
        )
        return Response(self.get_serializer(plan).data)

    @action(detail=True, methods=['post'], url_path='add-dependency')
    def add_dependency(self, request, pk=None):
        plan = self.get_object()
        if plan.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Generation Plans are immutable.'}, status=status.HTTP_409_CONFLICT)
        serializer = AddGenerationDependencySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data
        entries = plan.deliverables.filter(is_deleted=False, id__in=[data['predecessor'], data['successor']])
        if entries.count() != 2 or data['predecessor'] == data['successor']:
            return Response({'error': 'Select two different deliverables from this Generation Plan.'}, status=status.HTTP_400_BAD_REQUEST)
        dependency, created = GenerationDependency.objects.update_or_create(
            plan=plan, predecessor_id=data['predecessor'], successor_id=data['successor'],
            relationship_type=data['relationship_type'],
            defaults={
                'lag_days': data['lag_days'], 'rationale': data['rationale'],
                'source_type': 'planner', 'source_references': [], 'status': 'confirmed',
                'reviewed_by': request.user, 'reviewed_at': timezone.now(),
                'is_deleted': False, 'deleted_at': None,
            },
        )
        refresh_generation_plan_readiness(plan)
        record_event(
            project=plan.project, actor=request.user, action='generation_plan.dependency_added',
            entity=dependency, after={'created': created, 'rationale': dependency.rationale},
        )
        return Response(GenerationDependencySerializer(dependency).data, status=status.HTTP_201_CREATED if created else status.HTTP_200_OK)


class PlanDeliverableViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = PlanDeliverableSerializer
    http_method_names = ['get', 'patch', 'head', 'options']
    queryset = PlanDeliverable.objects.filter(is_deleted=False).select_related('plan__project', 'basis_deliverable')

    def get_queryset(self):
        return super().get_queryset().filter(plan__project__in=accessible_projects(self.request.user))

    def partial_update(self, request, *args, **kwargs):
        entry = self.get_object()
        if entry.plan.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Generation Plans are immutable.'}, status=status.HTTP_409_CONFLICT)
        response = super().partial_update(request, *args, **kwargs)
        refresh_generation_plan_readiness(entry.plan)
        return response


class GenerationDependencyViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated, PlanningObjectPermission]
    serializer_class = GenerationDependencySerializer
    http_method_names = ['get', 'patch', 'head', 'options']
    queryset = GenerationDependency.objects.filter(is_deleted=False).select_related(
        'plan__project', 'predecessor__basis_deliverable', 'successor__basis_deliverable',
    )

    def get_queryset(self):
        return super().get_queryset().filter(plan__project__in=accessible_projects(self.request.user))

    def partial_update(self, request, *args, **kwargs):
        dependency = self.get_object()
        if dependency.plan.status in ('approved', 'superseded'):
            return Response({'error': 'Approved or superseded Generation Plans are immutable.'}, status=status.HTTP_409_CONFLICT)
        response = super().partial_update(request, *args, **kwargs)
        refresh_generation_plan_readiness(dependency.plan)
        return response
