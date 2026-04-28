"""
RBAC Configuration
Centralized configuration for Role-Based Access Control system.
All RBAC settings live here — edit this file to change roles, modules, and policies.
Follows soft-coding principles: no role/module names are hardcoded in views or logic.

Cross-verified against:
  - user_management/  (standalone RBAC microservice package)
  - data-management/  (document/dataset microservice with append-only audit)
"""

# ─────────────────────────────────────────────────────────────────────────────
# COMPLETE MODULE CATALOGUE
# Single source of truth — seed_rbac.py reads this list.
# Each entry maps to Module.code in the DB.
# ─────────────────────────────────────────────────────────────────────────────
ALL_MODULES_CATALOGUE = [
    # ── Core Engineering ──────────────────────────────────────────────────
    {'code': 'pid_analysis',           'name': 'P&ID Analysis',               'icon': 'FileText',    'order': 1,  'description': 'P&ID document analysis and processing'},
    {'code': 'pfd_to_pid',             'name': 'PFD to P&ID Converter',        'icon': 'RefreshCw',   'order': 2,  'description': 'AI-powered conversion of PFD to P&ID drawings'},
    {'code': 'pfd_quality',            'name': 'PFD Quality Check',            'icon': 'CheckSquare', 'order': 3,  'description': 'AI-powered quality verification of PFD documents'},
    {'code': 'crs_documents',          'name': 'CRS Document Management',      'icon': 'FolderOpen',  'order': 4,  'description': 'Upload and manage CRS documents with AI analysis'},
    {'code': 'designiq',               'name': 'DesignIQ',                     'icon': 'Cpu',         'order': 5,  'description': 'AI-powered design intelligence and PFD verification'},
    {'code': 'qhse',                   'name': 'QHSE Management',              'icon': 'Shield',      'order': 6,  'description': 'Quality, Health, Safety and Environment project management'},
    # ── Discipline Datasheets ─────────────────────────────────────────────
    {'code': 'process_datasheet',      'name': 'Process Datasheet',            'icon': 'FileText',    'order': 10, 'description': 'Process equipment datasheets — MOV, SDV, pumps, pressure instruments'},
    {'code': 'electrical_datasheet',   'name': 'Electrical Datasheet',         'icon': 'Zap',         'order': 11, 'description': 'Electrical equipment and SLD-based datasheet generation'},
    {'code': 'electrical_sld',         'name': 'Electrical SLD',               'icon': 'Zap',         'order': 12, 'description': 'Single Line Diagram analysis and tagging'},
    {'code': 'instrument_datasheet',   'name': 'Instrument Datasheet',         'icon': 'Activity',    'order': 13, 'description': 'Instrument equipment datasheets and tag lists'},
    {'code': 'instrument_index',       'name': 'Instrument Index',             'icon': 'List',        'order': 14, 'description': 'AI extraction of instrument index from P&ID drawings'},
    {'code': 'mechanical_datasheet',   'name': 'Mechanical Datasheet',         'icon': 'Tool',        'order': 15, 'description': 'Mechanical equipment datasheets and inspection records'},
    {'code': 'civil_datasheet',        'name': 'Civil Datasheet',              'icon': 'Home',        'order': 16, 'description': 'Civil and structural engineering datasheets'},
    {'code': 'piping_datasheet',       'name': 'Piping Datasheet',             'icon': 'GitBranch',   'order': 17, 'description': 'Piping material specifications and critical line list'},
    {'code': 'piping_pms',             'name': 'Piping Material Specification', 'icon': 'Database',   'order': 18, 'description': 'Piping material specification management'},
    {'code': 'digitization_datasheet', 'name': 'Digitization Datasheet',       'icon': 'Scan',        'order': 19, 'description': 'AI-powered digitization of legacy datasheets'},
    {'code': 'spec_customization',     'name': 'Spec Customization',           'icon': 'Settings',    'order': 20, 'description': 'Engineering specification customization tools'},
    {'code': 'non_teff_metadata',      'name': 'Non-TEFF Metadata Extractor',  'icon': 'Search',      'order': 21, 'description': 'Extract metadata from Non-TEFF documents (PDF, Excel, Word, AutoCAD)'},
    # ── Admin / Platform ─────────────────────────────────────────────────
    {'code': 'user_mgmt',              'name': 'User Management',              'icon': 'Users',       'order': 50, 'description': 'Manage users, roles, and permissions'},
    {'code': 'org_settings',           'name': 'Organization Settings',        'icon': 'Settings',    'order': 51, 'description': 'Configure organization settings and preferences'},
    {'code': 'audit_logs',             'name': 'Audit Logs',                   'icon': 'FileSearch',  'order': 52, 'description': 'View system audit logs and activity (append-only per data-management spec)'},
    {'code': 'file_storage',           'name': 'File Storage',                 'icon': 'Database',    'order': 53, 'description': 'Manage files and documents in S3'},
    {'code': 'reports',                'name': 'Reports & Analytics',          'icon': 'BarChart',    'order': 54, 'description': 'Generate reports and view analytics'},
    {'code': 'api_access',             'name': 'API Access',                   'icon': 'Code',        'order': 55, 'description': 'Access REST APIs programmatically'},
]

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM ROLES CONFIGURATION
# All system roles with their display metadata.
# Seed and migrations read this — never hardcode role names elsewhere.
#
# level hierarchy (from user_management package spec):
#   1 = Super Admin  |  2 = Admin  |  3 = Manager
#   4 = Engineer     |  5 = Reviewer  |  6 = Viewer
# ─────────────────────────────────────────────────────────────────────────────
SYSTEM_ROLES_CONFIG = [
    {
        'code': 'super_admin',
        'name': 'Super Administrator',
        'level': 1,
        'description': 'Full system access — manages all organizations and users. Bypasses all module checks.',
        'is_system_role': True,
        'badge_color': 'red',
    },
    {
        'code': 'admin',
        'name': 'Administrator',
        'level': 2,
        'description': 'Organization administrator — manages users, roles, modules, and settings.',
        'is_system_role': True,
        'badge_color': 'orange',
    },
    {
        'code': 'process_engineer',
        'name': 'Process Engineer',
        'level': 4,
        'description': 'Process discipline engineer — access to process datasheets, P&ID, PFD tools.',
        'is_system_role': True,
        'badge_color': 'blue',
    },
    {
        'code': 'electrical_engineer',
        'name': 'Electrical Engineer',
        'level': 4,
        'description': 'Electrical discipline engineer — access to electrical datasheets and SLD analysis.',
        'is_system_role': True,
        'badge_color': 'yellow',
    },
    {
        'code': 'instrument_engineer',
        'name': 'Instrument Engineer',
        'level': 4,
        'description': 'Instrument discipline engineer — access to instrument datasheets and index.',
        'is_system_role': True,
        'badge_color': 'purple',
    },
    {
        'code': 'mechanical_engineer',
        'name': 'Mechanical Engineer',
        'level': 4,
        'description': 'Mechanical discipline engineer — access to mechanical datasheets.',
        'is_system_role': True,
        'badge_color': 'gray',
    },
    {
        'code': 'civil_engineer',
        'name': 'Civil Engineer',
        'level': 4,
        'description': 'Civil/structural discipline engineer — access to civil datasheets.',
        'is_system_role': True,
        'badge_color': 'green',
    },
    {
        'code': 'piping_engineer',
        'name': 'Piping Engineer',
        'level': 4,
        'description': 'Piping discipline engineer — access to piping datasheets and PMS.',
        'is_system_role': True,
        'badge_color': 'indigo',
    },
    {
        'code': 'qhse_engineer',
        'name': 'QHSE Engineer',
        'level': 4,
        'description': 'Quality, Health, Safety and Environment engineer.',
        'is_system_role': True,
        'badge_color': 'teal',
    },
    {
        'code': 'design_engineer',
        'name': 'Design Engineer',
        'level': 4,
        'description': 'Design/digital twin engineer — DesignIQ, PFD to P&ID, P&ID analysis.',
        'is_system_role': True,
        'badge_color': 'cyan',
    },
    {
        'code': 'project_manager',
        'name': 'Project Manager',
        'level': 3,
        'description': 'Cross-discipline project manager — read access across engineering modules.',
        'is_system_role': True,
        'badge_color': 'pink',
    },
    {
        'code': 'viewer',
        'name': 'Viewer',
        'level': 6,
        'description': 'Read-only access. No module access unless explicitly assigned.',
        'is_system_role': True,
        'badge_color': 'slate',
    },
]

