"""Shared library of default legend symbol pictures.

These are NOT database rows — they're repo-committed static files under
``static/default_symbols/<section>/<slug>.<ext>``, discovered through
Django's normal staticfiles machinery (``STATICFILES_DIRS`` in
settings.py). A fresh server with an empty database still has every
default picture, since nothing needs to be seeded — the files just need
to exist in the deployed code.

A project's own upload (``LegendSymbolImage``, stored in S3/local disk)
always takes priority over a default with the same (section, symbol_name)
— see ``SymbolImagesListView`` / ``DefaultSymbolImagesView`` in views.py
for how the two are merged on the read side.

To add a new default picture: drop a PNG/JPG/SVG file at
``static/default_symbols/<section>/<slug_for_symbol_name(name)>.<ext>``
and commit it — no code change needed. ``SymbolImageSetDefaultView``
(admin-only) can also write one directly from an already-uploaded project
picture, for convenience while testing locally; see its docstring for why
that alone isn't sufficient in most production deployments.
"""
from __future__ import annotations

import logging
import re

from django.conf import settings
from django.contrib.staticfiles import finders
from django.templatetags.static import static

logger = logging.getLogger(__name__)

DEFAULT_SYMBOLS_STATIC_SUBDIR = 'default_symbols'
DEFAULT_SYMBOL_IMAGE_EXTENSIONS = ('png', 'jpg', 'jpeg', 'svg')


def slug_for_symbol_name(symbol_name: str) -> str:
    """Filesystem-safe slug for a symbol name, e.g.
    'GATE VALVE (NORMAL OPEN)' -> 'GATE_VALVE_NORMAL_OPEN'."""
    normalised = re.sub(r'\s+', ' ', str(symbol_name or '')).strip().upper()
    return re.sub(r'[^A-Z0-9]+', '_', normalised).strip('_')


def _known_symbol_names(section: str) -> list[str]:
    """Every symbol name legend_defaults.py knows about for a picture
    section (the canonical, correctly-formatted names — not reconstructed
    from filenames, which would be lossy since slugging isn't reversible).
    Defensive against sections whose template doesn't have this shape
    (e.g. the composite-tag sections like line_list/equipment_list)."""
    from apps.pid_checker_v2.legend_defaults import DEFAULT_TEMPLATES
    tpl = DEFAULT_TEMPLATES.get(section)
    if not tpl:
        return []
    try:
        fields = tpl['definition']['fields']
        lookup = fields[0].get('lookup') or {}
        return list(lookup.keys())
    except (KeyError, IndexError, TypeError):
        return []


def list_default_symbol_images(exclude_keys: set[tuple[str, str]] | None = None) -> list[dict]:
    """Every (section, symbol_name) with a repo-committed static picture,
    in the same {'section','symbol_name','content_type','image_url'} shape
    SymbolImagesListView already returns for database-backed uploads — so
    the two can be concatenated directly.

    `exclude_keys` — a set of (section, symbol_name.upper()) already
    covered by a database row (a project's own upload, or another
    project's, via the existing cross-project fallback) — those take
    priority and are skipped here to avoid a duplicate entry.
    """
    from apps.pid_checker_v2.legend_defaults import SECTIONS

    exclude_keys = exclude_keys or set()
    out: list[dict] = []
    for section in SECTIONS:
        for name in _known_symbol_names(section):
            # Same normalisation as views._normalise_symbol_name (collapse
            # whitespace runs + uppercase) so exclude_keys built from
            # database rows lines up with keys built here.
            key = (section, re.sub(r'\s+', ' ', name).strip().upper())
            if key in exclude_keys:
                continue
            url = get_default_symbol_image_url(section, name)
            if not url:
                continue
            ext = url.rsplit('.', 1)[-1].lower() if '.' in url else 'png'
            content_type = 'image/svg+xml' if ext == 'svg' else f'image/{"jpeg" if ext == "jpg" else ext}'
            out.append({
                'section': section,
                'symbol_name': name,
                'content_type': content_type,
                'image_url': url,
            })
    return out


def get_default_symbol_image_url(section: str, symbol_name: str) -> str | None:
    """Return the static URL for a section/symbol's default picture, or
    None if no such file has been committed to the repo."""
    slug = slug_for_symbol_name(symbol_name)
    if not slug:
        return None
    for ext in DEFAULT_SYMBOL_IMAGE_EXTENSIONS:
        relative_path = f'{DEFAULT_SYMBOLS_STATIC_SUBDIR}/{section}/{slug}.{ext}'
        if not finders.find(relative_path):
            continue
        try:
            return static(relative_path)
        except ValueError:
            # static() resolves through STATICFILES_STORAGE — when that's a
            # manifest-based backend (ManifestStaticFilesStorage etc.), it
            # requires the file to already be registered via `collectstatic`
            # in staticfiles.json, which finders.find() above does NOT need
            # (it just checks the source STATICFILES_DIRS folder directly).
            # A default picture dropped into the repo without an immediate
            # collectstatic run would otherwise 500 here — directly
            # contradicting this module's whole point (a fresh/updated
            # server has every default picture with zero extra steps).
            # Fall back to a plain, un-hashed STATIC_URL path instead of
            # failing the whole request; it's a valid URL WhiteNoise/the
            # static file server can still serve from the source file.
            logger.warning(
                "[default_symbol_images] %r found by finders but missing from the "
                "staticfiles manifest (needs `collectstatic`) — falling back to a "
                "plain STATIC_URL path.", relative_path,
            )
            return f'{settings.STATIC_URL.rstrip("/")}/{relative_path}'
    return None
