"""
CRS Documents Serializers
"""

from rest_framework import serializers
from .models import CRSDocument, CRSDocumentVersion


class CRSDocumentSerializer(serializers.ModelSerializer):
    """Serializer for CRS Documents"""
    
    uploaded_by_name = serializers.CharField(source='uploaded_by.username', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.username', read_only=True)
    
    class Meta:
        model = CRSDocument
        fields = '__all__'
        read_only_fields = ['uploaded_by', 'approved_by', 'created_at', 'updated_at', 'approved_at']


class CRSDocumentVersionSerializer(serializers.ModelSerializer):
    """Serializer for document versions"""
    
    created_by_name = serializers.CharField(source='created_by.username', read_only=True)
    
    class Meta:
        model = CRSDocumentVersion
        fields = '__all__'
        read_only_fields = ['created_by', 'created_at']
