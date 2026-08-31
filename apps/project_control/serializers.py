from rest_framework import serializers
from django.core.exceptions import ValidationError as DjangoValidationError

from .config import MAX_DOCUMENT_BYTES
from .models import (
    BudgetAllocation,
    ChangeEvent,
    CostAllocation,
    CostLedgerEntry,
    CostSnapshot,
    Estimate,
    EstimateLineItem,
    PlanningPackage,
    ProjectDocument,
    WBSNode,
)
from .services.cost_ledger import allocation_totals, source_record, source_value


class EstimateLineItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = EstimateLineItem
        fields = [
            'id', 'estimate', 'wbs_code', 'description', 'discipline', 'category',
            'unit', 'quantity', 'unit_rate', 'line_total', 'sort_order', 'source_row',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at')


class EstimateSerializer(serializers.ModelSerializer):
    line_items = EstimateLineItemSerializer(many=True, read_only=True)
    line_item_count = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    source_display = serializers.CharField(source='get_source_display', read_only=True)

    class Meta:
        model = Estimate
        fields = [
            'id', 'project', 'version', 'kind', 'kind_display',
            'source', 'source_display', 'status', 'status_display',
            'title', 'currency', 'total_amount', 'snapshot_date', 'notes',
            'source_document', 'created_by',
            'line_items', 'line_item_count',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at', 'created_by', 'line_items', 'line_item_count')

    def get_line_item_count(self, obj):
        return obj.line_items.filter(is_deleted=False).count()


class EstimateListSerializer(serializers.ModelSerializer):
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    line_item_count = serializers.SerializerMethodField()

    class Meta:
        model = Estimate
        fields = [
            'id', 'project', 'version', 'kind', 'kind_display',
            'status', 'status_display', 'source',
            'title', 'currency', 'total_amount', 'snapshot_date',
            'line_item_count', 'created_at', 'updated_at',
        ]

    def get_line_item_count(self, obj):
        return obj.line_items.filter(is_deleted=False).count()


class WBSNodeSerializer(serializers.ModelSerializer):
    class Meta:
        model = WBSNode
        fields = ['id', 'project', 'parent', 'code', 'name', 'level', 'sort_order',
                  'created_at', 'updated_at']
        read_only_fields = ('created_at', 'updated_at')


class BudgetAllocationSerializer(serializers.ModelSerializer):
    wbs_code = serializers.CharField(source='wbs_node.code', read_only=True)
    wbs_name = serializers.CharField(source='wbs_node.name', read_only=True)
    approved_by_name = serializers.SerializerMethodField()

    class Meta:
        model = BudgetAllocation
        fields = [
            'id', 'project', 'wbs_node', 'wbs_code', 'wbs_name', 'code', 'name',
            'category', 'amount', 'currency', 'status', 'source_budget', 'notes',
            'approved_by', 'approved_by_name', 'approved_at', 'created_at', 'updated_at',
        ]
        read_only_fields = ('status', 'approved_by', 'approved_by_name', 'approved_at', 'created_at', 'updated_at')

    def get_approved_by_name(self, obj):
        return obj.approved_by.get_full_name() if obj.approved_by else None

    def validate(self, attrs):
        attrs = super().validate(attrs)
        project = attrs.get('project', getattr(self.instance, 'project', None))
        wbs = attrs.get('wbs_node', getattr(self.instance, 'wbs_node', None))
        if project and wbs and wbs.project_id != project.pk:
            raise serializers.ValidationError({'wbs_node': 'WBS node must belong to the selected project.'})
        currency = attrs.get('currency', getattr(self.instance, 'currency', project.currency if project else 'AED'))
        if project and currency != (project.currency or 'AED'):
            raise serializers.ValidationError({'currency': 'Budget currency must match the project control currency.'})
        attrs['currency'] = currency
        source_budget = attrs.get('source_budget', getattr(self.instance, 'source_budget', None))
        if source_budget and source_budget.project.enterprise_project_id != project.pk:
            raise serializers.ValidationError({'source_budget': 'Procurement budget is not linked to this enterprise project.'})
        return attrs


class CostAllocationSerializer(serializers.ModelSerializer):
    project_code = serializers.CharField(source='project.code', read_only=True)
    wbs_code = serializers.CharField(source='wbs_node.code', read_only=True)
    wbs_name = serializers.CharField(source='wbs_node.name', read_only=True)
    budget_code = serializers.CharField(source='budget_allocation.code', read_only=True, allow_null=True)

    class Meta:
        model = CostAllocation
        fields = [
            'id', 'project', 'project_code', 'wbs_node', 'wbs_code', 'wbs_name',
            'budget_allocation', 'budget_code', 'source_type', 'source_id',
            'source_reference', 'amount', 'currency', 'status', 'notes',
            'allocated_by', 'approved_by', 'approved_at', 'created_at', 'updated_at',
        ]
        read_only_fields = (
            'source_reference', 'currency', 'status', 'allocated_by', 'approved_by',
            'approved_at', 'created_at', 'updated_at',
        )

    def validate(self, attrs):
        attrs = super().validate(attrs)
        project = attrs.get('project', getattr(self.instance, 'project', None))
        wbs = attrs.get('wbs_node', getattr(self.instance, 'wbs_node', None))
        budget = attrs.get('budget_allocation', getattr(self.instance, 'budget_allocation', None))
        if project and wbs and wbs.project_id != project.pk:
            raise serializers.ValidationError({'wbs_node': 'WBS node must belong to the selected project.'})
        if budget and (budget.project_id != project.pk or budget.wbs_node_id != wbs.pk):
            raise serializers.ValidationError({'budget_allocation': 'Budget must belong to the selected project and WBS node.'})

        source_type = attrs.get('source_type', getattr(self.instance, 'source_type', None))
        source_id = attrs.get('source_id', getattr(self.instance, 'source_id', None))
        if source_type == 'manual':
            attrs.setdefault('source_reference', 'Manual adjustment')
            return attrs
        try:
            row = source_record(source_type, source_id)
        except (DjangoValidationError, ValueError, TypeError):
            row = None
        if row is None:
            raise serializers.ValidationError({'source_id': 'The source record was not found.'})
        source_amount, currency, reference = source_value(source_type, row)
        if project and currency and currency != (project.currency or 'AED'):
            raise serializers.ValidationError({
                'currency': 'Source currency differs from the project currency. Convert it before allocation.'
            })
        amount = attrs.get('amount', getattr(self.instance, 'amount', 0))
        allocated = allocation_totals(
            source_type, source_id, exclude_id=getattr(self.instance, 'pk', None),
        )
        if allocated + amount > source_amount:
            raise serializers.ValidationError({
                'amount': f'Allocations would exceed source value {source_amount} {currency}.'
            })
        attrs['source_reference'] = reference
        attrs['currency'] = currency or attrs.get('currency', 'AED')
        return attrs


class CostLedgerEntrySerializer(serializers.ModelSerializer):
    project_code = serializers.CharField(source='project.code', read_only=True)
    wbs_code = serializers.CharField(source='wbs_node.code', read_only=True, allow_null=True)
    wbs_name = serializers.CharField(source='wbs_node.name', read_only=True, allow_null=True)

    class Meta:
        model = CostLedgerEntry
        fields = [
            'id', 'project', 'project_code', 'wbs_node', 'wbs_code', 'wbs_name',
            'budget_allocation', 'cost_allocation', 'entry_key', 'entry_type',
            'amount', 'currency', 'source_type', 'source_id', 'source_reference',
            'entry_date', 'status', 'metadata', 'created_at', 'updated_at',
        ]
        read_only_fields = fields


class ProjectDocumentSerializer(serializers.ModelSerializer):
    file = serializers.FileField(write_only=True, required=True)
    file_url = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()
    kind_display = serializers.CharField(source='get_kind_display', read_only=True)
    parse_status_display = serializers.CharField(source='get_parse_status_display', read_only=True)
    uploaded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = ProjectDocument
        fields = [
            'id', 'project', 'kind', 'kind_display', 'title',
            'file', 'file_url', 'download_url',
            'original_filename', 'content_type', 'size_bytes',
            'parse_status', 'parse_status_display', 'parsed_data', 'parse_error',
            'uploaded_by', 'uploaded_by_name',
            'created_at', 'updated_at',
        ]
        read_only_fields = (
            'created_at', 'updated_at', 'uploaded_by', 'uploaded_by_name',
            'original_filename', 'content_type', 'size_bytes',
            'parse_status', 'parsed_data', 'parse_error',
            'file_url', 'download_url',
        )

    def get_file_url(self, obj):
        try:
            return obj.file.url if obj.file else None
        except Exception:
            return None

    def get_download_url(self, obj):
        # Direct presigned URL goes via the dedicated `presign-download` action.
        # This field surfaces the storage URL (already presigned for S3 backends).
        return self.get_file_url(obj)

    def get_uploaded_by_name(self, obj):
        if not obj.uploaded_by_id:
            return None
        u = obj.uploaded_by
        return u.get_full_name() or getattr(u, 'email', None) or getattr(u, 'username', None)

    def validate_file(self, value):
        if value.size > MAX_DOCUMENT_BYTES:
            raise serializers.ValidationError(f'File exceeds the {MAX_DOCUMENT_BYTES} byte limit.')
        return value


class CostSnapshotSerializer(serializers.ModelSerializer):
    class Meta:
        model = CostSnapshot
        fields = ['id', 'project', 'period_end', 'planned_value', 'earned_value', 'actual_cost',
                  'cpi', 'spi', 'eac', 'source', 'notes', 'created_at', 'updated_at']
        read_only_fields = ('created_at', 'updated_at')


class ChangeEventSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChangeEvent
        fields = [
            'id', 'project', 'source_document', 'detected_at',
            'summary', 'description', 'severity',
            'delta_amount', 'delta_currency',
            'status', 'ai_confidence', 'reviewed_by',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at', 'detected_at', 'ai_confidence')


class PlanningPackageSerializer(serializers.ModelSerializer):
    """
    SOFT-CODED: Planning Package serializer with computed fields
    All display fields come from model choice enums
    """
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    package_manager_name = serializers.SerializerMethodField()
    wbs_node_display = serializers.SerializerMethodField()
    budget_variance = serializers.DecimalField(
        max_digits=14, decimal_places=2, read_only=True
    )
    is_over_budget = serializers.BooleanField(read_only=True)
    days_remaining = serializers.IntegerField(read_only=True)
    
    class Meta:
        model = PlanningPackage
        fields = [
            'id', 'project', 'package_code', 'name', 'description',
            'status', 'status_display', 'priority', 'priority_display',
            'budget', 'currency', 'actual_cost', 'budget_variance', 'is_over_budget',
            'planned_start', 'planned_end', 'actual_start', 'actual_end', 'days_remaining',
            'progress_percentage', 'package_manager', 'package_manager_name',
            'wbs_node', 'wbs_node_display', 'deliverables', 'notes',
            'created_at', 'updated_at',
        ]
        read_only_fields = ('created_at', 'updated_at', 'budget_variance', 'is_over_budget', 'days_remaining')
    
    def get_package_manager_name(self, obj):
        if obj.package_manager:
            return f"{obj.package_manager.first_name} {obj.package_manager.last_name}".strip() or obj.package_manager.email
        return None
    
    def get_wbs_node_display(self, obj):
        if obj.wbs_node:
            return f"{obj.wbs_node.code} - {obj.wbs_node.name}"
        return None


class PlanningPackageListSerializer(serializers.ModelSerializer):
    """Lightweight list serializer for Planning Packages"""
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    progress_percentage = serializers.DecimalField(max_digits=5, decimal_places=2)
    
    class Meta:
        model = PlanningPackage
        fields = [
            'id', 'package_code', 'name', 'status', 'status_display',
            'priority', 'priority_display', 'budget', 'actual_cost',
            'planned_start', 'planned_end', 'progress_percentage',
            'created_at', 'updated_at',
        ]
