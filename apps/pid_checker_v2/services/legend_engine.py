"""Compile a Legend-Sheet `definition` JSON into:
  * a validated regex that matches full composite tags
  * a per-field lookup index for enrichment
  * an AI-prompt block that describes every field and its allowed codes

This module is the single source of truth for how a legend drives the
extraction pipeline. Keep all logic here so the OCR and Vision extractors
can share one code path.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# ─── Soft-coded config ────────────────────────────────────────────────
FIELD_KEY_ATTR = 'key'
FIELD_LABEL_ATTR = 'label'
FIELD_REGEX_ATTR = 'regex'
FIELD_SUFFIX_ATTR = 'suffix'
FIELD_NOTES_ATTR = 'notes'
FIELD_LOOKUP_ATTR = 'lookup'
FIELD_OPTIONAL_ATTR = 'optional'
DEFINITION_SEPARATOR_ATTR = 'separator'
DEFINITION_FIELDS_ATTR = 'fields'

DEFAULT_SEPARATOR = '-'


@dataclass(frozen=True)
class CompiledLegend:
    fields: list[dict]
    separator: str
    pattern: re.Pattern
    field_keys: tuple[str, ...]
    lookups: dict[str, dict[str, str]]

    def match(self, text: str) -> dict | None:
        """Return a canonicalised tag dict if `text` matches the legend, else None."""
        m = self.pattern.match(text.strip())
        if not m:
            return None
        parts = m.groups()
        row: dict[str, Any] = {}
        for key, value in zip(self.field_keys, parts):
            row[key] = value or ''
        # Enrich with lookups
        for key, lookup in self.lookups.items():
            code = (row.get(key) or '').upper()
            if code and code in lookup:
                row[f'{key}_label'] = lookup[code]
        row['tag'] = text.strip()
        return row


def compile_legend(definition: dict) -> CompiledLegend:
    """Compile a legend definition JSON into a matcher + prompt-ready structure."""
    if not isinstance(definition, dict):
        raise ValueError('legend definition must be a JSON object')
    fields = definition.get(DEFINITION_FIELDS_ATTR)
    if not isinstance(fields, list) or not fields:
        raise ValueError('legend definition must include at least one field')
    separator = definition.get(DEFINITION_SEPARATOR_ATTR) or DEFAULT_SEPARATOR

    field_keys: list[str] = []
    lookups: dict[str, dict[str, str]] = {}
    regex_parts: list[str] = []

    for i, field in enumerate(fields):
        if not isinstance(field, dict):
            raise ValueError(f'field #{i} must be an object')
        key = str(field.get(FIELD_KEY_ATTR) or f'field_{i}').strip()
        if not key:
            raise ValueError(f'field #{i} is missing key')
        regex = str(field.get(FIELD_REGEX_ATTR) or '').strip()
        if not regex:
            raise ValueError(f'field {key!r} is missing regex')
        try:
            re.compile(regex)
        except re.error as exc:
            raise ValueError(f'field {key!r} has invalid regex: {exc}') from exc

        suffix = str(field.get(FIELD_SUFFIX_ATTR) or '')
        # NOTE: suffix (e.g. the closing " on size) is baked into the pattern
        # between this field's capture and the separator.
        optional = bool(field.get(FIELD_OPTIONAL_ATTR))
        group = f'({regex})'
        if suffix:
            # escape and place after the group
            group = f'{group}{re.escape(suffix)}'
        if i > 0:
            sep = re.escape(separator)
            if optional:
                # allow the whole optional field (with its leading separator) to be absent
                group = f'(?:{sep}{group})?'
                # optional groups without alt inner: normalise to capture ''
                # but keeping a capturing group is required for zip alignment
            else:
                group = f'{sep}{group}'
        else:
            if optional:
                group = f'(?:{group})?'
        regex_parts.append(group)
        field_keys.append(key)

        lookup = field.get(FIELD_LOOKUP_ATTR)
        if isinstance(lookup, dict) and lookup:
            lookups[key] = {str(k).upper(): str(v) for k, v in lookup.items()}

    full = r'^' + ''.join(regex_parts) + r'$'
    try:
        pattern = re.compile(full)
    except re.error as exc:
        raise ValueError(f'compiled pattern is invalid: {exc}') from exc

    # Expected group count check — every non-optional field contributes 1 group;
    # optional wraps in (?:...(...)...)? which still exposes 1 group in .groups().
    return CompiledLegend(
        fields=fields,
        separator=separator,
        pattern=pattern,
        field_keys=tuple(field_keys),
        lookups=lookups,
    )


def build_prompt_block(compiled: CompiledLegend) -> str:
    """Render a human-readable rules block that we inject into the AI prompt."""
    lines: list[str] = []
    field_labels = [f.get(FIELD_LABEL_ATTR) or f.get(FIELD_KEY_ATTR) for f in compiled.fields]
    template_parts: list[str] = []
    for f in compiled.fields:
        placeholder = f.get(FIELD_KEY_ATTR, '?').upper()
        suffix = f.get(FIELD_SUFFIX_ATTR) or ''
        opt = ' (optional)' if f.get(FIELD_OPTIONAL_ATTR) else ''
        template_parts.append(f'{{{placeholder}}}{suffix}{opt}')
    lines.append(f"Composite tag format:  {compiled.separator.join(template_parts)}")
    lines.append('')
    lines.append('Fields:')
    for f in compiled.fields:
        key = f.get(FIELD_KEY_ATTR)
        label = f.get(FIELD_LABEL_ATTR) or key
        regex = f.get(FIELD_REGEX_ATTR)
        notes = f.get(FIELD_NOTES_ATTR) or ''
        suffix = f.get(FIELD_SUFFIX_ATTR) or ''
        opt = ' (optional)' if f.get(FIELD_OPTIONAL_ATTR) else ''
        line = f"  • {label}{opt}: matches /{regex}/"
        if suffix:
            line += f' followed by "{suffix}"'
        if notes:
            line += f' — {notes}'
        lines.append(line)
        lookup = f.get(FIELD_LOOKUP_ATTR)
        if isinstance(lookup, dict) and lookup:
            lines.append(f"    Allowed codes for {label}:")
            for code, desc in lookup.items():
                lines.append(f"      {code} = {desc}")
    return '\n'.join(lines)