# Module Assignment Strategy
MODULE_ASSIGNMENT_CONFIG = {
    'strategy': 'role_based',  # 'role_based' or 'direct'
    'create_custom_roles': True,  # Create custom roles for module-based assignments
    'custom_role_prefix': 'custom_',
    'custom_role_level': 10,  # Level for custom roles
    'clear_existing_on_update': True,  # Clear existing module assignments when updating
    'assign_permissions_automatically': True,  # Auto-assign all module permissions
    'fallback_to_default_role': True,  # Assign default role if no roles specified
}

# Default Role Settings
DEFAULT_ROLE_CONFIG = {
    'code': 'user',
    'name': 'Regular User',
    'level': 100,
    'auto_assign_on_creation': True,
}

# Admin Role Detection
ADMIN_ROLE_CODES = ['super_admin', 'admin', 'administrator']
SUPERADMIN_ROLE_CODES = ['super_admin', 'superadmin']

# Module Access Rules
MODULE_ACCESS_RULES = {
    'check_role_first': True,  # Check role-based access first
    'check_direct_assignment': True,  # Then check direct module assignment
    'admin_has_all_access': True,  # Admins bypass module checks
    'superadmin_has_all_access': True,  # Super admins bypass all checks
}

# Audit Logging
AUDIT_CONFIG = {
    'log_role_assignments': True,
    'log_module_assignments': True,
    'log_permission_changes': True,
    'log_access_denials': True,
    'log_module_access_checks': True,  # Detailed logging for debugging
}

