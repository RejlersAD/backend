"""
API Views for Feature Registry
Provides dynamic feature discovery and configuration
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from apps.core.feature_registry import get_registry, FeatureCategory, FeatureStatus


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def list_features(request):
    """
    Get all features available to the authenticated user
    Query params:
        - category: Filter by category
        - status: Filter by status
        - search: Search query
    """
    try:
        print(f"[DEBUG list_features] Request from user: {request.user}")
        print(f"[DEBUG list_features] User authenticated: {request.user.is_authenticated}")
        print(f"[DEBUG list_features] User ID: {request.user.id if hasattr(request.user, 'id') else 'N/A'}")
        
        registry = get_registry()
        print(f"[DEBUG list_features] Registry loaded successfully")
        
        # Get user permissions (expand based on your RBAC system)
        user_permissions = []
        if hasattr(request.user, 'get_all_permissions'):
            user_permissions = list(request.user.get_all_permissions())
        print(f"[DEBUG list_features] User has {len(user_permissions)} permissions")
        
        # Get user department if applicable
        user_department = getattr(request.user, 'department', None)
        print(f"[DEBUG list_features] User department: {user_department}")
        
        # Get query parameters
        category_filter = request.query_params.get('category')
        status_filter = request.query_params.get('status')
        search_query = request.query_params.get('search')
        print(f"[DEBUG list_features] Filters - category: {category_filter}, status: {status_filter}, search: {search_query}")
        
        # Get features for user
        features = registry.get_features_for_user(user_permissions, user_department)

        # Filter by user's accessible module codes (RBAC)
        try:
            from apps.rbac.models import UserProfile
            profile = UserProfile.objects.prefetch_related('roles__modules').get(user=request.user)
            user_module_codes = set(
                mod.code
                for role in profile.roles.filter(is_active=True)
                for mod in role.modules.filter(is_active=True)
            )
            if not (request.user.is_superuser or request.user.is_staff):
                # Feature registry ids don't always match RBAC Module.code —
                # alias the ones that diverge (verified against live rbac_modules table)
                FEATURE_ID_TO_MODULE_CODE = {
                    'pfd_converter':      'pfd_to_pid',
                    'user_management':    'user_mgmt',
                    'sales_dashboard':    'sales',
                    'sales_crm':          'sales',
                    'sales_pipeline':     'sales',
                    'sales_ai_insights':  'sales',
                    'project_management': 'project_control',
                }
                features = [
                    f for f in features
                    if FEATURE_ID_TO_MODULE_CODE.get(f.id, f.id) in user_module_codes
                ]
        except Exception:
            pass
        print(f"[DEBUG list_features] get_features_for_user returned {len(features)} features")
        
        # Apply additional filters
        if category_filter:
            try:
                category = FeatureCategory(category_filter)
                features = [f for f in features if f.category == category]
                print(f"[DEBUG list_features] After category filter: {len(features)} features")
            except ValueError:
                print(f"[DEBUG list_features] Invalid category filter: {category_filter}")
                pass
        
        if status_filter:
            try:
                status_enum = FeatureStatus(status_filter)
                features = [f for f in features if f.status == status_enum]
                print(f"[DEBUG list_features] After status filter: {len(features)} features")
            except ValueError:
                print(f"[DEBUG list_features] Invalid status filter: {status_filter}")
                pass
        
        if search_query:
            search_results = registry.search(search_query)
            feature_ids = {f.id for f in search_results}
            features = [f for f in features if f.id in feature_ids]
            print(f"[DEBUG list_features] After search filter: {len(features)} features")
        
        print(f"[DEBUG list_features] Final feature count: {len(features)}")
        print(f"[DEBUG list_features] Returning {len(features)} features")
        for f in features:
            print(f"[DEBUG] Feature: {f.id}, Category: {f.category.value}, Name: {f.name}")
        
        result_dict = registry.to_dict_list(features)
        print(f"[DEBUG list_features] to_dict_list returned {len(result_dict)} items")
        
        response_data = {
            'success': True,
            'count': len(features),
            'features': result_dict
        }
        print(f"[DEBUG list_features] Response data: count={response_data['count']}, features={len(response_data['features'])}")
        return Response(response_data)
    except Exception as e:
        import traceback
        print(f"[ERROR list_features] Exception: {str(e)}")
        print(f"[ERROR list_features] Traceback:")
        traceback.print_exc()
        return Response({
            'success': True,
            'count': 0,
            'features': [],
            'message': 'Features unavailable, returning empty list',
            'error': str(e)
        })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_feature(request, feature_id):
    """Get details for a specific feature"""
    registry = get_registry()
    feature = registry.get(feature_id)
    
    if not feature:
        return Response({
            'success': False,
            'error': 'Feature not found'
        }, status=status.HTTP_404_NOT_FOUND)
    
    # Check if user has access
    user_permissions = []
    if hasattr(request.user, 'get_all_permissions'):
        user_permissions = list(request.user.get_all_permissions())
    
    user_department = getattr(request.user, 'department', None)
    accessible_features = registry.get_features_for_user(user_permissions, user_department)
    
    if feature not in accessible_features:
        return Response({
            'success': False,
            'error': 'Access denied'
        }, status=status.HTTP_403_FORBIDDEN)
    
    return Response({
        'success': True,
        'feature': feature.to_dict()
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_categories(request):
    """Get all feature categories with counts"""
    registry = get_registry()
    
    # Get user-accessible features
    user_permissions = []
    if hasattr(request.user, 'get_all_permissions'):
        user_permissions = list(request.user.get_all_permissions())
    
    user_department = getattr(request.user, 'department', None)
    features = registry.get_features_for_user(user_permissions, user_department)
    
    # Count by category
    category_counts = {}
    for feature in features:
        cat = feature.category.value
        category_counts[cat] = category_counts.get(cat, 0) + 1
    
    # Build response
    categories = []
    for cat in FeatureCategory:
        categories.append({
            'id': cat.value,
            'name': cat.value.replace('_', ' ').title(),
            'count': category_counts.get(cat.value, 0)
        })
    
    return Response({
        'success': True,
        'categories': categories
    })


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_navigation(request):
    """
    Get navigation structure based on available features
    Used to dynamically build menus and sidebars
    """
    registry = get_registry()
    
    # Get user-accessible features
    user_permissions = []
    if hasattr(request.user, 'get_all_permissions'):
        user_permissions = list(request.user.get_all_permissions())
    
    user_department = getattr(request.user, 'department', None)
    features = registry.get_features_for_user(user_permissions, user_department)
    
    # Group by category
    navigation = {}
    for feature in features:
        cat = feature.category.value
        if cat not in navigation:
            navigation[cat] = {
                'category': cat,
                'displayName': cat.replace('_', ' ').title(),
                'items': []
            }
        
        navigation[cat]['items'].append({
            'id': feature.id,
            'name': feature.name,
            'route': feature.frontend_route,
            'icon': feature.icon,
            'isNew': feature.is_new,
            'colorScheme': feature.color_scheme
        })
    
    return Response({
        'success': True,
        'navigation': list(navigation.values())
    })
