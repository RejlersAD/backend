"""
PFD Project Views
API endpoints for managing PFD projects and uploads
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

from ..models import PFDProject, PFDUpload
from ..serializers import (
    PFDProjectListSerializer,
    PFDProjectDetailSerializer,
    PFDProjectCreateSerializer,
    PFDUploadSerializer,
)

logger = logging.getLogger(__name__)


class PFDProjectViewSet(viewsets.ModelViewSet):
    """
    ViewSet for PFD Projects
    
    Endpoints:
    - GET /api/v1/pfd/projects/ - List all projects
    - POST /api/v1/pfd/projects/ - Create new project
    - GET /api/v1/pfd/projects/{id}/ - Get project details
    - PATCH /api/v1/pfd/projects/{id}/ - Update project
    - DELETE /api/v1/pfd/projects/{id}/ - Delete project
    - POST /api/v1/pfd/projects/{id}/upload-reference-doc/ - Upload reference document
    - POST /api/v1/pfd/projects/{id}/upload-pfd/ - Upload PFD to project
    """
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    
    def get_queryset(self):
        """Get projects for current user"""
        # Handle both User and UserProfile
        user = self.request.user
        if hasattr(user, 'user'):
            # UserProfile - get the actual User
            user = user.user
        
        queryset = PFDProject.objects.filter(created_by=user, is_active=True)
        
        # Filter by search query
        search = self.request.query_params.get('search', None)
        if search:
            queryset = queryset.filter(
                project_name__icontains=search
            ) | queryset.filter(
                project_id__icontains=search
            )
        
        return queryset.prefetch_related('pfd_uploads')
    
    def get_serializer_class(self):
        """Return appropriate serializer based on action"""
        if self.action == 'list':
            return PFDProjectListSerializer
        elif self.action == 'create':
            return PFDProjectCreateSerializer
        else:
            return PFDProjectDetailSerializer
    
    def create(self, request, *args, **kwargs):
        """Create a new PFD project"""
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)
            project = serializer.save()
            
            # Return detailed response
            detail_serializer = PFDProjectDetailSerializer(project)
            return Response({
                'success': True,
                'message': f'Project {project.project_id} created successfully',
                'project': detail_serializer.data
            }, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            logger.error(f"Error creating PFD project: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    
    def retrieve(self, request, *args, **kwargs):
        """Get project details with all uploads"""
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
    
    def list(self, request, *args, **kwargs):
        """List all projects"""
        try:
            queryset = self.get_queryset()
            serializer = self.get_serializer(queryset, many=True)
            return Response({
                'success': True,
                'count': queryset.count(),
                'projects': serializer.data
            })
        except Exception as e:
            logger.error(f"Error listing projects: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'], url_path='upload-reference-doc')
    def upload_reference_doc(self, request, pk=None):
        """
        Upload a reference document to the project
        
        Body:
        - document_type: One of [bfd, process_description, process_design_basis, 
                                  operation_control_philosophy, scope_of_work, 
                                  legends_symbols, equipment_datasheet, other]
        - file: File upload
        """
        try:
            project = self.get_object()
            document_type = request.data.get('document_type')
            file = request.FILES.get('file')
            
            if not document_type or not file:
                return Response({
                    'success': False,
                    'error': 'Both document_type and file are required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Validate document type
            valid_types = [
                'bfd', 'process_description', 'process_design_basis',
                'operation_control_philosophy', 'scope_of_work',
                'legends_symbols', 'equipment_datasheet', 'other'
            ]
            
            if document_type not in valid_types:
                return Response({
                    'success': False,
                    'error': f'Invalid document_type. Must be one of: {", ".join(valid_types)}'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Save file
            file_path = f'pfd_projects/{project.project_id}/reference_docs/{document_type}/{file.name}'
            saved_path = default_storage.save(file_path, file)
            
            # Update project reference documents
            if not project.reference_documents:
                project.reference_documents = {}
            
            project.reference_documents[document_type] = saved_path
            project.save()
            
            logger.info(f"Uploaded reference document {document_type} for project {project.project_id}")
            
            return Response({
                'success': True,
                'message': f'Reference document {document_type} uploaded successfully',
                'file_path': saved_path,
                'reference_documents': project.reference_documents
            })
        
        except Exception as e:
            logger.error(f"Error uploading reference document: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'], url_path='upload-pfd')
    def upload_pfd(self, request, pk=None):
        """
        Upload a PFD document to the project
        
        Body:
        - file: PFD file upload
        - drawing_number: (optional)
        - drawing_revision: (optional)
        - drawing_title: (optional)
        - project_name_field: (optional)
        """
        try:
            project = self.get_object()
            file = request.FILES.get('file')
            
            if not file:
                return Response({
                    'success': False,
                    'error': 'File is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Save PFD file
            file_path = f'pfd_projects/{project.project_id}/pfds/{file.name}'
            saved_path = default_storage.save(file_path, file)
            
            # Create PFD upload record
            pfd_upload = PFDUpload.objects.create(
                project=project,
                file_name=file.name,
                file_path=saved_path,
                file_size=file.size,
                drawing_number=request.data.get('drawing_number', ''),
                drawing_revision=request.data.get('drawing_revision', ''),
                drawing_title=request.data.get('drawing_title', ''),
                project_name_field=request.data.get('project_name_field', ''),
                uploaded_by=request.user,
                status='uploaded'
            )
            
            logger.info(f"Uploaded PFD {pfd_upload.upload_id} to project {project.project_id}")
            
            serializer = PFDUploadSerializer(pfd_upload)
            return Response({
                'success': True,
                'message': f'PFD uploaded successfully with ID {pfd_upload.upload_id}',
                'upload': serializer.data
            }, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            logger.error(f"Error uploading PFD: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['get'], url_path='reference-documents')
    def get_reference_documents(self, request, pk=None):
        """Get all reference documents for a project"""
        try:
            project = self.get_object()
            return Response({
                'success': True,
                'project_id': project.project_id,
                'reference_documents': project.reference_documents or {}
            })
        except Exception as e:
            logger.error(f"Error getting reference documents: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_404_NOT_FOUND)
    
    @action(detail=True, methods=['get'], url_path='pfd-uploads')
    def get_pfd_uploads(self, request, pk=None):
        """Get all PFD uploads for a project"""
        try:
            project = self.get_object()
            uploads = project.pfd_uploads.all()
            serializer = PFDUploadSerializer(uploads, many=True)
            
            return Response({
                'success': True,
                'project_id': project.project_id,
                'count': uploads.count(),
                'uploads': serializer.data
            })
        except Exception as e:
            logger.error(f"Error getting PFD uploads: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_404_NOT_FOUND)


    @action(detail=False, methods=['post'], url_path='upload')
    def combined_upload(self, request):
        """
        Combined endpoint for uploading PFD with reference documents
        
        Body:
        - project_id: Project ID to upload to
        - pfd_file: PFD file upload
        - drawing_number: Drawing number
        - drawing_revision: Drawing revision
        - drawing_title: Drawing title
        - reference_bfd: (optional) BFD file
        - reference_process_description: (optional)
        - reference_process_design_basis: (optional)
        - reference_operation_control_philosophy: (optional)
        - reference_scope_of_work: (optional)
        - reference_legends_symbols: (optional)
        - reference_equipment_data_sheet: (optional)
        - reference_other_documents: (optional)
        """
        try:
            # ✅ FIX: For multipart/form-data, check both request.data AND request.POST
            project_id = request.data.get('project_id') or request.POST.get('project_id')
            pfd_file = request.FILES.get('pfd_file')
            
            if not project_id:
                logger.error("No project_id provided in upload request")
                return Response({
                    'success': False,
                    'error': 'project_id is required'
                }, status=status.HTTP_400_BAD_REQUEST)
                
            if not pfd_file:
                return Response({
                    'success': False,
                    'error': 'pfd_file is required'
                }, status=status.HTTP_400_BAD_REQUEST)
            
            # Get project
            try:
                logger.info(f"Looking for project with ID: {project_id} for user: {request.user.username}")
                project = PFDProject.objects.get(
                    project_id=project_id,
                    created_by=request.user,
                    is_active=True
                )
                logger.info(f"✅ Found project: {project.project_name} ({project.project_id})")
            except PFDProject.DoesNotExist:
                logger.error(f"❌ Project {project_id} not found for user {request.user.username}")
                # Try to find if project exists for another user
                all_matching = PFDProject.objects.filter(project_id=project_id)
                if all_matching.exists():
                    logger.error(f"   Project exists but belongs to another user: {all_matching.first().created_by.username}")
                return Response({
                    'success': False,
                    'error': f'Project {project_id} not found'
                }, status=status.HTTP_404_NOT_FOUND)
            
            # Upload reference documents if provided
            reference_doc_mapping = {
                'reference_bfd': 'bfd',
                'reference_process_description': 'process_description',
                'reference_process_design_basis': 'process_design_basis',
                'reference_operation_control_philosophy': 'operation_control_philosophy',
                'reference_scope_of_work': 'scope_of_work',
                'reference_legends_symbols': 'legends_symbols',
                'reference_equipment_data_sheet': 'equipment_data_sheet',
                'reference_other_documents': 'other_documents'
            }
            
            if not project.reference_documents:
                project.reference_documents = {}
            
            uploaded_refs = []
            for form_key, doc_type in reference_doc_mapping.items():
                ref_file = request.FILES.get(form_key)
                if ref_file:
                    file_path = f'pfd_projects/{project.project_id}/reference_docs/{doc_type}/{ref_file.name}'
                    saved_path = default_storage.save(file_path, ref_file)
                    project.reference_documents[doc_type] = saved_path
                    uploaded_refs.append(doc_type)
            
            if uploaded_refs:
                project.save()
                logger.info(f"Uploaded {len(uploaded_refs)} reference documents for project {project.project_id}")
            
            # Upload PFD file
            pfd_file_path = f'pfd_projects/{project.project_id}/pfds/{pfd_file.name}'
            pfd_saved_path = default_storage.save(pfd_file_path, pfd_file)
            
            # Create PFD upload record
            pfd_upload = PFDUpload.objects.create(
                project=project,
                file_name=pfd_file.name,
                file_path=pfd_saved_path,
                file_size=pfd_file.size,
                drawing_number=request.data.get('drawing_number', ''),
                drawing_revision=request.data.get('revision', ''),
                drawing_title=request.data.get('drawing_title', ''),
                project_name_field=project.project_name,
                uploaded_by=request.user,
                status='uploaded'
            )
            
            logger.info(f"Combined upload: PFD {pfd_upload.upload_id} with {len(uploaded_refs)} refs to project {project.project_id}")
            
            serializer = PFDUploadSerializer(pfd_upload)
            return Response({
                'success': True,
                'message': f'PFD and {len(uploaded_refs)} reference documents uploaded successfully',
                'upload_id': pfd_upload.upload_id,
                'upload': serializer.data,
                'uploaded_references': uploaded_refs
            }, status=status.HTTP_201_CREATED)
            
        except Exception as e:
            logger.error(f"Error in combined upload: {e}")
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
