"""
QHSE Module Configuration
Soft-coded settings for QHSE feature access and permissions
"""

# Authorized QHSE Users (in addition to role-based access)
QHSE_AUTHORIZED_USERS = [
    'shaju.chacko@rejlers.ae',
    'tanzeem.agra@rejlers.ae',
    'darshna.chetwani@rejlers.ae',
    'admin@rejlers.com',
    'info@rejlers.com',
]

# QHSE Module Settings
QHSE_CONFIG = {
    'module_code': 'qhse',
    'module_name': 'QHSE Management',
    'enable_spot_checks': True,
    'enable_audit_trails': True,
    'require_approval': False,  # Set to True to require approval for QHSE changes
    
    # Feature flags
    'features': {
        'running_projects': True,
        'spot_check_register': True,  # Note: Currently disabled per QHSE Manager
        'quality_management': True,
        'health_safety': True,
        'environmental': False,  # DISABLED: 2026-07-11 - Not related to project quality
        'energy': False,  # DISABLED: 2026-07-11 - Not related to project quality
    },
    
    # Access control
    'access': {
        'view_all_projects': ['admin', 'super_admin', 'qhse_manager'],
        'create_projects': ['admin', 'super_admin', 'qhse_manager', 'engineer'],
        'edit_projects': ['admin', 'super_admin', 'qhse_manager', 'engineer'],
        'delete_projects': ['admin', 'super_admin', 'qhse_manager'],
        'export_data': ['admin', 'super_admin', 'qhse_manager', 'engineer', 'viewer'],
    },
    
    # Notification settings
    'notifications': {
        'enabled': True,
        'notify_on_create': True,
        'notify_on_update': True,
        'notify_on_overdue': True,
        'overdue_reminder_days': [7, 3, 1],  # Days before deadline
    },
    
    # Data retention
    'retention': {
        'keep_deleted_records': True,
        'soft_delete': True,
        'archive_after_days': 365,
    }
}

# Email notification recipients
QHSE_NOTIFICATION_EMAILS = [
    'shaju.chacko@rejlers.ae',
]

# Export settings
QHSE_EXPORT_CONFIG = {
    'allowed_formats': ['csv', 'excel', 'pdf'],
    'include_metadata': True,
    'watermark': True,
}
