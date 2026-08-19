from rest_framework import serializers
from .models import CustomerInvoice, InvoiceAttachment


class InvoiceAttachmentSerializer(serializers.ModelSerializer):
    file_url = serializers.SerializerMethodField()
    uploaded_by_email = serializers.CharField(source='uploaded_by.email', read_only=True)

    class Meta:
        model = InvoiceAttachment
        fields = [
            'id', 'file', 'file_url', 'original_filename', 'content_type',
            'size_bytes', 'uploaded_at', 'uploaded_by_email',
        ]
        read_only_fields = ['id', 'file_url', 'uploaded_at', 'uploaded_by_email',
                            'content_type', 'size_bytes']

    def get_file_url(self, obj):
        try:
            return obj.file.url if obj.file else None
        except Exception:
            return None


class CustomerInvoiceSerializer(serializers.ModelSerializer):
    attachments = InvoiceAttachmentSerializer(many=True, read_only=True)
    attachments_count = serializers.IntegerField(source='attachments.count', read_only=True)
    payment_status_label = serializers.CharField(source='get_payment_status_display', read_only=True)
    category_label = serializers.CharField(source='get_category_display', read_only=True)

    class Meta:
        model = CustomerInvoice
        fields = '__all__'
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by',
                            'days_overdue', 'attachments', 'attachments_count',
                            'payment_status_label', 'category_label']

    def validate(self, attrs):
        category = attrs.get('category', getattr(self.instance, 'category', None))
        financial_fields = (
            'ppc_value', 'retention', 'invoice_amount', 'invoice_amount_aed',
            'amount_excl_vat', 'grand_total', 'balance_to_be_received',
            'actual_payment_received', 'paid_amount_excl_vat',
        )
        if category != 'internal':
            invalid = [name for name in financial_fields if attrs.get(name) is not None and attrs[name] < 0]
            if invalid:
                raise serializers.ValidationError({name: 'Amount cannot be negative.' for name in invalid})
        return attrs
