from django.db import migrations

SERVICE_MODULES = [('finance_overview', 'Finance Overview', 'finance', 'Combined accounts payable and receivable summary'), ('finance_incoming', 'Incoming Invoices', 'finance', 'Supplier invoices, matching and payment approvals'), ('finance_outgoing', 'Outgoing Invoices', 'finance', 'Customer invoices, collections and attachments'), ('sales_overview', 'Sales Overview', 'sales', 'Sales dashboard and aggregate performance'), ('sales_opportunities', 'Sales Opportunities', 'sales', 'Opportunity pipeline and activity management'), ('sales_proposals', 'Sales Proposals', 'sales', 'Proposal preparation, approval and submission'), ('sales_clients', 'Sales Clients & Contacts', 'sales', 'Client accounts and contacts'), ('sales_frameworks', 'Sales Framework Agreements', 'sales', 'Framework and call-off agreements'), ('sales_forecasts', 'Sales Forecasts', 'sales', 'Revenue forecasting and planning'), ('sales_handovers', 'Sales Project Handovers', 'sales', 'Awarded-project handover into delivery'), ('sales_email_intake', 'Sales Email Intake', 'sales', 'Review incoming leads and manage mailbox connections')]


def refresh_service_access(apps, schema_editor):
    Module = apps.get_model('rbac', 'Module')
    RoleModule = apps.get_model('rbac', 'RoleModule')
    UserProfile = apps.get_model('rbac', 'UserProfile')
    db = schema_editor.connection.alias
    for index, (code, name, parent, description) in enumerate(SERVICE_MODULES):
        Module.objects.using(db).get_or_create(code=code, defaults={
            'name': name, 'description': description, 'icon': 'LayoutGrid',
            'order': 900 + index, 'is_active': True,
        })
    Module.objects.using(db).get_or_create(code='ai_champion', defaults={
        'name': 'AI Champion', 'icon': 'Trophy', 'order': 54, 'is_active': True,
        'description': 'AI engagement leaderboard and recognition',
    })
    # Revoke only the historical universal grant, preserving explicit roles.
    RoleModule.objects.using(db).filter(
        role__code='default', module__code='enquiry_management',
    ).delete()
    from django.core.cache import cache
    ids = UserProfile.objects.using(db).values_list('pk', flat=True)
    cache.delete_many([key for pk in ids for key in (f'user_modules_{pk}', f'user_permissions_{pk}')])


class Migration(migrations.Migration):
    dependencies = [('rbac', '0050_userprofile_signature')]
    operations = [migrations.RunPython(refresh_service_access, migrations.RunPython.noop)]
