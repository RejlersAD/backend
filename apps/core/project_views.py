"""
Project Management Views
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Q, Count, Sum
from django.utils import timezone
from django.core.cache import cache
import asyncio
import json
from typing import List, Dict

# Existing project management models
from apps.core.project_models import Project, ProjectMember, ProjectTask, ProjectMilestone
# Smart project collection models
from apps.core.models import (
    ProjectCollection, ProjectDiscipline, SmartProjectDocument, 
    CrossDisciplineRecommendation
)

from apps.core.project_serializers import (
    ProjectSerializer, ProjectListSerializer, ProjectMemberSerializer,
    ProjectTaskSerializer, ProjectMilestoneSerializer,
    # Smart Project Collection Serializers
    ProjectCollectionSerializer, ProjectDisciplineSerializer,
    SmartProjectDocumentSerializer, CrossDisciplineRecommendationSerializer,
    SmartProjectDocumentUploadSerializer, BatchUploadRequestSerializer
)

# Smart project collection imports
from apps.core.smart_project_collector import get_smart_project_collector

import logging
logger = logging.getLogger(__name__)


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


# ========================================
# SMART PROJECT DOCUMENT COLLECTION VIEWS
# ========================================

class SmartProjectCollectionViewSet(viewsets.ViewSet):
    """
    Smart Project Collection API for multi-disciplinary document organization
    
    This ViewSet handles intelligent document collection and organization by:
    - Auto-detecting project codes from document content
    - Classifying documents by engineering discipline  
    - Organizing files into project-specific folder structures
    - Providing cross-discipline recommendations
    - Enabling predictive analytics for document patterns
    """
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser]
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.smart_collector = get_smart_project_collector()
    
    @action(detail=False, methods=['post'], url_path='smart-upload')
    def smart_upload(self, request):
        """
        Intelligently upload and organize document by project and discipline
        
        POST /api/v1/smart-projects/smart-upload/
        
        Form data:
        - file: Document file (required)
        - hint_project_code: Optional project code hint
        - hint_discipline: Optional discipline hint (process, mechanical, electrical, etc.)
        - hint_document_type: Optional document type hint
        
        Example usage:
        ```python
        const formData = new FormData();
        formData.append('file', file);
        formData.append('hint_project_code', 'ADNOC-P16093');
        formData.append('hint_discipline', 'mechanical');
        
        fetch('/api/v1/smart-projects/smart-upload/', {
            method: 'POST',
            headers: { 'Authorization': `Bearer ${token}` },
            body: formData
        });
        ```
        """
        try:
            if 'file' not in request.FILES:
                return Response(
                    {'error': 'No file provided'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            file_obj = request.FILES['file']
            hint_project_code = request.data.get('hint_project_code')
            hint_discipline = request.data.get('hint_discipline')
            hint_document_type = request.data.get('hint_document_type')
            
            # Run smart collection analysis
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                project_doc = loop.run_until_complete(
                    self.smart_collector.collect_and_organize_document(
                        file_obj=file_obj,
                        filename=file_obj.name,
                        user_id=request.user.id,
                        hint_project_code=hint_project_code,
                        hint_discipline=hint_discipline,
                        hint_document_type=hint_document_type
                    )
                )
            finally:
                loop.close()
            
            # Save to database
            saved_doc = self._save_project_document(project_doc, request.user)
            
            # Get cross-discipline recommendations
            cross_recommendations = self._get_cross_discipline_recommendations(saved_doc)
            
            return Response({
                'success': True,
                'message': f'Document successfully organized in project {project_doc.project_code}',
                'document': {
                    'id': saved_doc.id,
                    'filename': saved_doc.filename,
                    'project_code': project_doc.project_code,
                    'discipline': project_doc.discipline,
                    'document_type': project_doc.document_type,
                    'organized_location': project_doc.organized_s3_key,
                    'confidence_score': project_doc.confidence_score
                },
                'organization': {
                    'project_code': project_doc.project_code,
                    'discipline': project_doc.discipline,
                    'document_type': project_doc.document_type,
                    'document_subtype': project_doc.document_subtype,
                    'confidence_score': project_doc.confidence_score,
                    'ai_extracted_metadata': project_doc.extracted_metadata
                },
                'cross_discipline_recommendations': cross_recommendations,
                'project_overview': self.smart_collector.get_project_overview(project_doc.project_code)
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Smart upload error: {str(e)}")
            return Response(
                {'error': 'Smart upload failed', 'details': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='projects')
    def list_projects(self, request):
        """
        Get all projects with document statistics and discipline breakdown
        
        GET /api/v1/smart-projects/projects/
        
        Query params:
        - status: Filter by project status (active, completed, on_hold, archived)
        - search: Search project codes, names, and client names
        - discipline: Filter projects that have specific discipline
        """
        try:
            queryset = ProjectCollection.objects.all()
            
            # Filter by status
            status_filter = request.query_params.get('status')
            if status_filter:
                queryset = queryset.filter(project_status=status_filter)
            
            # Search functionality
            search = request.query_params.get('search')
            if search:
                queryset = queryset.filter(
                    Q(project_code__icontains=search) | 
                    Q(project_name__icontains=search) |
                    Q(client_name__icontains=search)
                )
            
            # Filter by discipline
            discipline_filter = request.query_params.get('discipline')
            if discipline_filter:
                queryset = queryset.filter(
                    disciplines__discipline_name=discipline_filter
                ).distinct()
            
            # Build response with statistics
            projects_data = []
            for project in queryset:
                # Get discipline breakdown
                disciplines = ProjectDiscipline.objects.filter(project=project)
                
                # Get recent activity
                recent_docs = SmartProjectDocument.objects.filter(
                    project=project,
                    is_active=True
                ).order_by('-upload_date')[:3]
                
                project_data = {
                    'id': project.id,
                    'project_code': project.project_code,
                    'project_name': project.project_name,
                    'client_name': project.client_name,
                    'project_status': project.project_status,
                    'total_documents': project.total_documents,
                    'total_size_mb': round(project.total_size_mb, 2),
                    'discipline_count': project.discipline_count,
                    'last_document_upload': project.last_document_upload,
                    'disciplines': [
                        {
                            'name': d.discipline_name,
                            'type': d.discipline_type,
                            'document_count': d.document_count,
                            'size_mb': round(d.size_mb, 2)
                        } for d in disciplines
                    ],
                    'recent_documents': [
                        {
                            'filename': doc.filename,
                            'discipline': doc.discipline.discipline_name,
                            'document_type': doc.document_type,
                            'upload_date': doc.upload_date,
                            'uploaded_by': doc.uploaded_by.username
                        } for doc in recent_docs
                    ]
                }
                projects_data.append(project_data)
            
            return Response({
                'projects': projects_data,
                'total_count': queryset.count(),
                'filters_applied': {
                    'status': status_filter,
                    'search': search,
                    'discipline': discipline_filter
                }
            })
            
        except Exception as e:
            logger.error(f"Error listing projects: {str(e)}")
            return Response(
                {'error': 'Failed to list projects'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='project/(?P<project_code>[^/.]+)')
    def project_overview(self, request, project_code=None):
        """
        Get comprehensive project overview with folder structure and analytics
        
        GET /api/v1/smart-projects/project/{project_code}/
        """
        try:
            # Get project from database
            try:
                project = ProjectCollection.objects.get(project_code=project_code)
            except ProjectCollection.DoesNotExist:
                return Response(
                    {'error': f'Project {project_code} not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
            # Get comprehensive overview from smart collector
            overview = self.smart_collector.get_project_overview(project_code)
            
            if not overview:
                # Create basic overview if not cached
                overview = {
                    'project_code': project.project_code,
                    'project_name': project.project_name,
                    'total_documents': project.total_documents,
                    'total_size_mb': project.total_size_mb,
                    'disciplines': {},
                    'last_updated': project.updated_at.isoformat()
                }
            
            # Add database statistics
            disciplines = ProjectDiscipline.objects.filter(project=project)
            overview['discipline_details'] = [
                {
                    'id': d.id,
                    'name': d.discipline_name, 
                    'type': d.discipline_type,
                    'document_count': d.document_count,
                    'size_mb': round(d.size_mb, 2),
                    'document_types': d.document_types,
                    'lead_engineer': d.lead_engineer.username if d.lead_engineer else None
                } for d in disciplines
            ]
            
            # Add recent activity
            recent_docs = SmartProjectDocument.objects.filter(
                project=project,
                is_active=True
            ).order_by('-upload_date')[:10]
            
            overview['recent_activity'] = [
                {
                    'id': doc.id,
                    'filename': doc.filename,
                    'discipline': doc.discipline.discipline_name,
                    'document_type': doc.document_type,
                    'uploaded_by': doc.uploaded_by.username,
                    'upload_date': doc.upload_date,
                    'file_size_mb': round(doc.file_size_mb, 2),
                    'confidence_score': doc.ai_classification_confidence
                } for doc in recent_docs
            ]
            
            # Add cross-discipline recommendations
            pending_recs = CrossDisciplineRecommendation.objects.filter(
                project=project,
                status='pending'
            )[:5]
            
            overview['pending_recommendations'] = [
                {
                    'id': rec.id,
                    'type': rec.recommendation_type,
                    'from_discipline': rec.source_discipline.discipline_name,
                    'to_discipline': rec.target_discipline.discipline_name,
                    'text': rec.recommendation_text,
                    'confidence': rec.ai_confidence,
                    'created_at': rec.created_at
                } for rec in pending_recs
            ]
            
            # Add document type distribution
            doc_type_distribution = SmartProjectDocument.objects.filter(
                project=project,
                is_active=True
            ).values('document_type').annotate(
                count=Count('id'),
                total_size=Sum('file_size')
            ).order_by('-count')
            
            overview['document_type_distribution'] = [
                {
                    'document_type': item['document_type'],
                    'count': item['count'],
                    'total_size_mb': round(item['total_size'] / (1024*1024), 2)
                } for item in doc_type_distribution
            ]
            
            return Response(overview)
            
        except Exception as e:
            logger.error(f"Error getting project overview: {str(e)}")
            return Response(
                {'error': 'Failed to get project overview'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['post'], url_path='batch-upload')
    def batch_upload(self, request):
        """
        Batch upload multiple documents with smart organization
        
        POST /api/v1/smart-projects/batch-upload/
        
        Form data:
        - Multiple files
        - hint_project_code: Optional default project code
        - hint_discipline: Optional default discipline
        - file_specific_hints: JSON object with file-specific hints
        """
        try:
            if not request.FILES:
                return Response(
                    {'error': 'No files provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            hint_project_code = request.data.get('hint_project_code')
            hint_discipline = request.data.get('hint_discipline')
            
            # Parse file-specific hints if provided
            file_specific_hints = {}
            hints_json = request.data.get('file_specific_hints')
            if hints_json:
                try:
                    file_specific_hints = json.loads(hints_json)
                except json.JSONDecodeError:
                    pass
            
            results = []
            projects_affected = set()
            disciplines_involved = set()
            
            for file_key, file_obj in request.FILES.items():
                try:
                    # Get file-specific hints
                    file_hints = file_specific_hints.get(file_obj.name, {})
                    file_project_hint = file_hints.get('project_code', hint_project_code)
                    file_discipline_hint = file_hints.get('discipline', hint_discipline)
                    file_doc_type_hint = file_hints.get('document_type')
                    
                    # Run smart collection for each file
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    
                    try:
                        project_doc = loop.run_until_complete(
                            self.smart_collector.collect_and_organize_document(
                                file_obj=file_obj,
                                filename=file_obj.name,
                                user_id=request.user.id,
                                hint_project_code=file_project_hint,
                                hint_discipline=file_discipline_hint,
                                hint_document_type=file_doc_type_hint
                            )
                        )
                    finally:
                        loop.close()
                    
                    # Save to database
                    saved_doc = self._save_project_document(project_doc, request.user)
                    
                    projects_affected.add(project_doc.project_code)
                    disciplines_involved.add(project_doc.discipline)
                    
                    results.append({
                        'filename': file_obj.name,
                        'project_code': project_doc.project_code,
                        'discipline': project_doc.discipline,
                        'document_type': project_doc.document_type,
                        'confidence': round(project_doc.confidence_score, 3),
                        'organized_location': project_doc.organized_s3_key,
                        'success': True
                    })
                    
                except Exception as file_error:
                    logger.error(f"Error processing file {file_obj.name}: {str(file_error)}")
                    results.append({
                        'filename': file_obj.name if hasattr(file_obj, 'name') else 'unknown',
                        'error': str(file_error),
                        'success': False
                    })
            
            successful_uploads = [r for r in results if r.get('success')]
            
            return Response({
                'batch_results': results,
                'summary': {
                    'total_files': len(request.FILES),
                    'successful_uploads': len(successful_uploads),
                    'failed_uploads': len(results) - len(successful_uploads),
                    'projects_affected': list(projects_affected),
                    'disciplines_involved': list(disciplines_involved),
                    'success_rate': round(len(successful_uploads) / len(request.FILES) * 100, 1)
                },
                'next_actions': [
                    'Review organized documents in project folders',
                    'Check cross-discipline recommendations',
                    'Verify AI classification accuracy'
                ]
            })
            
        except Exception as e:
            logger.error(f"Batch upload error: {str(e)}")
            return Response(
                {'error': 'Batch upload failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'], url_path='analytics')
    def project_analytics(self, request):
        """
        Get comprehensive cross-project analytics and insights
        
        GET /api/v1/smart-projects/analytics/
        """
        try:
            # Project statistics
            total_projects = ProjectCollection.objects.count()
            active_projects = ProjectCollection.objects.filter(project_status='active').count()
            
            # Document statistics
            total_documents = SmartProjectDocument.objects.filter(is_active=True).count()
            total_size_bytes = SmartProjectDocument.objects.filter(
                is_active=True
            ).aggregate(total_size=Sum('file_size'))['total_size'] or 0
            total_size_gb = total_size_bytes / (1024 * 1024 * 1024)
            
            # Discipline breakdown across all projects
            discipline_stats = ProjectDiscipline.objects.values(
                'discipline_type'
            ).annotate(
                project_count=Count('project', distinct=True),
                document_count=Count('documents', distinct=True),
                total_size=Sum('size_bytes')
            ).order_by('-document_count')
            
            # Top projects by document count and activity
            top_projects = ProjectCollection.objects.annotate(
                doc_count=Count('documents', distinct=True)
            ).order_by('-doc_count')[:10]
            
            # Document type popularity
            doc_type_stats = SmartProjectDocument.objects.filter(
                is_active=True
            ).values('document_type').annotate(
                count=Count('id')
            ).order_by('-count')[:15]
            
            # Cross-discipline activity
            cross_discipline_activity = CrossDisciplineRecommendation.objects.values(
                'recommendation_type'
            ).annotate(count=Count('id')).order_by('-count')
            
            # AI classification accuracy trends
            ai_confidence_stats = SmartProjectDocument.objects.filter(
                is_active=True
            ).aggregate(
                avg_confidence=Avg('ai_classification_confidence'),
                high_confidence_count=Count(
                    'id', 
                    filter=Q(ai_classification_confidence__gte=0.8)
                )
            )
            
            # Upload activity over time (last 30 days)
            from datetime import timedelta
            thirty_days_ago = timezone.now() - timedelta(days=30)
            
            daily_uploads = SmartProjectDocument.objects.filter(
                upload_date__gte=thirty_days_ago,
                is_active=True
            ).extra(
                select={'upload_date_only': 'DATE(upload_date)'}
            ).values('upload_date_only').annotate(
                count=Count('id')
            ).order_by('upload_date_only')
            
            return Response({
                'overview': {
                    'total_projects': total_projects,
                    'active_projects': active_projects,
                    'total_documents': total_documents,
                    'total_size_gb': round(total_size_gb, 2),
                    'avg_documents_per_project': round(total_documents / max(total_projects, 1), 1)
                },
                'discipline_breakdown': [
                    {
                        'discipline': item['discipline_type'],
                        'project_count': item['project_count'],
                        'document_count': item['document_count'],
                        'total_size_mb': round(item['total_size'] / (1024*1024), 2) if item['total_size'] else 0
                    } for item in discipline_stats
                ],
                'top_projects': [
                    {
                        'project_code': p.project_code,
                        'project_name': p.project_name,
                        'document_count': p.total_documents,
                        'size_mb': round(p.total_size_mb, 2),
                        'disciplines': p.discipline_count
                    } for p in top_projects
                ],
                'document_type_popularity': list(doc_type_stats),
                'cross_discipline_activity': list(cross_discipline_activity),
                'ai_classification_performance': {
                    'average_confidence': round(ai_confidence_stats['avg_confidence'] or 0, 3),
                    'high_confidence_percentage': round(
                        (ai_confidence_stats['high_confidence_count'] / max(total_documents, 1)) * 100, 1
                    )
                },
                'upload_activity': list(daily_uploads),
                'generated_at': timezone.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Analytics error: {str(e)}")
            return Response(
                {'error': 'Failed to generate analytics'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    # Helper methods
    
    def _save_project_document(self, project_doc, user) -> SmartProjectDocument:
        """Save project document to database with all relationships"""
        try:
            # Get or create project
            project_obj, created = ProjectCollection.objects.get_or_create(
                project_code=project_doc.project_code,
                defaults={
                    'project_name': project_doc.project_code,  # Can be enhanced later
                    's3_root_path': f'projects/{project_doc.project_code}/',
                    'auto_organize_enabled': True,
                    'ai_classification_enabled': True,
                    'cross_discipline_recommendations': True
                }
            )
            
            # Get or create discipline
            discipline_obj, created = ProjectDiscipline.objects.get_or_create(
                project=project_obj,
                discipline_name=project_doc.discipline,
                defaults={
                    'discipline_type': project_doc.discipline,
                    's3_discipline_path': f'projects/{project_doc.project_code}/disciplines/{project_doc.discipline}/'
                }
            )
            
            # Create document record
            doc_obj = SmartProjectDocument.objects.create(
                document_id=project_doc.document_id,
                filename=project_doc.filename,
                original_filename=project_doc.filename,
                project=project_obj,
                discipline=discipline_obj,
                document_type=project_doc.document_type,
                document_subtype=project_doc.document_subtype,
                s3_key=project_doc.organized_s3_key,
                file_size=project_doc.file_size,
                file_extension=project_doc.filename.split('.')[-1].lower() if '.' in project_doc.filename else '',
                content_hash=getattr(project_doc, 'content_hash', ''),
                uploaded_by=user,
                ai_classification_confidence=project_doc.confidence_score,
                ai_extracted_metadata=project_doc.extracted_metadata or {}
            )
            
            # Update project statistics
            project_obj.total_documents += 1
            project_obj.total_size_bytes += project_doc.file_size
            project_obj.last_document_upload = timezone.now()
            
            # Update discipline count if new discipline
            if created:
                project_obj.discipline_count += 1
            
            project_obj.save()
            
            # Update discipline statistics
            discipline_obj.document_count += 1
            discipline_obj.size_bytes += project_doc.file_size
            
            # Update document types list
            if project_doc.document_type not in discipline_obj.document_types:
                discipline_obj.document_types.append(project_doc.document_type)
            
            discipline_obj.save()
            
            return doc_obj
            
        except Exception as e:
            logger.error(f"Error saving project document: {str(e)}")
            raise
    
    def _get_cross_discipline_recommendations(self, document) -> List:
        """Generate cross-discipline recommendations based on document upload"""
        try:
            # This integrates with the recommendation system
            # For now, return basic structure
            
            recommendations = []
            
            # Example: If mechanical engineer uploads pump datasheet,
            # recommend process engineers to upload PID drawings
            if document.discipline.discipline_name == 'mechanical' and 'pump' in document.document_type.lower():
                recommendations.append({
                    'type': 'complementary_document',
                    'message': 'Process engineers should upload PID drawings showing this pump installation',
                    'target_discipline': 'process',
                    'suggested_document_types': ['pid_drawing', 'process_flow'],
                    'confidence': 0.8
                })
            
            # Example: If process engineer uploads PID, recommend instrument engineer for loop diagrams
            elif document.discipline.discipline_name == 'process' and 'pid' in document.document_type.lower():
                recommendations.append({
                    'type': 'interface_check',
                    'message': 'Instrumentation team should verify control loops shown in this PID',
                    'target_discipline': 'instrumentation', 
                    'suggested_document_types': ['loop_diagram', 'instrument_datasheet'],
                    'confidence': 0.75
                })
            
            return recommendations
            
        except Exception as e:
            logger.error(f"Error generating cross-discipline recommendations: {str(e)}")
            return []
