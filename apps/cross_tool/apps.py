"""
Cross-Tool Recommendation Engine — App Config
==============================================
SOFT-CODED: Drop-in bridge between P&ID QC and PFD QC.
No changes to either core app are needed.
"""
from django.apps import AppConfig


class CrossToolConfig(AppConfig):
    name = 'apps.cross_tool'
    verbose_name = 'Cross-Tool Recommendation Engine'

    def ready(self):
        # Wire up Django signals — auto-register PIDVDocument + PFDQDocument on save.
        # This import is intentionally deferred to avoid circular imports at startup.
        import apps.cross_tool.signals  # noqa: F401
