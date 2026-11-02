"""SLD Checker API — mounted at /api/v1/sld-checker/ (config/urls.py,
behind SLD_CHECKER_ENABLED).

Ownership pattern copied from apps.electrical_comparison.views_staged:
projects are scoped by created_by=request.user; every other object is
reached through its analysis → project.
"""
from __future__ import annotations

import logging
import threading

from django.db import close_old_connections
from django.http import FileResponse, HttpResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.electrical_comparison.models import (
    ElectricalProject, SldAnalysis, SldAnalysisFile, SldProjectSettings,
)

from .models import (
    SldCheckConfig, SldChecklistItem, SldChecklistResult, SldEdge,
    SldFinding, SldNode, SldSheet,
)
from .serializers import (
    config_dict, edge_row, finding_row, node_row, sheet_header,
)
from .services import pipeline
from .services.checklist_eval import default_template, evaluate
from .services.rules import RULE_NODE_FIELDS, Ctx, run_rules

logger = logging.getLogger(__name__)

# node fields a PATCH may write
EDITABLE_NODE_FIELDS = {'tag', 'description', 'voltage_v', 'rated_a', 'isc_ka',
                        'kva', 'kw', 'z_pct', 'xd_pct', 'vector_group',
                        'essential', 'kind'}

EXTRACTION_TABS = {'sources', 'boards', 'feeders', 'loads', 'subpanels',
                   'cables', 'interfaces'}


def _project(request, project_id):
    return get_object_or_404(ElectricalProject, project_id=project_id,
                             created_by=request.user)


def _analysis(request, analysis_id):
    a = get_object_or_404(SldAnalysis, id=analysis_id)
    get_object_or_404(ElectricalProject, project_id=a.project_id,
                      created_by=request.user)
    return a


class RunAnalysisView(APIView):
    """POST analyses/<id>/run/ — rebuild the checker rows for a completed
    core analysis. Threaded like the core analyse endpoint; ?sync=1 runs
    inline (tests / small drawings)."""

    permission_classes = [IsAuthenticated]

    def post(self, request, analysis_id):
        analysis = _analysis(request, analysis_id)
        if analysis.status != 'completed':
            return Response({'error': 'Core analysis is not completed yet.'}, status=409)

        if request.query_params.get('sync'):
            stats = pipeline.run(analysis.id)
            return Response({'analysisId': str(analysis.id), 'status': 'done', **stats})

        def _run(aid):
            close_old_connections()
            try:
                pipeline.run(aid)
            except Exception:
                logger.exception('[SLDC] run failed for %s', aid)
            finally:
                close_old_connections()

        threading.Thread(target=_run, args=(analysis.id,), daemon=True).start()
        return Response({'analysisId': str(analysis.id), 'status': 'running'}, status=202)


class WorkspaceView(APIView):
    """GET projects/<pid>/documents/<fileId>/workspace/?sheet=<page_index>"""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id, file_id):
        project = _project(request, project_id)
        sld_file = get_object_or_404(SldAnalysisFile, id=file_id, project=project)
        sheet_qs = SldSheet.objects.filter(file_id=sld_file.id)
        analysis = None
        if sheet_qs.exists():
            analysis = SldAnalysis.objects.filter(
                id=sheet_qs.latest('page_index').analysis_id).first()
        if analysis is None:
            analysis = SldAnalysis.objects.filter(
                project=project, status='completed').first()
        sheets = SldSheet.objects.filter(analysis_id=analysis.id) if analysis else SldSheet.objects.none()
        sheet_param = request.query_params.get('sheet')
        current = None
        if sheet_param is not None:
            try:
                current = sheets.filter(page_index=int(sheet_param)).first()
            except (TypeError, ValueError):
                current = None
        current = current or sheets.first()

        result = (analysis.result or {}) if analysis else {}
        headline = result.get('headline', {})
        findings = SldFinding.objects.filter(analysis_id=analysis.id) if analysis else SldFinding.objects.none()
        st = SldProjectSettings.objects.filter(project=project).first()
        return Response({
            'header': sheet_header(current) or {'drawing_no': 'Drawing number to confirm',
                                                'drawing_no_confirmed': False,
                                                'rev': '', 'title': '',
                                                'readability': 'scan'},
            'sheets': [sheet_header(s) for s in sheets],
            'kpis': {
                'boards': SldNode.objects.filter(analysis_id=analysis.id, kind='board').count()
                          if analysis else 0,
                'findingsOpen': findings.filter(status='open').count(),
                'critical': findings.filter(severity='critical', status='open').count(),
                'loadConflicts': headline.get('loadConflicts', 0),
                'loadTotal': headline.get('loadTotal', 0),
                'eqMatched': headline.get('matched', 0),
                'eqTotal': headline.get('matchedTotal', 0),
                'drafting': headline.get('draftingTotal', 0),
                'interfaces': headline.get('interfaces', 0),
            },
            'steps': (st.stage_state if st else {}),
            'analysis': ({'id': str(analysis.id), 'status': analysis.status,
                          'created_at': analysis.created_at} if analysis else None),
        })


