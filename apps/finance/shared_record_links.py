"""Reviewed client/project references without changing recorded Finance facts."""
from django.db.models import BooleanField, Case, Count, F, Q, Value, When
from django.db.models.fields.json import KeyTextTransform
from django.db.models.functions import Trim
from rest_framework.exceptions import PermissionDenied, ValidationError

from apps.core.shared_record_targets import (
    candidate_payload, project_client_id, require_target, search_targets,
    validate_project_client, visible_clients, visible_projects,
)
from apps.invoice_tracker.models import CustomerInvoice
from apps.rbac.action_policy import module_action_allowed

from .receivables_source_models import (
    ReceivablesSourceIdentity, ReceivablesSourceRow, ReceivablesSourceSnapshot,
)


IDENTITY_FIELDS = (
    'invoice_number', 'category', 'company', 'account', 'rad_project_no',
    'project_id', 'project_name',
)
TARGET_KEYS = {'project_id', 'client_id'}


def identity_basis(row):
    """Keep the exact source spelling, not an inferred client or external ID."""
    return {field: getattr(row, field) for field in IDENTITY_FIELDS}


def _allowed(user, action):
    return bool(user and user.is_authenticated and user.is_active
                and module_action_allowed(user, 'finance_outgoing', action))


def _mapping(row):
    if isinstance(row, CustomerInvoice):
        return row
    try:
        return row.canonical_identity
    except ReceivablesSourceIdentity.DoesNotExist:
        return None


def _stored_ids(mapping):
    return {
        'project_id': str(mapping.canonical_project_id) if mapping and mapping.canonical_project_id else None,
        'client_id': str(mapping.canonical_client_id) if mapping and mapping.canonical_client_id else None,
    }


def canonical_links(row, user):
    """Expose only current, authorized references; never replace source labels."""
    empty = {'project': None, 'client': None}
    if not _allowed(user, 'read'):
        return {'state': 'unlinked', 'links': empty}
    mapping = _mapping(row)
    ids = _stored_ids(mapping)
    if not any(ids.values()):
        return {'state': 'unlinked', 'links': empty}
    if isinstance(row, CustomerInvoice) and CustomerInvoice.objects.filter(pk=row.pk).count() != 1:
        return {'state': 'needs_review', 'links': empty,
                'warning': 'Resolve the duplicate invoice ID in the outgoing invoice register first.'}
    if mapping.canonical_identity_basis != identity_basis(row):
        return {'state': 'needs_review', 'links': empty,
                'warning': 'Source identity changed. Review the recorded labels before linking again.'}
    project = (visible_projects(user).filter(pk=ids['project_id']).first()
               if ids['project_id'] else None)
    client = (visible_clients(user).filter(pk=ids['client_id']).first()
              if ids['client_id'] else None)
    if (ids['project_id'] and project is None) or (ids['client_id'] and client is None):
        return {'state': 'needs_review', 'links': empty,
                'warning': 'A linked record is unavailable in your current access scope.'}
    if project and client and project_client_id(project) not in (None, '', str(client.pk), client.pk):
        return {'state': 'needs_review', 'links': empty,
                'warning': 'The project client changed. Review these references before using them.'}
    needs_client = bool(row.company.strip()) and client is None
    needs_project = any(getattr(row, field).strip() for field in ('rad_project_no', 'project_id', 'project_name')) and project is None
    return {'state': 'needs_review' if needs_client or needs_project else 'linked', 'links': {
        'project': candidate_payload(project, 'project') if project else None,
        'client': candidate_payload(client, 'client') if client else None,
    }, **({'warning': 'Some recorded customer or project references still need a canonical link.'}
          if needs_client or needs_project else {})}


def project_register_links(rows, user, *, source=False):
    """Batch source reads for existing paged register projections."""
    rows = list(rows)
    model = ReceivablesSourceRow if source else CustomerInvoice
    related = ('canonical_identity',) if source else ()
    queryset = model.objects.filter(pk__in=[row['id'] for row in rows])
    if related:
        queryset = queryset.select_related(*related)
    records = {}
    duplicates = set()
    for record in queryset:
        if record.pk in records:
            duplicates.add(record.pk)
        records[record.pk] = record
    for row in rows:
        record = records.get(row['id'])
        if row['id'] in duplicates:
            row['canonical_links'] = {
                'state': 'needs_review', 'links': {'project': None, 'client': None},
                'warning': 'Resolve the duplicate invoice ID in the outgoing invoice register first.',
            }
        elif record is not None:
            row['canonical_links'] = canonical_links(record, user)
        else:
            row['canonical_links'] = {'state': 'unlinked', 'links': {'project': None, 'client': None}}
    return rows


