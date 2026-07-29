"""P&ID Checker V2 — DRF serializers."""
from rest_framework import serializers

from .models import (
    PidCheckerV2Extraction,
    PidCheckerV2LineTag,
    PidCheckerV2LegendSheet,
    PidCheckerV2LineListUpload,
    PidCheckerV2LineListRow,
    PidCheckerV2EquipmentListUpload,
    PidCheckerV2EquipmentListRow,
    PidCheckerV2InstrumentIndexUpload,
    PidCheckerV2InstrumentIndexRow,
)
from .services.legend_engine import compile_legend


# Soft-coded field lists — mirror model fields so we never over-expose.
LINE_TAG_FIELDS = (
    'tag', 'size', 'service', 'spec', 'serial', 'service_group',
)

EXTRACTION_LIST_FIELDS = (
    'extraction_id', 'filename', 'file_size_bytes',
    'mode', 'provider', 'model', 'force_ocr',
    'tag_count', 'summary_json', 'created_at',
)


class PidCheckerV2LineTagSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2LineTag
        fields = LINE_TAG_FIELDS


class PidCheckerV2ExtractionListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2Extraction
        fields = EXTRACTION_LIST_FIELDS
        read_only_fields = fields


class PidCheckerV2ExtractionDetailSerializer(serializers.ModelSerializer):
    tags = PidCheckerV2LineTagSerializer(source='line_tags', many=True, read_only=True)

    class Meta:
        model = PidCheckerV2Extraction
        fields = EXTRACTION_LIST_FIELDS + ('tags',)
        read_only_fields = fields


# ── Legend Sheets ─────────────────────────────────────────────────────
LEGEND_FIELDS = (
    'legend_id', 'section', 'name', 'description',
    'definition', 'is_active', 'created_at', 'updated_at',
)
LEGEND_READ_ONLY = ('legend_id', 'is_active', 'created_at', 'updated_at')


class PidCheckerV2LegendSheetSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2LegendSheet
        fields = LEGEND_FIELDS
        read_only_fields = LEGEND_READ_ONLY

    def validate_definition(self, value):
        try:
            compile_legend(value)
        except Exception as exc:
            raise serializers.ValidationError(str(exc))
        return value


# ── Line List uploads ────────────────────────────────────────────────
LINE_LIST_ROW_FIELDS = (
    'excel_row', 'tag', 'size', 'service_code', 'serial', 'spec',
    'from_ref', 'to_ref', 'pid_no', 'fluid_service', 'extras',
)
LINE_LIST_LIST_FIELDS = (
    'line_list_id', 'filename', 'sheet_name', 'title', 'doc_no', 'doc_date',
    'pid_extract_ref', 'total_rows', 'summary', 'is_active',
    'created_at', 'updated_at',
)


class PidCheckerV2LineListRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2LineListRow
        fields = LINE_LIST_ROW_FIELDS


class PidCheckerV2LineListListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2LineListUpload
        fields = LINE_LIST_LIST_FIELDS
        read_only_fields = fields


class PidCheckerV2LineListDetailSerializer(serializers.ModelSerializer):
    rows = PidCheckerV2LineListRowSerializer(many=True, read_only=True)
    columns = serializers.JSONField(read_only=True)

    class Meta:
        model = PidCheckerV2LineListUpload
        fields = LINE_LIST_LIST_FIELDS + ('columns', 'rows',)
        read_only_fields = fields


# ── Equipment List uploads ───────────────────────────────────────────
EQUIPMENT_LIST_ROW_FIELDS = (
    'excel_row', 'tag', 'description', 'design_flow', 'op_pressure', 'op_temp',
    'design_p_min', 'design_p_max', 'design_t_min', 'design_t_max',
    'moc', 'insulation', 'dim_length', 'dim_diameter', 'motor_rating',
    'pid_no', 'qty', 'phase', 'remarks', 'extras',
)
EQUIPMENT_LIST_LIST_FIELDS = (
    'equipment_list_id', 'filename', 'sheet_name', 'title', 'doc_no', 'doc_date',
    'pid_extract_ref', 'company', 'project', 'total_rows', 'summary', 'is_active',
    'created_at', 'updated_at',
)


class PidCheckerV2EquipmentListRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2EquipmentListRow
        fields = EQUIPMENT_LIST_ROW_FIELDS


class PidCheckerV2EquipmentListListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2EquipmentListUpload
        fields = EQUIPMENT_LIST_LIST_FIELDS
        read_only_fields = fields


class PidCheckerV2EquipmentListDetailSerializer(serializers.ModelSerializer):
    rows = PidCheckerV2EquipmentListRowSerializer(many=True, read_only=True)
    columns = serializers.JSONField(read_only=True)

    class Meta:
        model = PidCheckerV2EquipmentListUpload
        fields = EQUIPMENT_LIST_LIST_FIELDS + ('columns', 'rows',)
        read_only_fields = fields


# ── Instrument Index uploads ─────────────────────────────
INSTRUMENT_INDEX_ROW_FIELDS = (
    'excel_row', 'tag', 'instrument_type', 'service_description',
    'pid_no', 'line_no', 'eqpt_no', 'location', 'ex_class', 'power_supply',
    'range_min', 'range_max', 'range_unit',
    'cal_min', 'cal_max', 'cal_unit',
    'datasheet_no', 'loop_dwg_no', 'hookup_dwg_no', 'location_layout_no',
    'manufacturer', 'model', 'remarks', 'rev', 'extras',
)
INSTRUMENT_INDEX_LIST_FIELDS = (
    'instrument_index_id', 'filename', 'sheet_name', 'title', 'doc_no', 'doc_date',
    'pid_extract_ref', 'company', 'project', 'total_rows', 'summary', 'is_active',
    'created_at', 'updated_at',
)


class PidCheckerV2InstrumentIndexRowSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2InstrumentIndexRow
        fields = INSTRUMENT_INDEX_ROW_FIELDS


class PidCheckerV2InstrumentIndexListSerializer(serializers.ModelSerializer):
    class Meta:
        model = PidCheckerV2InstrumentIndexUpload
        fields = INSTRUMENT_INDEX_LIST_FIELDS
        read_only_fields = fields


class PidCheckerV2InstrumentIndexDetailSerializer(serializers.ModelSerializer):
    rows = PidCheckerV2InstrumentIndexRowSerializer(many=True, read_only=True)
    columns = serializers.JSONField(read_only=True)

    class Meta:
        model = PidCheckerV2InstrumentIndexUpload
        fields = INSTRUMENT_INDEX_LIST_FIELDS + ('columns', 'rows',)
        read_only_fields = fields

