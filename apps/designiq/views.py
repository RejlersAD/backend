"""
DesignIQ Views - AI-Powered Design Analysis API
Intelligent design verification, optimization, and recommendations
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.db.models import Q, Count
from django.utils import timezone
import logging
import json

from .models import DesignProject, DesignAnalysis, DesignOptimization, DesignTemplate, EngineeringListItem, LIST_TYPES
from .serializers import (
    DesignProjectListSerializer, DesignProjectDetailSerializer,
    DesignProjectCreateSerializer, DesignAnalysisSerializer,
    DesignOptimizationSerializer, DesignTemplateSerializer,
    DesignAnalysisCreateSerializer, EngineeringListItemSerializer,
    EngineeringListItemListSerializer, ListTypeConfigSerializer
)

logger = logging.getLogger(__name__)


class DesignProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for DesignIQ Projects
    Handles design project creation, analysis, and AI-powered insights
    """
    
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    
    def get_queryset(self):
        """Get projects for current user with optional filtering"""
        queryset = DesignProject.objects.all()
        
        # Filter by user unless staff
        if not self.request.user.is_staff:
            queryset = queryset.filter(created_by=self.request.user)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by design type
        design_type = self.request.query_params.get('design_type')
        if design_type:
            queryset = queryset.filter(design_type=design_type)
        
        # Search
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(project_name__icontains=search) |
                Q(description__icontains=search) |
                Q(organization__icontains=search)
            )
        
        return queryset.select_related('created_by').prefetch_related('analyses', 'optimizations')
    
    def get_serializer_class(self):
        """Use different serializers for different actions"""
        if self.action == 'retrieve':
            return DesignProjectDetailSerializer
        elif self.action in ['create', 'update', 'partial_update']:
            return DesignProjectCreateSerializer
        return DesignProjectListSerializer
    
    def perform_create(self, serializer):
        """Create project and set user"""
        project = serializer.save(created_by=self.request.user)
        logger.info(f"[DesignIQ] Project created: {project.id} by {self.request.user.email}")
    
    @action(detail=True, methods=['post'])
    def analyze(self, request, pk=None):
        """
        Trigger AI analysis for a project
        POST /api/v1/designiq/projects/{id}/analyze/
        Body: {
            "parameters": {...},  // Optional analysis parameters
            "force_reanalysis": false  // Re-analyze even if already completed
        }
        """
        project = self.get_object()
        
        if project.status == 'analyzing':
            return Response(
                {"error": "Analysis already in progress"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            # Update status
            project.status = 'analyzing'
            project.save()
            
            # Here you would trigger your AI analysis
            # For now, we'll return a placeholder response
            # TODO: Integrate with actual AI service
            
            logger.info(f"[DesignIQ] Analysis triggered for project: {project.id}")
            
            return Response({
                "message": "Analysis started successfully",
                "project_id": str(project.id),
                "status": "analyzing"
            })
            
        except Exception as e:
            logger.error(f"[DesignIQ] Analysis error: {str(e)}")
            project.status = 'failed'
            project.error_message = str(e)
            project.save()
            
            return Response(
                {"error": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def summary(self, request, pk=None):
        """
        Get project summary with statistics
        GET /api/v1/designiq/projects/{id}/summary/
        """
        project = self.get_object()
        
        analyses_stats = project.analyses.aggregate(
            total=Count('id'),
            critical=Count('id', filter=Q(severity='critical')),
            high=Count('id', filter=Q(severity='high')),
            resolved=Count('id', filter=Q(is_resolved=True))
        )
        
        optimizations_stats = project.optimizations.aggregate(
            total=Count('id'),
            high_impact=Count('id', filter=Q(impact='high')),
            implemented=Count('id', filter=Q(is_implemented=True))
        )
        
        return Response({
            "project": DesignProjectDetailSerializer(project).data,
            "analyses": analyses_stats,
            "optimizations": optimizations_stats,
            "summary": {
                "total_findings": analyses_stats['total'],
                "critical_issues": analyses_stats['critical'],
                "high_priority_issues": analyses_stats['high'],
                "resolution_rate": (analyses_stats['resolved'] / analyses_stats['total'] * 100) if analyses_stats['total'] > 0 else 0,
                "optimization_count": optimizations_stats['total'],
                "implemented_optimizations": optimizations_stats['implemented']
            }
        })
    
    @action(detail=False, methods=['get'])
    def dashboard(self, request):
        """
        Get dashboard statistics for all user projects
        GET /api/v1/designiq/projects/dashboard/
        """
        queryset = self.get_queryset()
        
        stats = {
            "total_projects": queryset.count(),
            "by_status": {
                "draft": queryset.filter(status='draft').count(),
                "analyzing": queryset.filter(status='analyzing').count(),
                "completed": queryset.filter(status='completed').count(),
                "failed": queryset.filter(status='failed').count(),
            },
            "by_design_type": {},
            "recent_projects": DesignProjectListSerializer(
                queryset.order_by('-created_at')[:5],
                many=True
            ).data
        }
        
        # Get counts by design type
        for choice_value, choice_label in DesignProject.DESIGN_TYPE_CHOICES:
            stats['by_design_type'][choice_value] = queryset.filter(design_type=choice_value).count()
        
        return Response(stats)


class DesignAnalysisViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Design Analyses
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = DesignAnalysisSerializer
    
    def get_queryset(self):
        """Get analyses for user's projects"""
        if self.request.user.is_staff:
            return DesignAnalysis.objects.all().select_related('project', 'resolved_by')
        
        return DesignAnalysis.objects.filter(
            project__created_by=self.request.user
        ).select_related('project', 'resolved_by')
    
    def get_serializer_class(self):
        if self.action in ['create', 'update', 'partial_update']:
            return DesignAnalysisCreateSerializer
        return DesignAnalysisSerializer
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """
        Mark analysis as resolved
        POST /api/v1/designiq/analyses/{id}/resolve/
        Body: {"resolution_notes": "Fixed by..."}
        """
        analysis = self.get_object()
        
        analysis.is_resolved = True
        analysis.resolved_by = request.user
        analysis.resolved_at = timezone.now()
        analysis.resolution_notes = request.data.get('resolution_notes', '')
        analysis.save()
        
        return Response(DesignAnalysisSerializer(analysis).data)


class DesignOptimizationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Design Optimizations
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = DesignOptimizationSerializer
    
    def get_queryset(self):
        """Get optimizations for user's projects"""
        if self.request.user.is_staff:
            return DesignOptimization.objects.all().select_related('project', 'implemented_by')
        
        return DesignOptimization.objects.filter(
            project__created_by=self.request.user
        ).select_related('project', 'implemented_by')
    
    @action(detail=True, methods=['post'])
    def implement(self, request, pk=None):
        """
        Mark optimization as implemented
        POST /api/v1/designiq/optimizations/{id}/implement/
        Body: {"implementation_notes": "..."}
        """
        optimization = self.get_object()
        
        optimization.is_implemented = True
        optimization.implemented_by = request.user
        optimization.implemented_at = timezone.now()
        
        if 'implementation_notes' in request.data:
            optimization.implementation_notes = request.data['implementation_notes']
        
        optimization.save()
        
        return Response(DesignOptimizationSerializer(optimization).data)


class DesignTemplateViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Design Templates
    """
    
    permission_classes = [IsAuthenticated]
    serializer_class = DesignTemplateSerializer
    
    def get_queryset(self):
        """Get public templates and user's private templates"""
        queryset = DesignTemplate.objects.filter(
            Q(is_public=True) | Q(created_by=self.request.user)
        ).select_related('created_by')
        
        design_type = self.request.query_params.get('design_type')
        if design_type:
            queryset = queryset.filter(design_type=design_type)
        
        return queryset.order_by('-usage_count', 'name')
    
    @action(detail=True, methods=['post'])
    def use_template(self, request, pk=None):
        """
        Create a new project from template
        POST /api/v1/designiq/templates/{id}/use_template/
        Body: {
            "project_name": "...",
            "parameters": {...}
        }
        """
        template = self.get_object()
        
        # Increment usage count
        template.usage_count += 1
        template.save()
        
        # Create project from template
        project_data = {
            "project_name": request.data.get('project_name', f"Project from {template.name}"),
            "design_type": template.design_type,
            "description": f"Created from template: {template.name}",
            "design_parameters": request.data.get('parameters', template.template_data),
            "created_by": request.user,
        }
        
        project = DesignProject.objects.create(**project_data)
        
        return Response({
            "message": "Project created from template",
            "project": DesignProjectDetailSerializer(project).data
        }, status=status.HTTP_201_CREATED)


class EngineeringListItemViewSet(viewsets.ModelViewSet):
    """
    ViewSet for Engineering List Items
    Handles Line List, Equipment List, Tie-In List, and Alarm/Trip List
    """
    
    permission_classes = [IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser, FormParser]
    
    def get_queryset(self):
        """Get list items with filtering"""
        queryset = EngineeringListItem.objects.select_related('project', 'created_by')
        
        # Filter by list type
        list_type = self.request.query_params.get('list_type')
        if list_type:
            queryset = queryset.filter(list_type=list_type)
        
        # Filter by project
        project_id = self.request.query_params.get('project')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        # Filter by status
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by validation status
        is_validated = self.request.query_params.get('is_validated')
        if is_validated is not None:
            queryset = queryset.filter(is_validated=is_validated.lower() == 'true')
        
        # Search by item tag or description
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(item_tag__icontains=search) | Q(description__icontains=search)
            )
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """Use different serializers for list vs detail"""
        if self.action == 'list':
            return EngineeringListItemListSerializer
        return EngineeringListItemSerializer
    
    @action(detail=False, methods=['get'])
    def list_types(self, request):
        """Get available list types configuration"""
        list_types_data = [
            {
                'code': code,
                'name': config['name'],
                'icon': config['icon'],
                'description': config['description'],
                'default_fields': config['default_fields']
            }
            for code, config in LIST_TYPES.items()
        ]
        
        serializer = ListTypeConfigSerializer(list_types_data, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def stats(self, request):
        """Get statistics for list items"""
        list_type = request.query_params.get('list_type')
        project_id = request.query_params.get('project')
        
        queryset = self.get_queryset()
        if list_type:
            queryset = queryset.filter(list_type=list_type)
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        stats = {
            'total': queryset.count(),
            'by_status': {
                'active': queryset.filter(status='active').count(),
                'pending': queryset.filter(status='pending').count(),
                'approved': queryset.filter(status='approved').count(),
                'rejected': queryset.filter(status='rejected').count(),
                'inactive': queryset.filter(status='inactive').count(),
            },
            'validated': queryset.filter(is_validated=True).count(),
            'not_validated': queryset.filter(is_validated=False).count(),
            'by_list_type': {}
        }
        
        # Count by list type
        for code, config in LIST_TYPES.items():
            count = queryset.filter(list_type=code).count()
            if count > 0:
                stats['by_list_type'][code] = {
                    'name': config['name'],
                    'count': count
                }
        
        return Response(stats)
    
    @action(detail=True, methods=['post'])
    def validate_item(self, request, pk=None):
        """Validate a list item"""
        item = self.get_object()
        
        item.is_validated = True
        item.validated_by = request.user
        item.validated_at = timezone.now()
        item.validation_notes = request.data.get('notes', '')
        item.save()
        
        return Response({
            "message": "Item validated successfully",
            "item": EngineeringListItemSerializer(item, context={'request': request}).data
        })
    
    @action(detail=True, methods=['post'])
    def bulk_import(self, request):
        """Bulk import items from CSV/Excel data"""
        items_data = request.data.get('items', [])
        list_type = request.data.get('list_type')
        project_id = request.data.get('project')
        
        if not list_type or list_type not in LIST_TYPES:
            return Response(
                {"error": "Valid list_type is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        created_items = []
        errors = []
        
        for idx, item_data in enumerate(items_data):
            try:
                item = EngineeringListItem.objects.create(
                    list_type=list_type,
                    project_id=project_id if project_id else None,
                    item_tag=item_data.get('item_tag', f'ITEM-{idx+1}'),
                    description=item_data.get('description', ''),
                    data=item_data.get('data', {}),
                    status=item_data.get('status', 'active'),
                    created_by=request.user
                )
                created_items.append(item)
            except Exception as e:
                errors.append({
                    'row': idx + 1,
                    'error': str(e),
                    'data': item_data
                })
        
        return Response({
            "message": f"Imported {len(created_items)} items",
            "created": len(created_items),
            "errors": len(errors),
            "error_details": errors if errors else None
        }, status=status.HTTP_201_CREATED if created_items else status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['post'])
    def export(self, request):
        """Export list items to structured format"""
        queryset = self.get_queryset()
        list_type = request.query_params.get('list_type')
        
        if list_type:
            queryset = queryset.filter(list_type=list_type)
        
        serializer = EngineeringListItemSerializer(queryset, many=True, context={'request': request})
        
        return Response({
            "list_type": list_type,
            "count": queryset.count(),
            "items": serializer.data
        })
    
    @action(detail=False, methods=['post'])
    def clear_history(self, request):
        """
        Clear all history documents for a specific list type
        Soft-coded approach with safety checks
        """
        list_type = request.data.get('list_type')
        confirm = request.data.get('confirm', False)
        
        # Validation
        if not list_type:
            return Response({
                "error": "list_type is required"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if list_type not in LIST_TYPES:
            return Response({
                "error": f"Invalid list_type. Must be one of: {', '.join(LIST_TYPES.keys())}"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not confirm:
            return Response({
                "error": "Confirmation required. Set 'confirm': true"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get queryset for specific list type
        queryset = EngineeringListItem.objects.filter(list_type=list_type)
        
        # Optional: Filter by project if specified
        project_id = request.data.get('project_id')
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        # Count before deletion
        count = queryset.count()
        
        if count == 0:
            return Response({
                "message": "No items found to delete",
                "deleted_count": 0,
                "list_type": list_type
            })
        
        # Perform deletion
        try:
            deleted_count, _ = queryset.delete()
            
            logger.info(f"🗑️ User {request.user.username} deleted {deleted_count} items from {LIST_TYPES[list_type]['name']}")
            
            return Response({
                "message": f"Successfully deleted all {LIST_TYPES[list_type]['name']} items",
                "deleted_count": deleted_count,
                "list_type": list_type,
                "list_type_name": LIST_TYPES[list_type]['name']
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Error deleting items: {str(e)}", exc_info=True)
            return Response({
                "error": f"Failed to delete items: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['delete'])
    def bulk_delete(self, request):
        """
        Delete multiple items by IDs
        Soft-coded bulk deletion with validation
        """
        item_ids = request.data.get('item_ids', [])
        
        if not item_ids or not isinstance(item_ids, list):
            return Response({
                "error": "item_ids (array) is required"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate IDs exist and user has permission
        queryset = EngineeringListItem.objects.filter(id__in=item_ids)
        count = queryset.count()
        
        if count == 0:
            return Response({
                "error": "No items found with provided IDs"
            }, status=status.HTTP_404_NOT_FOUND)
        
        if count != len(item_ids):
            return Response({
                "warning": f"Only {count} of {len(item_ids)} items found",
                "found_count": count
            })
        
        try:
            deleted_count, details = queryset.delete()
            
            logger.info(f"🗑️ User {request.user.username} bulk deleted {deleted_count} items")
            
            return Response({
                "message": f"Successfully deleted {deleted_count} items",
                "deleted_count": deleted_count,
                "details": details
            }, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Error bulk deleting items: {str(e)}", exc_info=True)
            return Response({
                "error": f"Failed to delete items: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def upload_pid(self, request):
        """
        Upload P&ID PDF and extract line list items using OCR (Background Processing)
        Uses Celery task for async processing with progress tracking
        Returns task_id for status polling
        """
        pid_file = request.FILES.get('pid_file')
        list_type = request.data.get('list_type', 'line_list')
        line_format_config = request.data.get('line_format_config')
        use_async = request.data.get('use_async', 'true').lower() == 'true'
        
        if not pid_file:
            return Response({
                "error": "No P&ID file provided"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if not pid_file.name.endswith('.pdf'):
            return Response({
                "error": "Only PDF files are supported"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        if list_type not in LIST_TYPES:
            return Response({
                "error": f"Invalid list_type. Must be one of: {', '.join(LIST_TYPES.keys())}"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            import tempfile
            import os
            from django.core.files.storage import default_storage
            from .pid_ocr_extractor_v2 import get_pid_extractor
            from .models import DesignProject
            from .tasks import process_pid_upload
            
            # Get or create project
            project, _ = DesignProject.objects.get_or_create(
                project_name="P&ID Upload Project",
                defaults={
                    'created_by': request.user,
                    'design_type': 'pid',
                    'status': 'active'
                }
            )
            
            # Save PDF file
            file_path = f"designiq/pid_uploads/{timezone.now().strftime('%Y/%m/%d')}/{pid_file.name}"
            saved_path = default_storage.save(file_path, pid_file)
            
            # Save to temp file for OCR processing
            with tempfile.NamedTemporaryFile(delete=False, suffix='.pdf') as tmp:
                pid_file.seek(0)
                tmp.write(pid_file.read())
                tmp_path = tmp.name
            
            try:
                # Parse line format config if provided
                if line_format_config:
                    try:
                        line_format_config = json.loads(line_format_config)
                    except Exception:
                        logger.warning("⚠️ Invalid line_format_config JSON; ignoring custom format")
                        line_format_config = None
                else:
                    line_format_config = None

                # For async processing (default), use Celery task
                if use_async:
                    # Queue background task
                    task = process_pid_upload.delay(
                        tmp_path,
                        line_format_config=line_format_config,
                        user_id=request.user.id,
                        list_type=list_type
                    )
                    
                    logger.info(f"📤 [P&ID Upload] Queued background task: {task.id}")
                    
                    return Response({
                        "message": "P&ID upload queued for processing",
                        "task_id": task.id,
                        "filename": pid_file.name,
                        "status": "queued",
                        "note": "Use /designiq/lists/upload_pid_status/{task_id}/ to check progress"
                    }, status=status.HTTP_202_ACCEPTED)
                
                # For synchronous processing (fallback or testing)
                else:
                    # Extract line numbers using singleton extractor (fast, reuses models)
                    extractor = get_pid_extractor()
                    line_items = extractor.extract_from_pdf(tmp_path, line_format_config=line_format_config)
                    table_data = extractor.format_as_table_data(line_items)
                    
                    logger.info(f"📊 Extracted {len(line_items)} line numbers from {pid_file.name}")
                    logger.info(f"🎯 Using Multi-Engine OCR (Tesseract + EasyOCR + PaddleOCR) + Strict Validation")
                    
                    # Return extracted data immediately
                    return Response({
                        "message": "P&ID processed successfully",
                        "filename": pid_file.name,
                        "total_items": len(table_data),
                        "extracted_lines": table_data,
                        "pdf_path": saved_path,
                        "note": "Multi-engine OCR (Tesseract + EasyOCR + PaddleOCR) + Strict pattern matching with programmatic validation. Filters out garbage words and validates each component. Data displayed for review - not saved to database."
                    }, status=status.HTTP_201_CREATED)
                    "filename": pid_file.name,
                    "total_items": len(table_data),
                    "extracted_lines": table_data,
                    "pdf_path": saved_path,
                    "note": "Multi-engine OCR (Tesseract + EasyOCR + PaddleOCR) + Strict pattern matching with programmatic validation. Filters out garbage words and validates each component. Data displayed for review - not saved to database."
                }, status=status.HTTP_201_CREATED)
                
            finally:
                # Clean up temp file (only for sync mode)
                if not use_async and os.path.exists(tmp_path):
                    os.unlink(tmp_path)
            
        except Exception as e:
            logger.error(f"❌ Error processing P&ID: {str(e)}", exc_info=True)
            return Response({
                "error": f"Failed to process P&ID: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=False, methods=['get'], url_path='upload_pid_status/(?P<task_id>[^/.]+)')
    def upload_pid_status(self, request, task_id=None):
        """
        Check status of P&ID upload background task
        GET /api/v1/designiq/lists/upload_pid_status/{task_id}/
        
        Returns:
            - state: PENDING, PROGRESS, SUCCESS, FAILURE
            - current/total: Progress percentage
            - status: Human-readable status message
            - result: Final result if SUCCESS
        """
        from celery.result import AsyncResult
        
        if not task_id:
            return Response({
                "error": "No task_id provided"
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            task = AsyncResult(task_id)
            
            if task.state == 'PENDING':
                response = {
                    'state': task.state,
                    'status': 'Task is waiting in queue...',
                    'current': 0,
                    'total': 100
                }
            elif task.state == 'PROGRESS':
                response = {
                    'state': task.state,
                    'current': task.info.get('current', 0),
                    'total': task.info.get('total', 100),
                    'status': task.info.get('status', 'Processing...')
                }
            elif task.state == 'SUCCESS':
                result = task.result
                response = {
                    'state': task.state,
                    'status': 'Completed successfully!',
                    'current': 100,
                    'total': 100,
                    'result': result
                }
            else:
                # FAILURE or other states
                response = {
                    'state': task.state,
                    'status': str(task.info),
                    'error': str(task.info) if task.failed() else None
                }
            
            return Response(response, status=status.HTTP_200_OK)
            
        except Exception as e:
            logger.error(f"❌ Error checking task status: {str(e)}", exc_info=True)
            return Response({
                "error": f"Failed to check task status: {str(e)}"
            logger.error(f"Error uploading P&ID: {str(e)}", exc_info=True)
            return Response({
                "error": f"Failed to upload P&ID: {str(e)}"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
