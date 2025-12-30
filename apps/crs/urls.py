from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CRSDocumentViewSet,
    CRSCommentViewSet,
    CRSActivityViewSet,
    GoogleSheetConfigViewSet
)
from .revision_views import (
    CRSRevisionChainViewSet,
    CRSRevisionViewSet,
    CRSCommentLinkViewSet,
    CRSAIInsightViewSet,
    CRSRevisionActivityViewSet
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
        get_history_config,
        delete_from_history,
        generate_share_link,
        get_file_metadata,
    )
    HISTORY_AVAILABLE = True
except ImportError:
    HISTORY_AVAILABLE = False

router = DefaultRouter()
router.register(r'documents', CRSDocumentViewSet, basename='crs-document')
router.register(r'comments', CRSCommentViewSet, basename='crs-comment')
router.register(r'activities', CRSActivityViewSet, basename='crs-activity')
router.register(r'google-configs', GoogleSheetConfigViewSet, basename='google-config')

# CRS Multiple Revision endpoints
router.register(r'revision-chains', CRSRevisionChainViewSet, basename='crs-revision-chain')
router.register(r'revisions', CRSRevisionViewSet, basename='crs-revision')
router.register(r'comment-links', CRSCommentLinkViewSet, basename='crs-comment-link')
router.register(r'ai-insights', CRSAIInsightViewSet, basename='crs-ai-insight')
router.register(r'revision-activities', CRSRevisionActivityViewSet, basename='crs-revision-activity')

# Start with history endpoints (must come before router.urls to avoid conflicts)
urlpatterns = []

# Add history endpoints if available
if HISTORY_AVAILABLE:
    urlpatterns += [
        path('documents/history/', user_history_overview, name='crs-history-overview'),
        path('documents/history/config/', get_history_config, name='crs-history-config'),
        path('documents/history/uploads/', user_uploads, name='crs-history-uploads'),
        path('documents/history/exports/', user_exports, name='crs-history-exports'),
        path('documents/history/profile/', user_profile, name='crs-history-profile'),
        path('documents/history/activity/', user_activity, name='crs-history-activity'),
        path('documents/history/download/', download_from_history, name='crs-history-download'),
        path('documents/history/delete/', delete_from_history, name='crs-history-delete'),
        path('documents/history/share/', generate_share_link, name='crs-history-share'),
        path('documents/history/metadata/', get_file_metadata, name='crs-history-metadata'),
    ]

# Add router URLs last (less specific patterns)
urlpatterns += [
    path('', include(router.urls)),
]
