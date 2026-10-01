"""
Valve MTO — server-side persistence API.

Endpoints
─────────
GET/POST   /api/v1/valve-mto/projects/
GET/PUT/DELETE /api/v1/valve-mto/projects/{id}/
POST       /api/v1/valve-mto/projects/{id}/save-rows/   (bulk replace)
GET        /api/v1/valve-mto/projects/{id}/rows/
POST       /api/v1/valve-mto/projects/{id}/export/      (xlsx download)
GET        /api/v1/valve-mto/fluid-codes/               (static reference)

Scoped to the requesting user's own projects (created_by) — a Valve MTO
workspace is personal extraction/working data, same isolation as
apps.instrument_tools and other per-user extraction tools in this
codebase; nothing here is a shared/team resource.
"""
from __future__ import annotations

from django.db import transaction
from django.http import HttpResponse

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from .excel_export import export_project_to_xlsx
from .fluid_codes import FLUID_CODES
from .models import ValveMTOProject, ValveMTORow
from .serializers import (
    ValveMTOProjectDetailSerializer, ValveMTOProjectSerializer, ValveMTORowSerializer,
)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def fluid_codes_view(request):
    """Returns the SAME dict apps.pid_verification's own Vision prompt
    is built from (see .fluid_codes's own module docstring for the full
    single-source-of-truth rationale) — {"codes": {CODE: DESCRIPTION}}.
    Consumed by ValveMTO.jsx when auto-creating its default "Fluid Code
    - Standard" legend, so that legend's content can never drift from
    what extraction itself actually uses."""
    return Response({'codes': FLUID_CODES})


class ValveMTOProjectViewSet(viewsets.ModelViewSet):
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return ValveMTOProject.objects.filter(created_by=self.request.user)

    def get_serializer_class(self):
        if self.action == 'retrieve':
            return ValveMTOProjectDetailSerializer
        return ValveMTOProjectSerializer

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    # BUG FIX (real, confirmed root cause of ValveMTOProject.status being
    # stuck at 'extracting' forever): the frontend's valveMtoService.
    # updateProject() sends a PUT with a PARTIAL payload (just {status:
    # 'completed'}, see ValveMTO.jsx's syncToServer) — DRF's default PUT
    # handler uses partial=False, which requires every non-nullable field
    # (project_name has no blank=True/null=True) to be present, so that
    # PUT 400s every time and gets silently discarded by the caller's own
    # `.catch(() => {})`. Confirmed live: a real project's status was
    # still 'extracting' in the DB well after its extraction job finished
    # and 216 rows had already been saved. Forcing every PUT to this
    # viewset to behave as a partial update (same as PATCH) matches what
    # the frontend actually sends, without needing to change its HTTP
    # verb — ValveMTO.jsx stays untouched for this half of the fix.
    def update(self, request, *args, **kwargs):
        kwargs['partial'] = True
        return super().update(request, *args, **kwargs)

    # ---- bulk-replace this project's rows -----------------------------
    @action(detail=True, methods=['post'], url_path='save-rows')
    def save_rows(self, request, pk=None):
        """Replaces the ENTIRE row set for this project in one call — the
        frontend's own row list (island/field/combined tabs combined) is
        always the full, current source of truth for a save (auto-save on
        edit, or the post-extraction save), so delete+recreate is both
        simpler and safer than diffing than trying to reconcile a partial
        patch against whatever's already stored. Wrapped in one
        transaction so a mid-save failure never leaves a project with
        half its old rows deleted and none of the new ones written.

        Body: {"rows": [{...row fields...}, ...]}. 'row_order' is set
        from each row's array position when not explicitly provided, so
        the frontend doesn't have to compute it itself.
        """
        project = self.get_object()
        rows_data = request.data.get('rows')
        if not isinstance(rows_data, list):
            return Response(
                {'error': "'rows' must be a list"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Explicit allowlist (not introspected from the model) so a
        # client-supplied 'id'/'project'/'created_at'/'updated_at' can
        # never override what this endpoint itself controls.
        allowed_fields = {
            'tag_number', 'valve_type', 'pms_class', 'rating', 'facing', 'size_primary',
            'size_secondary', 'size_secondary_2', 'line_number', 'line_list_ref', 'pid_number',
            'description', 'qty_island', 'qty_field', 'qty_combined', 'unit', 'area',
            'operational_status', 'remarks', 'tab',
        }
        with transaction.atomic():
            project.rows.all().delete()
            ValveMTORow.objects.bulk_create([
                ValveMTORow(
                    project=project,
                    row_order=row.get('row_order', idx),
                    **{k: v for k, v in row.items() if k in allowed_fields and k != 'row_order'},
                )
                for idx, row in enumerate(rows_data)
            ])
            # BUG FIX (real, confirmed): ValveMTOProject.status defaults to
            # 'extracting' and nothing previously ever flipped it —
            # verified live, a project with 216 saved rows still showed
            # 'extracting' in the DB. A project that just had its row set
            # saved (the extraction's own completion path, or a later
            # hand-edit) is by definition no longer "extracting"; this is
            # the single authoritative place that transition belongs,
            # rather than depending on a second, separate status-update
            # request from the frontend to also succeed.
            if project.status != ValveMTOProject.STATUS_COMPLETED:
                project.status = ValveMTOProject.STATUS_COMPLETED
        project.save(update_fields=['updated_at', 'status'])

        ser = ValveMTORowSerializer(project.rows.all(), many=True)
        return Response({'rows': ser.data, 'row_count': len(ser.data)}, status=status.HTTP_200_OK)

    # ---- list this project's rows --------------------------------------
    @action(detail=True, methods=['get'], url_path='rows')
    def rows(self, request, pk=None):
        project = self.get_object()
        ser = ValveMTORowSerializer(project.rows.all(), many=True)
        return Response(ser.data)

    # ---- xlsx export -----------------------------------------------------
    @action(detail=True, methods=['post'], url_path='export')
    def export(self, request, pk=None):
        project = self.get_object()
        data = export_project_to_xlsx(project)
        filename = f'ValveMTO_{project.project_name or project.id}.xlsx'.replace('/', '-')
        resp = HttpResponse(
            data,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp
