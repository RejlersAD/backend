"""
Data Visibility Mixin
Reusable mixin for ViewSets to automatically apply row-level security

FEATURES:
- Automatic configuration via dynamic discovery
- Works with future modules without code changes
- Smart defaults based on conventions
- Manual override support

USAGE:
------
1. Import the mixin
2. Add to your ViewSet (before ModelViewSet)
3. Set visibility_module_code (required)
4. Optional: Set visibility_owner_field (auto-detected if not provided)
5. Done! Automatic data filtering applied

EXAMPLE:
--------
from apps.rbac.data_visibility_mixin import DataVisibilityMixin

class NewModuleViewSet(DataVisibilityMixin, viewsets.ModelViewSet):
    visibility_module_code = 'new_module'  # That's all you need!
    queryset = NewModule.objects.all()
    # System auto-configures everything else
"""
from rest_framework import viewsets
from django.db.models import Q

# Support both static and dynamic configurations
try:
    from .data_visibility_config_dynamic import (
        build_visibility_filter,
        is_admin_user,
        user_has_module_access,
        get_visibility_description,
        log_data_access,
        get_visibility_config,
    )
    DYNAMIC_CONFIG = True
except ImportError:
    # Fallback to static config
    from .data_visibility_config import (
        build_visibility_filter,
        is_admin_user,
        user_has_module_access,
        get_visibility_description,
        log_data_access,
        VisibilityStrategy
    )
    DYNAMIC_CONFIG = False


class DataVisibilityMixin:
    """
    Mixin to automatically apply data visibility filters to ViewSet querysets
    
    Attributes to set in your ViewSet:
        visibility_module_code (str): Module code from data_visibility_config
        visibility_owner_field (str, optional): Field that stores the record owner
        visibility_additional_filters (Q, optional): Additional filters to apply
        visibility_bypass (bool, optional): Set to True to bypass filtering (dangerous!)
        visibility_logging (bool, optional): Enable audit logging (default: True)
    
    Methods you can override:
        get_visibility_filter(): Customize the filter logic
        apply_visibility_filter(queryset): Customize how filter is applied
    """
    
    # Configuration attributes (set these in your ViewSet)
    visibility_module_code = None  # Required: e.g., 'crs_documents'
    visibility_owner_field = None  # Optional: e.g., 'uploaded_by', 'created_by'
    visibility_additional_filters = None  # Optional: Additional Q filters
    visibility_bypass = False  # Set to True to disable filtering (use with caution!)
    visibility_logging = True  # Set to False to disable audit logging
    
    def get_visibility_filter(self):
        """
        Build the visibility filter based on configuration
        Override this method for custom filtering logic
        
        Returns:
            Django Q object for filtering
        """
        if self.visibility_bypass:
            return Q()  # No filtering
        
        if not self.visibility_module_code:
            # No module code configured - default to no filtering (backward compatible)
            return Q()
        
        # Get model class for auto-detection
        model_class = self.queryset.model if hasattr(self.queryset, 'model') else None
        
        # Build filter using configuration (dynamic or static)
        if DYNAMIC_CONFIG:
            # Dynamic config can auto-detect owner field
            return build_visibility_filter(
                user=self.request.user,
                module_code=self.visibility_module_code,
                owner_field=self.visibility_owner_field,  # Can be None for auto-detect
                additional_filters=self.visibility_additional_filters,
                model_class=model_class  # Pass model for field detection
            )
        else:
            # Static config requires owner_field
            return build_visibility_filter(
                user=self.request.user,
                module_code=self.visibility_module_code,
                owner_field=self.visibility_owner_field,
                additional_filters=self.visibility_additional_filters
            )
    
    def apply_visibility_filter(self, queryset):
        """
        Apply visibility filter to queryset
        Override this method to customize how the filter is applied
        
        Args:
            queryset: Original queryset
        
        Returns:
            Filtered queryset
        """
        visibility_filter = self.get_visibility_filter()
        
        # Apply filter
        filtered_queryset = queryset.filter(visibility_filter)
        
        # Audit logging
        if self.visibility_logging:
            try:
                count = filtered_queryset.count()
                filter_desc = self._get_filter_description()
                log_data_access(
                    user=self.request.user,
                    module_code=self.visibility_module_code or 'unknown',
                    record_count=count,
                    filters_applied=filter_desc
                )
            except Exception:
                # Don't fail requests due to logging errors
                pass
        
        return filtered_queryset
    
    def get_queryset(self):
        """
        Override get_queryset to apply visibility filtering
        This is called by DRF for all list/retrieve operations
        """
        # Get base queryset from parent class
        queryset = super().get_queryset()
        
        # Apply visibility filter
        return self.apply_visibility_filter(queryset)
    
    def _get_filter_description(self):
        """Get human-readable description of applied filters"""
        if self.visibility_bypass:
            return "No filtering (bypass enabled)"
        
        if is_admin_user(self.request.user):
            return "Admin access - all records visible"
        
        if not self.visibility_module_code:
            return "No visibility configuration"
        
        desc = get_visibility_description(self.visibility_module_code)
        
        if user_has_module_access(self.request.user, self.visibility_module_code):
            return f"{desc} (Team member access)"
        else:
            return f"{desc} (Personal access only)"


