"""Public provider choices and encrypted, provider-bound project credentials."""
from copy import deepcopy

from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework import serializers

from ..config import CLAUDE_API_KEY_PATTERN
from . import byok_crypto, project_ai


def settings_payload(project):
    saved = project.ai_settings or {}
    provider = saved.get('provider') or project_ai.DEFAULT_PROVIDER
    choices = project_ai.MODEL_CHOICES_BY_PROVIDER
    if provider not in choices:
        provider = project_ai.DEFAULT_PROVIDER
    return {
        'enabled': bool(saved.get('enabled')),
        'provider': provider,
        'model': saved.get('model') or project_ai.DEFAULT_MODEL_BY_PROVIDER[provider],
        'key_configured': bool(saved.get('api_key_encrypted')),
        'model_choices': deepcopy(choices[provider]),
        'provider_choices': [
            {**row, 'default_model': project_ai.DEFAULT_MODEL_BY_PROVIDER[row['value']],
             'model_choices': deepcopy(choices[row['value']])}
            for row in project_ai.PROVIDER_CHOICES
        ],
        'encryption_configured': byok_crypto.is_encryption_configured(),
    }


class SettingsUpdateSerializer(serializers.Serializer):
    provider = serializers.ChoiceField(choices=project_ai.MODEL_CHOICES_BY_PROVIDER, required=False)
    enabled = serializers.BooleanField(required=False)
    model = serializers.CharField(max_length=160, required=False)
    api_key = serializers.CharField(max_length=4096, required=False, allow_blank=True, write_only=True)


@sensitive_variables()
def updated_settings(current, data):
    """Validate before replacing anything; failed saves preserve the previous key."""
    serializer = SettingsUpdateSerializer(data=data)
    serializer.is_valid(raise_exception=True)
    values = serializer.validated_data
    previous_provider = current.get('provider') or project_ai.DEFAULT_PROVIDER
    provider = values.get('provider', previous_provider)
    if provider not in project_ai.MODEL_CHOICES_BY_PROVIDER:
        raise serializers.ValidationError({'provider': 'Choose a supported AI provider.'})
    switched = provider != previous_provider
    model = values.get('model') or (None if switched else current.get('model')) or project_ai.DEFAULT_MODEL_BY_PROVIDER[provider]
    if model not in {row['value'] for row in project_ai.MODEL_CHOICES_BY_PROVIDER[provider]}:
        raise serializers.ValidationError({'model': 'Choose a model available for the selected provider.'})
    key = values.get('api_key', '')
    if switched and current.get('api_key_encrypted') and not key:
        raise serializers.ValidationError({'api_key': 'Enter a new API key for the selected provider before switching.'})
    if key:
        if provider == 'anthropic' and not CLAUDE_API_KEY_PATTERN.fullmatch(key):
            raise serializers.ValidationError({'api_key': 'Enter an Anthropic API key starting with sk-ant-.'})
        if provider == 'gemini' and (len(key) < 20 or key.startswith('sk-') or any(ord(char) < 33 or ord(char) > 126 for char in key)):
            raise serializers.ValidationError({'api_key': 'Enter a Gemini API key from Google AI Studio.'})
    updated = deepcopy(current)
    updated.update(provider=provider, model=model, enabled=values.get('enabled', bool(current.get('enabled'))))
    if key:
        updated.update(api_key_encrypted=byok_crypto.encrypt_api_key(key), api_key_provider=provider,
                       key_updated_at=timezone.now().isoformat())
    elif updated['enabled'] and not updated.get('api_key_encrypted'):
        raise serializers.ValidationError({'api_key': 'Enter an API key before enabling AI for this project.'})
    return updated
