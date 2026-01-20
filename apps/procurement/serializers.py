"""
Procurement Management Serializers
API data serialization for procurement workflows
"""

from rest_framework import serializers
from .models import Vendor, PurchaseRequisition, PurchaseOrder, Receipt, PROCUREMENT_CATEGORIES


class VendorSerializer(serializers.ModelSerializer):
    """Serializer for Vendor model"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    rating_display = serializers.CharField(source='get_rating_display', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = Vendor
        fields = [
            'id', 'vendor_code', 'name', 'contact_person', 'email', 'phone', 'address',
            'country', 'tax_id', 'payment_terms', 'credit_limit', 'status', 'status_display',
            'rating', 'rating_display', 'performance_notes', 'categories', 'created_by',
            'created_by_name', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class PurchaseRequisitionSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Requisition"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    priority_display = serializers.CharField(source='get_priority_display', read_only=True)
    requested_by_name = serializers.CharField(source='requested_by.get_full_name', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    category_display = serializers.SerializerMethodField()
    
    class Meta:
        model = PurchaseRequisition
        fields = [
            'id', 'pr_number', 'title', 'description', 'category', 'category_display',
            'requested_by', 'requested_by_name', 'department', 'project', 'status',
            'status_display', 'priority', 'priority_display', 'required_date',
            'estimated_budget', 'items', 'approved_by', 'approved_by_name',
            'approved_at', 'rejection_reason', 'notes', 'attachments',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)
    
    def create(self, validated_data):
        validated_data['requested_by'] = self.context['request'].user
        return super().create(validated_data)


class PurchaseOrderSerializer(serializers.ModelSerializer):
    """Serializer for Purchase Order"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    vendor_name = serializers.CharField(source='vendor.name', read_only=True)
    category_display = serializers.SerializerMethodField()
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True, allow_null=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    
    class Meta:
        model = PurchaseOrder
        fields = [
            'id', 'po_number', 'pr_reference', 'vendor', 'vendor_name', 'title',
            'description', 'status', 'status_display', 'category', 'category_display',
            'total_amount', 'currency', 'tax_amount', 'discount_amount', 'items',
            'po_date', 'expected_delivery', 'actual_delivery', 'created_by',
            'created_by_name', 'approved_by', 'approved_by_name', 'terms_and_conditions',
            'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'po_date', 'created_at', 'updated_at']
    
    def get_category_display(self, obj):
        return PROCUREMENT_CATEGORIES.get(obj.category, {}).get('name', obj.category)
    
    def create(self, validated_data):
        validated_data['created_by'] = self.context['request'].user
        return super().create(validated_data)


class ReceiptSerializer(serializers.ModelSerializer):
    """Serializer for Goods Receipt"""
    
    status_display = serializers.CharField(source='get_status_display', read_only=True)
    received_by_name = serializers.CharField(source='received_by.get_full_name', read_only=True, allow_null=True)
    po_number = serializers.CharField(source='purchase_order.po_number', read_only=True)
    
    class Meta:
        model = Receipt
        fields = [
            'id', 'receipt_number', 'purchase_order', 'po_number', 'receipt_date',
            'received_by', 'received_by_name', 'status', 'status_display',
            'items_received', 'quality_check_passed', 'inspection_notes',
            'delivery_note_number', 'notes', 'attachments', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'receipt_date', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        validated_data['received_by'] = self.context['request'].user
        return super().create(validated_data)


class ProcurementCategorySerializer(serializers.Serializer):
    """Serializer for procurement category configuration"""
    
    code = serializers.CharField()
    name = serializers.CharField()
    icon = serializers.CharField()
    color = serializers.CharField()
