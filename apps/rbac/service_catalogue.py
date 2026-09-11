"""Individually assignable business services; parent codes are grouping metadata."""

SERVICE_MODULES = [
    ('finance_salary', 'Salary Slips', 'finance', 'View salary slips subject to payroll record permissions'),
    ('finance_overview', 'Finance Overview', 'finance', 'Combined accounts payable and receivable summary'),
    ('finance_incoming', 'Incoming Invoices', 'finance', 'Supplier invoices, matching and payment approvals'),
    ('finance_outgoing', 'Outgoing Invoices', 'finance', 'Customer invoices, collections and attachments'),
    ('sales_overview', 'Sales Overview', 'sales', 'Sales dashboard and aggregate performance'),
    ('sales_opportunities', 'Sales Opportunities', 'sales', 'Opportunity pipeline and activity management'),
    ('sales_proposals', 'Sales Proposals', 'sales', 'Proposal preparation, approval and submission'),
    ('sales_clients', 'Sales Clients & Contacts', 'sales', 'Client accounts and contacts'),
    ('sales_frameworks', 'Sales Framework Agreements', 'sales', 'Framework and call-off agreements'),
    ('sales_forecasts', 'Sales Forecasts', 'sales', 'Revenue forecasting and planning'),
    ('sales_handovers', 'Sales Project Handovers', 'sales', 'Awarded-project handover into delivery'),
    ('sales_email_intake', 'Sales Email Intake', 'sales', 'Review incoming leads and manage mailbox connections'),
]

SERVICE_PARENTS = {code: parent for code, _, parent, _ in SERVICE_MODULES}

# ModelViewSet permission checks run before queryset/action execution, including
# all custom actions. Summary actions have their own read-only service grant.
VIEW_SERVICE_MODULES = {
    'InvoiceViewSet': 'finance_incoming',
    'ApprovalRouteViewSet': 'finance_incoming',
    'CustomerInvoiceViewSet': 'finance_outgoing',
    'InvoiceAttachmentViewSet': 'finance_outgoing',
    'SalesDashboardViewSet': 'sales_overview',
    'SalesAIInsightsView': 'sales_overview',
    'ClientViewSet': 'sales_clients',
    'ContactViewSet': 'sales_clients',
    'SalesClientsAnalyticsView': 'sales_clients',
    'DealViewSet': 'sales_opportunities',
    'SalesActivityViewSet': 'sales_opportunities',
    'SalesPipelineAnalyticsView': 'sales_opportunities',
    'SalesActivitiesView': 'sales_opportunities',
    'QuoteViewSet': 'sales_proposals',
    'FrameworkAgreementViewSet': 'sales_frameworks',
    'SalesForecastViewSet': 'sales_forecasts',
    'ProjectHandoverViewSet': 'sales_handovers',
    'SalesMailboxConnectionViewSet': 'sales_email_intake',
    'SalesEmailIntakeViewSet': 'sales_email_intake',
}


def required_service(view):
    """Resolve only the registered Finance/Sales views, not similarly named apps."""
    if not view.__class__.__module__.startswith(('apps.finance.', 'apps.sales.', 'apps.invoice_tracker.')):
        return getattr(view, 'module_required', None)
    if view.__class__.__name__ == 'InvoiceViewSet' and getattr(view, 'action', None) == 'combined_summary':
        return 'finance_overview'
    return VIEW_SERVICE_MODULES.get(view.__class__.__name__, getattr(view, 'module_required', None))
