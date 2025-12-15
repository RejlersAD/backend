from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    PIDDrawingViewSet, 
    PIDAnalysisReportViewSet, 
    PIDIssueViewSet,
    ReferenceDocumentViewSet
)

router = DefaultRouter()
router.register(r'drawings', PIDDrawingViewSet, basename='pid-drawing')
router.register(r'reports', PIDAnalysisReportViewSet, basename='pid-report')
router.register(r'issues', PIDIssueViewSet, basename='pid-issue')
router.register(r'reference-documents', ReferenceDocumentViewSet, basename='reference-document')

urlpatterns = [
    path('', include(router.urls)),
]
