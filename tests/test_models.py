"""
Backend Models Tests
Smart tests for database models
"""
import pytest
from django.contrib.auth import get_user_model
from apps.users.models import UserProfile

User = get_user_model()


@pytest.mark.django_db
class TestUserModel:
    """Test User model."""
    
    def test_create_user(self):
        """Test creating a user."""
        user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        assert user.email == 'test@example.com'
        assert user.username == 'testuser'
        assert user.check_password('testpass123')
        assert not user.is_verified
    
    def test_create_superuser(self):
        """Test creating a superuser."""
        admin = User.objects.create_superuser(
            username='admin',
            email='admin@example.com',
            password='admin123'
        )
        assert admin.is_superuser
        assert admin.is_staff
    
    def test_user_str(self):
        """Test user string representation."""
        user = User(email='test@example.com')
        assert str(user) == 'test@example.com'


@pytest.mark.django_db
class TestUserProfile:
    """Test UserProfile model."""
    
    def test_create_profile(self):
        """Test creating a user profile."""
        user = User.objects.create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        profile = UserProfile.objects.create(
            user=user,
            city='Test City',
            country='Test Country'
        )
        assert profile.user == user
        assert profile.city == 'Test City'
        assert profile.country == 'Test Country'
    
    def test_profile_str(self):
        """Test profile string representation."""
        user = User(email='test@example.com')
        profile = UserProfile(user=user)
        assert str(profile) == 'Profile of test@example.com'
