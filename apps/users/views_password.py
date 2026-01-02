"""
Views for first-time login and password reset
Soft-coded authentication and password management
"""
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.response import Response
from django.contrib.auth import get_user_model
from django.utils import timezone
from django.db import transaction
import logging
from .password_reset_service import PasswordResetService

User = get_user_model()
logger = logging.getLogger(__name__)


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def check_first_login(request):
    """
    Check if current user needs to reset password on first login
    
    Returns:
        {
            "is_first_login": bool,
            "must_reset_password": bool,
            "message": str
        }
    """
    try:
        user = request.user
        
        return Response({
            'is_first_login': user.is_first_login,
            'must_reset_password': user.must_reset_password,
            'message': 'Password reset required' if user.must_reset_password else 'No action needed'
        })
    except Exception as e:
        logger.error(f"Error checking first login: {str(e)}")
        return Response(
            {'error': 'Failed to check first login status'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def reset_first_login_password(request):
    """
    Reset password on first login
    
    Request body:
        {
            "current_password": "string",
            "new_password": "string",
            "confirm_password": "string"
        }
    
    Returns:
        {
            "success": bool,
            "message": str
        }
    """
    try:
        user = request.user
        current_password = request.data.get('current_password')
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')
        
        # Validate inputs
        if not all([current_password, new_password, confirm_password]):
            return Response(
                {'error': 'All password fields are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if passwords match
        if new_password != confirm_password:
            return Response(
                {'error': 'New passwords do not match'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate password strength
        if len(new_password) < 8:
            return Response(
                {'error': 'Password must be at least 8 characters long'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if new password is different from current
        if current_password == new_password:
            return Response(
                {'error': 'New password must be different from current password'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify current password
        if not user.check_password(current_password):
            return Response(
                {'error': 'Current password is incorrect'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update password and reset flags
        with transaction.atomic():
            user.set_password(new_password)
            user.is_first_login = False
            user.must_reset_password = False
            user.last_password_change = timezone.now()
            user.temp_password_created_at = None
            user.save()
        
        logger.info(f"User {user.email} successfully reset password on first login")
        
        return Response({
            'success': True,
            'message': 'Password successfully updated. You can now use your new password to login.'
        })
        
    except Exception as e:
        logger.error(f"Error resetting first login password: {str(e)}")
        return Response(
            {'error': 'Failed to reset password'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
def validate_email(request):
    """
    Validate email address format and availability
    
    Request body:
        {
            "email": "string"
        }
    
    Returns:
        {
            "is_valid": bool,
            "is_available": bool,
            "message": str
        }
    """
    try:
        email = request.data.get('email', '').strip()
        
        if not email:
            return Response({
                'is_valid': False,
                'is_available': False,
                'message': 'Email address is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate email format
        from apps.users.email_service import EmailService
        validation_result = EmailService.validate_email_deliverability(email)
        
        if not validation_result['is_valid']:
            return Response({
                'is_valid': False,
                'is_available': False,
                'message': validation_result['message']
            })
        
        # Check if email is already in use
        email_exists = User.objects.filter(email=email).exists()
        
        if email_exists:
            return Response({
                'is_valid': True,
                'is_available': False,
                'message': 'This email address is already registered'
            })
        
        return Response({
            'is_valid': True,
            'is_available': True,
            'message': 'Email address is valid and available'
        })
        
    except Exception as e:
        logger.error(f"Error validating email: {str(e)}")
        return Response(
            {'error': 'Failed to validate email'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([IsAuthenticated])
def change_password(request):
    """
    Change password for authenticated user
    
    Request body:
        {
            "current_password": "string",
            "new_password": "string",
            "confirm_password": "string"
        }
    """
    try:
        user = request.user
        current_password = request.data.get('current_password')
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')
        
        # Validate inputs
        if not all([current_password, new_password, confirm_password]):
            return Response(
                {'error': 'All password fields are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if passwords match
        if new_password != confirm_password:
            return Response(
                {'error': 'New passwords do not match'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate password strength
        if len(new_password) < 8:
            return Response(
                {'error': 'Password must be at least 8 characters long'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify current password
        if not user.check_password(current_password):
            return Response(
                {'error': 'Current password is incorrect'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update password
        with transaction.atomic():
            user.set_password(new_password)
            user.last_password_change = timezone.now()
            user.save()
        
        logger.info(f"User {user.email} successfully changed password")
        
        return Response({
            'success': True,
            'message': 'Password successfully updated'
        })
        
    except Exception as e:
        logger.error(f"Error changing password: {str(e)}")
        return Response(
            {'error': 'Failed to change password'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
def request_password_reset(request):
    """
    Request password reset link via email
    
    Request body:
        {
            "email": "user@example.com"
        }
    
    Returns:
        {
            "success": bool,
            "message": str
        }
    """
    try:
        email = request.data.get('email', '').strip()
        
        if not email:
            return Response(
                {'error': 'Email address is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Always return success message for security (don't reveal if user exists)
        try:
            user = User.objects.get(email=email)
            
            # Generate token and send email
            token, expiry = PasswordResetService.create_reset_token(user)
            email_sent = PasswordResetService.send_password_reset_email(user, token, request)
            
            if email_sent:
                logger.info(f"Password reset email sent to {email}")
            else:
                logger.warning(f"Failed to send password reset email to {email}")
                
        except User.DoesNotExist:
            logger.info(f"Password reset requested for non-existent email: {email}")
            # Don't reveal that user doesn't exist
            pass
        
        return Response({
            'success': True,
            'message': 'If an account exists with this email, you will receive a password reset link shortly.'
        })
        
    except Exception as e:
        logger.error(f"Error requesting password reset: {str(e)}")
        return Response(
            {'error': 'Failed to process password reset request'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
def verify_reset_token(request):
    """
    Verify password reset token validity
    
    Request body:
        {
            "email": "user@example.com",
            "token": "reset_token_string"
        }
    
    Returns:
        {
            "valid": bool,
            "message": str,
            "user": {
                "email": str,
                "first_name": str,
                "last_name": str
            } (if valid)
        }
    """
    try:
        email = request.data.get('email', '').strip()
        token = request.data.get('token', '').strip()
        
        if not email or not token:
            return Response(
                {'error': 'Email and token are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify token
        is_valid, message, user = PasswordResetService.verify_reset_token(email, token)
        
        if is_valid:
            return Response({
                'valid': True,
                'message': message,
                'user': {
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name
                }
            })
        else:
            return Response({
                'valid': False,
                'message': message
            }, status=status.HTTP_400_BAD_REQUEST)
            
    except Exception as e:
        logger.error(f"Error verifying reset token: {str(e)}")
        return Response(
            {'error': 'Failed to verify reset token'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


@api_view(['POST'])
@permission_classes([AllowAny])
def reset_password_with_token(request):
    """
    Reset password using valid token
    
    Request body:
        {
            "email": "user@example.com",
            "token": "reset_token_string",
            "new_password": "string",
            "confirm_password": "string"
        }
    
    Returns:
        {
            "success": bool,
            "message": str
        }
    """
    try:
        email = request.data.get('email', '').strip()
        token = request.data.get('token', '').strip()
        new_password = request.data.get('new_password')
        confirm_password = request.data.get('confirm_password')
        
        # Validate inputs
        if not all([email, token, new_password, confirm_password]):
            return Response(
                {'error': 'All fields are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Check if passwords match
        if new_password != confirm_password:
            return Response(
                {'error': 'Passwords do not match'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate password strength
        if len(new_password) < 8:
            return Response(
                {'error': 'Password must be at least 8 characters long'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Verify token
        is_valid, message, user = PasswordResetService.verify_reset_token(email, token)
        
        if not is_valid:
            return Response(
                {'error': message},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Reset password
        with transaction.atomic():
            user.set_password(new_password)
            user.save()
            
            # Clear reset token and update flags
            PasswordResetService.clear_reset_token(user)
            
            # Mark email as verified if not already
            try:
                profile = user.rbac_profile
                if not profile.metadata:
                    profile.metadata = {}
                profile.metadata['email_verified'] = True
                profile.metadata['email_verified_at'] = timezone.now().isoformat()
                profile.save(update_fields=['metadata'])
            except Exception as e:
                logger.warning(f"Could not update email verification: {e}")
        
        logger.info(f"Password successfully reset for user {email}")
        
        return Response({
            'success': True,
            'message': 'Password successfully reset. You can now login with your new password.'
        })
        
    except Exception as e:
        logger.error(f"Error resetting password: {str(e)}")
        return Response(
            {'error': 'Failed to reset password'},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
