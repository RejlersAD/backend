from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    ElectricalEquipmentTypeViewSet,
    ElectricalDatasheetViewSet,
    DatasheetCommentViewSet
)

app_name = 'electrical_datasheet'

router = DefaultRouter()
router.register(r'equipment-types', ElectricalEquipmentTypeViewSet, basename='equipment-type')
router.register(r'datasheets', ElectricalDatasheetViewSet, basename='datasheet')
router.register(r'comments', DatasheetCommentViewSet, basename='comment')

urlpatterns = [
    path('', include(router.urls)),
]