class SheetImageView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, sheet_id):
        sheet = get_object_or_404(SldSheet, id=sheet_id)
        _analysis(request, sheet.analysis_id)
        from django.core.files.storage import default_storage
        if not sheet.image_key or not default_storage.exists(sheet.image_key):
            return Response({'error': 'Image not rendered.'}, status=404)
        fh = default_storage.open(sheet.image_key, 'rb')
        return FileResponse(fh, content_type='image/png')


class ExtractionView(APIView):
    """GET analyses/<id>/extraction/?tab=sources|boards|feeders|loads|
    subpanels|cables|interfaces"""

    permission_classes = [IsAuthenticated]

    def get(self, request, analysis_id):
        analysis = _analysis(request, analysis_id)
        tab = request.query_params.get('tab', 'boards')
        if tab not in EXTRACTION_TABS:
            return Response({'error': f'tab must be one of {sorted(EXTRACTION_TABS)}'}, status=400)
        nodes = SldNode.objects.filter(analysis_id=analysis.id)
        if tab == 'sources':
            rows = [node_row(n) for n in nodes.filter(kind__in=('source', 'transformer', 'generator'))]
        elif tab == 'boards':
            rows = [node_row(n) for n in nodes.filter(kind='board')]
        elif tab == 'loads':
            rows = [node_row(n) for n in nodes.filter(kind__in=('load', 'motor', 'ups'))]
        elif tab == 'subpanels':
            rows = [node_row(n) for n in nodes.filter(kind='subpanel')]
        elif tab == 'interfaces':
            rows = [node_row(n) for n in nodes.filter(kind='interface')]
        elif tab == 'feeders':
            rows = [edge_row(e) for e in
                    SldEdge.objects.filter(analysis_id=analysis.id, kind='feeder')
                    .select_related('from_node', 'to_node')]
        else:  # cables — read-only view of the core rows (never recomputed)
            rows = (analysis.result or {}).get('cables', [])
        return Response({'tab': tab, 'rows': rows})


class NodePatchView(APIView):
    """PATCH nodes/<id>/ {field, value, confirmed} — update a node and
    re-run only the rules that read that field (RULE_NODE_FIELDS)."""

    permission_classes = [IsAuthenticated]

    def patch(self, request, node_id):
        node = get_object_or_404(SldNode, id=node_id)
        analysis = _analysis(request, node.analysis_id)
        field = request.data.get('field')
        value = request.data.get('value')
        confirmed = bool(request.data.get('confirmed'))
        if field not in EDITABLE_NODE_FIELDS:
            return Response({'error': f'field must be one of {sorted(EDITABLE_NODE_FIELDS)}'},
                            status=400)
        node_model_fields = {f.name for f in SldNode._meta.get_fields()}
        if field not in node_model_fields:
            return Response({'error': 'unknown field'}, status=400)
        if field in {'voltage_v', 'rated_a', 'isc_ka', 'kva', 'kw', 'z_pct', 'xd_pct'}:
            try:
                value = float(value) if value not in (None, '') else None
            except (TypeError, ValueError):
                return Response({'error': 'numeric value expected'}, status=400)
        setattr(node, field, value)
        if field == 'tag':
            from .services.pipeline import _norm
            node.norm_tag = _norm(value)
        if confirmed:
            node.needs_review = False
            node.confidence = 1.0
        node.save()

        affected = {rid for rid, fields in RULE_NODE_FIELDS.items() if field in fields}
        updated = []
        if affected:
            updated = _rerun_rules(analysis, affected)
        return Response({'node': node_row(node),
                         'findings': [finding_row(f) for f in updated]})


