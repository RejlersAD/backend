"""EXEC-ACCESS-01: tab grants supplement Executive and source read grants."""
from rest_framework.exceptions import PermissionDenied, ValidationError
from rest_framework.permissions import BasePermission

TAB_ACTIONS = {
    'overview': 'view_overview',
    'financial': 'view_financial',
    'portfolio': 'view_portfolio',
    'commercial': 'view_commercial',
    'workforce': 'view_workforce',
    'risk': 'view_risk',
}
TABS = tuple(TAB_ACTIONS)
LEGACY_TABS = {'commercial', 'workforce', 'risk'}


def has_tab_grant(profile):
    """A tab grant supplies Executive entry only, never a source/write grant."""
    return any(profile.has_permission(f'executive_dashboard.{action}')
               for action in TAB_ACTIONS.values())


def allowed_tabs(user):
    from apps.rbac.action_policy import module_action_allowed
    profile = getattr(user, 'rbac_profile', None)
    if not profile or not module_action_allowed(user, 'executive_dashboard', 'read'):
        return []
    # Preserve legacy broad readers without giving those tabs to tab-only users.
    legacy_reader = module_action_allowed(user, 'executive_dashboard', 'read', include_executive_tabs=False)
    result = []
    for tab, action in TAB_ACTIONS.items():
        code = f'executive_dashboard.{action}'
        override = profile.permission_overrides.filter(permission__code=code, permission__is_active=True).first()
        if profile.has_permission(code) or (tab in LEGACY_TABS and legacy_reader and override is None):
            result.append(tab)
    return result


class HasExecutiveTabAccess(BasePermission):
    message = 'You do not have permission to view this Executive tab.'

    def has_permission(self, request, view):
        return bool(set(getattr(view, 'executive_tabs', ())) & set(allowed_tabs(request.user)))


def filter_executive_report(report, user, selected=None):
    tabs = allowed_tabs(user)
    if selected is not None:
        if selected not in TABS:
            raise ValidationError({'tab': 'Unknown Executive tab.'})
        if selected not in tabs:
            raise PermissionDenied('You do not have permission to view this Executive tab.')
    result = dict(report, allowed_tabs=tabs)
    if 'overview' not in tabs:
        result.update(kpis=[], departments=[], actions=[], action_count=0,
                      actions_returned=0, actions_truncated=False,
                      portfolio={'status': 'restricted', 'counts': None, 'projects': []},
                      workforce={'status': 'restricted', 'allocation': []},
                      coverage={'available_departments': 0, 'total_departments': 0})
    if 'financial' not in tabs:
        result.pop('financial_performance', None)
    for tab, key in (('commercial', 'commercial_performance'), ('workforce', 'workforce_performance'),
                     ('risk', 'risk_compliance')):
        if tab not in tabs:
            result.pop(key, None)
    if 'portfolio' not in tabs:
        # Overview consumes the operational portfolio summary, not the dedicated
        # workbook or revenue report. Its own source checks still apply.
        if 'overview' in tabs and result.get('portfolio_performance'):
            result['portfolio_performance'] = {
                key: value for key, value in result['portfolio_performance'].items()
                if key not in {'workbook', 'revenue_dashboard', 'source'}
            }
        else:
            result.pop('portfolio_performance', None)
    return result
