from django.urls import path

from .ai_credential_views import (
    CredentialDetailView, CredentialListView, CredentialSelectView, CredentialTestView, ProviderSettingsView,
)


urlpatterns = [
    path('', CredentialListView.as_view(), name='ai-api-key-list'),
    path('providers/<str:provider>/', ProviderSettingsView.as_view(), name='ai-provider-settings'),
    path('<uuid:pk>/', CredentialDetailView.as_view(), name='ai-api-key-detail'),
    path('<uuid:pk>/select/', CredentialSelectView.as_view(), name='ai-api-key-select'),
    path('<uuid:pk>/test/', CredentialTestView.as_view(), name='ai-api-key-test'),
]
