import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def _seed_default_symbols(sender, **kwargs):
    """post_migrate receiver — mirrors static/default_symbols/ into
    LegendSymbolImage (is_default=True) automatically right after
    `manage.py migrate` finishes, so a fresh/updated database always has
    the shared picture library without a manual seeding step. Same
    function the seed_pid_default_symbols management command calls
    directly — see services/seed_default_symbols.py. Idempotent
    (skip-if-exists), so this runs safely on every migrate, not just the
    first one.

    Wrapped in try/except — a seeding hiccup must never turn
    `manage.py migrate` itself into a failure; the command remains
    available to retry by hand.
    """
    try:
        from .services.seed_default_symbols import seed_pid_default_symbols
        seed_pid_default_symbols()
    except Exception:
        logger.exception('[PidCheckerV2] post_migrate default-symbol seeding failed')


class PidCheckerV2Config(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.pid_checker_v2'
    verbose_name = 'P&ID Checker V2'

    def ready(self):
        post_migrate.connect(_seed_default_symbols, sender=self)
