"""
Password Reset Service with Token Generation
Handles secure token-based password reset flow with email verification
"""
import secrets
import hashlib
from datetime import timedelta
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from django.contrib.auth import get_user_model
import logging

User = get_user_model()
logger = logging.getLogger(__name__)


class PasswordResetService:
    """Service for handling password reset operations"""
    
    # Token configuration - soft-coded for flexibility
    TOKEN_LENGTH = 64  # Length of the token in characters
    TOKEN_EXPIRY_HOURS = 24  # Token validity period
    
    @staticmethod
    def generate_reset_token():
        """
        Generate a secure random token for password reset
        
        Returns:
            str: Secure random token
        """
        return secrets.token_urlsafe(PasswordResetService.TOKEN_LENGTH)
    
    @staticmethod
    def hash_token(token):
        """
        Hash token for secure storage
        
        Args:
            token: Plain text token
            
        Returns:
            str: Hashed token
        """
        return hashlib.sha256(token.encode()).hexdigest()
    
    @staticmethod
    def create_reset_token(user):
        """
        Create and store a password reset token for user
        
        Args:
            user: User instance
            
        Returns:
            tuple: (plain_token, expiry_datetime)
        """
        try:
            # Generate token
            plain_token = PasswordResetService.generate_reset_token()
            hashed_token = PasswordResetService.hash_token(plain_token)
            
            # Set expiry
            expiry = timezone.now() + timedelta(hours=PasswordResetService.TOKEN_EXPIRY_HOURS)
            
            # Get or create user profile metadata
            try:
                profile = user.rbac_profile
                if not profile.metadata:
                    profile.metadata = {}
                
                # Store hashed token
                profile.metadata['password_reset_token'] = hashed_token
                profile.metadata['password_reset_expiry'] = expiry.isoformat()
                profile.metadata['password_reset_created_at'] = timezone.now().isoformat()
                profile.save(update_fields=['metadata'])
                
                logger.info(f"Password reset token created for user {user.email}")
                
            except Exception as e:
                logger.error(f"Error storing token in profile: {e}")
                # Fallback: store in user model if profile doesn't exist
                user.temp_password_created_at = timezone.now()
                user.must_reset_password = True
                user.save(update_fields=['temp_password_created_at', 'must_reset_password'])
            
            return plain_token, expiry
            
        except Exception as e:
            logger.error(f"Error creating reset token: {e}")
            raise
    
    @staticmethod
    def verify_reset_token(email, token):
        """
        Verify password reset token
        
        Args:
            email: User email
            token: Plain text token to verify
            
        Returns:
            tuple: (is_valid: bool, message: str, user: User or None)
        """
        try:
            # Find user
            try:
                user = User.objects.get(email=email)
            except User.DoesNotExist:
                return False, "User not found", None
            
            # Get stored token from profile
            try:
                profile = user.rbac_profile
                if not profile.metadata:
                    return False, "No reset token found", None
                
                stored_token_hash = profile.metadata.get('password_reset_token')
                expiry_str = profile.metadata.get('password_reset_expiry')
                
                if not stored_token_hash or not expiry_str:
                    return False, "No valid reset token found", None
                
                # Verify token
                token_hash = PasswordResetService.hash_token(token)
                if token_hash != stored_token_hash:
                    return False, "Invalid reset token", None
                
                # Check expiry
                from datetime import datetime
                expiry = datetime.fromisoformat(expiry_str)
                
                if timezone.now() > expiry:
                    # Clean up expired token
                    profile.metadata.pop('password_reset_token', None)
                    profile.metadata.pop('password_reset_expiry', None)
                    profile.metadata.pop('password_reset_created_at', None)
                    profile.save(update_fields=['metadata'])
                    return False, "Reset token has expired", None
                
                logger.info(f"Password reset token verified for user {email}")
                return True, "Token verified successfully", user
                
            except Exception as e:
                logger.error(f"Error verifying token: {e}")
                return False, f"Verification error: {str(e)}", None
                
        except Exception as e:
            logger.error(f"Error in token verification: {e}")
            return False, f"Verification failed: {str(e)}", None
    
    @staticmethod
    def clear_reset_token(user):
        """
        Clear password reset token after successful reset
        
        Args:
            user: User instance
        """
        try:
            profile = user.rbac_profile
            if profile.metadata:
                profile.metadata.pop('password_reset_token', None)
                profile.metadata.pop('password_reset_expiry', None)
                profile.metadata.pop('password_reset_created_at', None)
                profile.save(update_fields=['metadata'])
            
            # Also clear user model flags
            user.must_reset_password = False
            user.is_first_login = False
            user.last_password_change = timezone.now()
            user.save(update_fields=['must_reset_password', 'is_first_login', 'last_password_change'])
            
            logger.info(f"Password reset token cleared for user {user.email}")
            
        except Exception as e:
            logger.error(f"Error clearing reset token: {e}")
    
    @staticmethod
    def send_password_reset_email(user, token, request=None):
        """
        Send password reset email with verification link
        
        Args:
            user: User instance
            token: Plain text token
            request: HTTP request object (optional, for building absolute URL)
            
        Returns:
            bool: True if email sent successfully
        """
        try:
            # Build reset URL
            if request:
                base_url = request.build_absolute_uri('/')[:-1]
            else:
                base_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            
            # Remove trailing slash if present
            base_url = base_url.rstrip('/')
            
            reset_url = f"{base_url}/reset-password?token={token}&email={user.email}"
            
            # Get email template
            from apps.users.email_templates import EMAIL_TEMPLATES
            template = EMAIL_TEMPLATES['password_reset']
            
            # Prepare email context
            context = {
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
                'reset_url': reset_url,
                'expiry_hours': PasswordResetService.TOKEN_EXPIRY_HOURS,
            }
            
            # Format templates
            subject = template['subject'].format(**context)
            html_message = template['html_template'].format(**context)
            text_message = template['text_template'].format(**context)
            
            # Send email
            send_mail(
                subject=subject,
                message=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
            
            logger.info(f"Password reset email sent to {user.email}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending password reset email: {e}")
            return False
    
    @staticmethod
    def send_welcome_email_with_reset(user, token, request=None):
        """
        Send welcome email for new users with password setup link
        
        Args:
            user: User instance
            token: Plain text reset token
            request: HTTP request object (optional)
            
        Returns:
            bool: True if email sent successfully
        """
        try:
            # Build setup URL
            if request:
                base_url = request.build_absolute_uri('/')[:-1]
            else:
                base_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            
            base_url = base_url.rstrip('/')
            setup_url = f"{base_url}/setup-password?token={token}&email={user.email}"
            login_url = f"{base_url}/login"
            
            # Get email template
            from apps.users.email_templates import EMAIL_TEMPLATES
            template = EMAIL_TEMPLATES['welcome_with_setup']
            
            # Prepare context
            context = {
                'first_name': user.first_name,
                'last_name': user.last_name,
                'email': user.email,
                'setup_url': setup_url,
                'login_url': login_url,
                'expiry_hours': PasswordResetService.TOKEN_EXPIRY_HOURS,
            }
            
            # Format templates
            subject = template['subject'].format(**context)
            html_message = template['html_template'].format(**context)
            text_message = template['text_template'].format(**context)
            
            # Send email
            send_mail(
                subject=subject,
                message=text_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[user.email],
                html_message=html_message,
                fail_silently=False,
            )
            
            logger.info(f"Welcome email with password setup sent to {user.email}")
            return True
            
        except Exception as e:
            logger.error(f"Error sending welcome email with setup: {e}")
            return False
