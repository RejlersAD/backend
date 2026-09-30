"""Administrator commands for encrypted platform AI provider credentials."""
import re
import uuid
from contextlib import contextmanager

from django.core.cache import cache
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.debug import sensitive_post_parameters, sensitive_variables
from rest_framework import serializers
from rest_framework.exceptions import APIException, NotFound, PermissionDenied, ValidationError
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.rbac.action_policy import module_action_allowed
from apps.rbac.database_maintenance_views import IsActiveUser
from apps.rbac.permissions import IsAdmin
from apps.rbac.utils import create_audit_log
from .ai_credential_models import AIProviderConfiguration, AIProviderCredential
from .ai_credentials import (
    AICredentialUnavailable, PROVIDERS, TestCredential, configuration_metadata, decrypt_api_key,
    encrypt_api_key, encryption_ready,
)
from .ai_credential_probes import FAILURE_REASONS, probe_credential


class CredentialConflict(APIException):
    status_code = 409
    default_detail = {'detail': 'These AI settings changed. Refresh before saving again.', 'code': 'ai_credentials_stale'}


class CredentialUnavailable(APIException):
    status_code = 503
    default_detail = {'detail': 'AI credential management is unavailable. Saved settings were kept.',
                      'code': 'ai_credentials_unavailable'}


class TestInProgress(APIException):
    status_code = 429
    default_detail = {'detail': 'A connection test is already running. Try again shortly.', 'code': 'test_in_progress'}


@contextmanager
def _test_lease(actor):
    key, lease = f'admin-ai-credential-test:{actor.pk}', uuid.uuid4().hex
    try:
        acquired = cache.add(key, lease, timeout=60)
    except Exception:
        raise CredentialUnavailable() from None
    if not acquired:
        raise TestInProgress()
    try:
        yield
    finally:
        try:
            if cache.get(key) == lease:
                cache.delete(key)
        except Exception:
            pass


class StrictInput(serializers.Serializer):
    def to_internal_value(self, data):
        if not isinstance(data, dict) or set(data) - set(self.fields):
            raise ValidationError({'detail': 'Provide only supported AI credential fields.'})
        return super().to_internal_value(data)


class StrictText(serializers.CharField):
    def to_internal_value(self, value):
        if not isinstance(value, str):
            self.fail('invalid')
        return super().to_internal_value(value)


class ModelField(StrictText):
    def __init__(self, **kwargs):
        super().__init__(max_length=200, **kwargs)

    def to_internal_value(self, value):
        value = super().to_internal_value(value)
        if value and not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:-]*', value):
            raise ValidationError('Enter a provider model identifier, without URLs or spaces.')
        return value


class KeyField(StrictText):
    def __init__(self, **kwargs):
        super().__init__(min_length=16, max_length=4096, write_only=True, **kwargs)

    def to_internal_value(self, value):
        value = super().to_internal_value(value)
        if any(ord(char) < 33 or ord(char) > 126 for char in value):
            raise ValidationError('Enter an API key without spaces or control characters.')
        return value


class RevisionField(serializers.IntegerField):
    def __init__(self, **kwargs):
        super().__init__(min_value=0, **kwargs)

    def to_internal_value(self, value):
        if type(value) is not int:
            self.fail('invalid')
        return super().to_internal_value(value)


class CreateInput(StrictInput):
    provider = serializers.ChoiceField(choices=PROVIDERS, error_messages={'invalid_choice': 'Choose a supported provider.'})
    label = StrictText(max_length=120)
    api_key = KeyField()
    enabled = serializers.BooleanField(default=True)
    model = ModelField(required=False, allow_blank=True)
    expected_provider_revision = RevisionField()


class UpdateInput(StrictInput):
    expected_revision = RevisionField()
    expected_provider_revision = RevisionField()
    label = StrictText(max_length=120, required=False)
    api_key = KeyField(required=False)
    enabled = serializers.BooleanField(required=False)


class RevisionInput(StrictInput):
    expected_revision = RevisionField()
    expected_provider_revision = RevisionField()


class ProviderInput(StrictInput):
    expected_revision = RevisionField()
    enabled = serializers.BooleanField(required=False)
    model = ModelField(required=False, allow_blank=True)


class TestInput(StrictInput):
    expected_revision = RevisionField()
    model = ModelField()


def _input(serializer_class, request):
    serializer = serializer_class(data=request.data)
    serializer.is_valid(raise_exception=True)
    return serializer.validated_data


def _public_key(row, configurations):
    selected = configurations.get(row.provider)
    return {
        'id': str(row.pk), 'provider': row.provider, 'label': row.label, 'enabled': row.enabled,
        'is_selected': bool(selected and selected.selected_credential_id == row.pk), 'revision': row.revision,
        'created_at': row.created_at, 'updated_at': row.updated_at, 'last_tested_at': row.last_tested_at,
        'last_test_status': row.last_test_status, 'last_test_reason': row.last_test_reason,
        'last_test_model': row.last_test_model,
    }


