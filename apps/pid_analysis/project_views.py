"""
P&ID Project Views
API endpoints for managing P&ID projects with RBAC S3 integration
"""

from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from django.core.files.storage import default_storage
from django.utils import timezone
import logging
import os
import boto3
from botocore.exceptions import ClientError

from .models import PIDProject, PIDDrawing
from .serializers import (
    PIDProjectListSerializer,
    PIDProjectDetailSerializer,
    PIDProjectCreateSerializer,
)

logger = logging.getLogger(__name__)


class PIDProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for P&ID Projects with RBAC S3 storage
    
    Endpoints:
    - GET /api/v1/pid/projects/ - List all projects
    - POST /api/v1/pid/projects/ - Create new project
    - GET /api/v1/pid/projects/{id}/ - Get project details
    - PATCH /api/v1/pid/projects/{id}/ - Update project
    - DELETE /api/v1/pid/projects/{id}/ - Delete project
    
    S3 Structure: pid_drawings/{organization}/{project_id}/
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    
    def get_queryset(self):
        """Get projects for current user - RBAC filtered"""
        user = self.request.user
        
        # Get organization for RBAC filtering
        organization = None
        if hasattr(user, 'organization'):
            organization = user.organization.name
        elif hasattr(user, 'userprofile') and hasattr(user.userprofile, 'organization'):
            organization = user.userprofile.organization.name
        
        # Filter by created_by and optionally by organization
        queryset = PIDProject.objects.filter(created_by=user, is_active=True)
        
        if organization:
            queryset = queryset.filter(organization=organization)
        
        # Search filtering
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                project_name__icontains=search
            ) | queryset.filter(
                project_id__icontains=search
            )
        
        return queryset.order_by('-created_at')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return PIDProjectListSerializer
        elif self.action == 'create':
            return PIDProjectCreateSerializer
        else:
            return PIDProjectDetailSerializer
    
    def create(self, request, *args, **kwargs):
        """Create a new P&ID project with S3 setup"""
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            project = serializer.save()
            
            # Create S3 folder structure for RBAC
            self._create_s3_project_folder(project)
            
            # Return detailed response
            detail_serializer = PIDProjectDetailSerializer(project)
            return Response({
                'success': True,
                'message': f'Project {project.project_id} created successfully',
                'project': detail_serializer.data
            }, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            logger.error(f"Error creating P&ID project: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    
    def retrieve(self, request, *args, **kwargs):
        """Get project details with all drawings"""
        try:
            instance = self.get_object()
            serializer = self.get_serializer(instance)
            return Response({
                'success': True,
                'project': serializer.data
            })
        except Exception as e:
            logger.error(f"Error retrieving project: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_404_NOT_FOUND)
    
    def update(self, request, *args, **kwargs):
        """Update project details"""
        try:
            partial = kwargs.pop('partial', False)
            instance = self.get_object()
            
            # Update allowed fields
            if 'name' in request.data:
                instance.project_name = request.data['name']
            if 'description' in request.data:
                instance.description = request.data['description']
            
            instance.save()
            
            serializer = PIDProjectDetailSerializer(instance)
            return Response({
                'success': True,
                'message': 'Project updated successfully',
                'project': serializer.data
            })
        except Exception as e:
            logger.error(f"Error updating project: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    
    def destroy(self, request, *args, **kwargs):
        """Soft delete project"""
        try:
            instance = self.get_object()
            instance.is_active = False
            instance.save()
            
            return Response({
                'success': True,
                'message': f'Project {instance.project_id} deleted successfully'
            })
        except Exception as e:
            logger.error(f"Error deleting project: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    
    def _create_s3_project_folder(self, project):
        """
        Create S3 folder for project with RBAC structure
        Format: pid_drawings/{organization}/{project_id}/
        """
        try:
            from django.conf import settings
            
            # Check if using S3
            if not hasattr(settings, 'AWS_STORAGE_BUCKET_NAME'):
                logger.info("S3 not configured, skipping folder creation")
                return
            
            s3_path = project.get_s3_path()
            
            # Create empty .keep file to establish folder
            s3_client = boto3.client(
                's3',
                aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
                aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
                region_name=settings.AWS_S3_REGION_NAME
            )
            
            # Upload .keep file
            s3_client.put_object(
                Bucket=settings.AWS_STORAGE_BUCKET_NAME,
                Key=f"{s3_path}.keep",
                Body=b''
            )
            
            logger.info(f"Created S3 folder structure: {s3_path}")
        
        except ClientError as e:
            logger.error(f"S3 error creating project folder: {e}")
        except Exception as e:
            logger.error(f"Error creating project folder: {e}")
