"""
Process Datasheet URLs
API endpoint routing
"""
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    EquipmentTypeViewSet,
    ProcessDatasheetViewSet,
    DatasheetTemplateViewSet,
    DatasheetValidationRuleViewSet,
    DatasheetExtractionJobViewSet
)

router = DefaultRouter()
router.register(r'equipment-types', EquipmentTypeViewSet, basename='equipment-type')
router.register(r'datasheets', ProcessDatasheetViewSet, basename='datasheet')
router.register(r'templates', DatasheetTemplateViewSet, basename='datasheet-template')
router.register(r'validation-rules', DatasheetValidationRuleViewSet, basename='validation-rule')
router.register(r'extraction-jobs', DatasheetExtractionJobViewSet, basename='extraction-job')

urlpatterns = [
    path('', include(router.urls)),
]
