"""
Email Verification System
Handles email verification tokens and sending verification emails
"""
import secrets
from datetime import timedelta
from django.utils import timezone
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.conf import settings
from apps.rbac.models import UserProfile


def generate_verification_token():
    """Generate a secure random token"""
    return secrets.token_urlsafe(32)


def send_verification_email(user_profile, request=None):
    """
    Send email verification to user
    
    Args:
        user_profile: UserProfile instance
        request: HTTP request object (optional, for building absolute URL)
    
    Returns:
        bool: True if email sent successfully, False otherwise
    """
    try:
        # Generate verification token
        token = generate_verification_token()
        expiry = timezone.now() + timedelta(seconds=settings.EMAIL_VERIFICATION_TOKEN_EXPIRY)
        
        # Store token in user profile metadata
        if not user_profile.metadata:
            user_profile.metadata = {}
        
        user_profile.metadata['email_verification_token'] = token
        user_profile.metadata['email_verification_expiry'] = expiry.isoformat()
        user_profile.save(update_fields=['metadata'])
        
        # Build verification URL
        if request:
            base_url = request.build_absolute_uri('/')[:-1]
        else:
            base_url = settings.FRONTEND_URL
        
        verification_url = f"{base_url}/verify-email?token={token}&email={user_profile.user.email}"
        
        # Email context
        context = {
            'user': user_profile.user,
            'user_profile': user_profile,
            'verification_url': verification_url,
            'expiry_hours': settings.EMAIL_VERIFICATION_TOKEN_EXPIRY // 3600,
            'company_name': 'Rejlers AB',
            'support_email': settings.SERVER_EMAIL,
        }
        
        # Render email templates
        subject = f"{settings.EMAIL_SUBJECT_PREFIX}Verify Your Email Address"
        html_message = render_to_string('email/verification_email.html', context)
        plain_message = render_to_string('email/verification_email.txt', context)
        
        # Send email
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user_profile.user.email],
            html_message=html_message,
            fail_silently=False,
        )
        
        print(f"✅ Verification email sent to {user_profile.user.email}")
        return True
        
    except Exception as e:
        print(f"❌ Failed to send verification email: {e}")
        return False


def verify_email_token(email, token):
    """
    Verify email verification token
    
    Args:
        email: User email address
        token: Verification token
    
    Returns:
        tuple: (success: bool, message: str, user_profile: UserProfile or None)
    """
    try:
        from django.contrib.auth import get_user_model
        User = get_user_model()
        
        # Find user
        try:
            user = User.objects.get(email=email)
            user_profile = user.rbac_profile
        except (User.DoesNotExist, UserProfile.DoesNotExist):
            return False, "User not found", None
        
        # Check if already verified
        if user_profile.metadata and user_profile.metadata.get('email_verified'):
            return False, "Email already verified", user_profile
        
        # Check token
        stored_token = user_profile.metadata.get('email_verification_token') if user_profile.metadata else None
        if not stored_token or stored_token != token:
            return False, "Invalid verification token", None
        
        # Check expiry
        expiry_str = user_profile.metadata.get('email_verification_expiry') if user_profile.metadata else None
        if not expiry_str:
            return False, "Verification token expired", None
        
        from datetime import datetime
        expiry = datetime.fromisoformat(expiry_str)
        
        if timezone.now() > expiry:
            return False, "Verification token expired", None
        
        # Mark as verified
        if not user_profile.metadata:
            user_profile.metadata = {}
        
        user_profile.metadata['email_verified'] = True
        user_profile.metadata['email_verified_at'] = timezone.now().isoformat()
        user_profile.metadata.pop('email_verification_token', None)
        user_profile.metadata.pop('email_verification_expiry', None)
        
        # Activate user if status is pending
        if user_profile.status == 'pending':
            user_profile.status = 'active'
        
        user_profile.save()
        
        print(f"✅ Email verified for {email}")
        return True, "Email verified successfully", user_profile
        
    except Exception as e:
        print(f"❌ Email verification error: {e}")
        return False, f"Verification error: {str(e)}", None


def send_welcome_email(user_profile):
    """Send welcome email after successful verification"""
    try:
        context = {
            'user': user_profile.user,
            'user_profile': user_profile,
            'dashboard_url': f"{settings.FRONTEND_URL}/dashboard",
            'company_name': 'Rejlers AB',
            'support_email': settings.SERVER_EMAIL,
        }
        
        subject = f"{settings.EMAIL_SUBJECT_PREFIX}Welcome to RADAI!"
        html_message = render_to_string('email/welcome_email.html', context)
        plain_message = render_to_string('email/welcome_email.txt', context)
        
        send_mail(
            subject=subject,
            message=plain_message,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=[user_profile.user.email],
            html_message=html_message,
            fail_silently=True,
        )
        
        print(f"✅ Welcome email sent to {user_profile.user.email}")
        return True
        
    except Exception as e:
        print(f"⚠️ Failed to send welcome email: {e}")
        return False
