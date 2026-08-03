from rest_framework import serializers

from .models import (
    IOListProject, IOListDocument, IOListExtractedComment, IOListExtractedRow,
)


class IOListExtractedCommentSerializer(serializers.ModelSerializer):
    class Meta:
        model  = IOListExtractedComment
        fields = [
            'id', 's_no', 'company_comment', 'contractor_reply',
            'company_decision', 'status_code', 'status_meaning',
            'page_number', 'linked_tags',
        ]


class IOListExtractedRowSerializer(serializers.ModelSerializer):
    class Meta:
        model  = IOListExtractedRow
        fields = ['id', 'tag_number', 'page_number', 'data']


class IOListProjectSerializer(serializers.ModelSerializer):
    """Serializer for I/O List Project CRUD."""
    
    document_count = serializers.IntegerField(read_only=True, required=False)
    created_by_name = serializers.CharField(
        source='created_by.get_full_name', read_only=True, required=False
    )
    
    class Meta:
        model = IOListProject
        fields = [
            'id', 'project_name', 'project_code', 'description',
            'category', 'status', 'client', 'location', 'tags',
            'created_by', 'created_by_name', 'created_at', 'updated_at',
            'document_count',
        ]
        read_only_fields = ['created_by', 'created_at', 'updated_at']
    
    def create(self, validated_data):
        """Automatically set created_by from request context."""
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            validated_data['created_by'] = request.user
        return super().create(validated_data)


class IOListDocumentListSerializer(serializers.ModelSerializer):
    uploaded_by_email = serializers.SerializerMethodField()
    project_name_ref = serializers.CharField(
        source='project.project_name', read_only=True, allow_null=True
    )

    class Meta:
        model  = IOListDocument
        fields = [
            'id', 'project_name', 'document_number', 'revision_label',
            'plant', 'unit', 'status', 'extraction_stats',
            'crs_chain_id', 'project', 'project_name_ref',
            'uploaded_by_email',
            'created_at', 'updated_at',
        ]

    def get_uploaded_by_email(self, obj):
        return obj.uploaded_by.email if obj.uploaded_by else None


class IOListDocumentDetailSerializer(IOListDocumentListSerializer):
    extracted_comments = IOListExtractedCommentSerializer(many=True, read_only=True)
    extracted_rows     = IOListExtractedRowSerializer(many=True, read_only=True)
    pdf_url            = serializers.SerializerMethodField()

    class Meta(IOListDocumentListSerializer.Meta):
        fields = IOListDocumentListSerializer.Meta.fields + [
            'pdf_url', 'extraction_error',
            'extracted_comments', 'extracted_rows',
        ]

    def get_pdf_url(self, obj):
        try:
            return obj.pdf_file.url
        except Exception:
            return None
