"""
Serializers for ML Detection API
"""
from rest_framework import serializers
from .models import (
    DetectionConfig, MLDetectionModel, RealTimeAlert,
    AnomalyDetection, DetectionMetrics, RealTimeDataStream
)


class DetectionConfigSerializer(serializers.ModelSerializer):
    """Serializer for detection configuration"""
    
    class Meta:
        model = DetectionConfig
        fields = '__all__'
        read_only_fields = ['created_at', 'updated_at', 'created_by']


class MLDetectionModelSerializer(serializers.ModelSerializer):
    """Serializer for ML models"""
    
    class Meta:
        model = MLDetectionModel
        fields = '__all__'
        read_only_fields = ['created_at']


class RealTimeAlertSerializer(serializers.ModelSerializer):
    """Serializer for real-time alerts"""
    
    time_since_detection = serializers.SerializerMethodField()
    
    class Meta:
        model = RealTimeAlert
        fields = '__all__'
        read_only_fields = ['detected_at', 'created_at', 'updated_at']
    
    def get_time_since_detection(self, obj):
        """Get time since detection in seconds"""
        return int(obj.time_since_detection.total_seconds())


class AnomalyDetectionSerializer(serializers.ModelSerializer):
    """Serializer for anomaly detections"""
    
    class Meta:
        model = AnomalyDetection
        fields = '__all__'
        read_only_fields = ['created_at']


class DetectionMetricsSerializer(serializers.ModelSerializer):
    """Serializer for detection metrics"""
    
    class Meta:
        model = DetectionMetrics
        fields = '__all__'


class RealTimeDataStreamSerializer(serializers.ModelSerializer):
    """Serializer for data streams"""
    
    class Meta:
        model = RealTimeDataStream
        fields = '__all__'
        read_only_fields = ['timestamp']
