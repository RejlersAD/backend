"""
Email Verification API Views
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.response import Response
from django.conf import settings
from apps.rbac.email_verification import verify_email_token, send_verification_email, send_welcome_email


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_email(request):
    """
    Verify user email with token
    
    POST /api/v1/auth/verify-email/
    Body: {
        "email": "user@example.com",
        "token": "verification_token"
    }
    """
    email = request.data.get('email')
    token = request.data.get('token')
    
    if not email or not token:
        return Response({
            'success': False,
            'message': 'Email and token are required'
        }, status=400)
    
    success, message, user_profile = verify_email_token(email, token)
    
    if success:
        # Send welcome email
        send_welcome_email(user_profile)
        
        return Response({
            'success': True,
            'message': message,
            'user': {
                'email': user_profile.user.email,
                'first_name': user_profile.user.first_name,
                'last_name': user_profile.user.last_name,
                'status': user_profile.status
            }
        }, status=200)
    else:
        return Response({
            'success': False,
            'message': message
        }, status=400)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def resend_verification_email(request):
    """
    Resend verification email to authenticated user
    
    POST /api/v1/auth/resend-verification/
    """
    try:
        user_profile = request.user.rbac_profile
        
        # Check if already verified
        if user_profile.metadata and user_profile.metadata.get('email_verified'):
            return Response({
                'success': False,
                'message': 'Email is already verified'
            }, status=400)
        
        # Send verification email
        success = send_verification_email(user_profile, request)
        
        if success:
            return Response({
                'success': True,
                'message': 'Verification email sent successfully'
            }, status=200)
        else:
            return Response({
                'success': False,
                'message': 'Failed to send verification email'
            }, status=500)
            
    except Exception as e:
        return Response({
            'success': False,
            'message': f'Error: {str(e)}'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def check_verification_status(request):
    """
    Check if user's email is verified
    
    GET /api/v1/auth/verification-status/
    """
    try:
        user_profile = request.user.rbac_profile
        
        is_verified = user_profile.metadata and user_profile.metadata.get('email_verified', False)
        verified_at = user_profile.metadata.get('email_verified_at') if user_profile.metadata else None
        
        return Response({
            'verified': is_verified,
            'verified_at': verified_at,
            'status': user_profile.status,
            'email': request.user.email
        }, status=200)
        
    except Exception as e:
        return Response({
            'error': str(e)
        }, status=500)
