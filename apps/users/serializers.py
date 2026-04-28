"""
Serializers for user models.
Smart data validation and transformation.
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.utils import timezone
from .models import UserProfile

User = get_user_model()

# ---------------------------------------------------------------------------
# SOFT-CODED: Controls whether self-registered accounts are immediately active.
# False (default) = account is disabled until a super-administrator activates
#   it via the Django admin or User Management panel.
# True            = accounts become active immediately on registration (legacy
#   behaviour — only use in trusted internal-only deployments).
# ---------------------------------------------------------------------------
SELF_REGISTRATION_ACTIVE = False


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
                  'phone_number', 'avatar', 'bio', 'is_verified', 'is_staff', 
                  'is_superuser', 'profile', 'roles']
        read_only_fields = ['id', 'is_verified', 'is_staff', 'is_superuser']
    
    def get_roles(self, obj):
        """Get user's RBAC roles."""
        try:
            from apps.rbac.models import UserProfile as RBACUserProfile
            rbac_profile = RBACUserProfile.objects.filter(user=obj, is_deleted=False).first()
            if rbac_profile:
                roles = rbac_profile.roles.all()
                return [{'id': str(role.id), 'code': role.code, 'name': role.name, 'level': role.level} for role in roles]
        except Exception as e:
            # Log error for debugging
            import traceback
            print(f"[ERROR] UserSerializer.get_roles failed: {str(e)}")
            print(traceback.format_exc())
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
        # SOFT-CODED: new accounts are inactive until approved by super admin.
        # Controlled by the SELF_REGISTRATION_ACTIVE module-level constant.
        user = User.objects.create_user(**validated_data)
        user.is_active = SELF_REGISTRATION_ACTIVE
        user.set_password(password)
        user.last_password_change = timezone.now()
        user.must_reset_password = False
        user.save()
        
        # Create associated profile
        UserProfile.objects.create(user=user)
        
        return user
