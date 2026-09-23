"""Read-only cross-department connections for already-authorized workbook rows.

Workbook identities never create or update operational projects. Saved canonical
foreign keys remain authoritative; QHSE and receivables use explicitly identified,
unambiguous project codes because those registers do not have canonical FKs.
"""
from collections import defaultdict
from urllib.parse import urlencode
import logging

from django.apps import apps
from django.core.exceptions import FieldError
from django.db import DatabaseError, transaction
from django.db.models import Count

from apps.core.project_models import Project
from apps.project_control.access import accessible_enterprise_projects
from apps.procurement.services.project_relationships import (
    normalize_project_code, resolve_enterprise_project_by_code,
)
from apps.rbac.action_policy import module_action_allowed
from apps.rbac.data_visibility_mixin import build_visibility_filter


logger = logging.getLogger(__name__)
DEPARTMENTS = (
    ('project_control', 'Project Control'), ('procurement', 'Procurement'),
    ('sales', 'Sales'), ('finance', 'Finance'), ('qhse', 'QHSE'),
)


def _department(key, *, status='unlinked', count=0, url=None, counts=None, **extra):
    return {'key': key, 'label': dict(DEPARTMENTS)[key], 'status': status,
            'record_count': count, 'url': url, **({'counts': counts} if counts is not None else {}),
            **extra}


def _group_counts(queryset, field):
    # Group only the FK column, not model PK + joined model columns. Some legacy
    # PostgreSQL tables lack the PK constraint needed for functional dependency.
    return {str(row[field]): row['count'] for row in
            queryset.order_by().values(field).annotate(count=Count('pk'))}


def _visible(queryset, user, module, owner=None):
    return queryset.filter(build_visibility_filter(
        user=user, module_code=module, owner_field=owner, model_class=queryset.model,
    )).distinct()


def _project_control(user, projects, allowed, rows):
    from apps.project_control.models import Estimate, IntegratedReportingSnapshot, ProjectDocument, WBSNode

    counts = {}
    for key, model in [('wbs_nodes', WBSNode), ('estimates', Estimate), ('documents', ProjectDocument)]:
        counts[key] = _group_counts(model.objects.filter(project_id__in=projects, is_deleted=False), 'project_id')
    counts['reporting_snapshots'] = _group_counts(
        IntegratedReportingSnapshot.objects.filter(project_id__in=projects), 'project_id')
    result = {}
    for key in projects:
        values = {'registered_projects': 1, **{name: value.get(key, 0) for name, value in counts.items()}}
        result[key] = _department('project_control', status='linked', count=sum(values.values()),
                                  counts=values, url='/projects?' + urlencode({'project': key}))
    return result, _department('project_control')


def _procurement(user, projects, allowed, rows):
    from apps.procurement.models import Project as ProcurementProject, PurchaseOrder, PurchaseRequisition

    sources = [('project_registers', 'procurement', ProcurementProject),
               ('purchase_orders', 'procurement_orders', PurchaseOrder),
               ('purchase_requisitions', 'procurement_requisitions', PurchaseRequisition)]
    grants = {name: allowed(module) for name, module, model in sources}
    if not any(grants.values()):
        return {}, _department('procurement', status='restricted', count=None)
    counts, records = {}, {}
    for name, module, model in sources:
        if not grants[name]:
            continue
        queryset = model.objects.filter(enterprise_project_id__in=projects)
        if name == 'project_registers':
            queryset = queryset.filter(is_active=True)
        counts[name] = _group_counts(queryset, 'enterprise_project_id')
        records[name] = {}
        for row in queryset.order_by('pk').values('pk', 'enterprise_project_id'):
            records[name].setdefault(str(row['enterprise_project_id']), str(row['pk']))
    result = {}
    for key in projects:
        values = {name: counts[name].get(key, 0) if grants[name] else None for name, module, model in sources}
        count = sum(value or 0 for value in values.values())
        master = records.get('project_registers', {}).get(key)
        # The master detail URL takes Procurement's UUID, never a core project ID.
        url = '/procurement/projects/' + master if master else (
            '/procurement/orders' if grants['purchase_orders'] else
            '/procurement/requisitions' if grants['purchase_requisitions'] else '/procurement/projects')
        result[key] = _department('procurement', status='linked' if count else 'unlinked',
                                  count=count, counts=values, url=url,
                                  link_scope='project' if master else 'register')
    return result, _department('procurement')


