"""
Wrench Integration Models
Stores encrypted credentials and sync records for the Wrench Project Platform.
"""
import secrets
from django.db import models
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError

User = get_user_model()


class WrenchConfig(models.Model):
    """
    Stores the Wrench SmartProject platform connection configuration.
    Auth flow: POST /api/AccessControl/Login → receive TOKEN → supply TOKEN on every request.
    The session token rolls – each API response returns a refreshed token.
    Credentials are Fernet-encrypted at rest.
    """
    # --- Connection ---
    base_url = models.URLField(
        max_length=500,
        help_text='Wrench WebAPI Server URL, e.g. https://your-org.wrenchproject.com'
    )
    svc_url = models.URLField(
        max_length=500, blank=True, default='',
        help_text=(
            'Wrench DocumentSearch Service URL (<<SVC URL>>). '
            'Leave blank to use the same base URL. '
            'e.g. https://svc.wrenchproject.com'
        )
    )
    # Wrench uses SERVER_ID (integer) to identify the server on login
    server_id = models.IntegerField(
        default=1,
        help_text='Wrench SERVER_ID – passed to /api/AccessControl/Login'
    )
    # --- Auth credentials (both encrypted at rest) ---
    login_name = models.CharField(
        max_length=255, default='',
        help_text='Wrench LOGIN_NAME (username)'
    )
    encrypted_password = models.TextField(
        default='',
        help_text='Fernet-encrypted Wrench password – never stored in plaintext'
    )
    # Legacy field kept for potential API-key auth mode (future)
    encrypted_api_key = models.TextField(blank=True, default='')

    # Rolling session token – updated after every successful API call
    session_token = models.TextField(
        blank=True, default='',
        help_text='Current Wrench session TOKEN (refreshed on each API call)'
    )
    token_obtained_at = models.DateTimeField(null=True, blank=True)

    organization_name = models.CharField(max_length=255, blank=True, default='')
    client_id = models.CharField(max_length=255, blank=True, default='')

    # --- Optional login parameters (from SmartProject API spec) ---
    # IS_PASSWORD_ENCRYPTED: 0 = plain-text password, 1 = pre-encrypted (default 0)
    is_password_encrypted = models.SmallIntegerField(
        default=0,
        choices=[(0, 'Plain-text (0)'), (1, 'Pre-encrypted (1)')],
        help_text='Wrench IS_PASSWORD_ENCRYPTED flag: 0=plain, 1=pre-encrypted'
    )
    # OTP: one-time password (leave blank unless MFA is enforced on the Wrench server)
    otp = models.CharField(
        max_length=64, blank=True, default='',
        help_text='One-time password for MFA (leave blank if not required)'
    )
    # Optional session parameters
    language = models.CharField(
        max_length=20, blank=True, default='',
        help_text='Wrench LANGUAGE code, e.g. en-US (leave blank for server default)'
    )
    time_zone_id = models.CharField(
        max_length=100, blank=True, default='',
        help_text='Wrench TIME_ZONE_ID (leave blank for server default)'
    )
    workstation_name = models.CharField(
        max_length=255, blank=True, default='RADAI',
        help_text='WORKSTATION_NAME sent on login to identify this client'
    )

    # --- Status ---
    is_active = models.BooleanField(default=True)
    connection_verified = models.BooleanField(default=False)
    last_verified_at = models.DateTimeField(null=True, blank=True)

    # Audit
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='wrench_configs_created'
    )
    updated_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='wrench_configs_updated'
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Wrench Configuration'
        verbose_name_plural = 'Wrench Configurations'
        ordering = ['-created_at']

    def __str__(self):
        return f'Wrench Config – {self.organization_name or self.base_url}'


class WrenchSyncLog(models.Model):
    """
    Records every sync attempt between RADAI and Wrench.
    """
    DIRECTION_CHOICES = [
        ('radai_to_wrench', 'RADAI → Wrench'),
        ('wrench_to_radai', 'Wrench → RADAI'),
    ]
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('in_progress', 'In Progress'),
        ('success', 'Success'),
        ('failed', 'Failed'),
        ('partial', 'Partial'),
    ]
    ENTITY_CHOICES = [
        ('project', 'Project'),
        ('document', 'Document'),
        ('transmittal', 'Transmittal'),
        ('user', 'User'),
        ('all', 'All'),
        ('doc_search', 'Document Search'),
    ]

    config = models.ForeignKey(
        WrenchConfig, on_delete=models.SET_NULL, null=True,
        related_name='sync_logs'
    )
    triggered_by = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True,
        related_name='wrench_sync_logs'
    )
    direction = models.CharField(max_length=20, choices=DIRECTION_CHOICES)
    entity_type = models.CharField(max_length=30, choices=ENTITY_CHOICES, default='all')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')

    records_requested = models.IntegerField(default=0)
    records_synced = models.IntegerField(default=0)
    records_failed = models.IntegerField(default=0)

    error_message = models.TextField(blank=True, default='')
    sync_details = models.JSONField(default=dict, blank=True)

    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = 'Wrench Sync Log'
        verbose_name_plural = 'Wrench Sync Logs'
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['status']),
            models.Index(fields=['started_at']),
        ]

    def __str__(self):
        return f'Sync {self.direction} [{self.status}] @ {self.started_at:%Y-%m-%d %H:%M}'

    @property
    def duration_seconds(self):
        if self.completed_at and self.started_at:
            return (self.completed_at - self.started_at).total_seconds()
        return None
