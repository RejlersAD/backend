"""Database maintenance checks on isolated SQLite; never the application DB."""
from .settings_console_test import *  # noqa: F403

# RBAC registers result signals for optional AI apps outside this test scope.
SILENCED_SYSTEM_CHECKS = ['signals.E001']
