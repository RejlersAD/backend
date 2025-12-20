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
    registry = get_registry()
    
    # Get user permissions (expand based on your RBAC system)
    user_permissions = []
    if hasattr(request.user, 'get_all_permissions'):
        user_permissions = list(request.user.get_all_permissions())
    
    # Get user department if applicable
    user_department = getattr(request.user, 'department', None)
    
    # Get query parameters
    category_filter = request.query_params.get('category')
    status_filter = request.query_params.get('status')
    search_query = request.query_params.get('search')
    
    # Get features for user
    features = registry.get_features_for_user(user_permissions, user_department)
    
    # Apply additional filters
    if category_filter:
        try:
            category = FeatureCategory(category_filter)
            features = [f for f in features if f.category == category]
        except ValueError:
            pass
    
    if status_filter:
        try:
            status_enum = FeatureStatus(status_filter)
            features = [f for f in features if f.status == status_enum]
        except ValueError:
            pass
    
    if search_query:
        search_results = registry.search(search_query)
        feature_ids = {f.id for f in search_results}
        features = [f for f in features if f.id in feature_ids]
    
    return Response({
        'success': True,
        'count': len(features),
        'features': registry.to_dict_list(features)
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
