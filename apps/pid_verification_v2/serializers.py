"""
P&ID Verification Serializers
"""
from rest_framework import serializers
from .models import PIDVProject, PIDVDocument, PIDVDrawing, PIDVFinding, PIDVComparisonFinding


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class PIDVProjectSerializer(serializers.ModelSerializer):
    document_count      = serializers.SerializerMethodField()
    has_project_legend  = serializers.SerializerMethodField()

    class Meta:
        model  = PIDVProject
        fields = [
            'project_id', 'project_name', 'description',
            'document_count', 'has_project_legend',
            'legend_knowledge_data', 'legend_built_at',
            'created_at', 'updated_at',
        ]

    def get_document_count(self, obj):
        return obj.documents.count()

    def get_has_project_legend(self, obj):
        # SOFT-CODED: Check if project has legend knowledge extracted
        return obj.legend_knowledge_data is not None and obj.legend_knowledge_data != {}


class PIDVProjectCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PIDVProject
        fields = ['project_name', 'description']


# ---------------------------------------------------------------------------
# Findings / Drawings / Documents
# ---------------------------------------------------------------------------

class PIDVFindingSerializer(serializers.ModelSerializer):
    locations = serializers.SerializerMethodField()
    sheet_id = serializers.SerializerMethodField()
    display_id = serializers.SerializerMethodField()
    report = serializers.SerializerMethodField()

    class Meta:
        model  = PIDVFinding
        fields = [
            'id', 'sl_no', 'display_id', 'category', 'issue_observed',
            'action_required', 'evidence', 'direction',
            'severity', 'status', 'rule_id', 'created_at',
            'entity_id', 'bbox', 'locations', 'sheet_id', 'report',
        ]

    def get_display_id(self, obj):
        """Checklist-serial label ('9.1', '9.1-2'…) — computed once per
        request for the whole document and cached in the serializer
        context; falls back to F<sl_no> for unmapped rules."""
        m = self.context.get('_display_ids')
        if m is None:
            m = {}
            doc = self.context.get('document')
            if doc is not None:
                try:
                    from .services.display_ids import assign_display_ids
                    m = assign_display_ids(
                        PIDVFinding.objects.filter(drawing__document=doc)
                        .select_related('drawing')
                    )
                except Exception:
                    m = {}
            self.context['_display_ids'] = m
        return m.get(obj.id) or f'F{obj.sl_no}'

    def get_report(self, obj):
        """§2.5 fields for the AI Comment Register. `valid` is False when a
        finding lacks a sheet, grid or tag — the export then lists it for
        manual check instead of guessing."""
        rm = obj.report_meta or {}
        out = {
            'sheet_label': rm.get('sheet_label'),
            'grid': rm.get('grid'),
            'tag': rm.get('tag'),
            'finding_text': rm.get('finding_text') or obj.issue_observed,
            'reference': rm.get('reference'),
            'suggested_correction': rm.get('suggested_correction') or obj.action_required,
            'confidence': rm.get('confidence'),
            'ai_result': rm.get('ai_result'),
            'checklist_items': rm.get('checklist_items') or [],
        }
        out['valid'] = bool(out['sheet_label'] and out['grid'] and out['grid'] != '-' and out['tag'] and out['tag'] != '-')
        return out

    def get_sheet_id(self, obj):
        # {drawing_id: sheet_id} — built once per request by get_results /
        # the cache writer (spec A1.4).
        m = self.context.get('_sheet_by_drawing')
        if m is None:
            m = {}
            doc = self.context.get('document')
            if doc is not None:
                try:
                    from .models import PIDVSheet
                    by_docpage = {(s.document_id, s.page_index): s.pk
                                  for s in PIDVSheet.objects.filter(document=doc)}
                    for d in doc.drawings.all():
                        m[d.id] = by_docpage.get((d.document_id, d.page_index))
                except Exception:
                    pass
            self.context['_sheet_by_drawing'] = m
        return m.get(obj.drawing_id)

    def get_locations(self, obj):
        """One bbox per linked extraction item (e.g. note 58 → one marker
        per affected BW line), falling back to the finding's own bbox.
        The linked-item map is built once per request and shared via the
        serializer context (set by get_results)."""
        ctx = self.context
        cache = ctx.get('_locations_cache')
        if cache is None:
            cache = {}
            doc = ctx.get('document')
            if doc is not None:
                try:
                    from .models import PIDVExtractionItem
                    for bbox, fids in (
                        PIDVExtractionItem.objects
                        .filter(document=doc)
                        .exclude(bbox__isnull=True)
                        .values_list('bbox', 'finding_ids')
                    ):
                        for fid in fids or []:
                            cache.setdefault(fid, []).append(bbox)
                except Exception:
                    pass
            ctx['_locations_cache'] = cache
        locs = list(cache.get(obj.id) or [])
        if not locs and obj.bbox:
            locs = [obj.bbox]
        return locs


class PIDVFindingUpdateSerializer(serializers.ModelSerializer):
    """Used by the PATCH /findings/<id>/ endpoint for engineer review overrides."""
    class Meta:
        model  = PIDVFinding
        fields = ['severity', 'status']
        extra_kwargs = {
            'severity': {'required': False},
            'status':   {'required': False},
        }


