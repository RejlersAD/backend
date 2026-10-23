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

from django.core.files.base import ContentFile
from django.db import transaction
from django.http import HttpResponse

from rest_framework import status, viewsets
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.negotiation import DefaultContentNegotiation
from rest_framework.permissions import IsAuthenticated
from rest_framework.parsers import MultiPartParser, FormParser
from rest_framework.response import Response


class LenientFormatNegotiation(DefaultContentNegotiation):
    """DRF treats `?format=` as a RENDERER override and 404s when no
    renderer matches — but Valve MTO's export endpoint (§8) uses
    `?format=mto|full|order` as a BUSINESS parameter. Fall back to the
    default renderer set instead of 404ing so the endpoint can read the
    param itself."""
    def filter_renderers(self, renderers, format):
        filtered = [r for r in renderers if r.format == format]
        return filtered or renderers

from .excel_export import export_project_to_xlsx
from .fluid_codes import FLUID_CODES
from .models import (
    ValveMTODrawing, ValveMTOJob, ValveMTOLegend,
    ValveMTOProject, ValveMTOProjectSettings, ValveMTORow,
)
from .serializers import (
    ValveMTODrawingSerializer,
    ValveMTOJobSerializer,
    ValveMTOLegendSerializer,
    ValveMTOProjectDetailSerializer, ValveMTOProjectSerializer,
    ValveMTOProjectSettingsSerializer, ValveMTORowSerializer,
)


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def job_detail_view(request, project_id, job_id):
    """GET …/projects/:projectId/jobs/:jobId — extraction job polling (§8)."""
    job = ValveMTOJob.objects.filter(
        id=job_id, project_id=project_id, project__created_by=request.user).first()
    if not job:
        return Response({'error': 'Job not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response(ValveMTOJobSerializer(job).data)


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
    content_negotiation_class = LenientFormatNegotiation
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
    # Class-level default so @action(permission_action=...) initkwargs are
    # accepted by as_view() (same pattern as ValveMTOLegendViewSet).
    permission_action = None

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
            # §7 staged-flow fields
            'page', 'pos_x', 'pos_y', 'variant', 'category', 'type_basis', 'symbol_group_id',
            'scope_status', 'piping_class_source', 'material_source', 'pid_rev', 'confidence_score',
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
    # §6 Export Center: GET …/export/?format=mto|full|order (POST kept for
    # the legacy caller in valveMtoService.exportXlsx).
    @action(detail=True, methods=['get', 'post'], url_path='export')
    def export(self, request, pk=None):
        project = self.get_object()
        fmt = (request.query_params.get('format')
               or request.data.get('format') or 'mto')
        if fmt not in ('mto', 'full', 'order'):
            return Response({'error': "format must be one of 'mto', 'full', 'order'"},
                            status=status.HTTP_400_BAD_REQUEST)
        data = export_project_to_xlsx(project, fmt=fmt)
        filename = f'ValveMTO_{project.project_name or project.id}_{fmt}.xlsx'.replace('/', '-')
        resp = HttpResponse(
            data,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp

    # ─────────────────────────────────────────────────────────────────
    # Staged extraction flow endpoints (§8)
    # ─────────────────────────────────────────────────────────────────

    def _settings(self, project) -> ValveMTOProjectSettings:
        obj, _ = ValveMTOProjectSettings.objects.get_or_create(project=project)
        return obj

    # ---- Stage 1: upload + parse a P&ID drawing ------------------------
    @action(detail=True, methods=['post'], url_path='drawings',
            parser_classes=[MultiPartParser, FormParser], permission_action='create')
    def drawings(self, request, pk=None):
        from . import pdf_parse

        project = self.get_object()
        upload = request.FILES.get('file')
        if not upload or not upload.name.lower().endswith('.pdf'):
            return Response({'error': 'Upload a .pdf drawing.'}, status=status.HTTP_400_BAD_REQUEST)
        if upload.size > 100 * 1024 * 1024:
            return Response({'error': 'Drawing exceeds the 100 MB limit.'}, status=status.HTTP_400_BAD_REQUEST)

        data = upload.read()
        parsed = pdf_parse.parse_drawing(data, upload.name)
        if parsed.get('error'):
            return Response({'error': parsed['error']}, status=status.HTTP_400_BAD_REQUEST)

        settings_obj = self._settings(project)
        pattern = pdf_parse.apply_line_pattern(
            parsed['line_labels'],
            settings_obj.line_pattern_preset,
            settings_obj.line_pattern_regex,
        )
        parsed['pattern'] = pattern
        parsed['classes'] = pattern.get('classes', [])

        # Replacing the Stage-1 PDF sets stages 2 & 4 to needs-recheck (§2).
        had_previous = project.drawings.exists()
        drawing = ValveMTODrawing.objects.create(
            project=project,
            original_name=upload.name,
            page_count=parsed['page_count'],
            pdf_type=parsed['pdf_type'],
            parsed=parsed,
            confirmed_drawing_number=parsed['title_block'].get('company_drawing_no', ''),
            confirmed_rev=parsed['title_block'].get('rev', ''),
        )
        drawing.file.save(upload.name, ContentFile(data), save=True)

        if had_previous:
            stage_state = settings_obj.stage_state or {}
            for st in ('2', '4'):
                if stage_state.get(st) == 'complete':
                    stage_state[st] = 'needs-recheck'
            settings_obj.stage_state = stage_state
            settings_obj.extraction_outdated = project.rows.exists()
            settings_obj.save(update_fields=['stage_state', 'extraction_outdated', 'updated_at'])

        return Response(ValveMTODrawingSerializer(drawing).data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['get'], url_path='drawings/list')
    def drawings_list(self, request, pk=None):
        project = self.get_object()
        return Response(ValveMTODrawingSerializer(project.drawings.all(), many=True).data)

    # ---- Stage 1 confirm: drawing number + pattern ---------------------
    @action(detail=True, methods=['post'], url_path=r'drawings/(?P<drawing_id>[^/.]+)/confirm',
            permission_action='update')
    def drawing_confirm(self, request, pk=None, drawing_id=None):
        from . import pdf_parse

        project = self.get_object()
        drawing = project.drawings.filter(id=drawing_id).first()
        if not drawing:
            return Response({'error': 'Drawing not found.'}, status=status.HTTP_404_NOT_FOUND)
        drawing.confirmed_drawing_number = str(
            request.data.get('drawing_number') or drawing.confirmed_drawing_number)[:255]
        drawing.confirmed_rev = str(
            request.data.get('rev') or drawing.confirmed_rev)[:16]

        preset = request.data.get('pattern_preset')
        custom_regex = request.data.get('pattern_regex', '')
        if preset:
            settings_obj = self._settings(project)
            settings_obj.line_pattern_preset = preset
            if custom_regex:
                settings_obj.line_pattern_regex = custom_regex
            settings_obj.save(update_fields=['line_pattern_preset', 'line_pattern_regex', 'updated_at'])
            pattern = pdf_parse.apply_line_pattern(
                drawing.parsed.get('line_labels', []), preset, custom_regex,
            )
            drawing.parsed = {**drawing.parsed, 'pattern': pattern,
                              'classes': pattern.get('classes', [])}
        drawing.save()
        return Response(ValveMTODrawingSerializer(drawing).data)

    # ---- Project settings (Stages 2–4 persist) --------------------------
    @action(detail=True, methods=['get', 'put'], url_path='settings')
    def project_settings(self, request, pk=None):
        project = self.get_object()
        settings_obj = self._settings(project)
        if request.method == 'GET':
            return Response(ValveMTOProjectSettingsSerializer(settings_obj).data)
        ser = ValveMTOProjectSettingsSerializer(settings_obj, data=request.data, partial=True)
        ser.is_valid(raise_exception=True)
        ser.save()
        # Changing PMS or legend after an extraction marks it outdated (§2).
        if any(k in request.data for k in ('pms_data', 'pms_skipped', 'legend')) \
                and project.rows.exists():
            settings_obj.extraction_outdated = True
            settings_obj.save(update_fields=['extraction_outdated', 'updated_at'])
        return Response(ser.data)

    # ---- Stage 2: PMS workbook upload -----------------------------------
    @action(detail=True, methods=['post'], url_path='pms',
            parser_classes=[MultiPartParser, FormParser], permission_action='create')
    def pms(self, request, pk=None):
        from .pms_parse import coverage_check, parse_pms_workbook

        project = self.get_object()
        upload = request.FILES.get('file')
        if not upload or not upload.name.lower().endswith(('.xlsx', '.xlsm')):
            return Response({'error': 'Upload a .xlsx PMS workbook.'}, status=status.HTTP_400_BAD_REQUEST)
        result = parse_pms_workbook(upload.read(), upload.name)
        if result.get('error'):
            return Response({'error': result['error']}, status=status.HTTP_400_BAD_REQUEST)

        drawing = project.drawings.order_by('-created_at').first()
        drawing_classes = (drawing.parsed or {}).get('classes', []) if drawing else []
        coverage = coverage_check(result['classes'], drawing_classes)

        settings_obj = self._settings(project)
        settings_obj.pms_data = {'classes': result['classes'], 'source': result['source'],
                                 'sheet': result.get('sheet', '')}
        settings_obj.pms_skipped = False
        settings_obj.pms_infer_from_drawing = False
        settings_obj.save(update_fields=['pms_data', 'pms_skipped', 'pms_infer_from_drawing', 'updated_at'])
        return Response({
            'class_count': result['class_count'],
            'classes': sorted(result['classes'].keys()),
            'coverage': coverage,
        })

    # ---- Stage 3: engine connectivity test -------------------------------
    @action(detail=True, methods=['post'], url_path='engine/test', permission_action='read')
    def engine_test(self, request, pk=None):
        self.get_object()  # 404s for other users' projects
        from .engine import test_engine
        result = test_engine()
        return Response(result, status=status.HTTP_200_OK if result['ok'] else status.HTTP_503_SERVICE_UNAVAILABLE)

    # ---- Stage 4: legend ↔ drawing match check ---------------------------
    @action(detail=True, methods=['post'], url_path='legend/check', permission_action='read')
    def legend_check(self, request, pk=None):
        from .engine import detect_symbols
        from .legend_check import check_legend_against_drawing

        project = self.get_object()
        legend = ValveMTOLegend.objects.filter(
            id=request.data.get('legend_id'), created_by=request.user).first()
        if not legend:
            return Response({'error': 'Legend not found.'}, status=status.HTTP_404_NOT_FOUND)
        drawing = project.drawings.filter(
            id=request.data.get('drawing_id')).first() or project.drawings.order_by('-created_at').first()
        if not drawing:
            return Response({'error': 'Upload a drawing first (Stage 1).'},
                            status=status.HTTP_400_BAD_REQUEST)

        # Symbol groups from vector detection (grouped counts + evidence).
        groups = []
        try:
            drawing.file.open('rb')
            import fitz
            doc = fitz.open(stream=drawing.file.read(), filetype='pdf')
            drawing.file.close()
            candidates = detect_symbols(doc)
            doc.close()
            by_group: dict = {}
            for c in candidates:
                g = by_group.setdefault(c['symbol_group_id'], {'group_id': c['symbol_group_id'],
                                                               'count': 0, 'evidence': []})
                g['count'] += 1
                if len(g['evidence']) < 3:
                    g['evidence'].append({'page': c['page'], 'x': c['x'], 'y': c['y']})
            groups = sorted(by_group.values(), key=lambda g: -g['count'])
        except Exception:
            logging.getLogger(__name__).exception('legend check: symbol detection failed')

        result = check_legend_against_drawing(legend.definition or {}, drawing, groups)
        result['legend_id'] = str(legend.id)
        result['legend_name'] = legend.name
        return Response(result)

    # ---- Start Extraction -------------------------------------------------
    @action(detail=True, methods=['post'], url_path='extract', permission_action='create')
    def extract(self, request, pk=None):
        project = self.get_object()
        running = project.mto_jobs.filter(
            status__in=[ValveMTOJob.STATUS_QUEUED, ValveMTOJob.STATUS_RUNNING]).first()
        if running:
            return Response(ValveMTOJobSerializer(running).data, status=status.HTTP_202_ACCEPTED)

        job = ValveMTOJob.objects.create(project=project)
        # Optional BYOK: a user-supplied vision key is used for THIS run only —
        # never stored on the job, settings, or anywhere server-side.
        vision_provider = (request.data.get('vision_provider') or '').strip().lower() or None
        vision_api_key = (request.data.get('vision_api_key') or '').strip() or None
        vision_model = (request.data.get('vision_model') or '').strip() or None
        # Async execution — a daemon thread keeps the dev-server flow
        # dependency-free (Celery wiring can replace this unchanged; the
        # job row carries all progress either way).
        import threading
        from django.db import close_old_connections

        def _run(job_id):
            close_old_connections()
            try:
                from .engine import run_extraction
                run_extraction(
                    ValveMTOJob.objects.get(id=job_id),
                    vision_provider=vision_provider, vision_api_key=vision_api_key,
                    vision_model=vision_model,
                )
            except Exception as exc:
                logging.getLogger(__name__).exception('valve mto extraction failed')
                try:
                    j = ValveMTOJob.objects.get(id=job_id)
                    j.status = ValveMTOJob.STATUS_FAILED
                    j.error = str(exc)
                    j.save(update_fields=['status', 'error', 'updated_at'])
                except Exception:
                    pass
            finally:
                close_old_connections()

        threading.Thread(target=_run, args=(job.id,), daemon=True).start()
        return Response(ValveMTOJobSerializer(job).data, status=status.HTTP_202_ACCEPTED)

    # ---- Register views (§6) ----------------------------------------------
    @action(detail=True, methods=['get'], url_path='register')
    def register(self, request, pk=None):
        project = self.get_object()
        view = (request.query_params.get('view') or 'combined').lower()
        rows = project.rows.all()
        if view == 'island':
            rows = rows.filter(qty_island__gt=0)
        elif view == 'field':
            rows = rows.filter(qty_field__gt=0)
        elif view == 'order':
            rows = rows.filter(qty_combined__gt=0)
        elif view == 'review':
            from django.db.models import Q
            threshold = self._settings(project).engine_review_threshold
            rows = rows.filter(
                Q(type_basis='unidentified')
                | Q(size_primary='') | Q(line_number='Not traced') | Q(piping_class='Class not shown')
                | Q(confidence_score__lt=threshold)
            ).distinct()
        elif view != 'combined':
            return Response({'error': "view must be island|field|combined|order|review"},
                            status=status.HTTP_400_BAD_REQUEST)
        ser = ValveMTORowSerializer(rows, many=True, context={'request': request})
        return Response({'view': view, 'count': rows.count(), 'rows': ser.data})


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
