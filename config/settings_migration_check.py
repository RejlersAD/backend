"""Minimal settings used to verify migration state without a live PostgreSQL connection."""
from .settings_test import *  # noqa: F403

MIGRATION_MODULES = {}
