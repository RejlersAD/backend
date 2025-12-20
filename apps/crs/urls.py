from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CRSDocumentViewSet,
    CRSCommentViewSet,
    CRSActivityViewSet,
    GoogleSheetConfigViewSet
)

router = DefaultRouter()
router.register(r'documents', CRSDocumentViewSet, basename='crs-document')
router.register(r'comments', CRSCommentViewSet, basename='crs-comment')
router.register(r'activities', CRSActivityViewSet, basename='crs-activity')
router.register(r'google-configs', GoogleSheetConfigViewSet, basename='google-config')

urlpatterns = [
    path('', include(router.urls)),
]
