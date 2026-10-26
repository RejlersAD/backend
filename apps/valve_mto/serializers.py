from rest_framework import serializers

from .models import (
    ValveMTODrawing, ValveMTOJob, ValveMTOLegend,
    ValveMTOProject, ValveMTOProjectSettings, ValveMTORow,
)


class ValveMTOLegendSerializer(serializers.ModelSerializer):
    # 'legend_id' (not 'id') on purpose — matches the shape
    # PidCheckerV2LegendSheetSerializer already returns, which
    # LegendSheetsModal.jsx/ValveMTO.jsx were written against (e.g.
    # `activateLegend(created.legend_id)`), so swapping which service
    # module they call needed no further shape changes.
    legend_id = serializers.UUIDField(source='id', read_only=True)

    class Meta:
        model = ValveMTOLegend
        fields = [
            'legend_id', 'section', 'name', 'description', 'definition',
            'is_active', 'created_at', 'updated_at',
        ]
        read_only_fields = ['is_active', 'created_at', 'updated_at']


class ValveMTORowSerializer(serializers.ModelSerializer):
    evidence_crop_url = serializers.SerializerMethodField()

    class Meta:
        model = ValveMTORow
        fields = [
            'id', 'project', 'tag_number', 'valve_type', 'pms_class', 'piping_class', 'rating', 'facing',
            'size_primary', 'size_secondary', 'size_secondary_2', 'line_number', 'line_list_ref',
            'pid_number', 'description', 'qty_island', 'qty_field', 'qty_combined', 'qty_observed', 'unit',
            'area', 'operational_status', 'remarks', 'confidence', 'location_hint', 'tab', 'row_order',
            'page', 'pos_x', 'pos_y', 'variant', 'category', 'type_basis', 'symbol_group_id',
            'scope_status', 'piping_class_source', 'material_source', 'pid_rev',
            'confidence_score', 'evidence_crop_url',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'project', 'created_at', 'updated_at']

    def get_evidence_crop_url(self, obj):
        if not obj.evidence_crop:
            return ''
        request = self.context.get('request')
        url = obj.evidence_crop.url
        return request.build_absolute_uri(url) if request else url


class ValveMTOProjectSettingsSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValveMTOProjectSettings
        fields = [
            'line_pattern_preset', 'line_pattern_regex',
            'pms_data', 'pms_skipped', 'pms_infer_from_drawing',
            'engine_method', 'engine_scope', 'engine_instruments',
            'engine_review_threshold', 'legend', 'legend_ack',
            'stage_state', 'extraction_outdated',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class ValveMTODrawingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValveMTODrawing
        fields = [
            'id', 'original_name', 'page_count', 'pdf_type', 'parsed',
            'confirmed_drawing_number', 'confirmed_rev', 'created_at', 'updated_at',
        ]
        read_only_fields = ['created_at', 'updated_at']


class ValveMTOJobSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValveMTOJob
        fields = ['id', 'project', 'status', 'current_step', 'steps', 'error',
                  'result', 'created_at', 'updated_at']
        read_only_fields = fields


class ValveMTOProjectSerializer(serializers.ModelSerializer):
    row_count = serializers.IntegerField(source='rows.count', read_only=True)

    class Meta:
        model = ValveMTOProject
        fields = [
            'id', 'project_name', 'created_by', 'created_at', 'updated_at',
            'source_pdf_name', 'status', 'row_count',
        ]
        read_only_fields = ['id', 'created_by', 'created_at', 'updated_at']


class ValveMTOProjectDetailSerializer(ValveMTOProjectSerializer):
    """Adds the full row list — used for retrieve(), not list() (a list
    of every project's every row would be an unbounded, unnecessary
    payload for the collection endpoint)."""
    rows = ValveMTORowSerializer(many=True, read_only=True)

    class Meta(ValveMTOProjectSerializer.Meta):
        fields = ValveMTOProjectSerializer.Meta.fields + ['rows']