def _sales(user, projects, allowed, rows):
    from apps.sales.models import Deal, ProjectHandover

    can_deals, can_handovers = allowed('sales_opportunities'), allowed('sales_handovers')
    if not can_deals and not can_handovers:
        return {}, _department('sales', status='restricted', count=None)
    deals, handovers = {}, {}
    if can_deals:
        deals = {str(row['converted_project_id']): str(row['pk']) for row in _visible(
            Deal.objects.filter(converted_project_id__in=projects), user, 'sales', 'owner',
        ).values('pk', 'converted_project_id')}
    if can_handovers:
        handovers = {str(row['project_id']): str(row['pk']) for row in
                     ProjectHandover.objects.filter(project_id__in=projects).values('pk', 'project_id')}
    result = {}
    for key in projects:
        values = {'converted_opportunities': int(key in deals) if can_deals else None,
                  'project_handovers': int(key in handovers) if can_handovers else None}
        count = sum(value or 0 for value in values.values())
        url = ('/sales/opportunities?' + urlencode({'record': deals[key]}) if key in deals else
               '/sales/project-handovers?' + urlencode({'record': handovers[key]}) if key in handovers else
               '/sales/opportunities' if can_deals else '/sales/project-handovers')
        result[key] = _department('sales', status='linked' if count else 'unlinked',
                                  count=count, counts=values, url=url,
                                  link_scope='record' if count else 'register')
    return result, _department('sales')


def _finance(user, projects, allowed, rows):
    if not allowed('finance_outgoing'):
        return {}, _department('finance', status='restricted', count=None)
    from .recorded_invoices import build_recorded_invoices, normalize_invoice_project_code

    report = build_recorded_invoices(user, rows, full_source=False, limit=1)
    if report.get('status') in {'error', 'unavailable', 'restricted'}:
        return {}, _department('finance', status=report['status'], count=None)
    groups = {normalize_invoice_project_code(item['project_number']): item for item in report.get('project_groups', [])}
    result = {}
    for key, project in projects.items():
        group = groups.get(normalize_invoice_project_code(project.code), {})
        count, conflicts = group.get('matched_invoice_count', 0), group.get('conflict_count', 0)
        result[key] = _department('finance', status='review_required' if conflicts else 'linked' if count else 'unlinked',
                                  count=count, counts={'customer_invoices': count, 'conflicting_invoices': conflicts},
                                  url=group.get('register_route') or '/finance/outgoing-invoices?' + urlencode(
                                      {'queue': 'all', 'project_exact': project.code}),
                                  link_scope='project', match_basis='exact_project_code')
    return result, _department('finance')


def _qhse(user, projects, allowed, rows):
    from apps.qhse.models import QHSERunningProject

    areas = [('qhse_detailed', '/qhse/general/detailed'), ('qhse_quality', '/qhse/general/quality'),
             ('qhse_health_safety', '/qhse/general/health-safety'),
             ('qhse_environmental', None), ('qhse_energy', None)]
    # Environmental/energy frontend routes are disabled; never fabricate links.
    area_grants = [(module, route) for module, route in areas if allowed(module)]
    area_route = next((route for module, route in area_grants if route), None)
    base_allowed = allowed('qhse')
    if not area_grants and not base_allowed:
        return {}, _department('qhse', status='restricted', count=None)
    queryset = QHSERunningProject.objects.filter(is_active=True)
    # Area-specific endpoints expose this shared team register under their own
    # grants. The base endpoint keeps its existing team visibility policy.
    if not area_grants:
        queryset = _visible(queryset, user, 'qhse')
    index = defaultdict(list)
    for row in queryset.values('pk', 'project_no'):
        index[normalize_project_code(row['project_no'])].append(row['pk'])
    result = {}
    for key, project in projects.items():
        candidates = index.get(normalize_project_code(project.code), [])
        ambiguous = len(candidates) > 1
        result[key] = _department('qhse', status='review_required' if ambiguous else 'linked' if candidates else 'unlinked',
                                  count=None if ambiguous else len(candidates),
                                  url=area_route or ('/qhse' if base_allowed else None), link_scope='register',
                                  match_basis='exact_project_code',
                                  description='QHSE uses a separate project-number register; this is an exact code association.')
    return result, _department('qhse')


