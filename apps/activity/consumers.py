"""
WebSocket Consumer for Real-time Activity Stream
"""
import json
import asyncio
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.utils import timezone
from datetime import timedelta
import logging

logger = logging.getLogger(__name__)


class ActivityStreamConsumer(AsyncWebsocketConsumer):
    """
    WebSocket consumer for real-time activity streaming
    """
    
    async def connect(self):
        """Handle WebSocket connection"""
        self.room_group_name = 'activity_stream'
        
        # Join room group
        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )
        
        await self.accept()
        
        # Send initial connection message
        await self.send(text_data=json.dumps({
            'type': 'connection_established',
            'message': 'Connected to activity stream',
            'timestamp': timezone.now().isoformat()
        }))
        
        # Send initial activities
        activities = await self.get_recent_activities(limit=20)
        await self.send(text_data=json.dumps({
            'type': 'initial_activities',
            'activities': activities,
            'timestamp': timezone.now().isoformat()
        }))
        
        # Send active users
        active_users = await self.get_active_users()
        await self.send(text_data=json.dumps({
            'type': 'active_users',
            'users': active_users,
            'count': len(active_users),
            'timestamp': timezone.now().isoformat()
        }))
        
        # Start periodic updates
        asyncio.create_task(self.send_periodic_updates())
    
    async def disconnect(self, close_code):
        """Handle WebSocket disconnection"""
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )
    
    async def receive(self, text_data):
        """Handle messages from WebSocket"""
        try:
            data = json.loads(text_data)
            message_type = data.get('type')
            
            if message_type == 'request_activities':
                limit = data.get('limit', 20)
                category = data.get('category')
                activities = await self.get_recent_activities(limit, category)
                await self.send(text_data=json.dumps({
                    'type': 'activities_update',
                    'activities': activities,
                    'timestamp': timezone.now().isoformat()
                }))
            
            elif message_type == 'request_stats':
                stats = await self.get_activity_statistics()
                await self.send(text_data=json.dumps({
                    'type': 'statistics_update',
                    'stats': stats,
                    'timestamp': timezone.now().isoformat()
                }))
            
            elif message_type == 'request_active_users':
                active_users = await self.get_active_users()
                await self.send(text_data=json.dumps({
                    'type': 'active_users',
                    'users': active_users,
                    'count': len(active_users),
                    'timestamp': timezone.now().isoformat()
                }))
                
        except Exception as e:
            logger.error(f"WebSocket receive error: {e}")
            await self.send(text_data=json.dumps({
                'type': 'error',
                'message': str(e)
            }))
    
    async def send_periodic_updates(self):
        """Send periodic updates every 3 seconds"""
        while True:
            try:
                await asyncio.sleep(3)
                
                # Get latest activities
                activities = await self.get_recent_activities(limit=5)
                
                # Get statistics
                stats = await self.get_activity_statistics()
                
                # Send update
                await self.send(text_data=json.dumps({
                    'type': 'periodic_update',
                    'activities': activities,
                    'stats': stats,
                    'timestamp': timezone.now().isoformat()
                }))
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Periodic update error: {e}")
    
    @database_sync_to_async
    def get_recent_activities(self, limit=20, category=None):
        """Get recent activities from database"""
        from .models import SystemActivity
        
        try:
            queryset = SystemActivity.objects.all()
            
            if category:
                queryset = queryset.filter(category=category)
            
            activities = queryset.select_related('user')[:limit]
            
            return [{
                'id': activity.id,
                'activity_type': activity.activity_type,
                'category': activity.category,
                'severity': activity.severity,
                'description': activity.description,
                'user_email': activity.user_email,
                'user_name': activity.user_full_name,
                'ip_address': activity.ip_address,
                'success': activity.success,
                'duration_ms': activity.duration_ms,
                'timestamp': activity.timestamp.isoformat(),
                'time_ago': activity.time_ago,
                'details': activity.details,
                'metadata': activity.metadata,
            } for activity in activities]
            
        except Exception as e:
            logger.error(f"Get activities error: {e}")
            return []
    
    @database_sync_to_async
    def get_active_users(self):
        """Get currently active users"""
        from .models import UserSession
        
        try:
            threshold = timezone.now() - timedelta(minutes=5)
            sessions = UserSession.objects.filter(
                last_activity__gte=threshold,
                is_active=True
            ).select_related('user').order_by('-last_activity')
            
            return [{
                'id': session.user.id,
                'email': session.user.email,
                'full_name': session.user.get_full_name(),
                'current_page': session.current_page,
                'device': session.device_type,
                'browser': session.browser,
                'os': session.os,
                'last_activity': session.last_activity.isoformat(),
                'duration_seconds': int(session.duration),
            } for session in sessions]
            
        except Exception as e:
            logger.error(f"Get active users error: {e}")
            return []
    
    @database_sync_to_async
    def get_activity_statistics(self):
        """Get activity statistics"""
        from .models import SystemActivity
        from django.db.models import Count, Avg, Q
        
        try:
            now = timezone.now()
            last_hour = now - timedelta(hours=1)
            last_24h = now - timedelta(hours=24)
            
            # Count by time period
            activities_last_hour = SystemActivity.objects.filter(
                timestamp__gte=last_hour
            ).count()
            
            activities_last_24h = SystemActivity.objects.filter(
                timestamp__gte=last_24h
            ).count()
            
            # Count by category
            by_category = dict(
                SystemActivity.objects.filter(
                    timestamp__gte=last_hour
                ).values('category').annotate(
                    count=Count('id')
                ).values_list('category', 'count')
            )
            
            # Count by severity
            by_severity = dict(
                SystemActivity.objects.filter(
                    timestamp__gte=last_hour
                ).values('severity').annotate(
                    count=Count('id')
                ).values_list('severity', 'count')
            )
            
            # Success rate
            total = SystemActivity.objects.filter(timestamp__gte=last_hour).count()
            successful = SystemActivity.objects.filter(
                timestamp__gte=last_hour,
                success=True
            ).count()
            success_rate = (successful / total * 100) if total > 0 else 100.0
            
            # Average duration
            avg_duration = SystemActivity.objects.filter(
                timestamp__gte=last_hour,
                duration_ms__isnull=False
            ).aggregate(Avg('duration_ms'))['duration_ms__avg'] or 0
            
            # Top activity types
            top_types = list(
                SystemActivity.objects.filter(
                    timestamp__gte=last_hour
                ).values('activity_type').annotate(
                    count=Count('id')
                ).order_by('-count')[:5].values_list('activity_type', 'count')
            )
            
            return {
                'activities_last_hour': activities_last_hour,
                'activities_last_24h': activities_last_24h,
                'activities_per_minute': round(activities_last_hour / 60, 2),
                'by_category': by_category,
                'by_severity': by_severity,
                'success_rate': round(success_rate, 2),
                'avg_duration_ms': round(avg_duration, 2),
                'top_activity_types': [{'type': t[0], 'count': t[1]} for t in top_types],
            }
            
        except Exception as e:
            logger.error(f"Get statistics error: {e}")
            return {}
    
    # Handle broadcast messages
    async def activity_update(self, event):
        """Handle activity broadcast from channel layer"""
        await self.send(text_data=json.dumps({
            'type': 'new_activity',
            'activity': event['activity'],
            'timestamp': timezone.now().isoformat()
        }))