def overview():
    configurations = {row.provider: row for row in AIProviderConfiguration.objects.select_related('selected_credential')}
    return {
        'encryption_ready': encryption_ready(),
        'providers': [configuration_metadata(provider, configurations.get(provider)) for provider in PROVIDERS],
        'credentials': [_public_key(row, configurations) for row in AIProviderCredential.objects.all()],
    }


def _provider_lock(provider, expected, *, create=False, actor=None):
    if provider not in PROVIDERS:
        raise NotFound('This AI provider is not supported.')
    row = AIProviderConfiguration.objects.select_for_update().filter(provider=provider).first()
    if (row.revision if row else 0) != expected:
        raise CredentialConflict()
    if row is None:
        if not create:
            raise CredentialConflict()
        row = AIProviderConfiguration.objects.create(provider=provider, updated_by=actor)
    return row


def _key_lock(identifier, revision):
    row = AIProviderCredential.objects.select_for_update().filter(pk=identifier).first()
    if row is None:
        raise NotFound('This AI credential no longer exists.')
    if row.revision != revision:
        raise CredentialConflict()
    return row


def _key_provider(identifier):
    provider = AIProviderCredential.objects.filter(pk=identifier).values_list('provider', flat=True).first()
    if provider is None:
        raise NotFound('This AI credential no longer exists.')
    return provider


def _updated_provider(row, actor, *, new=False):
    if not new:
        row.revision += 1
    row.updated_by = actor
    row.save()


def _audit(request, action, resource, *, operation, key_replaced=False, test=None):
    # Explicit metadata only: never serialize a model, request body or key.
    metadata = {'operation': operation, 'provider': resource.provider, 'revision': resource.revision,
                'key_replaced': key_replaced, 'request_path': request.path, 'audit_source': 'ai_credentials'}
    if test:
        metadata['test'] = {'success': test['success'], 'reason': test['reason']}
    try:
        create_audit_log(request.user, action, type(resource).__name__,
                         resource_id=resource.pk if isinstance(resource, AIProviderCredential) else None,
                         resource_repr=resource.provider, metadata=metadata)
    except Exception:
        raise CredentialUnavailable() from None


class _NoStoreView(APIView):
    def finalize_response(self, request, response, *args, **kwargs):
        response = super().finalize_response(request, response, *args, **kwargs)
        response['Cache-Control'] = 'private, no-store, max-age=0'
        return response

    def handle_exception(self, exc):
        if isinstance(exc, IntegrityError):
            exc = CredentialConflict()
        elif isinstance(exc, (AICredentialUnavailable, DatabaseError)):
            reason = exc.reason if isinstance(exc, AICredentialUnavailable) else 'registry_unavailable'
            exc = CredentialUnavailable({'detail': 'AI credential management is unavailable. Saved settings were kept.',
                                         'code': 'ai_credentials_unavailable', 'reason': reason})
        return super().handle_exception(exc)


@method_decorator(sensitive_post_parameters('api_key'), name='dispatch')
class AdminCredentialView(_NoStoreView):
    permission_classes = [IsAuthenticated, IsActiveUser, IsAdmin]

    def initial(self, request, *args, **kwargs):
        action = 'read' if request.method in ('GET', 'HEAD', 'OPTIONS') else 'delete' if request.method == 'DELETE' else 'update'
        if request.method == 'POST' and isinstance(self, CredentialListView):
            action = 'create'
        self.permission_action = action
        super().initial(request, *args, **kwargs)
        if not module_action_allowed(request.user, 'admin_dashboard', action):
            raise PermissionDenied('You do not have permission to manage AI credentials.')


class CredentialListView(AdminCredentialView):
    def get(self, request):
        return Response(overview())

    @sensitive_variables()
    def post(self, request):
        data = _input(CreateInput, request)
        encrypted = encrypt_api_key(data['api_key'])
        with transaction.atomic():
            configuration = _provider_lock(data['provider'], data['expected_provider_revision'], create=True, actor=request.user)
            row = AIProviderCredential.objects.create(
                provider=data['provider'], label=data['label'], encrypted_key=encrypted, enabled=data['enabled'],
                created_by=request.user, updated_by=request.user,
            )
            new = data['expected_provider_revision'] == 0
            if new:
                configuration.selected_credential = row
                configuration.model = data.get('model', '')
            elif 'model' in data and data['model'] != configuration.model:
                raise ValidationError({'model': 'Edit the provider settings to change its model.'})
            _updated_provider(configuration, request.user, new=new)
            _audit(request, 'create', row, operation='register', key_replaced=True)
        return Response(overview(), status=201)


