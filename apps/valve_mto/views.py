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

import json
import logging
from zipfile import BadZipFile

from django.db import transaction
from django.http import HttpResponse

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response

from .excel_export import export_project_to_xlsx
from .fluid_codes import FLUID_CODES
from .models import ValveMTOLegend, ValveMTOProject, ValveMTORow
from .serializers import (
    ValveMTOLegendSerializer,
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
    # BUG FIX (real, confirmed): ModelViewSet auto-applies this project's
    # DEFAULT_PAGINATION_CLASS (config/settings.py — PageNumberPagination)
    # unless told not to, wrapping list() responses as
    # {count, next, previous, results: [...]} instead of a plain array.
    # valveMtoService.js's listProjects() (and ValveMTO.jsx's own
    # `Array.isArray(data) ? data : []` callers) were written expecting a
    # plain array — found while diagnosing the SAME bug class on
    # ValveMTOLegendViewSet below (reported as Manage Legends showing
    # empty); this viewset has the identical shape and the identical
    # caller pattern, so it would silently drop every "Cloud Projects"
    # result the same way. Disabling pagination here restores the plain-
    # array response both call sites already assume.
    pagination_class = None

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
        for index, row in enumerate(rows_data):
            title_validation = ValveMTORowSerializer(data={'area': row.get('area', '')}, partial=True)
            if not title_validation.is_valid():
                return Response(
                    {'error': f'Row {index + 1}: AREA must contain a DRG. TITLE of at most 512 characters.',
                     'details': title_validation.errors},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if not row.get('size_primary'):
                from .specification_lookup import size_nb_from_line_number
                row['size_primary'] = size_nb_from_line_number(row.get('line_number'))
            if 'qty_observed' in row:
                validation = ValveMTORowSerializer(
                    data={'qty_observed': row['qty_observed']}, partial=True,
                )
                if not validation.is_valid():
                    return Response(
                        {'error': f'Row {index + 1}: observed quantity must be a non-negative whole number or null.',
                         'details': validation.errors},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                row['qty_observed'] = validation.validated_data['qty_observed']

        # Explicit allowlist (not introspected from the model) so a
        # client-supplied 'id'/'project'/'created_at'/'updated_at' can
        # never override what this endpoint itself controls.
        allowed_fields = {
            'tag_number', 'valve_type', 'pms_class', 'piping_class', 'rating', 'facing', 'size_primary',
            'size_secondary', 'size_secondary_2', 'line_number', 'line_list_ref', 'pid_number',
            'description', 'qty_island', 'qty_field', 'qty_combined', 'qty_observed', 'unit', 'area',
            'operational_status', 'remarks', 'confidence', 'location_hint', 'tab',
        }
        with transaction.atomic():
            # Serialize full-register replacements, including simultaneous autosaves.
            project = ValveMTOProject.objects.select_for_update().get(pk=project.pk)
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
            saved_rows = ValveMTORowSerializer(project.rows.all(), many=True).data

        return Response({'rows': saved_rows, 'row_count': len(saved_rows)}, status=status.HTTP_200_OK)

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


class ValveMTOLegendViewSet(viewsets.ModelViewSet):
    """Valve MTO's OWN, fully isolated legend/reference-data API — see
    models.ValveMTOLegend's own docstring for why this exists as a
    separate table+endpoint rather than reusing apps.pid_checker_v2's.

    GET/POST       /api/v1/valve-mto/legends/            (?section= filter)
    GET/PUT/DELETE /api/v1/valve-mto/legends/{id}/
    POST           /api/v1/valve-mto/legends/{id}/activate/
    GET            /api/v1/valve-mto/legends/active/      (?section= filter)
    """
    permission_classes = [IsAuthenticated]
    serializer_class = ValveMTOLegendSerializer
    lookup_field = 'id'
    # BUG FIX (real, confirmed root cause of "Manage Legends shows empty
    # / No legends yet" even with active rows in the DB): ModelViewSet
    # auto-applies this project's DEFAULT_PAGINATION_CLASS, wrapping
    # list() as {count, next, previous, results: [...]}. Both
    # ValveMTO.jsx's autoPopulateLegendSection and LegendSheetsModal.jsx's
    # refresh() do `Array.isArray(rows) ? rows : []` on the raw response
    # — an object (not an array) always evaluated to [], so the UI always
    # saw zero legends AND auto-create never saw its own prior creation,
    # recreating duplicates every time it ran. apps.pid_checker_v2's
    # equivalent endpoint is a plain APIView (no auto-pagination), which
    # is why that one never had this problem and nothing here was ever
    # written to unwrap `.results`.
    pagination_class = None
    permission_action = None

    @action(detail=False, methods=['post'], url_path='workbook-preview',
            parser_classes=[MultiPartParser, FormParser], permission_action='read')
    def workbook_preview(self, request):
        return self._workbook(request, preview=True)

    @action(detail=False, methods=['post'], url_path='workbook-import',
            parser_classes=[MultiPartParser, FormParser], permission_action='update')
    def workbook_import(self, request):
        return self._workbook(request, preview=False)

    def _workbook(self, request, *, preview):
        from apps.pid_checker_v2.services.legend_workbook_importer import preview_workbook, import_workbook
        from .models import VALVE_MTO_LEGEND_SECTIONS

        upload = request.FILES.get('file')
        if not upload or not upload.name.lower().endswith(('.xlsx', '.xlsm')):
            return Response({'error': 'Upload an .xlsx or .xlsm legend workbook.'}, status=400)
        if upload.size > 20 * 1024 * 1024:
            return Response({'error': 'Legend workbook exceeds the 20 MB limit.'}, status=400)
        try:
            data = upload.read()
            if preview:
                result = preview_workbook(data, upload.name)
                for sheet in result['sheets']:
                    predicted = sheet['predicted_section']
                    if predicted not in VALVE_MTO_LEGEND_SECTIONS:
                        sheet['predicted_section'] = (
                            'control_valve_regulator' if predicted == 'other_specialties' else 'piping'
                        )
                return Response(result)
            sheet_map = json.loads(request.data.get('sheet_map', '{}'))
            if not isinstance(sheet_map, dict):
                raise ValueError('Worksheet mapping must be an object.')
            return Response(import_workbook(
                data, upload.name, user=request.user, project=None, sheet_map=sheet_map, valve_mto=True,
            ))
        except (ValueError, BadZipFile) as exc:
            return Response({'error': str(exc)}, status=400)
        except Exception:
            logging.getLogger(__name__).exception('Valve MTO workbook import failed')
            return Response({'error': 'Could not import the workbook. Check the server logs.'}, status=500)

    def get_queryset(self):
        qs = ValveMTOLegend.objects.filter(created_by=self.request.user)
        section = self.request.query_params.get('section')
        if section:
            qs = qs.filter(section=section)
        return qs

    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)

    # ---- activate: deactivate every sibling in this section, activate this one
    @action(detail=True, methods=['post'], url_path='activate')
    def activate(self, request, id=None):
        legend = self.get_object()
        with transaction.atomic():
            ValveMTOLegend.objects.filter(
                created_by=request.user, section=legend.section, is_active=True,
            ).exclude(pk=legend.pk).update(is_active=False)
            legend.is_active = True
            legend.save(update_fields=['is_active', 'updated_at'])
        return Response(ValveMTOLegendSerializer(legend).data)

    # ---- the current user's active legend per section (optionally one section)
    @action(detail=False, methods=['get'], url_path='active')
    def active(self, request):
        qs = ValveMTOLegend.objects.filter(created_by=request.user, is_active=True)
        section = request.query_params.get('section')
        if section:
            qs = qs.filter(section=section)
        return Response(ValveMTOLegendSerializer(qs, many=True).data)
