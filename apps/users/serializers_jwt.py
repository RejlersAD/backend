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
                user = authenticate(
                    request=self.context.get('request'),
                    username=email,  # Django's authenticate expects 'username' parameter
                    password=password
                )
            except Exception as e:
                logger.exception(f"Authentication error for {email}: {e}")
                raise serializers.ValidationError(
                    'Authentication failed. Please try again.',
                    code='authorization'
                )
            
            if not user:
                raise serializers.ValidationError(
                    'No active account found with the given credentials',
                    code='authorization'
                )
            
            if not user.is_active:
                raise serializers.ValidationError(
                    'User account is disabled',
                    code='authorization'
                )
        else:
            raise serializers.ValidationError(
                'Must include "email" and "password"',
                code='authorization'
            )
        
        # Generate tokens using the parent class method
        refresh = self.get_token(user)
        
        data = {
            'refresh': str(refresh),
            'access': str(refresh.access_token),
        }
        
        return data
