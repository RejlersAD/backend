"""
Django signals for ML Detection
"""
from django.db.models.signals import post_save
from django.dispatch import receiver
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import logging

from .models import RealTimeAlert, AnomalyDetection

logger = logging.getLogger(__name__)


@receiver(post_save, sender=RealTimeAlert)
def broadcast_new_alert(sender, instance, created, **kwargs):
    """Broadcast new alerts via WebSocket"""
    if created:
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                'realtime_detection',
                {
                    'type': 'alert_broadcast',
                    'alert': {
                        'id': instance.id,
                        'type': instance.alert_type,
                        'title': instance.title,
                        'description': instance.description,
                        'severity': instance.severity,
                        'confidence': float(instance.confidence_score),
                        'detected_at': instance.detected_at.isoformat(),
                        'status': instance.status
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to broadcast alert: {e}")


@receiver(post_save, sender=AnomalyDetection)
def broadcast_anomaly(sender, instance, created, **kwargs):
    """Broadcast anomalies via WebSocket"""
    if created and instance.is_anomaly:
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                'realtime_detection',
                {
                    'type': 'anomaly_broadcast',
                    'anomaly': {
                        'id': instance.id,
                        'feature_name': instance.feature_name,
                        'observed_value': float(instance.observed_value),
                        'expected_value': float(instance.expected_value),
                        'anomaly_score': float(instance.anomaly_score),
                        'timestamp': instance.timestamp.isoformat()
                    }
                }
            )
        except Exception as e:
            logger.error(f"Failed to broadcast anomaly: {e}")
