"""
CRS Documents URL Configuration
"""

from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import CRSDocumentViewSet

# Import history views from history_api package
from .history_api.history_views import (
    user_history_overview,
    user_uploads,
    user_exports,
    user_profile,
    user_activity,
    download_from_history,
)

router = DefaultRouter()
router.register(r'documents', CRSDocumentViewSet, basename='crs-document')

urlpatterns = [
    path('', include(router.urls)),
    
    # User History & Storage Endpoints
    path('documents/history/', user_history_overview, name='crs-history-overview'),
    path('documents/history/uploads/', user_uploads, name='crs-history-uploads'),
    path('documents/history/exports/', user_exports, name='crs-history-exports'),
    path('documents/history/profile/', user_profile, name='crs-history-profile'),
    path('documents/history/activity/', user_activity, name='crs-history-activity'),
    path('documents/history/download/', download_from_history, name='crs-history-download'),
]
