"""Staged SLD analysis flow API ("RADAI SLD Comparison" canvas).

Endpoints (mounted under /api/v1/electrical-comparison/sld/):

    GET    <project>/state/                      stage state + files + legend
    POST   <project>/files/                      multipart {kind, file} -> read summary
    PUT    <project>/files/<file>/mapping/       sheet status / mapping overrides
    PUT    user/ai-key/                          save (encrypted) BYOK key
    POST   user/ai-key/test/                     test a key (not yet saved)
    DELETE user/ai-key/                          remove saved key
    PUT    <project>/legend/                     {fromDrawing:true} | {legendId}
    POST   <project>/analyse/                    start analysis -> {analysisId}
    GET    <project>/analyses/                   previous analyses
    GET    <project>/analyses/<id>/              ?tab=equipment|load|drafting|interfaces|cables
    GET    <project>/analyses/<id>/export/       ?format=workbook|drafting

Security notes: the BYOK key is stored encrypted (apps.core.ai_credentials),
never returned by any endpoint (only key_last4), never logged.
"""
from __future__ import annotations

import logging

from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import (
    ElectricalProject, SldAnalysis, SldAnalysisFile, SldProjectSettings, SldUserAIKey,
)
from .services import sld_crypto, sld_excel, sld_pdf

logger = logging.getLogger(__name__)

FILE_KINDS = {'sld', 'equipment', 'load'}


def _project(request, project_id):
    return get_object_or_404(ElectricalProject, project_id=project_id, created_by=request.user)


def _settings(project):
    obj, _ = SldProjectSettings.objects.get_or_create(project=project)
    return obj


def _file_summary(kind, path, original_name):
    if kind == 'equipment':
        return sld_excel.read_equipment_list(path)
    if kind == 'load':
        return sld_excel.read_load_list(path)
    with open(path, 'rb') as fh:
        return sld_pdf.read_drawing_summary(fh.read())


class SldStateView(APIView):
    """Everything the New-analysis page needs in one call."""

    def get(self, request, project_id):
        project = _project(request, project_id)
        st = _settings(project)
        files = SldAnalysisFile.objects.filter(project=project).order_by('created_at')
        key = SldUserAIKey.objects.filter(user=request.user).first()
        analyses = SldAnalysis.objects.filter(project=project)
        return Response({
            'stage_state': st.stage_state,
            'legend': st.legend,
            'files': [{
                'id': str(f.id), 'kind': f.kind, 'name': f.original_name,
                'summary': f.summary, 'created_at': f.created_at,
            } for f in files],
            'byok': ({
                'provider': key.provider, 'last4': key.key_last4,
                'last_tested_at': key.last_tested_at,
            } if key else None),
            'analyses_count': analyses.count(),
            'latest_analysis': (
                {'id': str(a.id), 'status': a.status, 'created_at': a.created_at, 'outdated': a.outdated}
                if (a := analyses.first()) else None
            ),
        })


class SldFileUploadView(APIView):
    def post(self, request, project_id):
        project = _project(request, project_id)
        kind = (request.data.get('kind') or '').strip()
        upload = request.FILES.get('file')
        if kind not in FILE_KINDS:
            return Response({'error': "kind must be one of 'sld', 'equipment', 'load'."}, status=400)
        if not upload:
            return Response({'error': 'No file uploaded.'}, status=400)
        import tempfile, os
        suffix = os.path.splitext(upload.name)[1]
        tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
        try:
            for chunk in upload.chunks():
                tmp.write(chunk)
            tmp.close()
            try:
                summary = _file_summary(kind, tmp.name, upload.name)
            except Exception as exc:
                logger.exception('sld file summary failed')
                return Response({'error': f'Could not read that file: {exc}'}, status=400)
        finally:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
        # one file per slot — replacing marks the last analysis outdated
        SldAnalysisFile.objects.filter(project=project, kind=kind).delete()
        if kind in ('equipment', 'load'):
            SldAnalysis.objects.filter(project=project).update(outdated=True)
        f = SldAnalysisFile.objects.create(
            project=project, kind=kind, file=upload, original_name=upload.name, summary=summary,
        )
        st = _settings(project)
        if kind == 'sld':
            st.stage_state = {**st.stage_state, '3': st.stage_state.get('3') if not st.legend else 'needs-recheck'}
            st.save(update_fields=['stage_state', 'updated_at'])
        return Response({'id': str(f.id), 'kind': kind, 'name': f.original_name, 'summary': summary})


class SldFileMappingView(APIView):
    """Sheet status / column mapping overrides from Stage 1."""

    def put(self, request, project_id, file_id):
        project = _project(request, project_id)
        f = get_object_or_404(SldAnalysisFile, id=file_id, project=project)
        sheet_status = request.data.get('sheet_status')  # {sheetName: new|demolition|ignore}
        if isinstance(sheet_status, dict):
            summary = dict(f.summary or {})
            for s in summary.get('sheets', []):
                if s['name'] in sheet_status:
                    s['status'] = sheet_status[s['name']]
            for r in summary.get('rows', []):
                if r.get('sheet') in sheet_status:
                    r['listStatus'] = sheet_status[r['sheet']]
            f.summary = summary
        f.save(update_fields=['summary'])
        return Response({'id': str(f.id), 'summary': f.summary})