class CredentialDetailView(AdminCredentialView):
    @sensitive_variables()
    def patch(self, request, pk):
        data = _input(UpdateInput, request)
        encrypted = encrypt_api_key(data['api_key']) if 'api_key' in data else None
        with transaction.atomic():
            configuration = _provider_lock(_key_provider(pk), data['expected_provider_revision'])
            row = _key_lock(pk, data['expected_revision'])
            for name in ('label', 'enabled'):
                if name in data:
                    setattr(row, name, data[name])
            if encrypted is not None:
                row.encrypted_key = encrypted
                row.last_tested_at = None
                row.last_test_status = row.last_test_reason = row.last_test_model = ''
            row.revision += 1
            row.updated_by = request.user
            row.save()
            _updated_provider(configuration, request.user)
            _audit(request, 'update', row, operation='update', key_replaced=encrypted is not None)
        return Response(overview())

    def delete(self, request, pk):
        data = _input(RevisionInput, request)
        with transaction.atomic():
            configuration = _provider_lock(_key_provider(pk), data['expected_provider_revision'])
            row = _key_lock(pk, data['expected_revision'])
            _audit(request, 'delete', row, operation='delete')
            if configuration.selected_credential_id == row.pk:
                configuration.selected_credential = None
            _updated_provider(configuration, request.user)
            row.delete()
        return Response(overview())


class CredentialSelectView(AdminCredentialView):
    permission_action = 'update'

    def post(self, request, pk):
        data = _input(RevisionInput, request)
        with transaction.atomic():
            configuration = _provider_lock(_key_provider(pk), data['expected_provider_revision'])
            row = _key_lock(pk, data['expected_revision'])
            if not row.enabled:
                raise ValidationError({'detail': 'Enable this credential before selecting it.'})
            # Selection does not turn a deliberately disabled provider back on.
            configuration.selected_credential = row
            _updated_provider(configuration, request.user)
            _audit(request, 'update', configuration, operation='select')
        return Response(overview())


class ProviderSettingsView(AdminCredentialView):
    def patch(self, request, provider):
        data = _input(ProviderInput, request)
        with transaction.atomic():
            row = _provider_lock(provider, data['expected_revision'], create=True, actor=request.user)
            for name in ('enabled', 'model'):
                if name in data:
                    setattr(row, name, data[name])
            _updated_provider(row, request.user, new=data['expected_revision'] == 0)
            _audit(request, 'update', row, operation='configure_provider')
        return Response(overview())


class CredentialTestView(AdminCredentialView):
    permission_action = 'update'

    @sensitive_variables()
    def post(self, request, pk):
        data = _input(TestInput, request)
        with _test_lease(request.user):
            return self._run_test(request, pk, data)

    @sensitive_variables()
    def _run_test(self, request, pk, data):
        row = AIProviderCredential.objects.filter(pk=pk).first()
        if row is None:
            raise NotFound('This AI credential no longer exists.')
        if row.revision != data['expected_revision']:
            raise CredentialConflict()
        result = probe_credential(TestCredential(row.provider, data['model'], decrypt_api_key(row.encrypted_key)))
        valid_result = isinstance(result, dict) and type(result.get('success')) is bool
        success = bool(valid_result and result['success'] is True and result.get('reason') == '')
        reason = result.get('reason') if valid_result else None
        test = {'success': success, 'reason': '' if success else reason if isinstance(reason, str) and reason in FAILURE_REASONS else 'provider_unavailable',
                'model': data['model']}
        with transaction.atomic():
            # Never hold locks across the external request or apply its result
            # after a concurrent key replacement/deletion.
            _provider_lock(row.provider, AIProviderConfiguration.objects.get(provider=row.provider).revision)
            row = _key_lock(pk, data['expected_revision'])
            row.last_tested_at = timezone.now()
            row.last_test_status = 'passed' if success else 'failed'
            row.last_test_reason = test['reason']
            row.last_test_model = data['model']
            row.revision += 1
            row.updated_by = request.user
            row.save()
            _audit(request, 'update', row, operation='test', test=test)
        return Response({**overview(), 'test': test})


class ProviderStatusView(_NoStoreView):
    permission_classes = [IsAuthenticated, IsActiveUser]

    def get(self, request):
        configurations = {row.provider: row for row in AIProviderConfiguration.objects.select_related('selected_credential')}
        fields = ('provider', 'managed', 'enabled', 'ready', 'model')
        return Response({'providers': [{name: values[name] for name in fields} for values in (
            configuration_metadata(provider, configurations.get(provider)) for provider in PROVIDERS
        )]})
