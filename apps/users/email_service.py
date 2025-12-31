"""
Email service for sending user management emails
Soft-coded email sending with proper error handling
"""
import logging
from django.core.mail import EmailMultiAlternatives
from django.conf import settings
from django.utils import timezone
from .email_templates import get_email_template

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails"""
    
    @staticmethod
    def send_welcome_email(user, temp_password, request=None):
        """
        Send welcome email to new user with login credentials
        
        Args:
            user: User instance
            temp_password: Temporary password for first login
            request: HTTP request object to get base URL
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            # Get base URL from request or settings
            if request:
                base_url = f"{request.scheme}://{request.get_host()}"
            else:
                base_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')
            
            login_url = f"{base_url}/login"
            
            # Prepare email context
            context = {
                'first_name': user.first_name or 'User',
                'last_name': user.last_name or '',
                'email': user.email,
                'temp_password': temp_password,
                'login_url': login_url
            }
            
            # Get email template
            email_content = get_email_template('welcome', context)
            
            # Create email
            email = EmailMultiAlternatives(
                subject=email_content['subject'],
                body=email_content['text_body'],
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email]
            )
            
            # Attach HTML version
            email.attach_alternative(email_content['html_body'], "text/html")
            
            # Send email
            email.send(fail_silently=False)
            
            logger.info(f"Welcome email sent successfully to {user.email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send welcome email to {user.email}: {str(e)}")
            return False
    
    @staticmethod
    def send_password_reset_required_email(user, reset_url):
        """
        Send email notifying user that password reset is required
        
        Args:
            user: User instance
            reset_url: URL for password reset
            
        Returns:
            bool: True if email sent successfully, False otherwise
        """
        try:
            context = {
                'first_name': user.first_name or 'User',
                'reset_url': reset_url
            }
            
            email_content = get_email_template('password_reset_required', context)
            
            email = EmailMultiAlternatives(
                subject=email_content['subject'],
                body=email_content['text_body'],
                from_email=settings.DEFAULT_FROM_EMAIL,
                to=[user.email]
            )
            
            email.attach_alternative(email_content['html_body'], "text/html")
            email.send(fail_silently=False)
            
            logger.info(f"Password reset required email sent to {user.email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send password reset email to {user.email}: {str(e)}")
            return False
    
    @staticmethod
    def validate_email_deliverability(email):
        """
        Validate email format and basic deliverability
        
        Args:
            email: Email address to validate
            
        Returns:
            dict: Validation result with is_valid and message
        """
        import re
        
        # Basic email regex pattern
        email_pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        
        if not email:
            return {
                'is_valid': False,
                'message': 'Email address is required'
            }
        
        if not re.match(email_pattern, email):
            return {
                'is_valid': False,
                'message': 'Invalid email format'
            }
        
        # Check for common disposable email domains
        disposable_domains = [
            'tempmail.com', 'throwaway.email', 'guerrillamail.com',
            '10minutemail.com', 'mailinator.com', 'trashmail.com'
        ]
        
        domain = email.split('@')[1].lower()
        if domain in disposable_domains:
            return {
                'is_valid': False,
                'message': 'Disposable email addresses are not allowed'
            }
        
        return {
            'is_valid': True,
            'message': 'Email is valid'
        }
