"""
Timesheet Mirror Sync Health Monitoring
========================================
Monitors the office-side sync agent health and sends alerts when data becomes stale.

Design:
- Celery Beat task runs every 15 minutes
- Checks TimesheetEvent table for last sync timestamp
- If data age exceeds threshold (soft-coded, default 2 hours), sends alert
- Integrates with apps.notifications for email/in-app notifications
- Logs all health checks for troubleshooting

Soft-coded configuration:
- TIMESHEET_SYNC_HEALTH_ENABLED (default: true when DATA_SOURCE=mirror)
- TIMESHEET_SYNC_STALE_THRESHOLD_HOURS (default: 2)
- TIMESHEET_SYNC_ALERT_COOLDOWN_HOURS (default: 4, prevents alert spam)
- TIMESHEET_SYNC_ALERT_RECIPIENTS (comma-separated emails, default: HR admins)
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Dict, Any

from django.core.cache import cache
from django.core.mail import send_mail
from django.conf import settings
from django.utils import timezone
from decouple import config

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Soft-coded configuration
# ─────────────────────────────────────────────────────────────────────────────

# Enable/disable sync health monitoring (auto-enabled when using mirror mode)
HEALTH_MONITORING_ENABLED = config(
    'TIMESHEET_SYNC_HEALTH_ENABLED',
    default='true'
).lower() in ('1', 'true', 'yes', 'on')

# Hours since last sync before considering data "stale"
STALE_THRESHOLD_HOURS = float(config(
    'TIMESHEET_SYNC_STALE_THRESHOLD_HOURS',
    default='2'
))

# Hours to wait between sending duplicate alerts (prevents spam)
ALERT_COOLDOWN_HOURS = float(config(
    'TIMESHEET_SYNC_ALERT_COOLDOWN_HOURS',
    default='4'
))

# Comma-separated list of email recipients for sync alerts
# Defaults to HR admins from RBAC system
ALERT_RECIPIENTS_RAW = config(
    'TIMESHEET_SYNC_ALERT_RECIPIENTS',
    default=''
)

# Cache key for tracking last alert sent
LAST_ALERT_CACHE_KEY = 'timesheet:sync_health:last_alert'

# Cache key for storing last sync status
SYNC_STATUS_CACHE_KEY = 'timesheet:sync_health:status'


# ─────────────────────────────────────────────────────────────────────────────
# Helper Functions
# ─────────────────────────────────────────────────────────────────────────────

def _get_alert_recipients() -> list[str]:
    """Get list of email addresses to send sync alerts to."""
    if ALERT_RECIPIENTS_RAW:
        return [e.strip() for e in ALERT_RECIPIENTS_RAW.split(',') if e.strip()]
    
    # Fallback: get all users with HR admin roles
    try:
        from apps.rbac.models import UserProfile
        from apps.rbac.rbac_config import HR_MANAGER_ROLE_CODES
        
        hr_profiles = UserProfile.objects.filter(
            roles__code__in=HR_MANAGER_ROLE_CODES,
            user__is_active=True,
            user__email__isnull=False
        ).distinct()
        
        emails = [p.user.email for p in hr_profiles if p.user.email]
        if emails:
            return emails
    except Exception as e:
        logger.debug(f'Failed to fetch HR admin emails: {e}')
    
    # Last resort: system admin email
    default = getattr(settings, 'DEFAULT_FROM_EMAIL', 'noreply@radai.ae')
    return [default]


def _get_last_sync_time() -> datetime | None:
    """Query TimesheetEvent table for the most recent event timestamp."""
    try:
        from .models import TimesheetEvent
        
        latest_event = TimesheetEvent.objects.order_by('-event_time').first()
        if latest_event:
            return latest_event.event_time
        return None
    except Exception as e:
        logger.error(f'Failed to query last sync time: {e}')
        return None


def _calculate_data_age(last_sync: datetime | None) -> timedelta | None:
    """Calculate how old the last synced data is."""
    if not last_sync:
        return None
    
    now = timezone.now()
    # Ensure last_sync is timezone-aware
    if timezone.is_naive(last_sync):
        last_sync = timezone.make_aware(last_sync)
    
    return now - last_sync


def _should_send_alert() -> bool:
    """Check if enough time has passed since last alert (cooldown period)."""
    last_alert_time = cache.get(LAST_ALERT_CACHE_KEY)
    if not last_alert_time:
        return True
    
    now = timezone.now()
    if isinstance(last_alert_time, str):
        try:
            last_alert_time = datetime.fromisoformat(last_alert_time)
        except Exception:
            return True
    
    if timezone.is_naive(last_alert_time):
        last_alert_time = timezone.make_aware(last_alert_time)
    
    cooldown_delta = timedelta(hours=ALERT_COOLDOWN_HOURS)
    return (now - last_alert_time) > cooldown_delta


def _mark_alert_sent():
    """Record that we just sent an alert (for cooldown tracking)."""
    # Cache for 24 hours - long enough to cover cooldown period
    cache.set(LAST_ALERT_CACHE_KEY, timezone.now().isoformat(), timeout=86400)


def _send_sync_alert(
    last_sync: datetime | None,
    data_age: timedelta | None,
    status: Dict[str, Any]
):
    """Send email and in-app notification about stale sync data."""
    
    recipients = _get_alert_recipients()
    if not recipients:
        logger.warning('No recipients configured for sync alerts')
        return
    
    # Format data age for display
    if data_age:
        hours = data_age.total_seconds() / 3600
        if hours < 24:
            age_str = f"{hours:.1f} hours"
        else:
            days = hours / 24
            age_str = f"{days:.1f} days"
    else:
        age_str = "unknown"
    
    # Format last sync time
    last_sync_str = last_sync.strftime('%Y-%m-%d %H:%M UTC') if last_sync else 'Never'
    
    # Email subject and body
    subject = '🚨 RAD AI: Attendance Sync Agent Has Stopped'
    
    body = f"""
