"""
Custom JWT serializers for email-based authentication.
"""
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer
from rest_framework import serializers
from django.contrib.auth import authenticate
import logging

logger = logging.getLogger(__name__)


class EmailTokenObtainPairSerializer(TokenObtainPairSerializer):
    """
    Custom JWT serializer that accepts email instead of username.
    """
    username_field = 'email'
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Replace username field with email field
        self.fields['email'] = serializers.EmailField(required=True)
        self.fields.pop('username', None)
    
    def validate(self, attrs):
        # Get email and password from request
        email = attrs.get('email')
        password = attrs.get('password')
        
        if email and password:
            # Authenticate using email
            try:
                logger.info(f"[JWT] Authenticating user: {email}")
                user = authenticate(
                    request=self.context.get('request'),
                    username=email,  # Django's authenticate expects 'username' parameter
                    password=password
                )
                
                if not user:
                    logger.warning(f"[JWT] Authentication failed - invalid credentials for: {email}")
                    raise serializers.ValidationError(
                        'No active account found with the given credentials',
                        code='authorization'
                    )
                
                if not user.is_active:
                    logger.warning(f"[JWT] Authentication failed - account pending approval for: {email}")
                    raise serializers.ValidationError(
                        'Your account is pending administrator approval. '
                        'You will be notified once your account is activated.',
                        code='authorization'
                    )
                
                logger.info(f"[JWT] Authentication successful for: {email}")
                
            except serializers.ValidationError:
                # Re-raise validation errors
                raise
            except Exception as e:
                logger.exception(f"[JWT] Unexpected authentication error for {email}: {e}")
                raise serializers.ValidationError(
                    'Authentication failed due to a system error. Please try again.',
                    code='authorization'
                )
        else:
            logger.warning(f"[JWT] Missing email or password in request")
            raise serializers.ValidationError(
                'Must include "email" and "password"',
                code='authorization'
            )
        
        # Generate tokens using the parent class method
        try:
            logger.info(f"[JWT] Generating tokens for: {email}")
            refresh = self.get_token(user)
            
            data = {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
            
            logger.info(f"[JWT] Token generation successful for: {email}")
            return data
            
        except Exception as e:
            logger.exception(f"[JWT] Token generation failed for {email}: {e}")
            raise serializers.ValidationError(
                'Failed to generate authentication tokens. Please try again.',
                code='authorization'
            )
