"""Register an installed loopback Ollama model in the existing AI Model Hub."""
from decimal import Decimal

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.core.ai_hub_adapters import AdapterError, list_models, audit
from apps.core.ai_hub_capabilities import describe_model
from apps.core.ai_hub_models import AIModel, AIProvider, AIRoute
from apps.core.ai_hub_runtime import local_ollama_base_url


class Command(BaseCommand):
    help = 'Configure RADAI Intelligence to use an installed local Ollama model.'

    def add_arguments(self, parser):
        parser.add_argument('--model', default='qwen3:8b')
        parser.add_argument('--endpoint', default='http://localhost:11434/v1')

    def handle(self, *args, **options):
        endpoint = options['endpoint'].rstrip('/')
        base = endpoint[:-3] if endpoint.endswith('/v1') else endpoint
        model_id = options['model'].strip()
        if not model_id or '/' in model_id or '\\' in model_id:
            raise CommandError('Choose an installed Ollama model identifier.')
        provider, _ = AIProvider.objects.get_or_create(pk='ollama', defaults={
            'name': 'Ollama', 'group': 'self', 'protocol': 'ollama',
            'auth_kind': 'local', 'default_base_url': base, 'list_endpoint': '/api/tags',
            'sort': 170, 'enabled': True})
        if provider.group != 'self' or provider.protocol != 'ollama' or provider.auth_kind != 'local':
            raise CommandError('The existing Ollama Hub provider has an incompatible configuration.')
        provider.enabled = True
        try:
            local_ollama_base_url(base)  # Validate before reading or sending any project content.
            installed = {row['model_id'] for row in list_models(provider, base_url=base)}
        except (ValueError, AdapterError) as exc:
            raise CommandError(str(exc)) from None
        if model_id not in installed:
            raise CommandError(f'{model_id} is not installed at the local Ollama endpoint.')
        described = describe_model(model_id)
        with transaction.atomic():
            provider.default_base_url = base
            provider.enabled = True
            provider.save(update_fields=['default_base_url', 'enabled'])
            AIModel.objects.update_or_create(provider=provider, model_id=model_id, defaults={
                'maker': 'Qwen' if model_id.startswith('qwen') else 'Local',
                'family': model_id.split(':', 1)[0], 'variant': model_id,
                'capabilities': sorted(set(described['capabilities']) | {'text'}),
                'weights': 'open', 'licence': described['licence'],
                'price_in_per_m': Decimal('0'), 'price_out_per_m': Decimal('0'),
                'status': 'ga', 'enabled': True,
            })
            AIRoute.objects.update_or_create(task_id='radai-intelligence-chat', defaults={
                'label': 'RADAI Intelligence local chat', 'module': 'radai_intelligence',
                'path': '/radai-intelligence', 'needs': ['text'],
                'data_policy': 'onprem', 'chain': [f'ollama:{model_id}'],
            })
            audit(None, 'routing_change', provider='ollama',
                  detail={'task_id': 'radai-intelligence-chat', 'model_id': model_id,
                          'data_policy': 'onprem'})
        self.stdout.write(self.style.SUCCESS(f'RADAI Intelligence configured for local Ollama {model_id}.'))