def _rerun_rules(analysis, rule_ids):
    """Delete open findings of the given rules, re-run them against the
    current nodes/edges, and renumber the affected families in place."""
    config, _ = SldCheckConfig.objects.get_or_create(project=analysis.project)
    nodes = list(SldNode.objects.filter(analysis_id=analysis.id))
    edges = list(SldEdge.objects.filter(analysis_id=analysis.id))
    ctx = Ctx(analysis_id=str(analysis.id), config=config, nodes=nodes, edges=edges)
    new = run_rules(ctx, only=rule_ids)

    SldFinding.objects.filter(analysis_id=analysis.id, rule_id__in=rule_ids,
                              status='open').delete()

    families = {f['family'] for f in new}
    from .services.pipeline import FAMILY_PREFIX, SEV_ORDER
    created = []
    for family in families:
        prefix = FAMILY_PREFIX.get(family, 'Q')
        existing = list(SldFinding.objects.filter(analysis_id=analysis.id,
                                                  family=family))
        fam_new = [f for f in new if f['family'] == family]
        combined = ([{'row': f, 'severity': f.severity, 'rule_id': f.rule_id,
                      'title': f.title} for f in existing]
                    + [{'row': f, 'severity': f['severity'], 'rule_id': f['rule_id'],
                        'title': f['title']} for f in fam_new])
        combined.sort(key=lambda x: (SEV_ORDER.get(x['severity'], 9), x['rule_id'], x['title']))
        for i, entry in enumerate(combined, 1):
            number = f'{prefix}{i}'
            row = entry['row']
            if isinstance(row, SldFinding):
                if row.number != number:
                    row.number = number
                    row.save(update_fields=['number'])
            else:
                row['number'] = number
                created.append(SldFinding.objects.create(analysis_id=analysis.id, **row))
    return SldFinding.objects.filter(analysis_id=analysis.id, rule_id__in=rule_ids)


class TopologyView(APIView):
    permission_classes = [IsAuthenticated]

    LEVELS = {'source': 0, 'transformer': 1, 'generator': 1,
              'board': 2, 'subpanel': 3, 'ups': 3, 'motor': 4, 'load': 4,
              'interface': 5}

    def get(self, request, analysis_id):
        analysis = _analysis(request, analysis_id)
        nodes = list(SldNode.objects.filter(analysis_id=analysis.id))
        edges = list(SldEdge.objects.filter(analysis_id=analysis.id))
        levels = {}
        for n in nodes:
            levels.setdefault(self.LEVELS.get(n.kind, 4), []).append(str(n.id))
        return Response({
            'levels': [{'level': k, 'nodes': v} for k, v in sorted(levels.items())],
            'nodes': [node_row(n) for n in nodes],
            'edges': [edge_row(e) for e in edges],
        })


class FindingsView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, analysis_id):
        analysis = _analysis(request, analysis_id)
        qs = SldFinding.objects.filter(analysis_id=analysis.id)
        family = request.query_params.get('family')
        if family:
            qs = qs.filter(family=family)
        sheet = request.query_params.get('sheet')
        if sheet:
            try:
                qs = qs.filter(sheet__page_index=int(sheet))
            except (TypeError, ValueError):
                pass
        sev_order = {'critical': 0, 'major': 1, 'minor': 2}
        rows = sorted(qs, key=lambda f: (sev_order.get(f.severity, 9), f.number))
        return Response({'findings': [finding_row(f) for f in rows]})


class FindingPatchView(APIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, id):
        f = get_object_or_404(SldFinding, id=id)
        _analysis(request, f.analysis_id)
        status_val = request.data.get('status')
        if status_val is not None:
            if status_val not in ('open', 'confirmed', 'rejected'):
                return Response({'error': 'status must be open|confirmed|rejected'}, status=400)
            f.status = status_val
        if 'engineer_note' in request.data:
            f.engineer_note = request.data.get('engineer_note') or ''
        f.save(update_fields=['status', 'engineer_note'])
        return Response(finding_row(f))


class ChecklistView(APIView):
    """GET/PATCH analyses/<id>/checklist/ — live AI-SLD-01 evaluation;
    PATCH persists engineer state only."""

    permission_classes = [IsAuthenticated]

    def get(self, request, analysis_id):
        analysis = _analysis(request, analysis_id)
        ev = evaluate(analysis)
        if ev is None:
            return Response({'error': 'No AI-SLD-01 template imported.'}, status=404)
        return Response(ev)

    def patch(self, request, analysis_id):
        analysis = _analysis(request, analysis_id)
        template = default_template()
        if template is None:
            return Response({'error': 'No AI-SLD-01 template imported.'}, status=404)
        no = request.data.get('no')
        item = get_object_or_404(SldChecklistItem, template=template, no=no)
        st, _ = SldChecklistResult.objects.get_or_create(
            analysis_id=analysis.id, page=0, item=item)
        if 'applicability' in request.data:
            app = request.data['applicability']
            if app not in ('Y', 'N', 'NA'):
                return Response({'error': 'applicability must be Y|N|NA'}, status=400)
            st.applicability = app
            st.applicability_by = 'engineer'
        if 'engineer_verification' in request.data:
            ver = request.data['engineer_verification']
            if ver not in ('Agree', 'Disagree', 'Modified', None):
                return Response({'error': 'engineer_verification must be Agree|Disagree|Modified'},
                                status=400)
            st.engineer_verification = ver
            if ver:
                st.verified_by = request.user
                st.verified_at = timezone.now()
        if 'remarks' in request.data:
            st.remarks = request.data.get('remarks') or ''
        st.save()
        return Response(evaluate(analysis))