# User Profile Settings
USER_PROFILE_CONFIG = {
    'require_organization': False,  # Organization is optional
    'auto_create_profile': True,  # Auto-create profile for existing users
    'default_status': 'active',
    'require_email_verification': False,  # Email verification optional
}

# Module Categories (for UI grouping)
MODULE_CATEGORIES = {
    'core': {
        'name': 'Core Modules',
        'description': 'Essential system modules',
        'icon': '🔧',
        'order': 1
    },
    'engineering': {
        'name': 'Engineering',
        'description': 'Engineering and design modules',
        'icon': '⚙️',
        'order': 2
    },
    'business': {
        'name': 'Business Operations',
        'description': 'Finance, procurement, and business modules',
        'icon': '💼',
        'order': 3
    },
    'compliance': {
        'name': 'QHSE & Compliance',
        'description': 'Quality, health, safety, and environment',
        'icon': '🛡️',
        'order': 4
    },
    'admin': {
        'name': 'Administration',
        'description': 'System administration modules',
        'icon': '👨‍💼',
        'order': 5
    }
}

# Error Messages
ERROR_MESSAGES = {
    'no_roles': 'User has no roles assigned. Please assign at least one role.',
    'no_modules': 'User has no accessible modules. Please assign modules or roles.',
    'module_not_found': 'Requested module not found or inactive.',
    'access_denied': 'You do not have access to this module.',
    'invalid_role': 'Invalid or inactive role specified.',
    'role_level_insufficient': 'Your role level is insufficient for this action.',
}

# Success Messages
SUCCESS_MESSAGES = {
    'role_assigned': 'Role successfully assigned to user.',
    'module_assigned': 'Module access granted successfully.',
    'permission_granted': 'Permission granted successfully.',
    'user_created': 'User created successfully with assigned roles and modules.',
}

# ─────────────────────────────────────────────────────────────────────────────
# ENGINEERING SECTION MODULE CATALOGUE
# Single source of truth for what constitutes "full Engineering section" access.
# All module codes here correspond to the 1. Engineering section in the frontend.
# Edit this list (not views/commands) when adding/removing engineering modules.
# ─────────────────────────────────────────────────────────────────────────────
ENGINEERING_SECTION_MODULES = [
    # ── Core P&ID / Process ───────────────────────────────────────────
    'pid_analysis',
    'pfd_to_pid',
    'pfd_quality',
    'crs_documents',
    'designiq',
    'qhse',
    # ── Discipline Datasheets ─────────────────────────────────────────
    'process_datasheet',
    'electrical_datasheet',
    'electrical_sld',
    'instrument_datasheet',
    'instrument_index',
    'mechanical_datasheet',
    'civil_datasheet',
    'piping_datasheet',
    'piping_pms',
    # ── Digitization ─────────────────────────────────────────────────
    'digitization_datasheet',
    'spec_customization',
    'non_teff_metadata',
]

