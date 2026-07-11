"""
Emergency admin endpoint to fix user Django flags
TEMPORARY - Should be removed after fix is applied
"""
from django.contrib.auth import get_user_model
from django.db import transaction
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from apps.rbac.permissions import IsSuperAdmin

User = get_user_model()


@api_view(['POST'])
@permission_classes([IsSuperAdmin])
def fix_user_django_flags(request):
    """
    Emergency endpoint to remove is_superuser and is_staff flags from non-admin users
    
    POST /api/rbac/admin/fix-user-flags/
    Body: {"email": "user@example.com"}
    """
    email = request.data.get('email')
    
    if not email:
        return Response(
            {'error': 'Email is required'}, 
            status=status.HTTP_400_BAD_REQUEST
        )
    
    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response(
            {'error': f'User not found: {email}'}, 
            status=status.HTTP_404_NOT_FOUND
        )
    
    # Check current state
    before_state = {
        'email': user.email,
        'is_superuser': user.is_superuser,
        'is_staff': user.is_staff,
        'is_active': user.is_active,
    }
    
    # Check RBAC roles
    try:
        profile = user.rbac_profile
        roles = list(profile.roles.filter(is_active=True).values_list('code', flat=True))
    except:
        roles = []
    
    before_state['rbac_roles'] = roles
    
    # Determine if fix is needed
    needs_fix = user.is_superuser or user.is_staff
    
    if not needs_fix:
        return Response({
            'status': 'no_action_needed',
            'message': 'User permissions are already correct',
            'current_state': before_state
        })
    
    # Apply fix
    with transaction.atomic():
        user.is_superuser = False
        user.is_staff = False
        user.save(update_fields=['is_superuser', 'is_staff'])
    
    # Get updated state
    user.refresh_from_db()
    after_state = {
        'email': user.email,
        'is_superuser': user.is_superuser,
        'is_staff': user.is_staff,
        'is_active': user.is_active,
        'rbac_roles': roles
    }
    
    return Response({
        'status': 'success',
        'message': 'User Django flags removed. User must logout and login again.',
        'before': before_state,
        'after': after_state,
        'changes_applied': {
            'is_superuser': f'{before_state["is_superuser"]} → {after_state["is_superuser"]}',
            'is_staff': f'{before_state["is_staff"]} → {after_state["is_staff"]}'
        }
    }, status=status.HTTP_200_OK)
