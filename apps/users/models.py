"""
User models for authentication and profile management.
Smart user management with custom fields.
"""
from django.contrib.auth.models import AbstractUser
from django.db import models


class User(AbstractUser):
    """
    Custom user model extending Django's AbstractUser.
    """
    email = models.EmailField(unique=True)
    phone_number = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    bio = models.TextField(blank=True, null=True)
    is_verified = models.BooleanField(default=False)
    
    # First-time login and password reset tracking
    is_first_login = models.BooleanField(default=True, help_text='True if user has not logged in yet')
    must_reset_password = models.BooleanField(default=False, help_text='True if user must reset password')
    temp_password_created_at = models.DateTimeField(null=True, blank=True, help_text='When temporary password was set')
    last_password_change = models.DateTimeField(null=True, blank=True, help_text='Last time password was changed')
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        db_table = 'users'
        verbose_name = 'User'
        verbose_name_plural = 'Users'

    def __str__(self):
        return self.email
