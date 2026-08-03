"""
CRS Documents History API Package
Provides endpoints for user file history and activity tracking
"""
from .history_views import (
    user_history_overview,
    user_uploads,
    user_exports,
    user_profile,
    user_activity,
    download_from_history,
    get_history_config,
    delete_from_history,
    generate_share_link,
    get_file_metadata,
)

__all__ = [
    'user_history_overview',
    'user_uploads',
    'user_exports',
    'user_profile',
    'user_activity',
    'download_from_history',
    'get_history_config',
    'delete_from_history',
    'generate_share_link',
    'get_file_metadata',
]
