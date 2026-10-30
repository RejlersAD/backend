"""AI Model hub — provider/credential/catalogue/routing/sync/audit tables.

Secrets live ONLY in AICredential.encrypted_secret (Fernet via
apps.core.ai_credentials); projections expose prefix…last4 only.
"""
import uuid

from django.conf import settings
from django.db import models


PROVIDER_GROUPS = [('paid', 'Paid'), ('cloud', 'Cloud'), ('open', 'Open-weight'),
                   ('gateway', 'Gateway'), ('self', 'Self-hosted')]
AUTH_KINDS = [('key', 'API key'), ('azure', 'Azure (endpoint + key)'),
              ('bedrock', 'AWS Bedrock'), ('vertex', 'Google Vertex'),
              ('base', 'Base URL only'), ('local', 'Local / no auth')]
MODEL_STATUSES = [('ga', 'GA'), ('new', 'New'), ('preview', 'Preview'),
                  ('deprecated', 'Deprecated'), ('shut_down', 'Shut down')]
CREDENTIAL_SCOPES = [('org', 'Organisation'), ('module', 'Module'), ('personal', 'Personal')]


class AIProvider(models.Model):
    id = models.SlugField(max_length=40, primary_key=True)
    name = models.CharField(max_length=120)
    group = models.CharField(max_length=10, choices=PROVIDER_GROUPS)
    protocol = models.CharField(max_length=30)  # openai|openai_compatible|anthropic|gemini|azure|bedrock|vertex|ollama|tgi
    auth_kind = models.CharField(max_length=10, choices=AUTH_KINDS, default='key')
    default_base_url = models.CharField(max_length=300, blank=True)
    list_endpoint = models.CharField(max_length=120, blank=True)
    enabled = models.BooleanField(default=True)
    sort = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['sort', 'id']

    def __str__(self):
        return self.name


class AICredential(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(AIProvider, on_delete=models.CASCADE, related_name='credentials')
    name = models.CharField(max_length=120)
    scope = models.CharField(max_length=10, choices=CREDENTIAL_SCOPES, default='org')
    module_ids = models.JSONField(default=list, blank=True)
    encrypted_secret = models.TextField(editable=False)
    secret_prefix = models.CharField(max_length=12, blank=True, editable=False)
    secret_last4 = models.CharField(max_length=8, blank=True, editable=False)
    base_url = models.CharField(max_length=300, blank=True)
    models_allowed = models.JSONField(default=list, blank=True)
    monthly_cap_usd = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True)
    rotate_days = models.PositiveIntegerField(default=90)
    enabled = models.BooleanField(default=True)
    last_test_at = models.DateTimeField(null=True, blank=True, editable=False)
    last_test_model = models.CharField(max_length=200, blank=True, editable=False)
    last_test_ok = models.BooleanField(null=True, blank=True, editable=False)
    created_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                                   related_name='+', editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True, editable=False)

    class Meta:
        ordering = ['provider_id', 'created_at', 'id']

    def __str__(self):
        return f'{self.provider_id}: {self.name}'

    @property
    def key_display(self):
        if not (self.secret_prefix or self.secret_last4):
            return ''
        return f'{self.secret_prefix}…{self.secret_last4}'


class AIModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    provider = models.ForeignKey(AIProvider, on_delete=models.CASCADE, related_name='models')
    model_id = models.CharField(max_length=300)
    maker = models.CharField(max_length=120, blank=True)
    family = models.CharField(max_length=120, blank=True)
    variant = models.CharField(max_length=120, blank=True)
    capabilities = models.JSONField(default=list, blank=True)
    weights = models.CharField(max_length=10, choices=[('open', 'Open'), ('closed', 'Closed')],
                               default='closed')
    licence = models.CharField(max_length=120, blank=True)
    context_tokens = models.PositiveIntegerField(null=True, blank=True)
    price_in_per_m = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    price_out_per_m = models.DecimalField(max_digits=12, decimal_places=6, null=True, blank=True)
    status = models.CharField(max_length=12, choices=MODEL_STATUSES, default='new')
    status_note = models.CharField(max_length=300, blank=True)
    enabled = models.BooleanField(default=False)
    source = models.CharField(max_length=30, default='provider')  # provider|openrouter_meta
    first_seen = models.DateTimeField(auto_now_add=True)
    last_seen = models.DateTimeField(auto_now=True)
    consecutive_misses = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['provider_id', 'model_id']
        constraints = [
            models.UniqueConstraint(fields=['provider', 'model_id'], name='uniq_ai_model_per_provider'),
        ]

    def __str__(self):
        return f'{self.provider_id}:{self.model_id}'


class AIRoute(models.Model):
    task_id = models.SlugField(max_length=80, primary_key=True)
    label = models.CharField(max_length=160)
    module = models.CharField(max_length=80)
    path = models.CharField(max_length=200, blank=True)
    needs = models.JSONField(default=list)
    data_policy = models.CharField(max_length=10, choices=[('cloud', 'Cloud'), ('onprem', 'On-prem')],
                                   default='cloud')
    chain = models.JSONField(default=list, blank=True)  # ["provider:model"|null, ...3]

    class Meta:
        ordering = ['task_id']

    def __str__(self):
        return self.task_id


class AISyncRun(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    started_at = models.DateTimeField(auto_now_add=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=12, default='running')  # running|ok|partial|failed
    per_source = models.JSONField(default=list, blank=True)   # [{provider, result, count, error?}]
    changes = models.JSONField(default=list, blank=True)      # [{kind, model_id, provider, effect_on_routing[]}]

    class Meta:
        ordering = ['-started_at']

    def __str__(self):
        return f'sync {self.id} ({self.status})'


class AIHubSettings(models.Model):
    """Singleton row for the sync schedule card."""
    id = models.PositiveSmallIntegerField(primary_key=True, default=1, editable=False)
    frequency = models.CharField(max_length=10, default='daily')  # 6h|daily|weekly|manual
    time = models.CharField(max_length=5, default='02:00')
    enrich_openrouter = models.BooleanField(default=False)
    new_models_disabled = models.BooleanField(default=True)
    email_on_deprecation = models.BooleanField(default=False)

    class Meta:
        verbose_name = 'AI hub settings'

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class AIAudit(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    who = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL,
                            related_name='+')
    when = models.DateTimeField(auto_now_add=True)
    action = models.CharField(max_length=40)  # create|test|rotate|revoke|routing_change|model_enable|model_disable|sync
    provider = models.CharField(max_length=40, blank=True)
    credential_id = models.CharField(max_length=40, blank=True)
    detail = models.JSONField(default=dict, blank=True)  # redacted metadata only

    class Meta:
        ordering = ['-when']