class FinanceIdentityAdapter:
    target_kinds = ('project', 'client')
    source = False

    def lock_scope(self, row, user, targets):
        """Lock canonical project parents before source rows, in stable order."""
        from apps.core.project_models import Project
        mapping = _mapping(row)
        identifiers = {
            identifier for identifier in (
                getattr(mapping, 'canonical_project_id', None), targets.get('project_id'),
            ) if identifier
        }
        for identifier in identifiers:
            require_target(user, 'project', identifier)
        list(Project.objects.filter(pk__in=identifiers).order_by('pk').select_for_update(of=('self',)))

    def queryset(self, user):
        if not _allowed(user, 'read'):
            return (ReceivablesSourceRow if self.source else CustomerInvoice).objects.none()
        if self.source:
            # Pin count and paged rows to the same publication. A later command
            # independently checks that this source is still active.
            snapshot_id = (ReceivablesSourceSnapshot.objects.filter(is_active=True)
                           .values_list('pk', flat=True).first())
            queryset = ReceivablesSourceRow.objects.filter(snapshot_id=snapshot_id)
        else:
            # A restored legacy register can lack a physical primary-key
            # constraint. Its dedicated duplicate review owns that recovery.
            duplicate_ids = (CustomerInvoice.objects.filter(pk__isnull=False).order_by().values('pk')
                             .annotate(copies=Count('*')).filter(copies__gt=1).values('pk'))
            queryset = CustomerInvoice.objects.exclude(pk__in=duplicate_ids).filter(pk__isnull=False)
        prefix = 'canonical_identity__' if self.source else ''
        for kind, visible in [('client', visible_clients(user)), ('project', visible_projects(user))]:
            field = prefix + 'canonical_' + kind
            queryset = queryset.annotate(**{
                '_canonical_' + kind + '_visible': Case(
                    When(**{field + '__isnull': True}, then=Value(True)),
                    When(**{field + '__in': visible.values('pk')}, then=Value(True)),
                    default=Value(False), output_field=BooleanField(),
                ),
            })
        return queryset

    def filter_queryset(self, queryset, status, search):
        prefix = 'canonical_identity__' if self.source else ''
        linked = (Q(**{prefix + 'canonical_project__isnull': False})
                  | Q(**{prefix + 'canonical_client__isnull': False}))
        fresh = Q()
        for field in IDENTITY_FIELDS:
            alias = '_identity_' + field
            queryset = queryset.annotate(**{
                alias: KeyTextTransform(field, prefix + 'canonical_identity_basis'),
            })
            fresh &= Q(**{alias: F(field)}) & Q(**{alias + '__isnull': False})
        for field in ('company', 'rad_project_no', 'project_id', 'project_name'):
            queryset = queryset.annotate(**{'_identity_required_' + field: Trim(field)})
        complete = (
            (Q(_identity_required_company='') | Q(**{prefix + 'canonical_client__isnull': False}))
            & ((Q(_identity_required_rad_project_no='') & Q(_identity_required_project_id='')
                & Q(_identity_required_project_name='')) | Q(**{prefix + 'canonical_project__isnull': False}))
        )
        coherent = (
            Q(**{prefix + 'canonical_project__isnull': True})
            | Q(**{prefix + 'canonical_client__isnull': True})
            | Q(**{prefix + 'canonical_project__client__isnull': True})
            | Q(**{prefix + 'canonical_project__client_id': F(prefix + 'canonical_client_id')})
        )
        resolved = linked & fresh & complete & coherent & Q(
            _canonical_client_visible=True, _canonical_project_visible=True,
        )
        if status == 'linked':
            queryset = queryset.filter(resolved)
        elif status in {'unlinked', 'needs_review'}:
            queryset = queryset.exclude(resolved)
        if search:
            queryset = queryset.filter(
                Q(invoice_number__icontains=search) | Q(company__icontains=search)
                | Q(rad_project_no__icontains=search) | Q(project_name__icontains=search)
            )
        return queryset.order_by('pk')

    def describe(self, row, user):
        projection = canonical_links(row, user)
        return {
            'source_type': self.key, 'id': str(row.pk),
            'reference': row.invoice_number,
            'label': row.company or row.project_name or row.invoice_number,
            'source_values': {
                'Company': row.company, 'Account': row.account,
                'Project number': row.rad_project_no, 'Project name': row.project_name,
                'External project ID': row.project_id,
                **({'Source snapshot': str(row.snapshot_id), 'Worksheet row': str(row.row_number)}
                   if self.source else {}),
            },
            'target_kinds': list(self.target_kinds),
            'can_link': _allowed(user, 'update') and module_action_allowed(user, 'project_control', 'update'),
            **projection,
        }

    def fingerprint(self, row):
        mapping = _mapping(row)
        return {
            'source_type': self.key, 'id': str(row.pk), 'source': identity_basis(row),
            'updated_at': row.updated_at.isoformat(), 'links': _stored_ids(mapping),
            'basis': mapping.canonical_identity_basis if mapping else {},
            **({'snapshot_id': str(row.snapshot_id),
                'mapping_updated_at': mapping.updated_at.isoformat() if mapping else None}
               if self.source else {}),
        }

    def require_write(self, row, user):
        if not _allowed(user, 'read') or not _allowed(user, 'update'):
            raise PermissionDenied('Update access to outgoing Finance records is required.')
        if self.source:
            # Coordinate review with publication: importer locks snapshots too.
            snapshot = ReceivablesSourceSnapshot.objects.select_for_update().get(pk=row.snapshot_id)
            if not snapshot.is_active:
                from apps.core.shared_records import LinkConflict
                raise LinkConflict('The published Finance source changed. Refresh the review queue.')
        elif CustomerInvoice.objects.filter(pk=row.pk).count() != 1:
            raise ValidationError('Resolve the duplicate invoice ID in the outgoing invoice register first.')

    def candidates(self, row, user, kind, search):
        if kind not in self.target_kinds:
            raise ValidationError('Select a supported Finance reference type.')
        return search_targets(user, kind, search)

    def apply(self, row, user, targets):
        self.require_write(row, user)
        if not isinstance(targets, dict) or not targets or set(targets) - TARGET_KEYS:
            raise ValidationError({'targets': 'Provide client_id and/or project_id only.'})
        mapping = _mapping(row)
        current = _stored_ids(mapping)
        # Do not preserve an unreviewed old counterpart after source identity
        # changes. The user must explicitly select it again if still applicable.
        if mapping and mapping.canonical_identity_basis != identity_basis(row):
            current = {key: None for key in TARGET_KEYS}
        selected = {**current, **targets}
        project = (require_target(user, 'project', selected['project_id'])
                   if selected.get('project_id') else None)
        client = (require_target(user, 'client', selected['client_id'], project=project)
                  if selected.get('client_id') else None)
        validate_project_client(project, client)
        if self.source:
            if mapping is None:
                mapping = ReceivablesSourceIdentity(source_row=row)
            mapping.canonical_project = project
            mapping.canonical_client = client
            mapping.canonical_identity_basis = identity_basis(row)
            mapping.save()
            row.canonical_identity = mapping
        else:
            row.canonical_project = project
            row.canonical_client = client
            row.canonical_identity_basis = identity_basis(row)
            # Identity review must never invoke financial recomputation.
            row.save(_skip_recompute=True, _write_identity=True, update_fields=[
                'canonical_project', 'canonical_client', 'canonical_identity_basis', 'updated_at',
            ])
        return row


class CustomerInvoiceAdapter(FinanceIdentityAdapter):
    key = 'customer_invoice'
    label = 'Outgoing customer invoices'


class ReceivablesSourceAdapter(FinanceIdentityAdapter):
    key = 'receivables_source'
    label = 'Finance workbook source'
    source = True


ADAPTERS = {adapter.key: adapter for adapter in (CustomerInvoiceAdapter(), ReceivablesSourceAdapter())}
