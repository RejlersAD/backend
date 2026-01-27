"""
WebSocket Consumers for Real-time Detection and Alerts
"""
import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class RealTimeDetectionConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time detection updates
    Streams alerts and anomalies to connected clients
    """
    
    async def connect(self):
        """Handle WebSocket connection"""
        self.room_group_name = 'realtime_detection'
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Send initial connection message
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'message': 'Connected to real-time detection stream',
            'timestamp': timezone.now().isoformat()
        }))
        
        # Start sending periodic updates
        asyncio.create_task(self.send_periodic_updates())
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        # Leave room group
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """Handle messages from WebSocket"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'request_alerts':
                await self.send_recent_alerts()
            elif message_type == 'request_metrics':
                await self.send_detection_metrics()
            elif message_type == 'acknowledge_alert':
                await self.acknowledge_alert(data.get('alert_id'))
                
        except Exception as e:
            logger.error(f"WebSocket receive error: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))
    
    async def send_periodic_updates(self):
        """Send periodic updates every 5 seconds"""
        while True:
            try:
                await asyncio.sleep(5)
                
                # Get latest alerts
                alerts = await self.get_recent_alerts()
                
                # Get detection metrics
                metrics = await self.get_detection_metrics()
                
                # Send update
                await self.send(text_data=json.dumps({
                    'type': 'periodic_update',
                    'timestamp': timezone.now().isoformat(),
                    'alerts': alerts,
                    'metrics': metrics
                }))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic update error: {e}")
    
    async def send_recent_alerts(self):
        """Send recent alerts to client"""
        alerts = await self.get_recent_alerts()
        
        await self.send(text_data=json.dumps({
            'type': 'alerts_update',
            'alerts': alerts,
            'timestamp': timezone.now().isoformat()
        }))
    
    async def send_detection_metrics(self):
        """Send detection metrics to client"""
        metrics = await self.get_detection_metrics()
        
        await self.send(text_data=json.dumps({
            'type': 'metrics_update',
            'metrics': metrics,
            'timestamp': timezone.now().isoformat()
        }))
    
    @database_sync_to_async
    def get_recent_alerts(self, limit=10):
        """Get recent alerts from database"""
        from .models import RealTimeAlert
        
        try:
            alerts = RealTimeAlert.objects.filter(
                status__in=['new', 'acknowledged']
            ).order_by('-detected_at')[:limit]
            
            return [{
                'id': alert.id,
                'type': alert.alert_type,
                'title': alert.title,
                'description': alert.description,
                'severity': alert.severity,
                'confidence': alert.confidence_score,
                'status': alert.status,
                'detected_at': alert.detected_at.isoformat(),
                'is_critical': alert.is_critical
            } for alert in alerts]
            
        except Exception as e:
            logger.error(f"Get alerts error: {e}")
            return []
    
    @database_sync_to_async
    def get_detection_metrics(self):
        """Get detection system metrics"""
        from .models import RealTimeAlert, AnomalyDetection, DetectionMetrics
        from django.db.models import Count, Avg
        
        try:
            now = timezone.now()
            last_hour = now - timedelta(hours=1)
            
            # Alert counts
            total_alerts = RealTimeAlert.objects.filter(detected_at__gte=last_hour).count()
            critical_alerts = RealTimeAlert.objects.filter(
                detected_at__gte=last_hour,
                severity='critical'
            ).count()
            
            # Anomaly stats
            anomalies = AnomalyDetection.objects.filter(
                timestamp__gte=last_hour
            ).aggregate(
                total=Count('id'),
                avg_score=Avg('anomaly_score')
            )
            
            # Detection accuracy (from latest metrics)
            latest_metrics = DetectionMetrics.objects.order_by('-timestamp').first()
            
            return {
                'total_alerts_last_hour': total_alerts,
                'critical_alerts_last_hour': critical_alerts,
                'total_anomalies': anomalies['total'] or 0,
                'avg_anomaly_score': float(anomalies['avg_score'] or 0),
                'accuracy': float(latest_metrics.accuracy) if latest_metrics and latest_metrics.accuracy else 0.0,
                'precision': float(latest_metrics.precision) if latest_metrics and latest_metrics.precision else 0.0
            }
            
        except Exception as e:
            logger.error(f"Get metrics error: {e}")
            return {}
    
    @database_sync_to_async
    def acknowledge_alert(self, alert_id):
        """Acknowledge an alert"""
        from .models import RealTimeAlert
        
        try:
            alert = RealTimeAlert.objects.get(id=alert_id)
            alert.status = 'acknowledged'
            alert.acknowledged_at = timezone.now()
            alert.save()
            
            return True
        except Exception as e:
            logger.error(f"Acknowledge alert error: {e}")
            return False
    
    # Handle broadcast messages
    async def alert_broadcast(self, event):
        """Handle alert broadcast from channel layer"""
        await self.send(text_data=json.dumps({
            'type': 'new_alert',
            'alert': event['alert'],
            'timestamp': timezone.now().isoformat()
        }))
    
    async def anomaly_broadcast(self, event):
        """Handle anomaly broadcast from channel layer"""
        await self.send(text_data=json.dumps({
            'type': 'anomaly_detected',
            'anomaly': event['anomaly'],
            'timestamp': timezone.now().isoformat()
        }))


class SystemMetricsConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time system metrics
    """
    
    async def connect(self):
        """Handle WebSocket connection"""
        self.room_group_name = 'system_metrics'
        
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Start streaming metrics
        asyncio.create_task(self.stream_metrics())
    
    async def disconnect(self, close_code):
        """Handle disconnection"""
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def stream_metrics(self):
        """Stream system metrics every 2 seconds"""
        while True:
            try:
                await asyncio.sleep(2)
                
                metrics = await self.collect_system_metrics()
                
                await self.send(text_data=json.dumps({
                    'type': 'metrics_update',
                    'metrics': metrics,
                    'timestamp': timezone.now().isoformat()
                }))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Metrics streaming error: {e}")
    
    @database_sync_to_async
    def collect_system_metrics(self):
        """Collect current system metrics"""
        from .models import RealTimeDataStream
        import psutil
        
        try:
            # System resource metrics
            cpu_percent = psutil.cpu_percent(interval=0.1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            # Database metrics
            recent_streams = RealTimeDataStream.objects.filter(
                timestamp__gte=timezone.now() - timedelta(minutes=5)
            ).count()
            
            return {
                'cpu_usage': cpu_percent,
                'memory_usage': memory.percent,
                'disk_usage': disk.percent,
                'active_streams': recent_streams,
                'timestamp': timezone.now().isoformat()
            }
            
        except Exception as e:
            logger.error(f"Collect metrics error: {e}")
            return {}
    
    async def metrics_broadcast(self, event):
        """Handle metrics broadcast"""
        await self.send(text_data=json.dumps(event['data']))
