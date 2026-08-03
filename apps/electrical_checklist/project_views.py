"""
Project Management Views for Electrical Checklist
Professional project-based system with full CRUD operations
"""
import logging
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.db.models import Q, Count, Avg, Sum
from django.utils import timezone

from .models import ChecklistProject, ChecklistProjectMember, ChecklistExtractionJob
from .serializers import (
    ChecklistProjectListSerializer,
    ChecklistProjectDetailSerializer,
    ChecklistProjectCreateSerializer,
    ChecklistProjectMemberSerializer,
    ChecklistExtractionJobBriefSerializer
)
from .s3_service import get_s3_service

logger = logging.getLogger(__name__)


class ChecklistProjectViewSet(viewsets.ModelViewSet):
    """
    Professional Project Management API
    
    Endpoints:
    - GET    /electrical-checklist/projects/                    - List all projects
    - POST   /electrical-checklist/projects/                    - Create new project
    - GET    /electrical-checklist/projects/{id}/               - Get project details
    - PUT    /electrical-checklist/projects/{id}/               - Update project
    - DELETE /electrical-checklist/projects/{id}/               - Delete project
    - GET    /electrical-checklist/projects/{id}/checklists/    - List project checklists
    - GET    /electrical-checklist/projects/{id}/statistics/    - Get project statistics
    - POST   /electrical-checklist/projects/{id}/members/       - Add team member
    - DELETE /electrical-checklist/projects/{id}/members/{user_id}/ - Remove member
    - GET    /electrical-checklist/projects/templates/          - List project templates
    """
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """
        Return projects owned by or accessible to the current user
        Supports search, filter, and ordering
        """
        user = self.request.user
        
        # Base queryset: user's owned projects + projects they're members of
        queryset = ChecklistProject.objects.filter(
            Q(owner=user) | Q(members=user),
            is_deleted=False
        ).distinct()
        
        # Search by name, location, client, code
        search = self.request.query_params.get('search', '').strip()
        if search:
            queryset = queryset.filter(
                Q(project_name__icontains=search) |
                Q(project_code__icontains=search) |
                Q(location__icontains=search) |
                Q(client_name__icontains=search)
            )
        
        # Filter by status
        status_filter = self.request.query_params.get('status', '').strip()
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by template
        template_filter = self.request.query_params.get('template', '').strip()
        if template_filter:
            queryset = queryset.filter(template_id=template_filter)
        
        # Ordering (default: newest first)
        ordering = self.request.query_params.get('ordering', '-created_at')
        queryset = queryset.order_by(ordering)
        
        return queryset
    
    def get_serializer_class(self):
        """Use appropriate serializer based on action"""
        if self.action == 'list':
            return ChecklistProjectListSerializer
        elif self.action == 'create':
            return ChecklistProjectCreateSerializer
        else:
            return ChecklistProjectDetailSerializer
    
    def list(self, request):
        """
        List all accessible projects with pagination and search
        
        Query Parameters:
        - search: Search term (name, code, location, client)
        - status: Filter by status
        - template: Filter by template
        - ordering: Sort field (default: -created_at)
        - page: Page number
        - page_size: Items per page (default: 25)
        """
        try:
            queryset = self.get_queryset()
            
            # Pagination
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 25))
            
            start = (page - 1) * page_size
            end = start + page_size
            
            total_count = queryset.count()
            projects = queryset[start:end]
            
            serializer = self.get_serializer(projects, many=True)
            
            return Response({
                'success': True,
                'projects': serializer.data,
                'pagination': {
                    'total': total_count,
                    'page': page,
                    'page_size': page_size,
                    'total_pages': (total_count + page_size - 1) // page_size
                }
            })
            
        except Exception as e:
            logger.error(f"[ProjectAPI] List failed: {e}", exc_info=True)
            return Response({
                'success': False,
                'message': 'Failed to load projects'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def create(self, request):
        """
        Create new checklist project
        
        Request Body:
        {
            "project_name": "Al-Ruwais UPS Inspection",
            "description": "Quarterly UPS inspection for Al-Ruwais facility",
            "location": "Abu Dhabi, UAE",
            "client_name": "ADNOC",
            "template_id": "ups_battery_standard",
            "settings": {
                "extract_signatures": true,
                "require_approval": true
            },
            "tags": ["UPS", "Q2-2026"],
            "start_date": "2026-07-01",
            "end_date": "2026-09-30"
        }
        """
        try:
            serializer = self.get_serializer(data=request.data, context={'request': request})
            
            if not serializer.is_valid():
                return Response({
                    'success': False,
                    'message': 'Validation failed',
                    'errors': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)
            
            project = serializer.save()
            
            # Add owner as project member with owner role
            ChecklistProjectMember.objects.create(
                project=project,
                user=request.user,
                role='owner',
                added_by=request.user
            )
            
            # Return detailed project info
            detail_serializer = ChecklistProjectDetailSerializer(project)
            
            logger.info(f"[ProjectAPI] ✅ Project created: {project.project_code} by {request.user.username}")
            
            return Response({
                'success': True,
                'message': f'Project {project.project_code} created successfully!',
                'project': detail_serializer.data
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"[ProjectAPI] Create failed: {e}", exc_info=True)
            return Response({
                'success': False,
                'message': 'Failed to create project'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def retrieve(self, request, pk=None):
        """Get detailed project information"""
        try:
            project = self.get_queryset().get(pk=pk)
            serializer = self.get_serializer(project)
            
            return Response({
                'success': True,
                'project': serializer.data
            })
            
        except ChecklistProject.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Project not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    def update(self, request, pk=None):
        """Update project details"""
        try:
            project = self.get_queryset().get(pk=pk)
            
            # Check permission (owner or manager only)
            if not self._has_project_permission(project, request.user, ['owner', 'manager']):
                return Response({
                    'success': False,
                    'message': 'You don\'t have permission to update this project'
                }, status=status.HTTP_403_FORBIDDEN)
            
            # Partial update allowed
            serializer = ChecklistProjectCreateSerializer(
                project,
                data=request.data,
                partial=True
            )
            
            if not serializer.is_valid():
                return Response({
                    'success': False,
                    'errors': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)
            
            updated_project = serializer.save()
            detail_serializer = ChecklistProjectDetailSerializer(updated_project)
            
            logger.info(f"[ProjectAPI] ✅ Project updated: {project.project_code}")
            
            return Response({
                'success': True,
                'message': 'Project updated successfully',
                'project': detail_serializer.data
            })
            
        except ChecklistProject.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Project not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    def destroy(self, request, pk=None):
        """Soft-delete project (only owner can delete)"""
        try:
            project = self.get_queryset().get(pk=pk)
            
            # Only owner can delete
            if project.owner != request.user:
                return Response({
                    'success': False,
                    'message': 'Only project owner can delete the project'
                }, status=status.HTTP_403_FORBIDDEN)
            
            # Soft delete
            project.is_deleted = True
            project.status = 'archived'
            project.save()
            
            logger.info(f"[ProjectAPI] ✅ Project deleted: {project.project_code}")
            
            return Response({
                'success': True,
                'message': 'Project deleted successfully'
            })
            
        except ChecklistProject.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Project not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=True, methods=['get'], url_path='checklists')
    def project_checklists(self, request, pk=None):
        """
        Get all checklists for a project
        
        Query Parameters:
        - status: Filter by status
        - page, page_size: Pagination
        """
        try:
            project = self.get_queryset().get(pk=pk)
            
            jobs = ChecklistExtractionJob.objects.filter(project=project)
            
            # Filter by status
            status_filter = request.query_params.get('status', '').strip()
            if status_filter:
                jobs = jobs.filter(status=status_filter)
            
            # Pagination
            page = int(request.query_params.get('page', 1))
            page_size = int(request.query_params.get('page_size', 25))
            
            start = (page - 1) * page_size
            end = start + page_size
            
            total_count = jobs.count()
            jobs = jobs.order_by('-created_at')[start:end]
            
            serializer = ChecklistExtractionJobBriefSerializer(jobs, many=True)
            
            return Response({
                'success': True,
                'checklists': serializer.data,
                'pagination': {
                    'total': total_count,
                    'page': page,
                    'page_size': page_size
                }
            })
            
        except ChecklistProject.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Project not found'
            }, status=status.HTTP_404_NOT_FOUND)

    @action(detail=True, methods=['post'], url_path='save-checklist')
    def save_checklist(self, request, pk=None):
        """
        Save/update the (possibly hand-edited) checklist data for a job that
        belongs to this project.

        Request Body:
        {
            "job_id": 123,
            "checklist_data": { "<field_id>": { "site_value": "...", ... }, ... },
            "checklist_name": "Optional label to tell checklists apart in history"
        }
        """
        try:
            project = self.get_queryset().get(pk=pk)
        except ChecklistProject.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Project not found'
            }, status=status.HTTP_404_NOT_FOUND)

        job_id = request.data.get('job_id')
        checklist_data = request.data.get('checklist_data')
        checklist_name = request.data.get('checklist_name')
        if checklist_name is not None:
            checklist_name = str(checklist_name).strip() or None

        if not job_id:
            return Response({
                'success': False,
                'message': 'job_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        if not isinstance(checklist_data, dict):
            return Response({
                'success': False,
                'message': 'checklist_data must be an object'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            job = ChecklistExtractionJob.objects.get(id=job_id, project=project)
        except ChecklistExtractionJob.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Checklist job not found in this project'
            }, status=status.HTTP_404_NOT_FOUND)

        # Owners, managers and engineers may save edits; viewers are read-only.
        if not self._has_project_permission(
            project, request.user, ['owner', 'manager', 'engineer']
        ):
            return Response({
                'success': False,
                'message': 'You don\'t have permission to edit this checklist'
            }, status=status.HTTP_403_FORBIDDEN)

        extracted_data = job.extracted_data or {}
        extracted_data['checklist_data'] = checklist_data
        if checklist_name is not None:
            extracted_data['checklist_name'] = checklist_name
        extracted_data['last_edited_by'] = request.user.get_full_name() or request.user.username
        extracted_data['last_edited_at'] = timezone.now().isoformat()
        job.extracted_data = extracted_data
        job.save(update_fields=['extracted_data', 'updated_at'])

        logger.info(f"[ProjectAPI] ✅ Checklist saved for job {job.id} in project {project.project_code}")

        return Response({
            'success': True,
            'message': 'Checklist saved successfully',
            'job_id': job.id,
            'updated_at': extracted_data['last_edited_at']
        })

    @action(detail=True, methods=['get'], url_path='statistics')
    def project_statistics(self, request, pk=None):
        """
        Get comprehensive project statistics
        
        Returns:
        - Overview metrics
        - Status breakdown
        - Extraction trends
        - Confidence distribution
        """
        try:
            project = self.get_queryset().get(pk=pk)
            
            # Overview metrics
            jobs = ChecklistExtractionJob.objects.filter(project=project)
            total_jobs = jobs.count()
            completed_jobs = jobs.filter(status='completed').count()
            failed_jobs = jobs.filter(status='failed').count()
            pending_jobs = jobs.filter(status__in=['pending', 'processing']).count()
            
            # Aggregated stats
            stats = jobs.filter(status='completed').aggregate(
                total_fields=Sum('fields_extracted'),
                total_signatures=Sum('signatures_found'),
                avg_confidence=Avg('confidence_score')
            )
            
            # Status breakdown
            status_breakdown = {
                'completed': completed_jobs,
                'failed': failed_jobs,
                'pending': pending_jobs,
                'total': total_jobs
            }
            
            # Recent activity (last 30 days)
            thirty_days_ago = timezone.now() - timezone.timedelta(days=30)
            recent_jobs = jobs.filter(created_at__gte=thirty_days_ago)
            
            return Response({
                'success': True,
                'statistics': {
                    'overview': {
                        'total_checklists': total_jobs,
                        'fields_extracted': stats['total_fields'] or 0,
                        'signatures_found': stats['total_signatures'] or 0,
                        'avg_confidence': round(stats['avg_confidence'] or 0, 2)
                    },
                    'status_breakdown': status_breakdown,
                    'recent_activity': {
                        'count_30_days': recent_jobs.count(),
                        'completed_30_days': recent_jobs.filter(status='completed').count()
                    },
                    'project_info': {
                        'project_code': project.project_code,
                        'status': project.status,
                        'member_count': project.project_members.count()
                    }
                }
            })
            
        except ChecklistProject.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Project not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=True, methods=['post'], url_path='members')
    def add_member(self, request, pk=None):
        """
        Add team member to project
        
        Request Body:
        {
            "user_id": 123,
            "role": "engineer"
        }
        """
        try:
            project = self.get_queryset().get(pk=pk)
            
            # Check permission (owner or manager only)
            if not self._has_project_permission(project, request.user, ['owner', 'manager']):
                return Response({
                    'success': False,
                    'message': 'You don\'t have permission to add members'
                }, status=status.HTTP_403_FORBIDDEN)
            
            serializer = ChecklistProjectMemberSerializer(data=request.data)
            
            if not serializer.is_valid():
                return Response({
                    'success': False,
                    'errors': serializer.errors
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Create membership
            member = ChecklistProjectMember.objects.create(
                project=project,
                user_id=request.data['user_id'],
                role=request.data.get('role', 'viewer'),
                added_by=request.user
            )
            
            result_serializer = ChecklistProjectMemberSerializer(member)
            
            return Response({
                'success': True,
                'message': 'Member added successfully',
                'member': result_serializer.data
            })
            
        except ChecklistProject.DoesNotExist:
            return Response({
                'success': False,
                'message': 'Project not found'
            }, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=False, methods=['get'], url_path='templates')
    def list_templates(self, request):
        """
        List available project templates
        
        Returns soft-coded templates from config
        """
        # These would come from a config file in production
        templates = [
            {
                'id': 'ups_battery_standard',
                'name': 'UPS/Battery Inspection - Standard',
                'description': 'Standard UPS and Battery system inspection project',
                'category': 'Electrical',
                'default_settings': {
                    'extract_signatures': True,
                    'require_approval': True,
                    'auto_generate_excel': True,
                    's3_storage': True
                }
            },
            {
                'id': 'ups_battery_commissioning',
                'name': 'UPS/Battery - Commissioning',
                'description': 'UPS commissioning and acceptance testing',
                'category': 'Electrical',
                'default_settings': {
                    'extract_signatures': True,
                    'require_approval': True,
                    'auto_generate_excel': True,
                    's3_storage': True
                }
            },
            {
                'id': 'custom_project',
                'name': 'Custom Project',
                'description': 'Create a custom checklist project from scratch',
                'category': 'Custom',
                'default_settings': {
                    'extract_signatures': False,
                    'require_approval': False,
                    'auto_generate_excel': True,
                    's3_storage': True
                }
            }
        ]
        
        return Response({
            'success': True,
            'templates': templates
        })
    
    # ─── HELPER METHODS ───────────────────────────────────────────────────────
    
    def _has_project_permission(self, project, user, required_roles):
        """Check if user has required role in project"""
        if project.owner == user:
            return True
        
        try:
            member = ChecklistProjectMember.objects.get(project=project, user=user)
            return member.role in required_roles
        except ChecklistProjectMember.DoesNotExist:
            return False
