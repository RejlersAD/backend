from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views

app_name = 'electrical_datasheet'

# DRF Router for ViewSet
router = DefaultRouter()
router.register(r'datasheets', views.ElectricalDatasheetViewSet, basename='datasheets')

urlpatterns = [
    # ViewSet routes (includes verify-transformer action)
    path('', include(router.urls)),
]
