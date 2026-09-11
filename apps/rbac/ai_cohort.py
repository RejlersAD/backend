"""Explicit current eligibility, reused by prospective snapshot capture."""
from django.conf import settings
from django.db.models import Prefetch
from .models import Module, Role, UserProfile
from .rbac_config import is_module_enabled

DEFAULT_MODULES = {
    'planning_package': ['planning_intelligence', 'planning-package', 'planning_package'],
    'pid_analysis': ['pid_analysis', 'pid-analysis', 'pid-verification'],
    'pfd_to_pid': ['pfd_to_pid', 'pfd-to-pid'],
    'pfd_quality': ['pfd_quality', 'pfd-quality'],
    'designiq': ['designiq', 'design_iq'],
    'crs_documents': ['crs_documents', 'crs-documents'],
}


def current_cohort(user_ids=None, organization_id=None):
    mapping = getattr(settings, 'AI_ADOPTION_MODULE_APPLICATIONS', DEFAULT_MODULES)
    modules = list(Module.objects.filter(code__in=mapping, is_active=True))
    modules = [m for m in modules if is_module_enabled(m.code)]
    codes = {m.code for m in modules}
    applications = {a for code in codes for a in mapping[code]}
    profiles = UserProfile.objects.filter(is_deleted=False, status='active', user__is_active=True)
    if organization_id is not None:
        profiles = profiles.filter(organization_id=organization_id)
    if user_ids is not None:
        profiles = profiles.filter(user_id__in=user_ids)
    profiles = list(profiles.select_related('user', 'canonical_employee', 'organization', 'manager__user')
                    .prefetch_related(Prefetch('roles', queryset=Role.objects.filter(is_active=True).prefetch_related('modules'))))
    eligible = {}
    unlinked = inactive_employee = excluded = no_access = 0
    from .discipline_config import DisciplineAccessConfig
    global_codes = set(DisciplineAccessConfig.get_globally_enabled_module_codes()) & codes
    for profile in profiles:
        if (profile.metadata or {}).get('ai_adoption_excluded') is True:
            excluded += 1
            continue
        if not profile.canonical_employee_id:
            unlinked += 1
            continue
        if profile.canonical_employee.employment_status != 'active':
            inactive_employee += 1
            continue
        roles = list(profile.roles.all())
        grants = codes if profile.user.is_superuser or any(r.code == 'super_admin' for r in roles) else (
            {m.code for r in roles for m in r.modules.all()} | global_codes) & codes
        if not grants:
            no_access += 1
            continue
        # Never expose a manager from another organization in an org-scoped report.
        manager = profile.manager if profile.manager_id and profile.manager.organization_id == profile.organization_id else None
        eligible[profile.user_id] = {
            'name': profile.user.get_full_name() or profile.user.email,
            'grants': sorted(grants),
            'department': profile.department.strip() or 'Not specified',
            'organization_id': str(profile.organization_id), 'organization': profile.organization.name,
            'manager_id': str(manager.pk) if manager else 'unassigned',
            'manager': (manager.user.get_full_name() or manager.user.email) if manager else 'Not assigned',
        }
    return eligible, {'unlinked_accounts': unlinked, 'inactive_employees': inactive_employee,
                      'excluded_accounts': excluded, 'employees_without_module_access': no_access}, modules, mapping