class SldUserAIKeyView(APIView):
    """Stage 2 BYOK: save / inspect / remove the user's own vision key.
    The raw key is never returned."""

    def get(self, request):
        key = SldUserAIKey.objects.filter(user=request.user).first()
        if not key:
            return Response({'saved': False})
        return Response({
            'saved': True, 'provider': key.provider, 'last4': key.key_last4,
            'last_tested_at': key.last_tested_at,
        })

    def put(self, request):
        provider = (request.data.get('provider') or '').strip().lower()
        raw = (request.data.get('api_key') or '').strip()
        if provider not in ('openai', 'claude'):
            return Response({'error': 'Choose OpenAI or Claude.'}, status=400)
        if not raw:
            return Response({'error': 'API key is required.'}, status=400)
        if not sld_crypto.encryption_ready():
            return Response({'error': 'Server key storage is not configured.'}, status=503)
        SldUserAIKey.objects.update_or_create(
            user=request.user, provider=provider,
            defaults={
                'encrypted_key': sld_crypto.encrypt_api_key(raw),
                'key_last4': raw[-4:],
                'last_tested_at': timezone.now(),
            },
        )
        # a key for the other provider is replaced, not kept alongside
        SldUserAIKey.objects.filter(user=request.user).exclude(provider=provider).delete()
        return Response({'saved': True, 'provider': provider, 'last4': raw[-4:]})

    def delete(self, request):
        SldUserAIKey.objects.filter(user=request.user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SldUserAIKeyTestView(APIView):
    """Test a key BEFORE saving (spec §4.1: Save stays disabled until the
    test passes). Exact result strings come from the UI copy table."""

    def post(self, request):
        provider = (request.data.get('provider') or '').strip().lower()
        raw = (request.data.get('api_key') or '').strip()
        if provider not in ('openai', 'claude'):
            return Response({'valid': False, 'message': 'Choose OpenAI or Claude.'})
        if not raw:
            return Response({'valid': False, 'message': 'API key is required.'})
        import time
        from apps.pid_verification.services.piping_valve_mto_extractor import test_api_key
        t0 = time.monotonic()
        valid, message = test_api_key(provider, raw)
        elapsed = round(time.monotonic() - t0, 1)
        if valid:
            return Response({'valid': True, 'message': f'Key valid · {elapsed} s'})
        low = (message or '').lower()
        if 'invalid' in low or '401' in low or '403' in low:
            friendly = 'Key rejected by provider — check the key and try again'
        elif 'quota' in low or 'rate limit' in low or '429' in low:
            friendly = 'Quota exceeded on your key — try later or replace the key'
        else:
            friendly = 'Provider not reachable — try again'
        return Response({'valid': False, 'message': friendly})


class SldLegendView(APIView):
    def put(self, request, project_id):
        project = _project(request, project_id)
        st = _settings(project)
        if request.data.get('fromDrawing'):
            st.legend = {'source': 'drawing', 'name': 'Legend from drawing (sheet 002)'}
        elif request.data.get('legendId'):
            st.legend = {'source': 'manager', 'id': request.data['legendId'],
                         'name': request.data.get('name', '')}
        else:
            return Response({'error': 'Provide fromDrawing or legendId.'}, status=400)
        if isinstance(request.data.get('tagRules'), list):
            st.legend['tagRules'] = request.data['tagRules']
        st.stage_state = {**st.stage_state, '3': 'complete'}
        st.save(update_fields=['legend', 'stage_state', 'updated_at'])
        return Response({'legend': st.legend})


class SldAnalyseView(APIView):
    def post(self, request, project_id):
        project = _project(request, project_id)
        running = SldAnalysis.objects.filter(project=project, status__in=('queued', 'running')).first()
        if running:
            return Response({'analysisId': str(running.id)}, status=202)
        analysis = SldAnalysis.objects.create(project=project, created_by=request.user)

        import threading
        from django.db import close_old_connections

        def _run(analysis_id):
            close_old_connections()
            try:
                from .services.sld_pipeline import run_analysis
                run_analysis(SldAnalysis.objects.get(id=analysis_id))
            except Exception as exc:
                logger.exception('sld analysis failed')
                try:
                    a = SldAnalysis.objects.get(id=analysis_id)
                    a.status = 'failed'
                    a.error = str(exc)
                    a.save(update_fields=['status', 'error'])
                except Exception:
                    pass
            finally:
                close_old_connections()

        threading.Thread(target=_run, args=(analysis.id,), daemon=True).start()
        return Response({'analysisId': str(analysis.id)}, status=202)


class SldAnalysesView(APIView):
    """Previous analyses list (View Results / Download / Delete live here)."""

    def get(self, request, project_id):
        project = _project(request, project_id)
        return Response({'analyses': [{
            'id': str(a.id), 'status': a.status, 'created_at': a.created_at,
            'outdated': a.outdated,
            'headline': (a.result or {}).get('headline', {}),
        } for a in SldAnalysis.objects.filter(project=project)]})


class SldAnalysisDetailView(APIView):
    def get(self, request, project_id, analysis_id):
        project = _project(request, project_id)
        a = get_object_or_404(SldAnalysis, id=analysis_id, project=project)
        tab = request.query_params.get('tab')
        result = a.result or {}
        payload = {
            'id': str(a.id), 'status': a.status, 'error': a.error,
            'created_at': a.created_at, 'outdated': a.outdated,
            'headline': result.get('headline', {}),
            'banner': result.get('banner', {}),
        }
        if tab:
            payload['tab'] = tab
            payload['rows'] = result.get(tab, [])
            payload['meta'] = result.get(f'{tab}_meta', {})
        else:
            payload['result'] = result
        return Response(payload)

    def delete(self, request, project_id, analysis_id):
        project = _project(request, project_id)
        a = get_object_or_404(SldAnalysis, id=analysis_id, project=project)
        a.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class SldAnalysisExportView(APIView):
    def get(self, request, project_id, analysis_id):
        project = _project(request, project_id)
        a = get_object_or_404(SldAnalysis, id=analysis_id, project=project)
        fmt = request.query_params.get('format', 'workbook')
        from .services.sld_export import build_export
        content, filename = build_export(a, fmt)
        from django.http import HttpResponse
        resp = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        )
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp
