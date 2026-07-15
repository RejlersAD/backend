"""
Admin Utility View: Create RadAI Managers
==========================================
HTTP endpoint to create three RadAI managers in production.
Requires admin/superuser authentication.

URL: POST /api/v1/admin/create-radai-managers/
"""

from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model
from apps.rbac.models import UserProfile, Organization

User = get_user_model()


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def create_radai_managers(request):
    """
    Create three RadAI managers for the Reporting Manager dropdown.
    
    Requires: Authenticated user with superuser or admin privileges
    Returns: JSON with creation status and details
    """
    
    # Security check - only superusers or staff can run this
    if not (request.user.is_superuser or request.user.is_staff):
        return Response(
            {'error': 'Permission denied. Only administrators can create managers.'},
            status=status.HTTP_403_FORBIDDEN
        )
    
    # Get organization
    org = Organization.objects.filter(is_active=True).order_by('created_at').first()
    
    if not org:
        return Response(
            {'error': 'No active organization found. Create an organization first.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    # Managers to create
    managers = [
        {
            'email': 'rafat.sm.saqer@rejlers.ae',
            'first_name': 'Rafat',
            'last_name': 'S. M. Saqer',
            'username': 'rafat_sm_saqer',
        },
        {
            'email': 'anam.abbas@rejlers.ae',
            'first_name': 'Anam',
            'last_name': 'Abbas',
            'username': 'anam_abbas',
        },
        {
            'email': 'aleksi.murtomaki@rejlers.ae',
            'first_name': 'Aleksi',
            'last_name': 'Murtomaki',
            'username': 'aleksi_murtomaki',
        },
    ]
    
    created_users = []
    updated_users = []
    errors = []
    
    for mgr in managers:
        try:
            email = mgr['email']
            
            # Create or update user
            user, user_created = User.objects.update_or_create(
                email=email,
                defaults={
                    'username': mgr['username'],
                    'first_name': mgr['first_name'],
                    'last_name': mgr['last_name'],
                    'is_active': True,
                    'is_staff': False,
                    'is_superuser': False,
                }
            )
            
            if user_created:
                user.set_unusable_password()
                user.save()
            
            # Create or update profile
            profile, profile_created = UserProfile.objects.update_or_create(
                user=user,
                defaults={
                    'organization': org,
                    'department': 'radai',
                    'job_title': 'Manager',
                    'status': 'active',
                    'is_deleted': False,
                }
            )
            
            manager_data = {
                'email': email,
                'name': f"{mgr['first_name']} {mgr['last_name']}",
                'department': 'radai',
                'job_title': 'Manager',
                'status': 'active',
                'organization': org.name,
                'user_created': user_created,
                'profile_created': profile_created,
            }
            
            if user_created or profile_created:
                created_users.append(manager_data)
            else:
                updated_users.append(manager_data)
                
        except Exception as e:
            errors.append({
                'email': mgr['email'],
                'error': str(e)
            })
    
    return Response({
        'success': True,
        'message': f'Successfully processed {len(managers)} managers',
        'organization': {
            'id': str(org.id),
            'name': org.name,
            'code': org.code,
        },
        'created': created_users,
        'updated': updated_users,
        'errors': errors,
        'summary': {
            'total': len(managers),
            'created': len(created_users),
            'updated': len(updated_users),
            'failed': len(errors),
        },
        'next_steps': [
            'Go to https://www.radai.ae/profile',
            'Clear browser cache (Ctrl+Shift+R)',
            'Check "Reporting Manager" dropdown',
            'All 3 managers should now appear'
        ]
    }, status=status.HTTP_201_CREATED if created_users else status.HTTP_200_OK)