# ─────────────────────────────────────────────────────────────────────────────
# SOFT-CODED ROLE → MODULE POLICY
# Maps role codes to the module codes that role should always have access to.
# Used by: management commands (apply_role_module_policy, seed_rbac)
# When a user is assigned a role, they automatically receive all modules in that
# role's policy list.  This is the SINGLE source of truth for role-based module
# access — edit here to change what any role can see.
# ─────────────────────────────────────────────────────────────────────────────
ROLE_MODULE_POLICY = {
    # ── DEFAULT ENGINEERING ACCESS POLICY ────────────────────────────────
    # All roles get the full Engineering section (1.1–1.7) by default.
    # ENGINEERING_SECTION_MODULES is the single source of truth for what
    # constitutes the Engineering section.  To add/remove a module from the
    # default grant, edit ENGINEERING_SECTION_MODULES above — not here.
    # ─────────────────────────────────────────────────────────────────────

    # Process-focused engineers: full Engineering section
    'process_engineer': ENGINEERING_SECTION_MODULES,

    # Electrical discipline: full Engineering section
    'electrical_engineer': ENGINEERING_SECTION_MODULES,

    # Instrument discipline: full Engineering section
    'instrument_engineer': ENGINEERING_SECTION_MODULES,

    # Mechanical discipline: full Engineering section
    'mechanical_engineer': ENGINEERING_SECTION_MODULES,

    # Civil / structural discipline: full Engineering section
    'civil_engineer': ENGINEERING_SECTION_MODULES,

    # Piping discipline: full Engineering section
    'piping_engineer': ENGINEERING_SECTION_MODULES,

    # QHSE discipline: full Engineering section
    'qhse_engineer': ENGINEERING_SECTION_MODULES,

    # DesignIQ / digital twin roles: full Engineering section
    'design_engineer': ENGINEERING_SECTION_MODULES,

    # Project managers: full Engineering section + reports
    'project_manager': ENGINEERING_SECTION_MODULES + ['reports'],

    # Viewer: full Engineering section (read-only enforced by UI/view guards)
    'viewer': ENGINEERING_SECTION_MODULES,

    # Admin: full Engineering section + all admin/platform modules
    'admin': ENGINEERING_SECTION_MODULES + [
        'user_mgmt',
        'org_settings',
        'audit_logs',
        'reports',
        'api_access',
    ],

    # Super-admins bypass module checks in the app, but listed for completeness
    'super_admin': [],

    # ── Organisation-level custom roles (production DB) ───────────────────
    # These are non-system roles created manually in the production database
    # to grant "1. Engineering + 2. COMMON" access bundles.
    # SOFT-CODED: add new org-role codes here to grant full engineering access.
    # All entries here map to ENGINEERING_SECTION_MODULES so that every feature
    # under "1. Engineering" (1.1–1.7) and "2. COMMON" is accessible.
    'engineering_common_access': ENGINEERING_SECTION_MODULES,
}

# Which module codes map to which discipline (used for diagnostics)
MODULE_DISCIPLINE_MAP = {
    'process_datasheet': 'Process',
    'pid_analysis':      'Process / P&ID',
    'pfd_to_pid':        'Process',
    'pfd_quality':       'Process',
    'designiq':          'DesignIQ',
    'electrical_datasheet': 'Electrical',
    'electrical_sld':    'Electrical',
    'instrument_datasheet': 'Instrument',
    'instrument_index':  'Instrument',
    'mechanical_datasheet': 'Mechanical',
    'civil_datasheet':   'Civil',
    'piping_datasheet':  'Piping',
    'piping_pms':        'Piping',
    'digitization_datasheet': 'Digitization',
    'spec_customization': 'Digitization',
    'non_teff_metadata': 'Digitization',
    'qhse':              'QHSE',
    'crs_documents':     'CRS',
    'user_mgmt':         'Admin',
    'org_settings':      'Admin',
    'audit_logs':        'Admin',
    'reports':           'Admin',
    'api_access':        'Admin',
}


def get_custom_role_code(email):
    """Generate custom role code from email"""
    username = email.split('@')[0]
    return f"{MODULE_ASSIGNMENT_CONFIG['custom_role_prefix']}{username}"

def get_custom_role_name(first_name, last_name):
    """Generate custom role name from user details"""
    full_name = f"{first_name} {last_name}".strip()
    return f"Custom Role - {full_name}" if full_name else "Custom Role"

def should_create_custom_role():
    """Check if custom roles should be created for module assignments"""
    return MODULE_ASSIGNMENT_CONFIG['create_custom_roles']

def is_admin_role(role_code):
    """Check if a role code represents an admin role"""
    return role_code.lower() in ADMIN_ROLE_CODES

def is_superadmin_role(role_code):
    """Check if a role code represents a super admin role"""
    return role_code.lower() in SUPERADMIN_ROLE_CODES
