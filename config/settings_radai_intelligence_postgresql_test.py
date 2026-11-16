"""Disposable PostgreSQL verification for RADAI Intelligence development migrations."""
from .settings_pid_checker_postgresql_test import *  # noqa: F403
import os

ROOT_URLCONF = 'config.urls_radai_intelligence_test'
AI_WORKSPACE_STORAGE_ROOT = os.environ.get('RADAI_AI_TEST_STORAGE_ROOT') or str(BASE_DIR.parent / 'disposable_ai_workspace_files')
