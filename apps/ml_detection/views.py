"""
API Views for ML Detection System
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, IsAdminUser
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Avg, Q
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
import logging

from .models import (
    DetectionConfig, MLDetectionModel, RealTimeAlert,
    AnomalyDetection, DetectionMetrics, RealTimeDataStream
)
from .serializers import (
    DetectionConfigSerializer, MLDetectionModelSerializer,
    RealTimeAlertSerializer, AnomalyDetectionSerializer,
    DetectionMetricsSerializer, RealTimeDataStreamSerializer
)
from .ml_detector import MLDetectionEngine, AlertGenerator

logger = logging.getLogger(__name__)


class DetectionConfigViewSet(viewsets.ModelViewSet):
    """ViewSet for managing detection configurations"""
    
    queryset = DetectionConfig.objects.all()
    serializer_class = DetectionConfigSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    def perform_create(self, serializer):
        """Save config with creator"""
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def activate(self, request, pk=None):
        """Activate a detection config"""
        config = self.get_object()
        config.is_active = True
        config.save()
        return Response({'status': 'activated'})
    
    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        """Deactivate a detection config"""
        config = self.get_object()
        config.is_active = False
        config.save()
        return Response({'status': 'deactivated'})
    
    @action(detail=False, methods=['get'])
    def active_configs(self, request):
        """Get all active configurations"""
        active = self.queryset.filter(is_active=True)
        serializer = self.get_serializer(active, many=True)
        return Response(serializer.data)


class MLDetectionModelViewSet(viewsets.ModelViewSet):
    """ViewSet for ML detection models"""
    
    queryset = MLDetectionModel.objects.all()
    serializer_class = MLDetectionModelSerializer
    permission_classes = [IsAuthenticated, IsAdminUser]
    
    @action(detail=True, methods=['post'])
    def set_active(self, request, pk=None):
        """Set model as active"""
        model = self.get_object()
        model.is_active = True
        model.save()
        return Response({'status': 'activated'})
    
    @action(detail=False, methods=['get'])
    def active_models(self, request):
        """Get all active models"""
        active = self.queryset.filter(is_active=True)
        serializer = self.get_serializer(active, many=True)
        return Response(serializer.data)


class RealTimeAlertViewSet(viewsets.ModelViewSet):
    """ViewSet for real-time alerts"""
    
    queryset = RealTimeAlert.objects.all()
    serializer_class = RealTimeAlertSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter queryset based on user permissions"""
        queryset = super().get_queryset()
        
        # Filter by status if provided
        status_filter = self.request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        # Filter by severity
        severity_filter = self.request.query_params.get('severity')
        if severity_filter:
            queryset = queryset.filter(severity=severity_filter)
        
        # Filter by time range
        hours = self.request.query_params.get('hours')
        if hours:
            since = timezone.now() - timedelta(hours=int(hours))
            queryset = queryset.filter(detected_at__gte=since)
        
        return queryset.order_by('-detected_at')
    
    @action(detail=True, methods=['post'])
    def acknowledge(self, request, pk=None):
        """Acknowledge an alert"""
        alert = self.get_object()
        alert.status = 'acknowledged'
        alert.acknowledged_by = request.user
        alert.acknowledged_at = timezone.now()
        alert.save()
        
        # Broadcast update
        self._broadcast_alert_update(alert)
        
        return Response({
            'status': 'acknowledged',
            'alert_id': alert.id
        })
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Resolve an alert"""
        alert = self.get_object()
        alert.status = 'resolved'
        alert.resolved_by = request.user
        alert.resolved_at = timezone.now()
        alert.resolution_notes = request.data.get('notes', '')
        alert.save()
        
        # Broadcast update
        self._broadcast_alert_update(alert)
        
        return Response({
            'status': 'resolved',
            'alert_id': alert.id
        })
    
    @action(detail=True, methods=['post'])
    def mark_false_positive(self, request, pk=None):
        """Mark alert as false positive"""
        alert = self.get_object()
        alert.status = 'false_positive'
        alert.resolved_by = request.user
        alert.resolved_at = timezone.now()
        alert.resolution_notes = f"Marked as false positive: {request.data.get('reason', '')}"
        alert.save()
        
        return Response({
            'status': 'false_positive',
            'alert_id': alert.id
        })
    
    @action(detail=False, methods=['get'])
    def critical_alerts(self, request):
        """Get all critical alerts"""
        critical = self.queryset.filter(
            severity='critical',
            status__in=['new', 'acknowledged']
        )
        serializer = self.get_serializer(critical, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def dashboard_summary(self, request):
        """Get alert summary for dashboard"""
        now = timezone.now()
        last_hour = now - timedelta(hours=1)
        last_24h = now - timedelta(hours=24)
        
        summary = {
            'total_alerts_24h': self.queryset.filter(detected_at__gte=last_24h).count(),
            'total_alerts_1h': self.queryset.filter(detected_at__gte=last_hour).count(),
            'critical_active': self.queryset.filter(
                severity='critical',
                status__in=['new', 'acknowledged']
            ).count(),
            'high_active': self.queryset.filter(
                severity='high',
                status__in=['new', 'acknowledged']
            ).count(),
            'by_status': dict(
                self.queryset.filter(detected_at__gte=last_24h)
                .values('status')
                .annotate(count=Count('id'))
                .values_list('status', 'count')
            ),
            'by_severity': dict(
                self.queryset.filter(detected_at__gte=last_24h)
                .values('severity')
                .annotate(count=Count('id'))
                .values_list('severity', 'count')
            )
        }
        
        return Response(summary)
    
    def _broadcast_alert_update(self, alert):
        """Broadcast alert update via WebSocket"""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                'realtime_detection',
                {
                    'type': 'alert_broadcast',
                    'alert': {
                        'id': alert.id,
                        'status': alert.status,
                        'severity': alert.severity,
                        'title': alert.title
                    }
                }
            )
        except Exception as e:
            logger.error(f"Broadcast error: {e}")


class DetectionAnalyticsViewSet(viewsets.ViewSet):
    """ViewSet for detection analytics and real-time detection"""
    
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def analyze_realtime(self, request):
        """Analyze data point in real-time"""
        try:
            data_point = request.data.get('data_point')
            stream_type = request.data.get('stream_type', 'user_activity')
            
            if not data_point:
                return Response(
                    {'error': 'data_point required'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Get historical data
            historical = self._get_historical_data(stream_type, limit=100)
            
            # Initialize ML engine
            engine = MLDetectionEngine()
            
            # Perform analysis
            result = engine.analyze_realtime(data_point, stream_type, historical)
            
            # Store data point
            self._store_data_stream(stream_type, data_point, result)
            
            # Generate alert if needed
            if result['overall_anomaly']:
                self._handle_anomaly_detection(result, stream_type)
            
            return Response({
                'success': True,
                'result': result,
                'timestamp': timezone.now().isoformat()
            })
            
        except Exception as e:
            logger.error(f"Real-time analysis error: {e}")
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def detection_metrics(self, request):
        """Get detection system metrics"""
        hours = int(request.query_params.get('hours', 24))
        since = timezone.now() - timedelta(hours=hours)
        
        # Anomaly stats
        anomalies = AnomalyDetection.objects.filter(
            timestamp__gte=since
        ).aggregate(
            total=Count('id'),
            total_anomalies=Count('id', filter=Q(is_anomaly=True)),
            avg_score=Avg('anomaly_score')
        )
        
        # Alert stats
        alerts = RealTimeAlert.objects.filter(
            detected_at__gte=since
        ).aggregate(
            total=Count('id'),
            avg_confidence=Avg('confidence_score')
        )
        
        # Latest metrics
        latest_metrics = DetectionMetrics.objects.order_by('-timestamp').first()
        
        return Response({
            'anomalies': anomalies,
            'alerts': alerts,
            'system_metrics': {
                'accuracy': float(latest_metrics.accuracy) if latest_metrics and latest_metrics.accuracy else None,
                'precision': float(latest_metrics.precision) if latest_metrics and latest_metrics.precision else None,
                'recall': float(latest_metrics.recall) if latest_metrics and latest_metrics.recall else None,
                'f1_score': float(latest_metrics.f1_score) if latest_metrics and latest_metrics.f1_score else None,
            },
            'time_range_hours': hours
        })
    
    @action(detail=False, methods=['get'])
    def anomaly_trends(self, request):
        """Get anomaly trends over time"""
        hours = int(request.query_params.get('hours', 24))
        since = timezone.now() - timedelta(hours=hours)
        
        # Group by hour
        from django.db.models.functions import TruncHour
        
        trends = AnomalyDetection.objects.filter(
            timestamp__gte=since
        ).annotate(
            hour=TruncHour('timestamp')
        ).values('hour').annotate(
            total=Count('id'),
            anomalies=Count('id', filter=Q(is_anomaly=True)),
            avg_score=Avg('anomaly_score')
        ).order_by('hour')
        
        return Response({
            'trends': list(trends),
            'time_range_hours': hours
        })
    
    def _get_historical_data(self, stream_type, limit=100):
        """Get historical data for analysis"""
        streams = RealTimeDataStream.objects.filter(
            stream_type=stream_type
        ).order_by('-timestamp')[:limit]
        
        return [stream.data for stream in streams]
    
    def _store_data_stream(self, stream_type, data_point, analysis_result):
        """Store data stream point"""
        try:
            RealTimeDataStream.objects.create(
                stream_type=stream_type,
                data=data_point,
                features=analysis_result.get('detections', {}),
                analyzed=True,
                anomaly_detected=analysis_result.get('overall_anomaly', False),
                ml_score=analysis_result.get('max_confidence', 0.0)
            )
        except Exception as e:
            logger.error(f"Store data stream error: {e}")
    
    def _handle_anomaly_detection(self, result, stream_type):
        """Handle anomaly detection and alerting"""
        try:
            # Get active detection config
            config = DetectionConfig.objects.filter(
                is_active=True,
                detection_type='anomaly'
            ).first()
            
            if not config:
                return
            
            # Check if alert should be generated
            alert_gen = AlertGenerator()
            
            if alert_gen.should_alert(result, config.id):
                # Generate alert
                alert = alert_gen.generate_alert(result, config)
                
                if alert:
                    # Broadcast via WebSocket
                    self._broadcast_new_alert(alert)
                    
                    logger.info(f"Generated alert: {alert.id}")
            
        except Exception as e:
            logger.error(f"Handle anomaly error: {e}")
    
    def _broadcast_new_alert(self, alert):
        """Broadcast new alert via WebSocket"""
        try:
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                'realtime_detection',
                {
                    'type': 'alert_broadcast',
                    'alert': {
                        'id': alert.id,
                        'type': alert.alert_type,
                        'title': alert.title,
                        'description': alert.description,
                        'severity': alert.severity,
                        'confidence': float(alert.confidence_score),
                        'detected_at': alert.detected_at.isoformat()
                    }
                }
            )
        except Exception as e:
            logger.error(f"Broadcast new alert error: {e}")