class CustomVisibilityMixin(DataVisibilityMixin):
    """
    Advanced mixin with additional helper methods for complex scenarios
    """
    
    def get_team_members_queryset(self):
        """
        Get queryset of all team members (users with same module access)
        Useful for assignee dropdowns, collaboration features, etc.
        """
        from django.contrib.auth import get_user_model
        from .data_visibility_config import get_users_with_module_access
        
        User = get_user_model()
        
        if not self.visibility_module_code:
            return User.objects.none()
        
        team_user_ids = get_users_with_module_access(self.visibility_module_code)
        return User.objects.filter(id__in=team_user_ids, is_active=True)
    
    def check_record_access(self, record):
        """
        Check if current user has access to a specific record
        Useful for custom permission checks
        
        Args:
            record: Model instance
        
        Returns:
            bool: True if user has access, False otherwise
        """
        # Admins have access to everything
        if is_admin_user(self.request.user):
            return True
        
        # Check if record matches visibility filter
        from django.db.models import Model
        if not isinstance(record, Model):
            return False
        
        # Get queryset with visibility filter applied
        model_class = record.__class__
        filtered_queryset = model_class.objects.filter(
            pk=record.pk
        ).filter(self.get_visibility_filter())
        
        return filtered_queryset.exists()
    
    def get_accessible_records_count(self):
        """
        Get total count of records accessible to current user
        Useful for dashboard stats, analytics, etc.
        """
        queryset = self.get_queryset()
        return queryset.count()


# ============================================================================
# SPECIALIZED MIXINS FOR COMMON PATTERNS
# ============================================================================

class PersonalDataMixin(DataVisibilityMixin):
    """
    Mixin for personal data (user sees only their own records)
    Example: User preferences, notifications, personal files
    """
    visibility_bypass = False
    
    def get_visibility_filter(self):
        """Override to always use personal filtering"""
        if not self.visibility_owner_field:
            raise ValueError("visibility_owner_field must be set for PersonalDataMixin")
        
        return Q(**{self.visibility_owner_field: self.request.user})


class TeamCollaborationMixin(DataVisibilityMixin):
    """
    Mixin for team collaboration data (users with same module see each other's data)
    Example: QHSE projects, CRS documents, shared reports
    """
    visibility_bypass = False
    
    def get_visibility_filter(self):
        """Use module team strategy"""
        if not self.visibility_module_code:
            raise ValueError("visibility_module_code must be set for TeamCollaborationMixin")
        
        return build_visibility_filter(
            user=self.request.user,
            module_code=self.visibility_module_code,
            owner_field=self.visibility_owner_field
        )


