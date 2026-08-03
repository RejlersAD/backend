"""
RADAI Project Planning Application — App Config
"""
from django.apps import AppConfig


class PlanningIntelligenceConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'apps.planning_intelligence'
    label = 'planning_intelligence'
    verbose_name = 'Project Planning Intelligence'
