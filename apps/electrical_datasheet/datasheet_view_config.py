"""
Soft-coded configuration for Electrical Datasheet views
Enables flexible customization without code changes
"""

# Datasheet display configuration
DATASHEET_VIEW_CONFIG = {
    'display_modes': {
        'compact': {
            'show_full_metadata': False,
            'show_revision_history': False,
            'show_comments': False,
            'max_form_fields_displayed': 10
        },
        'detailed': {
            'show_full_metadata': True,
            'show_revision_history': True,
            'show_comments': True,
            'max_form_fields_displayed': 50
        },
        'full': {
            'show_full_metadata': True,
            'show_revision_history': True,
            'show_comments': True,
            'max_form_fields_displayed': None  # Show all
        }
    },
    
    'field_display_groups': {
        'basic_info': {
            'label': 'Basic Information',
            'fields': ['tag_number', 'service_description', 'location', 'project_name', 'project_number'],
            'icon': 'DocumentTextIcon',
            'priority': 1
        },
        'technical_specs': {
            'label': 'Technical Specifications',
            'fields': ['voltage', 'current', 'power', 'frequency', 'efficiency'],
            'icon': 'BoltIcon',
            'priority': 2
        },
        'manufacturer_info': {
            'label': 'Manufacturer Details',
            'fields': ['manufacturer', 'model', 'serial_number', 'part_number'],
            'icon': 'BuildingOfficeIcon',
            'priority': 3
        },
        'compliance_quality': {
            'label': 'Quality & Compliance',
            'fields': ['compliance_score', 'last_quality_check', 'standards', 'certification'],
            'icon': 'ShieldCheckIcon',
            'priority': 4
        }
    },
    
    'status_display': {
        'draft': {
            'color': 'gray',
            'icon': 'DocumentIcon',
            'label': 'Draft',
            'description': 'Initial draft - not reviewed'
        },
        'under_review': {
            'color': 'blue',
            'icon': 'ClockIcon',
            'label': 'Under Review',
            'description': 'Being reviewed by engineering team'
        },
        'approved': {
            'color': 'green',
            'icon': 'CheckCircleIcon',
            'label': 'Approved',
            'description': 'Approved for use'
        },
        'rejected': {
            'color': 'red',
            'icon': 'XCircleIcon',
            'label': 'Rejected',
            'description': 'Rejected - requires revision'
        },
        'revision_required': {
            'color': 'yellow',
            'icon': 'ExclamationTriangleIcon',
            'label': 'Needs Revision',
            'description': 'Revision required before approval'
        }
    },
    
    'compliance_score_ranges': {
        'excellent': {'min': 90, 'color': 'green', 'label': 'Excellent'},
        'good': {'min': 75, 'color': 'blue', 'label': 'Good'},
        'acceptable': {'min': 60, 'color': 'yellow', 'label': 'Acceptable'},
        'poor': {'min': 40, 'color': 'orange', 'label': 'Poor'},
        'critical': {'min': 0, 'color': 'red', 'label': 'Critical'}
    },
    
    'action_buttons_config': {
        'edit': {
            'label': 'Edit',
            'icon': 'PencilIcon',
            'color': 'blue',
            'permission_required': 'can_edit_datasheet',
            'visible_for_status': ['draft', 'revision_required']
        },
        'review': {
            'label': 'Review',
            'icon': 'EyeIcon',
            'color': 'purple',
            'permission_required': 'can_review_datasheet',
            'visible_for_status': ['under_review']
        },
        'approve': {
            'label': 'Approve',
            'icon': 'CheckIcon',
            'color': 'green',
            'permission_required': 'can_approve_datasheet',
            'visible_for_status': ['under_review']
        },
        'reject': {
            'label': 'Reject',
            'icon': 'XMarkIcon',
            'color': 'red',
            'permission_required': 'can_approve_datasheet',
            'visible_for_status': ['under_review']
        },
        'quality_check': {
            'label': 'Run Quality Check',
            'icon': 'SparklesIcon',
            'color': 'indigo',
            'permission_required': 'can_run_quality_check',
            'visible_for_status': ['draft', 'under_review', 'revision_required']
        },
        'download_pdf': {
            'label': 'Download PDF',
            'icon': 'ArrowDownTrayIcon',
            'color': 'gray',
            'permission_required': 'can_view_datasheet',
            'visible_for_status': 'all'
        }
    },
    
    'metadata_display_config': {
        'show_created_info': True,
        'show_updated_info': True,
        'show_revision_info': True,
        'show_approval_info': True,
        'date_format': 'YYYY-MM-DD HH:mm',
        'show_user_names': True,
        'show_user_roles': True
    }
}

