from django.urls import include, path
from rest_framework.routers import DefaultRouter
from . import views

router = DefaultRouter()
router.register('sources', views.SourceViewSet, basename='replica-source')
router.register('scopes', views.ScopeViewSet, basename='replica-scope')
router.register('entries', views.EntryViewSet, basename='replica-entry')
router.register('extractions', views.ExtractionViewSet, basename='replica-extraction')

urlpatterns = [
    path('agent/config/', views.agent_config),
    path('agent/scans/', views.agent_start),
    path('agent/scans/<uuid:scan_id>/entries/', views.agent_inventory),
    path('agent/scans/<uuid:scan_id>/heartbeat/', views.agent_heartbeat),
    path('agent/scans/<uuid:scan_id>/complete/', views.agent_complete),
    path('agent/entries/<uuid:entry_id>/content/', views.agent_content),
    path('', include(router.urls)),
]
