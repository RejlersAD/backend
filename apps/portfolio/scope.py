"""One authorized workbook scope for portfolio facts and operational links."""
from .access import can_upload_workbook
from .reporting import _visible_rows


IDENTITY = ('project_code', 'subproject_code', 'title', 'business_unit', 'client', 'pm', 'pc', 'scope_type')


def workbook_scope(snapshot, user, *, search='', pm='', business_unit='', client=''):
    full_source = can_upload_workbook(user)
    queryset = snapshot.rows.all() if full_source else _visible_rows(snapshot, user)
    all_rows = list(queryset.order_by('project_code', 'subproject_code').values())
    choices = {key: sorted({row[field] for row in all_rows if row[field]}, key=str.casefold)
               for key, field in (('business_units', 'business_unit'), ('clients', 'client'), ('project_managers', 'pm'))}
    rows = all_rows
    for field, term in (('pm', pm), ('business_unit', business_unit), ('client', client)):
        if term:
            rows = [row for row in rows if row[field].casefold() == term.casefold()]
    if search:
        rows = [row for row in rows if any(search.casefold() in (row.get(field) or '').casefold() for field in IDENTITY)]
    return {'all_rows': all_rows, 'rows': rows, 'filters': choices, 'full_source': full_source,
            'filtered': bool(search or pm or business_unit or client)}
