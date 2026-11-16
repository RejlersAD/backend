"""Isolated SQLite HTTP checks for RADAI Intelligence; no production services."""
from .settings_pid_checker_security_test import *  # noqa: F403,F401

ROOT_URLCONF = 'config.urls_radai_intelligence_test'
