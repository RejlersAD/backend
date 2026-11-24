"""Load and check a proposal standard (YAML)."""
from __future__ import annotations

from pathlib import Path

import yaml

DEFAULT_CONFIG = Path(__file__).parent / 'config' / 'rejlers_adnoc_standard.yaml'
REQUIRED_KEYS = ('company', 'templates', 'sections', 'components', 'supported_formats')


def load_config(path=None):
    path = Path(path or DEFAULT_CONFIG)
    with open(path, encoding='utf-8') as handle:
        config = yaml.safe_load(handle)
    missing = [key for key in REQUIRED_KEYS if key not in config]
    if missing:
        raise ValueError(f'{path}: missing keys {missing}')
    from .plan import walk_sections

    for section, _, _ in walk_sections(config['sections']):
        for component in section.get('components') or []:
            if component not in config['components']:
                raise ValueError(f"section {section['key']!r} references unknown component {component!r}")
    for key in ('body', 'divider'):
        config['templates'][key] = str((path.parent / config['templates'][key]).resolve())
    stamp = config['templates'].get('stamp')
    if stamp:
        stamp['image'] = str((path.parent / stamp['image']).resolve())
    config['_path'] = str(path)
    return config
