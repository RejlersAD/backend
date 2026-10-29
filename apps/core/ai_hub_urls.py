from django.urls import path

from .ai_hub_views import (
    CredentialAnonymousTestView, CredentialDetailView, CredentialRotateView, CredentialsView,
    CredentialTestView, ModelDetailView, ModelsView, OverviewView, ProvidersView,
    RouteDetailView, RoutesView, StorageStatusView, SyncRunView, SyncScheduleView, SyncView,
)

urlpatterns = [
    path('providers', ProvidersView.as_view(), name='ai-hub-providers'),
    path('models', ModelsView.as_view(), name='ai-hub-models'),
    path('models/<slug:provider_id>/<path:model_id>', ModelDetailView.as_view(), name='ai-hub-model-detail'),
    path('credentials/test', CredentialAnonymousTestView.as_view(), name='ai-hub-credential-anon-test'),
    path('credentials/<uuid:pk>/test', CredentialTestView.as_view(), name='ai-hub-credential-test'),
    path('credentials/<uuid:pk>/rotate', CredentialRotateView.as_view(), name='ai-hub-credential-rotate'),
    path('credentials/<uuid:pk>', CredentialDetailView.as_view(), name='ai-hub-credential-detail'),
    path('credentials', CredentialsView.as_view(), name='ai-hub-credentials'),
    path('routes/<slug:task_id>', RouteDetailView.as_view(), name='ai-hub-route-detail'),
    path('routes', RoutesView.as_view(), name='ai-hub-routes'),
    path('sync/schedule', SyncScheduleView.as_view(), name='ai-hub-sync-schedule'),
    path('sync/<uuid:run_id>', SyncRunView.as_view(), name='ai-hub-sync-run'),
    path('sync', SyncView.as_view(), name='ai-hub-sync'),
    path('storage-status', StorageStatusView.as_view(), name='ai-hub-storage-status'),
    path('overview', OverviewView.as_view(), name='ai-hub-overview'),
]
