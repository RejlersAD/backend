"""
Backend API Tests
Smart tests for API endpoints
"""
import pytest
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient
from django.contrib.auth import get_user_model

User = get_user_model()


@pytest.fixture
def api_client():
    """Return API client for testing."""
    return APIClient()


@pytest.fixture
def create_user():
    """Factory fixture for creating users."""
    def make_user(**kwargs):
        return User.objects.create_user(**kwargs)
    return make_user


@pytest.mark.django_db
class TestHealthCheck:
    """Test health check endpoint."""
    
    def test_health_check(self, api_client):
        """Test health check returns 200."""
        url = reverse('health-check')
        response = api_client.get(url)
        assert response.status_code == status.HTTP_200_OK
        assert response.data['status'] == 'healthy'


@pytest.mark.django_db
class TestUserRegistration:
    """Test user registration endpoint."""
    
    def test_register_user_success(self, api_client):
        """Test successful user registration."""
        url = reverse('user-list')
        data = {
            'username': 'testuser',
            'email': 'test@example.com',
            'password': 'testpass123',
            'password_confirm': 'testpass123',
            'first_name': 'Test',
            'last_name': 'User'
        }
        response = api_client.post(url, data, format='json')
        assert response.status_code == status.HTTP_201_CREATED
        assert User.objects.filter(email='test@example.com').exists()
    
    def test_register_user_password_mismatch(self, api_client):
        """Test registration fails with password mismatch."""
        url = reverse('user-list')
        data = {
            'username': 'testuser',
            'email': 'test@example.com',
            'password': 'testpass123',
            'password_confirm': 'differentpass',
        }
        response = api_client.post(url, data, format='json')
        assert response.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.django_db
class TestUserAuthentication:
    """Test user authentication."""
    
    def test_login_success(self, api_client, create_user):
        """Test successful login."""
        user = create_user(
            username='testuser',
            email='test@example.com',
            password='testpass123'
        )
        user.set_password('testpass123')
        user.save()
        
        url = reverse('token_obtain_pair')
        data = {
            'email': 'test@example.com',
            'password': 'testpass123'
        }
        response = api_client.post(url, data, format='json')
        assert response.status_code == status.HTTP_200_OK
        assert 'access' in response.data
        assert 'refresh' in response.data
    
    def test_login_invalid_credentials(self, api_client):
        """Test login fails with invalid credentials."""
        url = reverse('token_obtain_pair')
        data = {
            'email': 'nonexistent@example.com',
            'password': 'wrongpass'
        }
        response = api_client.post(url, data, format='json')
        assert response.status_code == status.HTTP_401_UNAUTHORIZED
