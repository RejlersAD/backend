from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CRSDocumentViewSet,
    CRSCommentViewSet,
    CRSActivityViewSet,
    GoogleSheetConfigViewSet
)

# Import history views from crs_documents helper
try:
    from apps.crs_documents.history_api.history_views import (
        user_history_overview,
        user_uploads,
        user_exports,
        user_profile,
        user_activity,
        download_from_history,
    )
    HISTORY_AVAILABLE = True
except ImportError:
    HISTORY_AVAILABLE = False

router = DefaultRouter()
router.register(r'documents', CRSDocumentViewSet, basename='crs-document')
router.register(r'comments', CRSCommentViewSet, basename='crs-comment')
router.register(r'activities', CRSActivityViewSet, basename='crs-activity')
router.register(r'google-configs', GoogleSheetConfigViewSet, basename='google-config')

urlpatterns = [
    path('', include(router.urls)),
]

# Add history endpoints if available
if HISTORY_AVAILABLE:
    urlpatterns += [
        path('documents/history/', user_history_overview, name='crs-history-overview'),
        path('documents/history/uploads/', user_uploads, name='crs-history-uploads'),
        path('documents/history/exports/', user_exports, name='crs-history-exports'),
        path('documents/history/profile/', user_profile, name='crs-history-profile'),
        path('documents/history/activity/', user_activity, name='crs-history-activity'),
        path('documents/history/download/', download_from_history, name='crs-history-download'),
    ]
