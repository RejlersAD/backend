"""Family-regex capability matcher backed by model_capability_rules.yaml."""
import os
import re

import yaml

_RULES_PATH = os.path.join(os.path.dirname(__file__), 'model_capability_rules.yaml')
_cache = None


def load_rules(path=_RULES_PATH):
    global _cache
    if _cache is None:
        with open(path, 'r', encoding='utf-8') as handle:
            data = yaml.safe_load(handle) or {}
        _cache = [
            (re.compile(rule['pattern'], re.IGNORECASE), rule)
            for rule in data.get('rules', [])
        ]
    return _cache


def describe_model(model_id):
    """Return {capabilities, weights, licence} for a model id (first rule wins)."""
    haystack = (model_id or '').lower()
    for regex, rule in load_rules():
        if regex.search(haystack):
            return {
                'capabilities': list(rule.get('capabilities') or ['text']),
                'weights': rule.get('weights', 'closed'),
                'licence': rule.get('licence', ''),
            }
    return {'capabilities': ['text'], 'weights': 'closed', 'licence': ''}
