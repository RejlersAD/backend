from rest_framework import serializers
from .models import CustomerInvoice, InvoiceAttachment
from .services.receivable_balance import receivable_balance


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
    canonical_project = serializers.SerializerMethodField()
    canonical_client = serializers.SerializerMethodField()
    canonical_links = serializers.SerializerMethodField()
    calculated_receivable_balance = serializers.SerializerMethodField()
    attachments = InvoiceAttachmentSerializer(many=True, read_only=True)
    attachments_count = serializers.IntegerField(source='attachments.count', read_only=True)
    payment_status_label = serializers.CharField(source='get_payment_status_display', read_only=True)
    category_label = serializers.CharField(source='get_category_display', read_only=True)

    def get_calculated_receivable_balance(self, obj):
        balance = receivable_balance(obj.invoice_amount, obj.actual_payment_received)
        return format(balance, '.2f') if balance is not None else None

    def get_canonical_links(self, obj):
        from apps.finance.shared_record_links import canonical_links
        cache = getattr(self, '_canonical_links_cache', None)
        if cache is None:
            cache = self._canonical_links_cache = {}
        if id(obj) not in cache:
            cache[id(obj)] = canonical_links(obj, getattr(self.context.get('request'), 'user', None))
        return cache[id(obj)]

    def get_canonical_project(self, obj):
        link = self.get_canonical_links(obj)['links']['project']
        return link['id'] if link else None

    def get_canonical_client(self, obj):
        link = self.get_canonical_links(obj)['links']['client']
        return link['id'] if link else None

    class Meta:
        model = CustomerInvoice
        exclude = ['canonical_identity_basis']
        read_only_fields = ['id', 'created_at', 'updated_at', 'created_by',
                            'days_overdue', 'attachments', 'attachments_count',
                            'payment_status_label', 'category_label']

    def validate(self, attrs):
        protected = {'canonical_project', 'canonical_client', 'canonical_identity_basis', 'canonical_links'}
        if protected.intersection(getattr(self, 'initial_data', {})):
            raise serializers.ValidationError({
                'canonical_links': 'Use the shared-record review command to change canonical references.',
            })
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
