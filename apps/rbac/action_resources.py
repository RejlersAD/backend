"""Resource-aware policies for shared engineering list endpoints."""
from django.core.exceptions import ValidationError
from rest_framework.exceptions import NotFound


def engineering_list_modules(request, view, default):
    from apps.designiq.models import EngineeringListItem, ProcessedPIDOutput, PIDSourceDrawing, LIST_TYPES
    mapping = {'line_list': default, 'equipment_list': 'pid_equipment_list',
               'critical_stress': 'piping_critical_line_list'}
    types = set()
    kwargs = getattr(view, 'kwargs', {})
    try:
        if kwargs.get('output_id'):
            types.add(ProcessedPIDOutput.objects.values_list('list_type', flat=True).get(pk=kwargs['output_id']))
        elif kwargs.get('drawing_id'):
            types.add(PIDSourceDrawing.objects.values_list('output__list_type', flat=True).get(pk=kwargs['drawing_id']))
        elif kwargs.get('document_id'):
            types.update(EngineeringListItem.objects.filter(data__document_id=kwargs['document_id']).values_list('list_type', flat=True))
        elif kwargs.get('pk') and getattr(view, 'action', '') != 'bulk_import':
            types.add(EngineeringListItem.objects.values_list('list_type', flat=True).get(pk=kwargs['pk']))
    except (EngineeringListItem.DoesNotExist, ProcessedPIDOutput.DoesNotExist, PIDSourceDrawing.DoesNotExist, ValidationError, ValueError):
        raise NotFound()
    selected = request.query_params.get('list_type')
    if request.method in {'POST', 'PUT', 'PATCH'}:
        selected = request.data.get('list_type', selected)
    if selected:
        types.add(selected)
    if not types:
        if getattr(view, 'action', '') in {'list', 'stats', 'export'}:
            types.update(LIST_TYPES)
        else:
            types.add('line_list')
    return {mapping.get(kind, 'designiq') for kind in types}
