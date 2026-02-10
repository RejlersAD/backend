"""
QHSE API Views - Soft-coded RESTful API endpoints
Maintains backward compatibility with existing frontend
"""
from rest_framework import viewsets, status, filters
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from django.db.models import Sum, Avg, Count, Q, Max
from django.db.models.functions import TruncMonth
from datetime import datetime, timedelta
try:
    import pandas as pd
    PANDAS_AVAILABLE = True
except ImportError:
    PANDAS_AVAILABLE = False
from decimal import Decimal

# Import Data Visibility for Row-Level Security
from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

from .models import QHSERunningProject, QHSEAudit  # QHSESpotCheckRegister - Disabled
from .serializers import (
    QHSERunningProjectSerializer, 
    # QHSESpotCheckRegisterSerializer,  # Disabled per QHSE Manager decision
    QHSEAuditSerializer,
    QHSEDashboardStatsSerializer
)


class QHSERunningProjectViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    """
    QHSE Running Projects API ViewSet
    Provides CRUD operations and custom actions
    
    🔐 Data Visibility:
    - QHSE team members (with qhse module) see all QHSE projects for collaboration
    - Users without QHSE module have no access (team-only module)
    - Admins see everything
    """
    # Data visibility configuration
    visibility_module_code = 'qhse'
    # No visibility_owner_field - team-based collaboration (no personal ownership)
    
    queryset = QHSERunningProject.objects.filter(is_active=True)
    serializer_class = QHSERunningProjectSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['project_no', 'project_title', 'client', 'project_manager']
    ordering_fields = ['sr_no', 'project_starting_date', 'project_closing_date', 'updated_at']
    ordering = ['sr_no']
    
    def perform_create(self, serializer):
        """
        Override perform_create to add logging and ensure data is saved
        """
        print(f"[QHSE ViewSet] Creating new project")
        print(f"[QHSE ViewSet] Request data keys: {list(self.request.data.keys())}")
        print(f"[QHSE ViewSet] User: {self.request.user.email if self.request.user.is_authenticated else 'Anonymous'}")
        
        instance = serializer.save()
        
        print(f"[QHSE ViewSet] Successfully created project ID: {instance.id}, sr_no: {instance.sr_no}")
        return instance
    
    def update(self, request, *args, **kwargs):
        """
        Override update to add comprehensive error logging
        SOFT-CODED: Debugging 400 errors during project updates
        """
        partial = kwargs.pop('partial', False)
        instance = self.get_object()
        
        print(f"\n{'='*80}")
        print(f"[QHSE ViewSet] UPDATE REQUEST DETAILS")
        print(f"{'='*80}")
        print(f"Project ID: {instance.id}")
        print(f"Project No: {instance.project_no}")
        print(f"Method: {request.method}")
        print(f"Partial: {partial}")
        print(f"User: {request.user.email if request.user.is_authenticated else 'Anonymous'}")
        print(f"Request Data Keys: {list(request.data.keys())}")
        print(f"Request Data: {request.data}")
        
        serializer = self.get_serializer(instance, data=request.data, partial=partial)
        
        try:
            serializer.is_valid(raise_exception=True)
            print(f"[QHSE ViewSet] ✅ Validation passed")
        except Exception as e:
            print(f"[QHSE ViewSet] ❌ Validation failed")
            print(f"[QHSE ViewSet] Error type: {type(e).__name__}")
            print(f"[QHSE ViewSet] Error message: {str(e)}")
            if hasattr(serializer, 'errors'):
                print(f"[QHSE ViewSet] Serializer errors: {serializer.errors}")
            print(f"{'='*80}\n")
            raise
        
        self.perform_update(serializer)
        
        if getattr(instance, '_prefetched_objects_cache', None):
            instance._prefetched_objects_cache = {}
        
        print(f"[QHSE ViewSet] ✅ Update successful")
        print(f"{'='*80}\n")
        
        return Response(serializer.data)
    
    def perform_update(self, serializer):
        """
        Override perform_update to add logging and ensure data is saved to database
        """
        print(f"[QHSE ViewSet] Saving updated data...")
        
        # Save the instance
        instance = serializer.save()
        
        # Verify the save by refreshing from database
        instance.refresh_from_db()
        
        print(f"[QHSE ViewSet] Successfully saved - sr_no: {instance.sr_no}, updated_at: {instance.updated_at}")
        
        return instance
    
    def get_queryset(self):
        """
        Soft-coded filtering based on query parameters
        """
        queryset = super().get_queryset()
        
        # Filter by client
        client = self.request.query_params.get('client', None)
        if client:
            queryset = queryset.filter(client__icontains=client)
        
        # Filter by project manager
        project_manager = self.request.query_params.get('project_manager', None)
        if project_manager:
            queryset = queryset.filter(project_manager__icontains=project_manager)
        
        # Filter by quality engineer
        quality_eng = self.request.query_params.get('quality_engineer', None)
        if quality_eng:
            queryset = queryset.filter(project_quality_eng__icontains=quality_eng)
        
        # Filter by overdue status
        overdue = self.request.query_params.get('overdue', None)
        if overdue == 'true':
            today = datetime.now().date()
            queryset = [p for p in queryset if p.is_overdue]
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date', None)
        end_date = self.request.query_params.get('end_date', None)
        if start_date:
            queryset = queryset.filter(project_starting_date__gte=start_date)
        if end_date:
            queryset = queryset.filter(project_closing_date__lte=end_date)
        
        return queryset
    
    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """
        Get comprehensive dashboard statistics
        Soft-coded to return various metrics
        """
        projects = self.get_queryset()
        
        # Calculate statistics
        total_projects = projects.count()
        active_projects = projects.filter(
            Q(project_closing_date__gte=datetime.now().date()) |
            Q(project_extension__gte=datetime.now().date())
        ).count()
        overdue_projects = len([p for p in projects if p.is_overdue])
        
        # CARs and Observations
        aggregates = projects.aggregate(
            total_cars_open=Sum('cars_open'),
            total_cars_closed=Sum('cars_closed'),
            total_obs_open=Sum('obs_open'),
            total_obs_closed=Sum('obs_closed'),
            total_manhours_allocated=Sum('man_hour_for_quality'),
            total_manhours_used=Sum('manhours_used'),
        )
        
        # Average percentages (parse from string format "XX%")
        billability_values = []
        completion_values = []
        for project in projects:
            try:
                billability_values.append(float(project.quality_billability_percent.rstrip('%')))
            except:
                pass
            try:
                completion_values.append(float(project.project_completion_percent.rstrip('%')))
            except:
                pass
        
        avg_billability = sum(billability_values) / len(billability_values) if billability_values else 0
        avg_completion = sum(completion_values) / len(completion_values) if completion_values else 0
        
        # Projects by client
        projects_by_client = {}
        for project in projects:
            client = project.client
            projects_by_client[client] = projects_by_client.get(client, 0) + 1
        
        # Spot checks
        spot_checks = QHSESpotCheckRegister.objects.filter(is_active=True)
        total_spot_checks = spot_checks.count()
        pending_spot_checks = spot_checks.filter(status='OPEN').count()
        
        # Monthly spot checks (last 12 months)
        today = datetime.now()
        twelve_months_ago = today - timedelta(days=365)
        monthly_spot_checks_data = spot_checks.filter(
            date_of_spot_check__gte=twelve_months_ago
        ).annotate(
            month=TruncMonth('date_of_spot_check')
        ).values('month').annotate(
            count=Count('id')
        ).order_by('month')
        
        monthly_spot_checks = {
            item['month'].strftime('%Y-%m'): item['count'] 
            for item in monthly_spot_checks_data
        }
        
        # Audits
        total_audits = QHSEAudit.objects.filter(status='COMPLETED').count()
        
        stats = {
            'total_projects': total_projects,
            'active_projects': active_projects,
            'overdue_projects': overdue_projects,
            'total_cars_open': aggregates['total_cars_open'] or 0,
            'total_cars_closed': aggregates['total_cars_closed'] or 0,
            'total_obs_open': aggregates['total_obs_open'] or 0,
            'total_obs_closed': aggregates['total_obs_closed'] or 0,
            'total_spot_checks': total_spot_checks,
            'pending_spot_checks': pending_spot_checks,
            'average_quality_billability': round(avg_billability, 2),
            'average_project_completion': round(avg_completion, 2),
            'total_manhours_allocated': float(aggregates['total_manhours_allocated'] or 0),
            'total_manhours_used': float(aggregates['total_manhours_used'] or 0),
            'total_audits_completed': total_audits,
            'projects_by_client': projects_by_client,
            'monthly_spot_checks': monthly_spot_checks,
        }
        
        serializer = QHSEDashboardStatsSerializer(stats)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """Duplicate a project for new phase"""
        project = self.get_object()
        project.pk = None
        project.sr_no = QHSERunningProject.objects.aggregate(Max('sr_no'))['sr_no__max'] + 1
        project.project_no = f"{project.project_no}-COPY"
        project.save()
        serializer = self.get_serializer(project)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    def destroy(self, request, *args, **kwargs):
        """
        Smart delete implementation
        Supports both hard delete and soft delete (set is_active=False)
        Uses query parameter 'hard_delete=true' for permanent deletion
        """
        try:
            # Get the instance - use unfiltered queryset to find even inactive ones
            instance = QHSERunningProject.objects.get(pk=kwargs.get('pk'))
            
            # Check for hard delete parameter
            hard_delete = request.query_params.get('hard_delete', 'false').lower() == 'true'
            
            if hard_delete:
                # Permanent deletion from database
                instance.delete()
                return Response(
                    {'message': 'Project permanently deleted from database'},
                    status=status.HTTP_204_NO_CONTENT
                )
            else:
                # Soft delete - set is_active to False
                instance.is_active = False
                instance.save()
                return Response(
                    {'message': 'Project marked as inactive (soft delete)'},
                    status=status.HTTP_200_OK
                )
        except QHSERunningProject.DoesNotExist:
            return Response(
                {'error': 'Project not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        except Exception as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser, FormParser])
    def bulk_upload(self, request):
        """
        Bulk upload QHSE projects from Excel file
        Replaces Google Forms manual entry
        """
        try:
            file = request.FILES.get('file')
            if not file:
                return Response(
                    {'detail': 'No file provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Read Excel file
            df = pd.read_excel(file, sheet_name='QHSE _Running Project Status ', header=2)
            
            created_count = 0
            updated_count = 0
            skipped_count = 0
            errors = []

            # Process each row
            for idx, row in df.iterrows():
                try:
                    # Skip empty rows
                    if pd.isna(row.get('Project No')):
                        skipped_count += 1
                        continue

                    project_no = str(row.get('Project No', '')).strip()
                    if not project_no:
                        skipped_count += 1
                        continue

                    # Prepare data
                    data = {
                        'project_no': project_no,
                        'project_title': str(row.get('Project Title', '')),
                        'client': str(row.get('CLIENT', row.get('Client', ''))),
                        'project_manager': str(row.get('Project Manager', '')),
                        'project_quality_eng': str(row.get('Project Quality Engineer ', row.get('Project Quality Engineer', ''))),
                        'man_hour_for_quality': Decimal(str(row.get('Manhours for Quality', 0) or 0)),
                        'manhours_used': Decimal(str(row.get('Manhours Used', 0) or 0)),
                        'quality_billability_percent': str(row.get('Quality Billability %', '0%')),
                        'cars_open': int(row.get('CARs Open', 0) or 0),
                        'cars_closed': int(row.get('CARs Closed', 0) or 0),
                        'obs_open': int(row.get('No. of Obs Open', 0) or 0),
                        'obs_closed': int(row.get('Obs Closed', 0) or 0),
                        'project_kpis_achieved_percent': str(row.get('Project KPIs Achieved %', '0%')),
                        'project_completion_percent': str(row.get('Project Completion %', '0%')),
                        'cost_of_poor_quality_aed': Decimal(str(row.get('Cost of poor quality AED', 0) or 0)),
                    }

                    # Handle dates
                    date_fields = {
                        'project_starting_date': 'Project Starting Date ',
                        'project_closing_date': 'Project Closing Date ',
                        'project_extension': 'Project Extension ',
                    }
                    for field, col in date_fields.items():
                        val = row.get(col, row.get(col.strip()))
                        if pd.notna(val) and val != '':
                            try:
                                data[field] = pd.to_datetime(val).date()
                            except:
                                pass

                    # Update or create
                    project, created = QHSERunningProject.objects.update_or_create(
                        project_no=project_no,
                        defaults=data
                    )

                    if created:
                        created_count += 1
                    else:
                        updated_count += 1

                except Exception as e:
                    skipped_count += 1
                    errors.append(f"Row {idx + 1}: {str(e)}")
                    continue

            return Response({
                'created': created_count,
                'updated': updated_count,
                'skipped': skipped_count,
                'errors': errors[:10]  # Limit errors to first 10
            }, status=status.HTTP_200_OK)

        except Exception as e:
            return Response(
                {'detail': f'Upload failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


# ============================================================================
# QHSESpotCheckRegisterViewSet - DISABLED per QHSE Manager decision
# ============================================================================
# class QHSESpotCheckRegisterViewSet(viewsets.ModelViewSet):
#     """
#     QHSE Spot Check Register API ViewSet
#     """
#     queryset = QHSESpotCheckRegister.objects.filter(is_active=True)
#     serializer_class = QHSESpotCheckRegisterSerializer
#     permission_classes = [IsAuthenticated]
#     filter_backends = [filters.SearchFilter, filters.OrderingFilter]
#     search_fields = ['project_no', 'project_title', 'qhse_engineer', 'document_no']
#     ordering_fields = ['date_of_spot_check', 'sr_no']
#     ordering = ['-date_of_spot_check']
#     
#     def get_queryset(self):
#         """
#         Soft-coded filtering
#         """
#         queryset = super().get_queryset()
#         
#         # Filter by project
#         project_no = self.request.query_params.get('project_no', None)
#         if project_no:
#             queryset = queryset.filter(project_no=project_no)
#         
#         # Filter by engineer
#         engineer = self.request.query_params.get('engineer', None)
#         if engineer:
#             queryset = queryset.filter(qhse_engineer__icontains=engineer)
#         
#         # Filter by status
#         spot_status = self.request.query_params.get('status', None)
#         if spot_status:
#             queryset = queryset.filter(status=spot_status)
#         
#         # Filter by category
#         category = self.request.query_params.get('category', None)
#         if category:
#             queryset = queryset.filter(category=category)
#         
#         # Filter by date range
#         start_date = self.request.query_params.get('start_date', None)
#         end_date = self.request.query_params.get('end_date', None)
#         if start_date:
#             queryset = queryset.filter(date_of_spot_check__gte=start_date)
#         if end_date:
#             queryset = queryset.filter(date_of_spot_check__lte=end_date)
#         
#         return queryset
#     
#     @action(detail=False, methods=['get'])
#     def by_project(self, request):
#         """Get spot checks grouped by project"""
#         queryset = self.get_queryset()
#         projects = {}
#         
#         for spot_check in queryset:
#             project_no = spot_check.project_no
#             if project_no not in projects:
#                 projects[project_no] = {
#                     'project_no': project_no,
#                     'project_title': spot_check.project_title,
#                     'client': spot_check.client,
#                     'spot_checks': []
#                 }
#             serializer = self.get_serializer(spot_check)
#             projects[project_no]['spot_checks'].append(serializer.data)
#         
#         return Response(list(projects.values()))
# ============================================================================


class QHSEAuditViewSet(viewsets.ModelViewSet):
    """
    QHSE Audit API ViewSet
    """
    queryset = QHSEAudit.objects.all()
    serializer_class = QHSEAuditSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['project__project_no', 'auditor']
    ordering_fields = ['audit_date']
    ordering = ['-audit_date']
    
    def get_queryset(self):
        """Filter audits"""
        queryset = super().get_queryset()
        
        # Filter by project
        project_id = self.request.query_params.get('project_id', None)
        if project_id:
            queryset = queryset.filter(project_id=project_id)
        
        # Filter by audit type
        audit_type = self.request.query_params.get('audit_type', None)
        if audit_type:
            queryset = queryset.filter(audit_type=audit_type)
        
        # Filter by status
        audit_status = self.request.query_params.get('status', None)
        if audit_status:
            queryset = queryset.filter(status=audit_status)
        
        return queryset
