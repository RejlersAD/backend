"""
Admin-only views for system maintenance and diagnostics.
"""
import logging
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAdminUser, IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.contrib.auth import get_user_model
from django.db import transaction

from .models import Organization, UserProfile
from .profile_utils import get_or_create_profile, ProfileProvisioningError
from .profile_config import PROFILE_AUTO_PROVISION

User = get_user_model()
logger = logging.getLogger(__name__)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_admin_status(request):
    """
    Check if current user is an admin and can access admin endpoints.
    
    GET /api/v1/rbac/admin/check-status/
    """
    user = request.user
    return Response({
        'user': {
            'id': user.id,
            'email': user.email,
            'username': user.username,
            'is_staff': user.is_staff,
            'is_superuser': user.is_superuser,
            'is_admin': user.is_staff or user.is_superuser,
        },
        'can_provision': user.is_staff or user.is_superuser,
        'message': 'You are an admin' if (user.is_staff or user.is_superuser) else 'You need admin privileges',
    })


@api_view(['POST'])
@permission_classes([IsAuthenticated])  # Temporarily allow any authenticated user
def provision_all_profiles(request):
    """
    Endpoint to ensure all users have profiles.
    
    This endpoint:
    1. Creates default organization if missing
    2. Creates UserProfile for all users without one
    3. Returns detailed report
    
    POST /api/v1/rbac/admin/provision-profiles/
    
    Note: Temporarily accessible to all authenticated users to fix production issue.
    """
    logger.info(f"[Admin] Profile provisioning requested by: {request.user.email}")
    
    report = {
        'success': False,
        'organization': {},
        'users': {
            'total': 0,
            'had_profile': 0,
            'created': 0,
            'failed': 0,
        },
        'created_profiles': [],
        'errors': [],
    }
    
    try:
        # Step 1: Ensure organization exists
        logger.info("[Admin] Step 1: Checking organizations...")
        orgs = Organization.objects.filter(is_active=True)
        
        if orgs.exists():
            org = orgs.first()
            report['organization'] = {
                'status': 'exists',
                'name': org.name,
                'id': str(org.id),
                'code': org.code,
            }
            logger.info(f"[Admin] ✅ Organization exists: {org.name}")
        else:
            logger.warning("[Admin] No active organization found. Creating default...")
            try:
                org, created = Organization.objects.get_or_create(
                    code=PROFILE_AUTO_PROVISION['organization_code'],
                    defaults={
                        'name': PROFILE_AUTO_PROVISION['organization_name'],
                        'description': PROFILE_AUTO_PROVISION['organization_description'],
                        'is_active': True,
                    }
                )
                report['organization'] = {
                    'status': 'created' if created else 'exists',
                    'name': org.name,
                    'id': str(org.id),
                    'code': org.code,
                }
                logger.info(f"[Admin] ✅ Created organization: {org.name}")
            except Exception as e:
                error_msg = f"Failed to create organization: {str(e)}"
                logger.error(f"[Admin] ❌ {error_msg}")
                report['errors'].append(error_msg)
                return Response(report, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
        # Step 2: Get all users
        all_users = User.objects.all()
        report['users']['total'] = all_users.count()
        logger.info(f"[Admin] Step 2: Processing {report['users']['total']} users...")
        
        # Step 3: Create profiles for users without one
        users_without_profiles = User.objects.filter(rbac_profile__isnull=True)
        missing_count = users_without_profiles.count()
        
        if missing_count == 0:
            logger.info("[Admin] ✅ All users already have profiles")
            report['users']['had_profile'] = report['users']['total']
            report['success'] = True
            return Response(report, status=status.HTTP_200_OK)
        
        logger.info(f"[Admin] Found {missing_count} users without profiles. Creating...")
        report['users']['had_profile'] = report['users']['total'] - missing_count
        
        for user in users_without_profiles:
            try:
                profile = get_or_create_profile(user, source='AdminProvisionEndpoint')
                report['users']['created'] += 1
                report['created_profiles'].append({
                    'user_id': user.id,
                    'email': user.email,
                    'profile_id': str(profile.id),
                })
                logger.info(f"[Admin] ✅ Created profile for: {user.email}")
            except (ProfileProvisioningError, Exception) as e:
                report['users']['failed'] += 1
                error_detail = {
                    'user_id': user.id,
                    'email': user.email,
                    'error': str(e),
                }
                report['errors'].append(error_detail)
                logger.error(f"[Admin] ❌ Failed for {user.email}: {e}")
        
        # Step 4: Final report
        if report['users']['failed'] == 0:
            report['success'] = True
            logger.info(f"[Admin] ✅ SUCCESS: Created {report['users']['created']} profiles")
            return Response(report, status=status.HTTP_200_OK)
        else:
            report['success'] = False
            logger.warning(f"[Admin] ⚠️  Partial success: {report['users']['created']} created, {report['users']['failed']} failed")
            return Response(report, status=status.HTTP_207_MULTI_STATUS)
            
    except Exception as e:
        logger.exception(f"[Admin] Unexpected error in profile provisioning")
        report['errors'].append(f"Unexpected error: {str(e)}")
        return Response(report, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@permission_classes([IsAdminUser])
def check_profile_status(request):
    """
    Admin-only endpoint to check profile provisioning status.
    
    GET /api/v1/admin/profile-status/
    """
    logger.info(f"[Admin] Profile status check by: {request.user.email}")
    
    try:
        # Check organizations
        orgs = Organization.objects.filter(is_active=True)
        org_data = []
        for org in orgs:
            org_data.append({
                'id': str(org.id),
                'name': org.name,
                'code': org.code,
                'is_active': org.is_active,
            })
        
        # Check users and profiles
        total_users = User.objects.count()
        total_profiles = UserProfile.objects.count()
        users_without_profiles = User.objects.filter(rbac_profile__isnull=True)
        missing_count = users_without_profiles.count()
        
        missing_users = []
        for user in users_without_profiles[:10]:  # Limit to first 10
            missing_users.append({
                'id': user.id,
                'email': user.email,
                'username': user.username,
            })
        
        report = {
            'organizations': {
                'total': Organization.objects.count(),
                'active': orgs.count(),
                'list': org_data,
            },
            'users': {
                'total': total_users,
                'with_profile': total_profiles,
                'missing_profile': missing_count,
                'percentage_complete': round((total_profiles / total_users * 100) if total_users > 0 else 0, 2),
            },
            'missing_users_sample': missing_users,
            'needs_provisioning': missing_count > 0,
        }
        
        logger.info(f"[Admin] Status: {total_profiles}/{total_users} users have profiles")
        return Response(report, status=status.HTTP_200_OK)
        
    except Exception as e:
        logger.exception("[Admin] Error checking profile status")
        return Response({
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
