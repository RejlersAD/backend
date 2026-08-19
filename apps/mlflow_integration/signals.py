"""
Django signals for automatic MLflow tracking
Hooks into existing features without modifying their code

NOTE: Signals are currently disabled to avoid circular import issues.
Use decorators instead for tracking.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver
from apps.mlflow_integration.tracker import mlflow_tracker


# Signals are commented out to avoid model loading issues
# Use decorators instead for explicit tracking

# # Track PID Analysis completions
# @receiver(post_save, sender='pid_analysis.AnalysisReport')
# def track_pid_analysis(sender, instance, created, **kwargs):
# # Track PID Analysis completions
# @receiver(post_save, sender='pid_analysis.AnalysisReport')
# def track_pid_analysis(sender, instance, created, **kwargs):
#     """Track P&ID analysis completion"""
#     pass


# # Track PFD to P&ID conversions
# @receiver(post_save, sender='pfd_converter.PIDConversion')
# def track_pfd_conversion(sender, instance, created, **kwargs):
#     """Track PFD to P&ID conversion completion"""
#     pass


# # Track CRS Document processing
# @receiver(post_save, sender='crs.CRSDocument')
# def track_crs_processing(sender, instance, created, **kwargs):
#     """Track CRS document processing"""
#     pass
