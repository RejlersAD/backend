"""
Serializers for user models.
Smart data validation and transformation.
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from .models import UserProfile

User = get_user_model()


class UserProfileSerializer(serializers.ModelSerializer):
    """Serializer for user profile."""
    
    class Meta:
        model = UserProfile
        fields = ['date_of_birth', 'address', 'city', 'country', 'postal_code']


class UserSerializer(serializers.ModelSerializer):
    """Serializer for user model."""
    profile = UserProfileSerializer(read_only=True)
    roles = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 
                  'phone_number', 'avatar', 'bio', 'is_verified', 'profile', 'roles']
        read_only_fields = ['id', 'is_verified']
    
    def get_roles(self, obj):
        """Get user's RBAC roles."""
        try:
            from apps.rbac.models import UserProfile as RBACUserProfile
            rbac_profile = RBACUserProfile.objects.filter(user=obj, is_deleted=False).first()
            if rbac_profile:
                roles = rbac_profile.roles.all()
                return [{'id': str(role.id), 'code': role.code, 'name': role.name, 'level': role.level} for role in roles]
        except Exception:
            pass
        return []


class UserRegistrationSerializer(serializers.ModelSerializer):
    """Serializer for user registration."""
    password = serializers.CharField(write_only=True, min_length=8)
    password_confirm = serializers.CharField(write_only=True, min_length=8)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'password_confirm', 
                  'first_name', 'last_name']
    
    def validate(self, attrs):
        """Validate password confirmation."""
        if attrs['password'] != attrs['password_confirm']:
            raise serializers.ValidationError({"password": "Passwords do not match."})
        return attrs
    
    def create(self, validated_data):
        """Create new user with hashed password."""
        validated_data.pop('password_confirm')
        password = validated_data.pop('password')
        user = User.objects.create_user(**validated_data)
        user.set_password(password)
        user.save()
        
        # Create associated profile
        UserProfile.objects.create(user=user)
        
        return user
