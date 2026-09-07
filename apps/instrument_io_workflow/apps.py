import logging

from django.apps import AppConfig
from django.db.models.signals import post_migrate

logger = logging.getLogger(__name__)


def _seed_default_symbols(sender, **kwargs):
    """post_migrate receiver — mirrors static/io_list_default_symbols/
    into IOListLegendSymbolImage (is_default=True) automatically right
    after `manage.py migrate` finishes, so a fresh/updated database always
    has the shared picture library without a manual seeding step. Same
    function the seed_io_default_symbols management command calls
    directly — see services/seed_default_symbols.py. Idempotent
    (skip-if-exists), so this runs safely on every migrate, not just the
    first one.

    Wrapped in try/except — a seeding hiccup (e.g. a transient storage
    backend issue) must never turn `manage.py migrate` itself into a
    failure; the command remains available to retry by hand.
    """
    try:
        from .services.seed_default_symbols import seed_io_default_symbols
        seed_io_default_symbols()
    except Exception:
        logger.exception('[IOWF] post_migrate default-symbol seeding failed')


def _seed_default_legends(sender, **kwargs):
    """post_migrate receiver — creates every legend section's shared
    default row (IOListLegendSheet, is_default=True) automatically right
    after `manage.py migrate` finishes. Same function the
    seed_io_default_legends management command calls directly — see
    services/seed_default_legends.py. Idempotent (skip-if-exists per
    section), so this runs safely on every migrate, not just the first
    one — and is exactly what makes a genuinely fresh production database
    (0 legend rows before this) end up fully seeded without any manual
    step, now that the old pid_checker_v2-copy migrations (0006/0008) no
    longer do that themselves.

    Wrapped in try/except — same reasoning as _seed_default_symbols above:
    a seeding hiccup must never turn `manage.py migrate` itself into a
    failure.
    """
    try:
        from .services.seed_default_legends import seed_io_default_legends
        seed_io_default_legends()
    except Exception:
        logger.exception('[IOWF] post_migrate default-legend seeding failed')


class InstrumentIOWorkflowConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.instrument_io_workflow'
    label = 'instrument_io_workflow'
    verbose_name = 'Instrument IO List Workflow'

    def ready(self):
        post_migrate.connect(_seed_default_symbols, sender=self)
        post_migrate.connect(_seed_default_legends, sender=self)