def build_project_connections(user, rows, *, limit=200, offset=0):
    """Match all authorized rows before pagination; never mutate source records."""
    permission_cache = {}
    limit, offset = max(0, min(limit, 200)), max(0, offset)

    def allowed(module):
        if module not in permission_cache:
            permission_cache[module] = module_action_allowed(user, module, 'read')
        return permission_cache[module]

    if not allowed('project_control'):
        return {'status': 'restricted', 'totals': {key: None for key in (
            'registered_projects', 'workbook_projects', 'matched_projects', 'matched_rows',
            'unmatched_rows', 'ambiguous_rows', 'restricted_rows')}, 'rows': [],
            'total_rows': None, 'returned_rows': 0, 'truncated': False, 'limit': limit, 'offset': offset}

    visible = {str(project.pk): project for project in accessible_enterprise_projects(user).only('id', 'code', 'name')}
    index = defaultdict(list)
    # Include deleted and hidden identities in collision/shadow checks. They must
    # block fallback to an accessible parent, without exposing their attributes.
    for project in Project.objects.only('id', 'code', 'name', 'is_deleted'):
        code = normalize_project_code(project.code)
        if code:
            index[code].append(project)
    totals = {'registered_projects': len(visible),
              'workbook_projects': len({normalize_project_code(row.get('project_code')) for row in rows
                                        if normalize_project_code(row.get('project_code'))}),
              'matched_projects': 0, 'matched_rows': 0, 'unmatched_rows': 0,
              'ambiguous_rows': 0, 'restricted_rows': 0}
    subproject_parents = defaultdict(set)
    for row in rows:
        subproject = normalize_project_code(row.get('subproject_code'))
        parent = normalize_project_code(row.get('project_code'))
        if subproject and parent:
            subproject_parents[subproject].add(parent)
    from .recorded_invoices import source_identity_collisions

    # Inspect identity fields in the pinned immutable snapshot, including rows
    # outside this filter/visibility scope. These keys can only suppress unsafe
    # matches; never return the hidden identities or their aggregate count.
    source_collisions = source_identity_collisions(rows, normalize=normalize_project_code)
    connections, matched = [], {}
    for row in rows:
        state, level, project = 'unmatched', None, None
        for field, candidate_level in [('subproject_code', 'subproject'), ('project_code', 'parent')]:
            code = normalize_project_code(row.get(field))
            if code in source_collisions or (
                candidate_level == 'subproject' and len(subproject_parents.get(code, ())) > 1
            ):
                state, level = 'ambiguous', candidate_level
                break
            candidates = index.get(code, []) if code else []
            if not candidates:
                continue
            # Do not let the resolver's empty-index default reload only live rows.
            candidate, result = resolve_enterprise_project_by_code(code, index=index)
            level = candidate_level
            if result == 'multiple_projects':
                state = 'ambiguous'
            elif candidate.is_deleted or str(candidate.pk) not in visible:
                state = 'restricted'
            else:
                state, project = 'matched', visible[str(candidate.pk)]
                matched[str(project.pk)] = project
            break
        totals[state + '_rows'] += 1
        connections.append({
            'portfolio_row_id': row.get('id', row.get('portfolio_row_id')),
            'project_code': row.get('project_code', ''), 'subproject_code': row.get('subproject_code', ''),
            'title': row.get('title', ''), 'match_status': state, 'match_level': level,
            'project': {'id': str(project.pk), 'code': project.code, 'name': project.name,
                        'url': '/projects?' + urlencode({'project': str(project.pk)})} if project else None,
            'departments': [],
        })
    totals['matched_projects'] = len(matched)
    page = connections[offset:offset + limit]
    page_projects = {row['project']['id']: matched[row['project']['id']] for row in page if row['project']}
    departments = {}
    if page_projects:
        for key, builder in [('project_control', _project_control), ('procurement', _procurement),
                             ('sales', _sales), ('finance', _finance), ('qhse', _qhse)]:
            app_name = 'invoice_tracker' if key == 'finance' else key
            if not apps.is_installed('apps.' + app_name):
                departments[key] = ({}, _department(key, status='unavailable', count=None))
                continue
            try:
                with transaction.atomic():
                    departments[key] = builder(user, page_projects, allowed, rows)
            except (DatabaseError, FieldError, ValueError):
                # A missing optional table must not relabel a project or make an
                # unavailable department look like a known empty register.
                logger.warning('Portfolio connection register unavailable: %s', key)
                departments[key] = ({}, _department(key, status='unavailable', count=None))
    for row in page:
        if row['project']:
            key = row['project']['id']
            row['departments'] = [departments[name][0].get(key, departments[name][1]) for name, label in DEPARTMENTS]
    return {'status': 'available', 'totals': totals, 'rows': page,
            'total_rows': len(connections), 'returned_rows': len(page), 'offset': offset, 'limit': limit,
            'truncated': offset + len(page) < len(connections),
            'registered_projects_url': '/projects?view=portfolio',
            'description': 'Registered projects are distinct accessible enterprise records. Workbook projects count parent codes; '
                           'matched projects count distinct canonical records. Parent matches do not create subprojects. '
                           'Department counts include only accessible recorded links; registers remain unchanged.'}