# API response configuration
API_RESPONSE_CONFIG = {
    'include_metadata': True,
    'include_revision_history': True,
    'include_comments': True,
    'include_attachments': True,
    'include_quality_metrics': True,
    'include_equipment_type_details': True,
    'paginate_comments': True,
    'comments_per_page': 10,
    'max_revision_history_items': 20
}

# Permission-based field access
FIELD_PERMISSIONS = {
    'view_basic_info': ['authenticated'],
    'view_technical_specs': ['engineering', 'technical_lead'],
    'view_compliance_data': ['quality_assurance', 'engineering'],
    'view_financial_data': ['procurement', 'project_manager'],
    'edit_basic_info': ['engineering', 'document_controller'],
    'edit_technical_specs': ['engineering'],
    'edit_compliance_data': ['quality_assurance'],
    'approve_datasheet': ['technical_lead', 'engineering_manager'],
    'reject_datasheet': ['technical_lead', 'engineering_manager']
}


def get_display_mode_config(mode='detailed'):
    """
    Get display configuration for specified mode
    
    Args:
        mode: Display mode (compact, detailed, full)
    
    Returns:
        dict: Display configuration
    """
    return DATASHEET_VIEW_CONFIG['display_modes'].get(mode, DATASHEET_VIEW_CONFIG['display_modes']['detailed'])


def get_status_display_info(status):
    """
    Get display information for datasheet status
    
    Args:
        status: Datasheet status
    
    Returns:
        dict: Status display configuration
    """
    return DATASHEET_VIEW_CONFIG['status_display'].get(status, {
        'color': 'gray',
        'icon': 'QuestionMarkCircleIcon',
        'label': status.title(),
        'description': f'Status: {status}'
    })


def get_compliance_score_info(score):
    """
    Get display information for compliance score
    
    Args:
        score: Compliance score (0-100)
    
    Returns:
        dict: Score display configuration
    """
    if score is None:
        return {
            'range': 'not_checked',
            'color': 'gray',
            'label': 'Not Checked'
        }
    
    score = float(score)
    ranges = DATASHEET_VIEW_CONFIG['compliance_score_ranges']
    
    for range_name, config in ranges.items():
        if score >= config['min']:
            return {
                'range': range_name,
                'color': config['color'],
                'label': config['label']
            }
    
    return ranges['critical']


def get_visible_actions(status, user_permissions):
    """
    Get visible action buttons based on status and user permissions
    
    Args:
        status: Current datasheet status
        user_permissions: List of user permissions
    
    Returns:
        list: List of visible action configurations
    """
    visible_actions = []
    actions = DATASHEET_VIEW_CONFIG['action_buttons_config']
    
    for action_id, config in actions.items():
        # Check permission
        if config['permission_required'] not in user_permissions:
            continue
        
        # Check status visibility
        visible_statuses = config['visible_for_status']
        if visible_statuses != 'all' and status not in visible_statuses:
            continue
        
        visible_actions.append({
            'id': action_id,
            **config
        })
    
    return visible_actions


def get_field_groups_for_equipment(equipment_type_code):
    """
    Get field groups customized for specific equipment type
    
    Args:
        equipment_type_code: Equipment type code (EM, EC, etc.)
    
    Returns:
        dict: Customized field groups
    """
    base_groups = DATASHEET_VIEW_CONFIG['field_display_groups'].copy()
    
    # Customize technical specs based on equipment type
    if equipment_type_code == 'EM':  # Motors
        base_groups['technical_specs']['fields'] = [
            'power_rating', 'voltage', 'current', 'frequency', 
            'speed', 'efficiency', 'power_factor', 'poles'
        ]
    elif equipment_type_code == 'EC':  # Cables
        base_groups['technical_specs']['fields'] = [
            'cable_size', 'voltage_rating', 'current_rating',
            'conductor_material', 'insulation_type', 'temperature_rating'
        ]
    elif equipment_type_code == 'ET':  # Transformers
        base_groups['technical_specs']['fields'] = [
            'power_rating', 'primary_voltage', 'secondary_voltage',
            'frequency', 'impedance', 'cooling_type'
        ]
    
    return base_groups


def should_show_field(field_name, user_permissions, equipment_type=None):
    """
    Determine if a field should be shown based on permissions
    
    Args:
        field_name: Name of the field
        user_permissions: List of user permissions
        equipment_type: Equipment type (optional)
    
    Returns:
        bool: True if field should be shown
    """
    # Check field-specific permissions
    for permission_type, required_permissions in FIELD_PERMISSIONS.items():
        if field_name in permission_type and not any(perm in user_permissions for perm in required_permissions):
            return False
    
    return True