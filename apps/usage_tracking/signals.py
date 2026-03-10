"""
Usage Tracking Signals

Handles automatic summary updates and cleanup.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from datetime import timedelta
import logging

from .models import UserUsageLog, DepartmentUsageSummary, FeatureUsageSummary

logger = logging.getLogger(__name__)


# Uncomment these receivers if you want real-time summary updates
# NOTE: For high-traffic systems, use async tasks instead

# @receiver(post_save, sender=UserUsageLog)
# def update_summaries_on_log(sender, instance, created, **kwargs):
#     """
#     Update summary tables when new log is created.
#     
#     NOTE: Disabled by default for performance.
#     Use async aggregation tasks instead.
#     """
#     if created:
#         try:
#             # Update department summary
#             dept_summary, _ = DepartmentUsageSummary.objects.get_or_create(
#                 department=instance.department
#             )
#             dept_summary.update_metrics()
#             
#             # Update feature summary
#             feature_summary, _ = FeatureUsageSummary.objects.get_or_create(
#                 feature_name=instance.feature_name
#             )
#             feature_summary.update_metrics()
#             
#         except Exception as e:
#             logger.error(f"[UsageTracking] Signal update failed: {e}")
