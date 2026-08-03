"""
Custom authentication backend for case-insensitive email login
"""
from django.contrib.auth import get_user_model
from django.contrib.auth.backends import ModelBackend
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


class CaseInsensitiveEmailBackend(ModelBackend):
    """
    Custom authentication backend that performs case-insensitive email lookup.
    
    This allows users to login with any case variation of their email:
    - User@Example.com
    - user@example.com
    - USER@EXAMPLE.COM
    
    All will match the email stored in the database.
    """
    
    def authenticate(self, request, username=None, password=None, **kwargs):
        """
        Authenticate user with case-insensitive email lookup.
        
        Args:
            request: The HTTP request object
            username: The email address (used as username)
            password: The user's password
            **kwargs: Additional authentication parameters
            
        Returns:
            User object if authentication successful, None otherwise
        """
        if username is None or password is None:
            return None
        
        try:
            # Perform case-insensitive email lookup
            logger.debug(f"[Auth] Attempting case-insensitive auth for: {username}")
            user = User.objects.get(email__iexact=username)
            
            # Verify password
            if user.check_password(password):
                logger.info(f"[Auth] ✅ Authentication successful for: {user.email}")
                return user
            else:
                logger.warning(f"[Auth] ❌ Invalid password for: {user.email}")
                return None
                
        except User.DoesNotExist:
            logger.warning(f"[Auth] ❌ User not found: {username}")
            return None
        except User.MultipleObjectsReturned:
            logger.error(f"[Auth] ❌ Multiple users found for: {username}")
            # This shouldn't happen if email is unique, but handle it gracefully
            return None
        except Exception as e:
            logger.exception(f"[Auth] ❌ Authentication error for {username}: {e}")
            return None
    
    def get_user(self, user_id):
        """
        Get user by ID.
        
        Args:
            user_id: The user's primary key
            
        Returns:
            User object if found, None otherwise
        """
        try:
            return User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return None
