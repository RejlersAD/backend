"""Personal, encrypted AI credentials used before a project exists."""
import json
import logging
import re
import time

from django.conf import settings
from django.utils import timezone
from django.views.decorators.debug import sensitive_variables
from rest_framework.exceptions import APIException, ValidationError

from ..models import ProjectSetupAISettings
from .byok_crypto import decrypt_api_key, encrypt_api_key, is_encryption_configured

logger = logging.getLogger(__name__)
OFFICIAL_OPENAI_URL = 'https://api.openai.com/v1'


class SetupAIUnavailable(APIException):
    status_code = 503
    default_detail = 'AI could not prepare a valid plan. Retry, or choose the project template.'
    default_code = 'project_setup_ai_unavailable'


def default_model():
    return getattr(settings, 'PROJECT_SETUP_AI_MODEL', None) or getattr(settings, 'OPENAI_MODEL', None) or 'gpt-4o'


def _personal_settings(actor):
    return ProjectSetupAISettings.objects.filter(user_id=actor.pk).first() if actor is not None else None


@sensitive_variables()
def _server_configuration_message():
    key = (getattr(settings, 'OPENAI_API_KEY', '') or '').strip()
    if not key:
        return 'AI is not configured on this RADAI server. Add your own API key here, or use an editable project template.'
    if re.search(r'(^|[-_\s])(?:placeholder|dummy|example)(?=$|[-_\s])|^(?:sk-)?(?:your[-_]|replace[-_])', key, re.IGNORECASE):
        return 'AI setup is incomplete: this RADAI server still uses a placeholder AI key. Add your own API key here, or use an editable project template.'
    return ''


@sensitive_variables()
def _configuration_message(personal):
    if personal is not None:
        if not is_encryption_configured():
            return 'Your saved API key is unavailable because secure storage is not configured. Ask an administrator to configure BYOK encryption.'
        if not decrypt_api_key(personal.api_key_encrypted):
            return 'Your saved API key could not be opened. Enter your key again and choose Test & save.'
        return ''
    return _server_configuration_message()


def ai_configuration_message(actor=None):
    return _configuration_message(_personal_settings(actor))


def ai_available(actor=None):
    # Configuration readiness; the provider may later revoke a tested credential.
    return not ai_configuration_message(actor)


def ai_settings_payload(actor):
    personal = _personal_settings(actor)
    message = _configuration_message(personal)
    return {
        'ai_available': not message,
        'ai_message': message,
        'ai_settings': {
            'provider': 'openai',
            'model': personal.model if personal else default_model(),
            'key_configured': personal is not None,
            'last_tested_at': personal.last_tested_at.isoformat() if personal else None,
            'storage_available': is_encryption_configured(),
        },
    }


@sensitive_variables()
def generation_credentials(actor):
    personal = _personal_settings(actor)
    message = _configuration_message(personal)
    if message:
        raise SetupAIUnavailable(message)
    if personal is not None:
        return decrypt_api_key(personal.api_key_encrypted), personal.model, True
    return settings.OPENAI_API_KEY.strip(), default_model(), False


@sensitive_variables()
def openai_client(api_key, *, personal=False, timeout=50):
    from openai import OpenAI
    options = {'api_key': api_key, 'timeout': timeout, 'max_retries': 0}
    if personal:
        # None would allow the SDK to inherit OPENAI_ORG_ID / OPENAI_PROJECT_ID.
        # Empty strings suppress that inheritance; base_url prevents sending a
        # personal credential to a server-configured proxy or other provider.
        options.update(base_url=OFFICIAL_OPENAI_URL, organization='', project='')
    return OpenAI(**options)


def provider_error_message(error_code, *, personal=False, testing=False):
    if personal:
        if error_code == 'AuthenticationError':
            return 'OpenAI rejected your API key. Check the key and choose Test & save again.'
        if error_code == 'PermissionDeniedError':
            return 'Your API key cannot access the selected model. Choose a model available to your OpenAI account.'
        if error_code == 'RateLimitError':
            return 'Your OpenAI account reached a usage or rate limit. Check your account billing or retry later.'
        if error_code in {'BadRequestError', 'NotFoundError'}:
            return 'The selected model could not complete structured output. Check the model name and access, then test again.'
        if testing:
            return 'The API key and model could not be verified. Check the model, then retry. Your saved configuration was kept.'
        return 'Your AI connection could not prepare a valid plan. Retry, update your API settings, or choose the project template.'
    if error_code == 'AuthenticationError':
        return "RADAI's AI provider rejected the server credentials. Add your own API key here, ask an administrator to update the server AI configuration, or choose the project template."
    if error_code == 'PermissionDeniedError':
        return "RADAI's AI account cannot use the configured model. Add your own API key here, ask an administrator to review model access, or choose the project template."
    if error_code == 'RateLimitError':
        return "RADAI's AI provider reached a usage or rate limit. Retry later, or choose the project template."
    return SetupAIUnavailable.default_detail


@sensitive_variables()
def test_and_save_settings(actor, data):
    if not is_encryption_configured():
        raise SetupAIUnavailable('Secure API key storage is not configured. Ask an administrator to configure BYOK encryption before saving a key.')
    personal = _personal_settings(actor)
    key = data.get('api_key')
    if key is None:
        key = decrypt_api_key(personal.api_key_encrypted) if personal else None
    if not key:
        raise ValidationError({'api_key': 'Enter your API key to test and save this connection.'})
    model = data['model']
    started = time.monotonic()
    success, code, input_tokens, output_tokens = False, '', 0, 0
    try:
        with openai_client(key, personal=True, timeout=20) as client:
            response = client.chat.completions.create(
                model=model, max_completion_tokens=256,
                response_format={'type': 'json_schema', 'json_schema': {
                    'name': 'radai_connection_check', 'strict': True,
                    'schema': {'type': 'object', 'additionalProperties': False,
                               'properties': {'ok': {'type': 'boolean'}}, 'required': ['ok']},
                }},
                messages=[{'role': 'user', 'content': 'Connection check. Return an object with ok set to true.'}],
            )
        usage = response.usage
        input_tokens = getattr(usage, 'prompt_tokens', 0) or 0
        output_tokens = getattr(usage, 'completion_tokens', 0) or 0
        if not response.choices or response.choices[0].finish_reason != 'stop' or getattr(response.choices[0].message, 'refusal', None):
            raise ValueError('incomplete_or_refused')
        if json.loads(response.choices[0].message.content or '') != {'ok': True}:
            raise ValueError('invalid_connection_check')
        success = True
    except Exception as exc:
        code = type(exc).__name__
        logger.warning('Project setup personal AI test failed (%s)', code)
        raise SetupAIUnavailable(provider_error_message(code, personal=True, testing=True)) from None
    finally:
        from apps.rbac.ai_telemetry import record_usage
        record_usage(user=actor, provider='openai', model=model, feature='project_setup_connection_test',
                     application='planning_intelligence', tokens_input=input_tokens, tokens_output=output_tokens,
                     latency_ms=int((time.monotonic() - started) * 1000), success=success, error_code=code,
                     usage_available=success or bool(input_tokens or output_tokens))
    # A failed provider test cannot replace a previously valid credential.
    ProjectSetupAISettings.objects.update_or_create(user_id=actor.pk, defaults={
        'provider': 'openai', 'model': model, 'api_key_encrypted': encrypt_api_key(key),
        'last_tested_at': timezone.now(),
    })
    return ai_settings_payload(actor)


def delete_settings(actor):
    ProjectSetupAISettings.objects.filter(user_id=actor.pk).delete()
    return ai_settings_payload(actor)
