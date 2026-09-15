from apps.hr_core.apps import HrCoreConfig


class HRCoreWithoutSignalsConfig(HrCoreConfig):
    """Register HR models for FK resolution without loading unrelated domains."""

    def ready(self):
        pass