class PIDVDrawingSerializer(serializers.ModelSerializer):
    issues = serializers.SerializerMethodField()
    issue_count = serializers.SerializerMethodField()

    class Meta:
        model  = PIDVDrawing
        fields = ['id', 'drawing_id', 'title', 'page_index', 'metadata', 'issue_count', 'issues', 'created_at']

    def get_issues(self, obj):
        # Per-sheet fetch (30 Oct 2026): with a 'page' in the serializer
        # context, only the requested sheet carries its findings — the
        # other sheets still appear (metadata drives the sheet selector)
        # but ship empty issue lists so a 76-sheet document answers in
        # under a second instead of a 1.1 MB payload.
        page = self.context.get('page')
        if page is not None and obj.page_index != page:
            return []
        return PIDVFindingSerializer(obj.findings.all(), many=True, context=self.context).data

    def get_issue_count(self, obj):
        return obj.findings.count()


class PIDVComparisonFindingSerializer(serializers.ModelSerializer):
    class Meta:
        model  = PIDVComparisonFinding
        fields = [
            'finding_id', 'finding_type', 'severity', 'title', 'description',
            'evidence', 'ai_suggestion', 'ai_confidence', 'location_info',
            'status', 'created_at',
        ]


class PIDVDocumentSerializer(serializers.ModelSerializer):
    drawings = PIDVDrawingSerializer(many=True, read_only=True)
    total_issues = serializers.SerializerMethodField()
    project_id   = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)
    # Legend Sheets / Symbol Images bridge (apps.pid_checker_v2) — text
    # matches and visually-identified symbols, cross-referenced. See
    # services/legend_bridge.py + orchestrator.LegendSymbolBridgeStage.
    comparison_findings = serializers.SerializerMethodField()

    class Meta:
        model  = PIDVDocument
        fields = [
            'document_id', 'file_name', 's3_path', 'status',
            'error_message', 'excel_s3_url', 'pdf_s3_url',
            'project_id', 'project_name',
            'total_issues', 'drawings', 'comparison_findings', 'created_at', 'updated_at',
        ]

    def get_total_issues(self, obj):
        return PIDVFinding.objects.filter(drawing__document=obj).count()

    def get_comparison_findings(self, obj):
        if obj.project_id is None:
            return []
        qs = PIDVComparisonFinding.objects.filter(
            project=obj.project,
            finding_type=PIDVComparisonFinding.FindingType.SYMBOL_LEGEND_MATCH,
            evidence__document_id=str(obj.document_id),
        ).order_by('-ai_confidence')
        return PIDVComparisonFindingSerializer(qs, many=True).data


class PIDVDocumentListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for list views (no nested findings)."""
    total_drawings = serializers.SerializerMethodField()
    total_issues   = serializers.SerializerMethodField()
    project_id     = serializers.UUIDField(source='project.project_id', read_only=True, allow_null=True)
    project_name   = serializers.CharField(source='project.project_name', read_only=True, allow_null=True)
    # Title-block fields from the first drawing's metadata (spec B).
    title          = serializers.SerializerMethodField()
    drawing_number = serializers.SerializerMethodField()
    revision       = serializers.SerializerMethodField()
    readability    = serializers.SerializerMethodField()
    # Per-sheet title blocks (one row per sheet in the drawings table) and
    # cross-sheet tag occurrences (the "Tags across sheets" table in
    # Drawing sets — the data that used to be raised as XREF-001 findings).
    sheets             = serializers.SerializerMethodField()
    tags_across_sheets = serializers.SerializerMethodField()

    class Meta:
        model  = PIDVDocument
        fields = [
            'document_id', 'file_name', 'status', 'drawing_type',
            'project_id', 'project_name',
            'title', 'drawing_number', 'revision', 'readability',
            'reviewed_by', 'approved_by',
            'total_drawings', 'total_issues',
            'sheets', 'tags_across_sheets',
            'excel_s3_url', 'pdf_s3_url',
            'created_at', 'updated_at',
        ]

    def get_sheets(self, obj):
        out = []
        for d in obj.drawings.order_by('page_index'):
            tb = (d.metadata or {}).get('title_block') or {}
            out.append({
                'page_index': d.page_index,
                'drawing_number': tb.get('drawing_number') or d.title or None,
                'title': tb.get('title') or None,
            })
        return out

    def get_tags_across_sheets(self, obj):
        try:
            from .services.cross_reference import group_multi_page_tags
            rows = list(obj.tag_index.values('tag', 'raw_tag', 'page_number', 'tag_type'))
            return group_multi_page_tags(rows)
        except Exception:
            return []

    def get_total_drawings(self, obj):
        return obj.drawings.count()

    def get_total_issues(self, obj):
        return PIDVFinding.objects.filter(drawing__document=obj).count()

    def _title_block(self, obj):
        first = obj.drawings.order_by('page_index').first()
        return (first.metadata or {}).get('title_block') or {} if first else {}

    def get_title(self, obj):
        return self._title_block(obj).get('title') or (obj.drawings.order_by('page_index').first().title if obj.drawings.exists() else None)

    def get_drawing_number(self, obj):
        return self._title_block(obj).get('drawing_number') or None

    def get_revision(self, obj):
        return self._title_block(obj).get('revision') or None

    def get_readability(self, obj):
        first = obj.drawings.order_by('page_index').first()
        if not first:
            return None
        meta = first.metadata or {}
        if meta.get('ocr_used') or meta.get('is_scanned'):
            return 'ocr'
        return meta.get('readability') or None


class UploadSerializer(serializers.Serializer):
    """Validates the file upload request."""
    file       = serializers.FileField()
    project_id = serializers.UUIDField(required=False, allow_null=True)
