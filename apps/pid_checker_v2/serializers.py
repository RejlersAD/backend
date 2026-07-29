"""P&ID Checker V2 — DRF serializers."""
from rest_framework import serializers

from .models import (
    PidCheckerV2Extraction,
    PidCheckerV2LineTag,
    PidCheckerV2LegendSheet,
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
