"""Seed the 20 hub providers (spec §4) and the §6.4 RADAI task routes."""
from django.db import migrations

PROVIDERS = [
    # id, name, group, protocol, auth_kind, default_base_url, list_endpoint, sort
    ('openai', 'OpenAI', 'paid', 'openai', 'key', 'https://api.openai.com/v1', '/models', 10),
    ('anthropic', 'Anthropic', 'paid', 'anthropic', 'key', 'https://api.anthropic.com', '/v1/models', 20),
    ('gemini', 'Google Gemini API', 'paid', 'gemini', 'key', 'https://generativelanguage.googleapis.com', '/v1beta/models', 30),
    ('mistral', 'Mistral AI', 'paid', 'openai_compatible', 'key', 'https://api.mistral.ai/v1', '/models', 40),
    ('xai', 'xAI', 'paid', 'openai_compatible', 'key', 'https://api.x.ai/v1', '/models', 50),
    ('azure-openai', 'Azure OpenAI', 'cloud', 'azure', 'azure', '', '/openai/models', 60),
    ('aws-bedrock', 'AWS Bedrock', 'cloud', 'bedrock', 'bedrock', 'https://bedrock.us-east-1.amazonaws.com', '/foundation-models', 70),
    ('google-vertex', 'Google Vertex AI', 'cloud', 'vertex', 'vertex', 'https://aiplatform.googleapis.com', '/v1/publishers/google/models', 80),
    ('deepseek', 'DeepSeek', 'open', 'openai_compatible', 'key', 'https://api.deepseek.com/v1', '/models', 90),
    ('qwen', 'Alibaba Qwen (Model Studio)', 'open', 'openai_compatible', 'key', 'https://dashscope.aliyuncs.com/compatible-mode/v1', '/models', 100),
    ('kimi', 'Moonshot Kimi', 'open', 'openai_compatible', 'key', 'https://api.moonshot.ai/v1', '/models', 110),
    ('glm', 'Zhipu GLM', 'open', 'openai_compatible', 'key', 'https://open.bigmodel.cn/api/paas/v4', '/models', 120),
    ('openrouter', 'OpenRouter', 'gateway', 'openai_compatible', 'key', 'https://openrouter.ai/api/v1', '/models', 130),
    ('together', 'Together AI', 'gateway', 'openai_compatible', 'key', 'https://api.together.xyz/v1', '/models', 140),
    ('groq', 'Groq', 'gateway', 'openai_compatible', 'key', 'https://api.groq.com/openai/v1', '/models', 150),
    ('fireworks', 'Fireworks AI', 'gateway', 'openai_compatible', 'key', 'https://api.fireworks.ai/inference/v1', '/models', 160),
    ('ollama', 'Ollama', 'self', 'ollama', 'local', 'http://localhost:11434', '/api/tags', 170),
    ('vllm', 'vLLM', 'self', 'openai_compatible', 'base', 'http://localhost:8000/v1', '/models', 180),
    ('tgi', 'Hugging Face TGI', 'self', 'tgi', 'local', 'http://localhost:8080', '/info', 190),
    ('custom-openai', 'Custom OpenAI-compatible', 'self', 'openai_compatible', 'base', '', '/models', 200),
]


def seed(apps, schema_editor):
    AIProvider = apps.get_model('core', 'AIProvider')
    for pid, name, group, protocol, auth_kind, base_url, list_endpoint, sort in PROVIDERS:
        AIProvider.objects.update_or_create(
            id=pid,
            defaults={'name': name, 'group': group, 'protocol': protocol,
                      'auth_kind': auth_kind, 'default_base_url': base_url,
                      'list_endpoint': list_endpoint, 'enabled': True, 'sort': sort})

    from apps.core.ai_hub_runtime import SEEDED_TASKS
    AIRoute = apps.get_model('core', 'AIRoute')
    for task_id, spec in SEEDED_TASKS.items():
        AIRoute.objects.update_or_create(
            task_id=task_id,
            defaults={'label': spec['label'], 'module': spec['module'],
                      'path': spec['path'], 'needs': spec['needs'],
                      'data_policy': spec['data_policy'], 'chain': [None, None, None]})


def unseed(apps, schema_editor):
    apps.get_model('core', 'AIRoute').objects.all().delete()
    apps.get_model('core', 'AIProvider').objects.filter(
        id__in=[p[0] for p in PROVIDERS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_aihubsettings_aiprovider_airoute_aisyncrun_aiaudit_and_more'),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