RAD AI Attendance Sync Health Alert
=====================================

⚠️  The office-side attendance sync agent has stopped running.

Details:
--------
Last punch synced: {last_sync_str}
Data age: {age_str}
Threshold: {STALE_THRESHOLD_HOURS} hours
Current time: {timezone.now().strftime('%Y-%m-%d %H:%M UTC')}

Impact:
-------
- Live attendance data on https://www.radai.ae/hr/employees is outdated
- New punch events from the biometric system are not being synchronized
- Employees cannot see their current attendance status

Action Required:
----------------
1. Log into the office server where the sync agent is installed
2. Check if the sync agent process is running:
   - Windows: Check Task Scheduler for "RAD AI Attendance Sync" task
   - Linux: Check systemd service: systemctl status radai-attendance-sync

3. If stopped, restart the agent:
   - Windows: Manually run the scheduled task OR restart the service
   - Linux: sudo systemctl restart radai-attendance-sync

4. Monitor the RAD AI backend logs to confirm sync resumes:
   - Railway: https://railway.app (check backend logs for "Mirror ingest")
   - Look for POST requests to /api/v1/timesheet/mirror/ingest/

5. Verify data is flowing:
   - Visit https://www.radai.ae/hr/employees
   - Check "Live" tab - data age should decrease
   - New punches should appear within 15-30 minutes

Troubleshooting:
----------------
- Sync agent script location: office server
- Railway production: https://railway.app (check backend health)
- Contact: IT Admin or DevOps team

This is an automated alert from the RAD AI monitoring system.
Alerts are throttled to once every {ALERT_COOLDOWN_HOURS} hours to prevent spam.

