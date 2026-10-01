from rest_framework import serializers

from .models import ValveMTOProject, ValveMTORow


class ValveMTORowSerializer(serializers.ModelSerializer):
    class Meta:
        model = ValveMTORow
        fields = [
            'id', 'project', 'tag_number', 'valve_type', 'pms_class', 'rating', 'facing',
            'size_primary', 'size_secondary', 'size_secondary_2', 'line_number', 'line_list_ref',
            'pid_number', 'description', 'qty_island', 'qty_field', 'qty_combined', 'unit',
            'area', 'operational_status', 'remarks', 'tab', 'row_order',
            'created_at', 'updated_at',
        ]
        read_only_fields = ['id', 'project', 'created_at', 'updated_at']


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