class ChecklistReportView(APIView):
    permission_classes = [IsAuthenticated]

    def get(self, request, analysis_id):
        analysis = _analysis(request, analysis_id)
        from .services.checklist_export import TemplateChanged, build_checklist_report
        try:
            content, filename = build_checklist_report(analysis)
        except TemplateChanged as exc:
            return Response({'error': f'Template changed — re-import it ({exc}).'}, status=409)
        if content is None:
            return Response({'error': 'No AI-SLD-01 template imported.'}, status=404)
        resp = HttpResponse(
            content,
            content_type='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
        resp['Content-Disposition'] = f'attachment; filename="{filename}"'
        return resp


class ConfigView(APIView):
    """GET/PUT projects/<pid>/config/ — SldCheckConfig with defaults."""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = _project(request, project_id)
        cfg, _ = SldCheckConfig.objects.get_or_create(project=project)
        return Response(config_dict(cfg))

    def put(self, request, project_id):
        project = _project(request, project_id)
        cfg, _ = SldCheckConfig.objects.get_or_create(project=project)
        changed = []
        for f in ('dol_kw_gen', 'dol_kw_other', 'spare_pct', 'load_amber',
                  'load_red', 'xd_pct_default', 'pf', 'eff', 'conf_min'):
            if f in request.data:
                try:
                    setattr(cfg, f, float(request.data[f]))
                    changed.append(f)
                except (TypeError, ValueError):
                    return Response({'error': f'{f} must be numeric'}, status=400)
        if changed:
            cfg.save(update_fields=changed)
        return Response(config_dict(cfg))


class ReviewView(APIView):
    """GET projects/<pid>/review/ — All-SLDs aggregate: latest completed
    analysis per document (SLD file), worst-of per checklist item, and all
    findings."""

    permission_classes = [IsAuthenticated]

    def get(self, request, project_id):
        project = _project(request, project_id)
        latest = {}
        for sheet in SldSheet.objects.filter(
                file_id__in=SldAnalysisFile.objects.filter(project=project, kind='sld')
                .values_list('id', flat=True)):
            key = str(sheet.file_id)
            cur = latest.get(key)
            a = SldAnalysis.objects.filter(id=sheet.analysis_id, status='completed').first()
            if a is None:
                continue
            if cur is None or a.created_at > cur.created_at:
                latest[key] = a
        analyses = sorted(latest.values(), key=lambda a: a.created_at)

        worst_order = {'Non-Conformance': 0, 'Query': 1, 'Not Verifiable': 2, 'OK': 3, None: 4}
        per_item = {}
        for a in analyses:
            ev = evaluate(a)
            if ev is None:
                continue
            for it in ev['items']:
                cur = per_item.setdefault(it['no'], {
                    'no': it['no'], 'check_point': it['check_point'],
                    'mode': it['mode'], 'severity': it['severity'],
                    'applicability': 'N', 'ai_result': None,
                    'finding_numbers': [], 'analyses': 0,
                })
                cur['analyses'] += 1
                if it['applicability'] == 'Y':
                    cur['applicability'] = 'Y'
                if worst_order.get(it['ai_result'], 4) < worst_order.get(cur['ai_result'], 4):
                    cur['ai_result'] = it['ai_result']
                cur['finding_numbers'].extend(it['finding_numbers'])

        findings = SldFinding.objects.filter(
            analysis_id__in=[a.id for a in analyses]).order_by('number')
        return Response({
            'documents': [{
                'analysis_id': str(a.id), 'created_at': a.created_at,
                'headline': (a.result or {}).get('headline', {}),
            } for a in analyses],
            'checklist': sorted(per_item.values(),
                                key=lambda x: [int(p) for p in x['no'].split('.')]),
            'findings': [finding_row(f) for f in findings],
        })
