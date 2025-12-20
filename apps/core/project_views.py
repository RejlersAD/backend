"""
Project Management Views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Count, Sum
from apps.core.project_models import Project, ProjectMember, ProjectTask, ProjectMilestone
from apps.core.project_serializers import (
    ProjectSerializer, ProjectListSerializer, ProjectMemberSerializer,
    ProjectTaskSerializer, ProjectMilestoneSerializer
)


class ProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Project management
    """
    permission_classes = [IsAuthenticated]
    queryset = Project.objects.filter(is_deleted=False)

    def get_serializer_class(self):
        if self.action == 'list':
            return ProjectListSerializer
        return ProjectSerializer

    def get_queryset(self):
        """Filter projects based on user access"""
        user = self.request.user
        queryset = super().get_queryset()

        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)

        # Filter by priority
        priority_filter = self.request.query_params.get('priority', None)
        if priority_filter:
            queryset = queryset.filter(priority=priority_filter)

        # Search
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                Q(name__icontains=search) |
                Q(code__icontains=search) |
                Q(description__icontains=search) |
                Q(client_name__icontains=search)
            )

        # Show only user's projects if not admin
        if not user.is_staff:
            queryset = queryset.filter(
                Q(owner=user) | Q(team_members=user)
            ).distinct()

        return queryset

    def perform_create(self, serializer):
        """Set owner to current user if not specified"""
        if 'owner_id' not in serializer.validated_data:
            serializer.save(owner=self.request.user)
        else:
            serializer.save()

    @action(detail=True, methods=['post'])
    def add_member(self, request, pk=None):
        """Add a team member to the project"""
        project = self.get_object()
        user_id = request.data.get('user_id')
        role = request.data.get('role', 'engineer')

        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            member, created = ProjectMember.objects.get_or_create(
                project=project,
                user_id=user_id,
                defaults={'role': role}
            )
            if not created:
                member.role = role
                member.is_active = True
                member.save()

            serializer = ProjectMemberSerializer(member)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

    @action(detail=True, methods=['post'])
    def remove_member(self, request, pk=None):
        """Remove a team member from the project"""
        project = self.get_object()
        user_id = request.data.get('user_id')

        if not user_id:
            return Response(
                {'error': 'user_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        ProjectMember.objects.filter(project=project, user_id=user_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get project statistics"""
        user = request.user
        queryset = self.get_queryset()

        stats = {
            'total_projects': queryset.count(),
            'by_status': {},
            'by_priority': {},
            'overdue': 0,
            'total_budget': 0,
            'total_spent': 0,
        }

        # Count by status
        for choice in Project.STATUS_CHOICES:
            code = choice[0]
            stats['by_status'][code] = queryset.filter(status=code).count()

        # Count by priority
        for choice in Project.PRIORITY_CHOICES:
            code = choice[0]
            stats['by_priority'][code] = queryset.filter(priority=code).count()

        # Overdue projects
        from django.utils import timezone
        stats['overdue'] = queryset.filter(
            end_date__lt=timezone.now().date(),
            status__in=['planning', 'active', 'on_hold']
        ).count()

        # Budget summary
        budget_data = queryset.aggregate(
            total_budget=Sum('budget'),
            total_spent=Sum('spent')
        )
        stats['total_budget'] = float(budget_data['total_budget'] or 0)
        stats['total_spent'] = float(budget_data['total_spent'] or 0)

        return Response(stats)


class ProjectTaskViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Project Tasks
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectTaskSerializer
    queryset = ProjectTask.objects.filter(is_deleted=False)

    def get_queryset(self):
        """Filter tasks by project"""
        queryset = super().get_queryset()
        project_id = self.request.query_params.get('project_id', None)
        
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        # Filter by status
        status_filter = self.request.query_params.get('status', None)
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        return queryset

    def perform_create(self, serializer):
        """Create task with project association"""
        serializer.save()


class ProjectMilestoneViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Project Milestones
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ProjectMilestoneSerializer
    queryset = ProjectMilestone.objects.filter(is_deleted=False)

    def get_queryset(self):
        """Filter milestones by project"""
        queryset = super().get_queryset()
        project_id = self.request.query_params.get('project_id', None)
        
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        return queryset

    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark milestone as completed"""
        milestone = self.get_object()
        from django.utils import timezone
        milestone.is_completed = True
        milestone.completed_date = timezone.now().date()
        milestone.save()
        
        serializer = self.get_serializer(milestone)
        return Response(serializer.data)
