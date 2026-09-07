"""
Shared library of default legend symbol pictures for I/O List — a one-time
copy of apps.pid_checker_v2's static/default_symbols/ folder (see
static/io_list_default_symbols/), not an ongoing import from that app.

These are NOT database rows — repo-committed static files under
static/io_list_default_symbols/<section>/<slug>.<ext>, discovered through
Django's normal staticfiles machinery. A fresh server with an empty
database still has every default picture.

To add a new one: drop a PNG/JPG/SVG at
static/io_list_default_symbols/<section>/<slug_for_symbol_name(name)>.<ext>
and commit it — no code change needed.
"""
from __future__ import annotations

import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

STATIC_SUBDIR = 'io_list_default_symbols'
IMAGE_EXTENSIONS = ('png', 'jpg', 'jpeg', 'svg')


def slug_for_symbol_name(symbol_name: str) -> str:
    """Filesystem-safe slug for a symbol name, e.g.
    'GATE VALVE (NORMAL OPEN)' -> 'GATE_VALVE_NORMAL_OPEN'."""
    normalised = re.sub(r'\s+', ' ', str(symbol_name or '')).strip().upper()
    return re.sub(r'[^A-Z0-9]+', '_', normalised).strip('_')


def get_default_symbol_image_url(section: str, symbol_name: str) -> str | None:
    """Return the static URL for a section/symbol's default picture, or
    None if no such file has been committed to the repo."""
    slug = slug_for_symbol_name(symbol_name)
    if not slug:
        return None
    for ext in IMAGE_EXTENSIONS:
        relative_path = f'{STATIC_SUBDIR}/{section}/{slug}.{ext}'
        if not finders.find(relative_path):
            continue
        try:
            return static(relative_path)
        except ValueError:
            # Same fallback as apps.pid_checker_v2's version of this
            # function — a manifest-based STATICFILES_STORAGE needs
            # collectstatic to have run; fall back to a plain URL instead
            # of 500ing.
            return f'{settings.STATIC_URL.rstrip("/")}/{relative_path}'
    return None