---
RAD AI - Intelligent Engineering Platform
https://www.radai.ae
""".strip()
    
    try:
        # Send email
        send_mail(
            subject=subject,
            message=body,
            from_email=settings.DEFAULT_FROM_EMAIL,
            recipient_list=recipients,
            fail_silently=False,
        )
        logger.info(
            f'Sync alert email sent to {len(recipients)} recipient(s): {", ".join(recipients)}'
        )
        
        # Try to create in-app notification (if notifications app is available)
        try:
            from apps.notifications.models import Notification
            from django.contrib.auth import get_user_model
            
            User = get_user_model()
            
            # Send notification to all recipients
            for email in recipients:
                try:
                    user = User.objects.get(email=email, is_active=True)
                    Notification.objects.create(
                        recipient=user,
                        category='system',
                        severity='critical',
                        title='Attendance Sync Agent Stopped',
                        message=(
                            f'The office-side attendance sync agent has stopped running. '
                            f'Last sync: {last_sync_str} ({age_str} ago). '
                            f'Restart the sync agent on the office server to resume data flow.'
                        ),
                        action_url='/hr/employees',
                        action_text='View Attendance',
                        metadata=status,
                    )
                except User.DoesNotExist:
                    logger.debug(f'User not found for notification: {email}')
                except Exception as e:
                    logger.debug(f'Failed to create notification for {email}: {e}')
        except ImportError:
            # Notifications app not available
            pass
        except Exception as e:
            logger.debug(f'Failed to create in-app notifications: {e}')
        
        _mark_alert_sent()
        
    except Exception as e:
        logger.error(f'Failed to send sync alert email: {e}', exc_info=True)


# ─────────────────────────────────────────────────────────────────────────────
# Main Monitoring Function
# ─────────────────────────────────────────────────────────────────────────────

def check_sync_health() -> Dict[str, Any]:
    """
    Check the health of the timesheet mirror sync agent.
    
    Returns:
        dict: Status dictionary containing:
            - healthy (bool): True if sync is recent, False if stale
            - last_sync (datetime|None): Timestamp of last synced event
            - data_age_hours (float|None): Hours since last sync
            - threshold_hours (float): Configured stale threshold
            - alert_sent (bool): True if alert was triggered this check
            - message (str): Human-readable status message
    """
    
    # Get last sync time from database
    last_sync = _get_last_sync_time()
    data_age = _calculate_data_age(last_sync)
    
    # Build status response
    status = {
        'healthy': False,
        'last_sync': last_sync.isoformat() if last_sync else None,
        'data_age_hours': None,
        'threshold_hours': STALE_THRESHOLD_HOURS,
        'alert_sent': False,
        'message': '',
        'checked_at': timezone.now().isoformat(),
    }
    
    # No events ever synced
    if not last_sync:
        status['message'] = 'No events have been synced yet. Sync agent needs initial run.'
        status['healthy'] = False
        
        # Send alert if we should
        if _should_send_alert():
            _send_sync_alert(last_sync, data_age, status)
            status['alert_sent'] = True
        
        return status
    
    # Calculate data age in hours
    if data_age:
        data_age_hours = data_age.total_seconds() / 3600
        status['data_age_hours'] = round(data_age_hours, 2)
        
        # Check if data is stale
        if data_age_hours > STALE_THRESHOLD_HOURS:
            status['healthy'] = False
            status['message'] = (
                f'Sync data is stale ({data_age_hours:.1f}h old, '
                f'threshold: {STALE_THRESHOLD_HOURS}h). '
                f'Sync agent may have stopped.'
            )
            
            # Send alert if cooldown period has passed
            if _should_send_alert():
                _send_sync_alert(last_sync, data_age, status)
                status['alert_sent'] = True
        else:
            status['healthy'] = True
            status['message'] = (
                f'Sync is healthy. Last sync {data_age_hours:.1f}h ago '
                f'(threshold: {STALE_THRESHOLD_HOURS}h).'
            )
    else:
        status['message'] = 'Unable to calculate data age.'
    
    # Cache status for API endpoint to read
    cache.set(SYNC_STATUS_CACHE_KEY, status, timeout=1800)  # 30 minutes
    
    return status


def get_cached_sync_status() -> Dict[str, Any] | None:
    """Retrieve the last cached sync health status (used by API endpoint)."""
    return cache.get(SYNC_STATUS_CACHE_KEY)