class ProjectBasedMixin(DataVisibilityMixin):
    """
    Mixin for project-based data (users see data for projects they're involved in)
    Example: Project tasks, milestones, project documents
    """
    project_owner_field = 'project__owner'  # Override in your ViewSet
    project_members_field = 'project__team_members'  # Override in your ViewSet
    
    def get_visibility_filter(self):
        """
        Users see records for projects they own or are members of
        """
        # Admins see everything
        if is_admin_user(self.request.user):
            return Q()
        
        # Build filter for project access
        filter_q = Q(**{self.project_owner_field: self.request.user})
        
        # Add team member filter
        if self.project_members_field:
            filter_q = filter_q | Q(**{self.project_members_field: self.request.user})
        
        return filter_q


# ============================================================================
# USAGE EXAMPLES
# ============================================================================

"""
EXAMPLE 1: Simple team collaboration (QHSE, CRS, Finance)
---------------------------------------------------------

from apps.rbac.data_visibility_mixin import DataVisibilityMixin

class QHSERunningProjectViewSet(DataVisibilityMixin, viewsets.ModelViewSet):
    visibility_module_code = 'qhse'
    # No owner_field needed - entire team sees all QHSE projects
    
    queryset = QHSERunningProject.objects.filter(is_active=True)
    serializer_class = QHSERunningProjectSerializer
    permission_classes = [IsAuthenticated]


EXAMPLE 2: Personal data with owner field
-----------------------------------------

from apps.rbac.data_visibility_mixin import PersonalDataMixin

class NotificationViewSet(PersonalDataMixin, viewsets.ModelViewSet):
    visibility_owner_field = 'recipient'
    
    queryset = Notification.objects.all()
    serializer_class = NotificationSerializer
    permission_classes = [IsAuthenticated]


EXAMPLE 3: Team collaboration with fallback to personal
-------------------------------------------------------

from apps.rbac.data_visibility_mixin import TeamCollaborationMixin

class CRSDocumentViewSet(TeamCollaborationMixin, viewsets.ModelViewSet):
    visibility_module_code = 'crs_documents'
    visibility_owner_field = 'uploaded_by'
    
    queryset = CRSDocument.objects.all()
    serializer_class = CRSDocumentSerializer
    permission_classes = [IsAuthenticated]


EXAMPLE 4: Custom visibility logic
----------------------------------

from apps.rbac.data_visibility_mixin import CustomVisibilityMixin

class DesignProjectViewSet(CustomVisibilityMixin, viewsets.ModelViewSet):
    visibility_module_code = 'designiq'
    visibility_owner_field = 'created_by'
    
    def get_visibility_filter(self):
        # Custom logic: Show public templates + user's private templates
        if self.action == 'list' and 'template' in self.request.path:
            return Q(is_public=True) | Q(created_by=self.request.user)
        
        # Default to parent logic for other cases
        return super().get_visibility_filter()
    
    queryset = DesignProject.objects.all()
    serializer_class = DesignProjectSerializer
    permission_classes = [IsAuthenticated]


EXAMPLE 5: Check access to specific record
------------------------------------------

from apps.rbac.data_visibility_mixin import CustomVisibilityMixin

class CRSDocumentViewSet(CustomVisibilityMixin, viewsets.ModelViewSet):
    visibility_module_code = 'crs_documents'
    visibility_owner_field = 'uploaded_by'
    
    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        document = self.get_object()
        
        # Check if user has access
        if not self.check_record_access(document):
            return Response(
                {'error': 'You do not have access to this document'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        # Proceed with archiving
        document.status = 'archived'
        document.save()
        return Response({'status': 'archived'})


EXAMPLE 6: Get team members for assignment
------------------------------------------

from apps.rbac.data_visibility_mixin import CustomVisibilityMixin

class QHSERunningProjectViewSet(CustomVisibilityMixin, viewsets.ModelViewSet):
    visibility_module_code = 'qhse'
    
    @action(detail=False, methods=['get'])
    def team_members(self, request):
        '''Get all QHSE team members for assignment dropdown'''
        team = self.get_team_members_queryset()
        data = [
            {
                'id': user.id,
                'email': user.email,
                'name': f"{user.first_name} {user.last_name}".strip()
            }
            for user in team
        ]
        return Response(data)
"""
