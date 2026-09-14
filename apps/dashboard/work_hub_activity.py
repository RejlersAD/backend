"""Personal work-action projection, deliberately separate from request telemetry.

ActivityMiddleware emits ``api_request`` for every call, including tracking POSTs
and photo/session GETs. Their HTTP success is not evidence of a business outcome.
Only explicit action types are eligible, and raw descriptions/metadata are never
used as display text or routes. Add producers here after verifying their meaning.
"""


# Successful title, action noun (also used for failures), precise source label.
WORK_ACTIONS = {
    'document_uploaded': ('Document uploaded', 'Document upload', 'Documents'),
    'document_processed': ('Document processed', 'Document processing', 'Documents'),
    'document_deleted': ('Document deleted', 'Document deletion', 'Documents'),
    'project_created': ('Project created', 'Project creation', 'Projects'),
    'project_updated': ('Project updated', 'Project update', 'Projects'),
    'project_deleted': ('Project deleted', 'Project deletion', 'Projects'),
    'data_export': ('Data exported', 'Data export', 'Data management'),
    'data_import': ('Data imported', 'Data import', 'Data management'),
    'report_generated': ('Report generated', 'Report generation', 'Reporting'),
    'ai_analysis': ('AI analysis recorded', 'AI analysis', 'AI tools'),
    'ml_prediction': ('Prediction generated', 'Prediction generation', 'AI tools'),
    'settings_changed': ('Settings changed', 'Settings change', 'Workspace settings'),
    'user_created': ('User account created', 'User account creation', 'User administration'),
    'user_updated': ('User account updated', 'User account update', 'User administration'),
    'user_deleted': ('User account deleted', 'User account deletion', 'User administration'),
    'role_assigned': ('Role assigned', 'Role assignment', 'Access management'),
    'role_removed': ('Role removed', 'Role removal', 'Access management'),
    'permission_granted': ('Permission granted', 'Permission grant', 'Access management'),
    'permission_revoked': ('Permission revoked', 'Permission revocation', 'Access management'),
    # Emitted after persist_generation in electrical_datasheet.views._smart_persist.
    'datasheet_generated': ('Electrical datasheet generated', 'Electrical datasheet generation', 'Electrical Engineering'),
}

# These structured equipment codes are supported by the smart generator. Unknown
# codes keep the generic title; never render arbitrary metadata as a human label.
EQUIPMENT_LABELS = {
    'transformer': 'Transformer',
    'dg_set': 'Diesel generator',
    'mv_switchgear': 'MV switchgear',
    'lv_switchgear': 'LV switchgear',
    'ac_ups': 'AC UPS',
    'dc_ups': 'DC UPS',
}


def work_action_records(Activity, user, start, end):
    """One filter shared by preview, exact count and daily aggregation."""
    return Activity.objects.filter(user=user, timestamp__gte=start, timestamp__lte=end,
                                   activity_type__in=WORK_ACTIONS).exclude(category='api')


def project_work_action(row):
    title, action, source = WORK_ACTIONS[row.activity_type]
    if row.activity_type == 'datasheet_generated':
        details = row.details if isinstance(row.details, dict) else {}
        code = details.get('equipment_type')
        equipment = EQUIPMENT_LABELS.get(code) if isinstance(code, str) else None
        if equipment:
            title, action = f'{equipment} datasheet generated', f'{equipment} datasheet generation'
    if not row.success:
        title = f'{action} unsuccessful'
    outcome = 'successful' if row.success else 'unsuccessful'
    return {'id': row.pk, 'type': row.activity_type, 'category': row.category,
            'title': title, 'source_label': source, 'basis': 'recorded_work_action',
            'status_label': 'Recorded' if row.success else 'Failed',
            'description': f'{action} was recorded as {outcome}.',
            'timestamp': row.timestamp.isoformat(), 'success': row.success, 'route': None}
