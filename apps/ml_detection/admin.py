"""
Django admin configuration for ML Detection
"""
from django.contrib import admin
from .models import (
    DetectionConfig, MLDetectionModel, RealTimeAlert,
    AnomalyDetection, DetectionMetrics, RealTimeDataStream
)


@admin.register(DetectionConfig)
class DetectionConfigAdmin(admin.ModelAdmin):
    list_display = ['name', 'detection_type', 'severity', 'is_active', 'is_ml_enabled', 'created_at']
    list_filter = ['detection_type', 'severity', 'is_active', 'is_ml_enabled']
    search_fields = ['name', 'description']
    readonly_fields = ['created_at', 'updated_at']
    
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'detection_type', 'description')
        }),
        ('ML Configuration', {
            'fields': ('ml_config', 'threshold_config', 'is_ml_enabled')
        }),
        ('Alert Configuration', {
            'fields': ('severity', 'auto_notify', 'notification_channels', 'cooldown_period_seconds')
        }),
        ('Status', {
            'fields': ('is_active', 'created_by')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at')
        })
    )


@admin.register(MLDetectionModel)
class MLDetectionModelAdmin(admin.ModelAdmin):
    list_display = ['name', 'model_type', 'version', 'accuracy', 'is_active', 'training_date']
    list_filter = ['model_type', 'is_active']
    search_fields = ['name', 'version']
    readonly_fields = ['created_at']


@admin.register(RealTimeAlert)
class RealTimeAlertAdmin(admin.ModelAdmin):
    list_display = ['id', 'alert_type', 'title', 'severity', 'status', 'confidence_score', 'detected_at']
    list_filter = ['alert_type', 'severity', 'status', 'detected_at']
    search_fields = ['title', 'description']
    readonly_fields = ['detected_at', 'created_at', 'updated_at']
    
    fieldsets = (
        ('Alert Details', {
            'fields': ('alert_type', 'title', 'description', 'severity', 'confidence_score')
        }),
        ('Detection Info', {
            'fields': ('detection_config', 'ml_model', 'detected_data', 'context', 'affected_resources')
        }),
        ('Status & Resolution', {
            'fields': ('status', 'acknowledged_by', 'acknowledged_at', 'resolved_by', 'resolved_at', 'resolution_notes')
        }),
        ('Timestamps', {
            'fields': ('detected_at', 'created_at', 'updated_at')
        })
    )


@admin.register(AnomalyDetection)
class AnomalyDetectionAdmin(admin.ModelAdmin):
    list_display = ['id', 'feature_name', 'observed_value', 'expected_value', 'anomaly_score', 'is_anomaly', 'timestamp']
    list_filter = ['is_anomaly', 'timestamp']
    search_fields = ['feature_name']
    readonly_fields = ['created_at']


@admin.register(DetectionMetrics)
class DetectionMetricsAdmin(admin.ModelAdmin):
    list_display = ['id', 'detection_config', 'accuracy', 'precision', 'recall', 'f1_score', 'timestamp']
    list_filter = ['detection_config', 'timestamp']
    readonly_fields = ['timestamp']


@admin.register(RealTimeDataStream)
class RealTimeDataStreamAdmin(admin.ModelAdmin):
    list_display = ['id', 'stream_type', 'analyzed', 'anomaly_detected', 'ml_score', 'timestamp']
    list_filter = ['stream_type', 'analyzed', 'anomaly_detected', 'timestamp']
    readonly_fields = ['timestamp']
